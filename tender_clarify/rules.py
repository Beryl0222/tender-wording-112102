"""澄清的效力、范围与译文核对。

- 只有已签发（decision=issued 且有 issued_at）的澄清产生效力。
- 效力不超出它明确指向的标段；原文未提标段时覆盖全部标段，但可用
  「某区无变化」明确排除。
- 普通回复、旧稿和译文不能自行扩大效力。
- 原文与译文在数值、量纲或要求强弱上冲突的部分留在待签状态，其余
  能独立解释的部分仍可发布。
- 同一问题有更正版时，按标段与签发时间取最新的签发文本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import text as textmod


@dataclass(frozen=True)
class Scope:
    lots: tuple[str, ...] | None = None
    excluded_lots: tuple[str, ...] = ()

    def covers(self, lot: str) -> bool:
        if lot in self.excluded_lots:
            return False
        return self.lots is None or lot in self.lots

    def intersect(self, other: "Scope") -> "Scope":
        if self.lots is None:
            lots = other.lots
        elif other.lots is None:
            lots = self.lots
        else:
            lots = tuple(lot for lot in self.lots if lot in other.lots)
        excluded = tuple(dict.fromkeys((*self.excluded_lots, *other.excluded_lots)))
        return Scope(lots=lots, excluded_lots=excluded)

    def is_empty(self) -> bool:
        if self.lots is not None and not self.lots:
            return True
        if self.lots is not None and all(lot in self.excluded_lots for lot in self.lots):
            return True
        return False

    def describe(self) -> str:
        base = "全部标段" if self.lots is None else "、".join(self.lots)
        if self.excluded_lots:
            return f"{base}（排除{'、'.join(self.excluded_lots)}）"
        return base


@dataclass
class Clarification:
    id: str
    text: str
    issued_at: datetime
    scope: Scope
    reading: textmod.Reading
    pair: str | None = None
    correction: bool = False
    supersedes: tuple[str, ...] = ()

    @property
    def clauses(self) -> tuple[str, ...]:
        found = [clause for fact in self.reading.facts for clause in fact.clauses]
        return tuple(dict.fromkeys((*found, *self.reading.unchanged)))

    @property
    def unchanged(self) -> tuple[str, ...]:
        return self.reading.unchanged

    def fact_for(
        self,
        subject: str,
        clause: str | None = None,
        methods: frozenset[str] | None = None,
    ) -> textmod.Fact | None:
        candidates = [
            fact
            for fact in self.reading.facts
            if fact.subject == subject
            and fact.modality != "unknown"
            and (clause is None or fact.applies_to(clause))
        ]
        if methods:
            overlapping = [fact for fact in candidates if fact.methods & methods]
            if overlapping:
                candidates = overlapping
            else:
                candidates = [fact for fact in candidates if not fact.methods]
        return candidates[-1] if candidates else None


@dataclass(frozen=True)
class Conflict:
    subject: str
    clause: str | None
    kind: str
    original: str
    translation: str
    detail: str


@dataclass
class TranslationReview:
    id: str
    pair: str | None
    received_at: datetime | None
    scope: Scope
    status: str                       # effective | partial | pending | out_of_scope
    conflicts: list[Conflict] = field(default_factory=list)
    pending_clauses: tuple[str, ...] = ()
    publishable_clauses: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)

    @property
    def can_be_used_as_basis(self) -> bool:
        return self.status == "effective" and not self.conflicts


@dataclass
class ExcludedText:
    id: str
    kind: str
    reason: str


@dataclass
class Model:
    clarifications: list[Clarification]
    translations: list[TranslationReview]
    excluded: list[ExcludedText]
    specs: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    by_id: dict[str, dict[str, Any]]
    lots: tuple[str, ...]

    def issued_for(self, lot: str, clause: str) -> list[Clarification]:
        found = [
            item
            for item in self.clarifications
            if clause in item.clauses and item.scope.covers(lot)
        ]
        found.sort(key=lambda item: item.issued_at, reverse=True)
        return found

    def effective_fact(
        self,
        lot: str,
        clause: str,
        subject: str,
        methods: frozenset[str] | None = None,
    ) -> tuple[textmod.Fact | None, str | None, str]:
        """最新签发且适用于该标段/条款/对象的断言，找不到再看原文。

        返回 (fact, 来源 id, 来源类型 spec|clarification)。
        """

        for item in self.issued_for(lot, clause):
            fact = item.fact_for(subject, clause, methods)
            if fact is not None:
                return fact, item.id, "clarification"
        for spec in self.specs:
            if spec.get("lot") != lot:
                continue
            reading = textmod.read(spec.get("text", ""))
            facts = [
                fact
                for fact in reading.facts
                if fact.subject == subject
                and fact.modality != "unknown"
                and (not fact.clauses or fact.applies_to(clause))
            ]
            if methods:
                overlapping = [fact for fact in facts if fact.methods & methods]
                if overlapping:
                    facts = overlapping
                else:
                    facts = [fact for fact in facts if not fact.methods]
            if facts:
                return facts[-1], spec["id"], "spec"
        return None, None, "none"

    def has_spec_requirement(self, lot: str, clause: str, subject: str) -> bool:
        """采购文件原文是否就该项提出了要求。"""

        fact, _ = self.baseline_fact(lot, clause, subject)
        return fact is not None and fact.modality != "unknown"

    def is_superseded(self, item: "Clarification", lot: str, clause: str) -> bool:
        """更正版在覆盖的标段与条款上取代被更正的文本；未点名不变的条款除外。"""

        for other in self.clarifications:
            if not other.correction or item.id not in other.supersedes:
                continue
            if not other.scope.covers(lot):
                continue
            if clause in other.clauses and clause not in other.reading.unchanged:
                return True
        return False

    def current_requirements(
        self,
        lot: str,
        clause: str,
        subject: str,
    ) -> list[tuple[textmod.Fact, str, str]]:
        """公开文本（采购文件原文 + 适用于该标段的签发释义）中，该对象在
        该条款上的全部要求，按来源与方式去重。"""

        found: list[tuple[textmod.Fact, str, str]] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for item in self.issued_for(lot, clause):
            if self.is_superseded(item, lot, clause):
                continue
            for fact in item.reading.facts:
                if (
                    fact.subject == subject
                    and fact.modality != "unknown"
                    and fact.applies_to(clause)
                ):
                    key = (fact.modality, tuple(sorted(fact.methods)))
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append((fact, item.id, "clarification"))
        for spec in self.specs:
            if spec.get("lot") != lot:
                continue
            for fact in textmod.read(spec.get("text", "")).facts:
                if (
                    fact.subject == subject
                    and fact.modality != "unknown"
                    and fact.applies_to(clause)
                ):
                    key = (fact.modality, tuple(sorted(fact.methods)))
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append((fact, spec["id"], "spec"))
        return found

    def baseline_fact(
        self,
        lot: str,
        clause: str,
        subject: str,
        methods: frozenset[str] | None = None,
    ) -> tuple[textmod.Fact | None, str | None]:
        """只看采购文件原条款时的断言。"""

        for spec in self.specs:
            if spec.get("lot") != lot:
                continue
            reading = textmod.read(spec.get("text", ""))
            facts = [
                fact
                for fact in reading.facts
                if fact.subject == subject
                and fact.modality != "unknown"
                and (not fact.clauses or fact.applies_to(clause))
            ]
            if methods:
                overlapping = [fact for fact in facts if fact.methods & methods]
                if overlapping:
                    facts = overlapping
                else:
                    facts = [fact for fact in facts if not fact.methods]
            if facts:
                return facts[-1], spec["id"]
        return None, None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _scope_of(record: dict[str, Any], reading: textmod.Reading) -> Scope:
    lot = record.get("lot")
    lots = (lot,) if lot else None
    return Scope(lots=lots, excluded_lots=tuple(reading.no_change_lots))


def _inherit_clauses(
    facts: list[textmod.Fact],
    source: Clarification | None,
) -> None:
    """把没有条款号的微句挂到原文有效的条款上。"""

    if source is None:
        return
    active = [
        clause
        for clause in source.clauses
        if clause not in source.reading.unchanged
    ]
    for fact in facts:
        if fact.clauses or not active:
            continue
        fact.clauses = (active[0],)


def _compare_translation(
    source: Clarification,
    reading: textmod.Reading,
    text: str,
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for subject in ("modular", "single"):
        original = source.fact_for(subject)
        translated = next(
            (
                fact
                for fact in reading.facts
                if fact.subject == subject and fact.modality != "unknown"
            ),
            None,
        )
        if translated is None:
            continue
        clause = (
            translated.clauses[0]
            if translated.clauses
            else (original.clauses[0] if original and original.clauses else None)
        )
        if original is None:
            conflicts.append(
                Conflict(
                    subject=subject,
                    clause=clause,
                    kind="original_missing",
                    original="未提",
                    translation=translated.modality,
                    detail=f"译文对「{subject}」增加了原文没有的要求，不能自行扩大效力",
                )
            )
            continue
        if original.modality != translated.modality:
            conflicts.append(
                Conflict(
                    subject=subject,
                    clause=clause,
                    kind="modality",
                    original=original.modality,
                    translation=translated.modality,
                    detail=(
                        f"「{subject}」的要求强弱相反：原文 {original.modality}，"
                        f"译文 {translated.modality}"
                    ),
                )
            )

    original_dimensions = set(textmod.dimensions(source.text))
    translated_dimensions = set(textmod.dimensions(text))
    if translated_dimensions and original_dimensions != translated_dimensions:
        conflicts.append(
            Conflict(
                subject="numeric",
                clause=source.clauses[0] if source.clauses else None,
                kind="numeric",
                original="、".join(sorted(original_dimensions)) or "未提",
                translation="、".join(sorted(translated_dimensions)),
                detail="数值或量纲不一致，相关部分待签",
            )
        )
    return conflicts


def build_model(
    briefs: dict[str, dict[str, Any]],
    submissions: dict[str, dict[str, Any]],
    clock: Any,
) -> Model:
    excluded: list[ExcludedText] = []
    specs: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    clarifications: list[Clarification] = []
    lots = sorted(
        {row["lot"] for row in submissions.values() if isinstance(row.get("lot"), str)}
    )

    for record in briefs.values():
        kind = record["kind"]
        reading = textmod.read(record.get("text", ""))
        if kind == "spec":
            specs.append(record)
        elif kind == "question":
            questions.append(record)
        elif kind == "clarification":
            if record.get("decision") != "issued" or not record.get("issued_at"):
                excluded.append(
                    ExcludedText(record["id"], kind, "未签发，不产生效力")
                )
                continue
            issued_at = _parse_time(record["issued_at"])
            if issued_at is None:
                excluded.append(
                    ExcludedText(record["id"], kind, "issued_at 不是可识别的时间")
                )
                continue
            clarifications.append(
                Clarification(
                    id=record["id"],
                    text=record.get("text", ""),
                    issued_at=issued_at,
                    scope=_scope_of(record, reading),
                    reading=reading,
                    pair=record.get("pair"),
                    correction=reading.corrections,
                )
            )
        elif kind == "reply":
            reason = "普通回复不产生效力，也不能自行扩大效力"
            if "旧稿" in record.get("text", "") or "旧版" in record.get("text", ""):
                reason += "；且为旧稿，不得当作现行文本"
            excluded.append(ExcludedText(record["id"], kind, reason))
        elif kind == "translation":
            continue

    clarifications.sort(key=lambda item: item.issued_at)
    by_clar = {item.id: item for item in clarifications}

    for item in clarifications:
        source = by_clar.get(item.pair) if item.pair else None
        _inherit_clauses(item.reading.facts, source)
        if source is not None and item.correction:
            item.supersedes = (source.id,)

    translations: list[TranslationReview] = []
    for record in briefs.values():
        if record["kind"] != "translation":
            continue
        pair_id = record.get("pair")
        source = by_clar.get(pair_id) if pair_id else None
        received = _parse_time(record.get("received_at")) or _parse_time(
            record.get("issued_at")
        )
        if source is None:
            pair_record = briefs.get(pair_id) if pair_id else None
            if pair_record is None:
                reason = "配对的原文不存在，译文不能单独产生效力"
            elif pair_record.get("kind") == "spec":
                reason = "配对的原文是采购文件条款，译文不改变原文效力"
            elif pair_record.get("kind") == "translation":
                reason = "配对的原文本身是译文，不能逐级叠加效力"
            else:
                reason = "配对的原文不是已签发的澄清，译文不能单独产生效力"
            translations.append(
                TranslationReview(
                    id=record["id"],
                    pair=pair_id,
                    received_at=received,
                    scope=Scope(),
                    status="out_of_scope",
                    notes=[reason],
                )
            )
            continue

        reading = textmod.read(record.get("text", ""))
        _inherit_clauses(reading.facts, source)
        scope = source.scope
        own_lot = record.get("lot")
        if own_lot:
            scope = scope.intersect(Scope(lots=(own_lot,)))
        review = TranslationReview(
            id=record["id"],
            pair=pair_id,
            received_at=received,
            scope=scope,
            status="effective",
        )
        if scope.is_empty():
            review.status = "out_of_scope"
            review.notes.append("译文标段超出原文明确指向的范围，不能借译文扩大效力")
            translations.append(review)
            continue

        review.conflicts = _compare_translation(source, reading, record.get("text", ""))
        all_clauses = tuple(
            dict.fromkeys((*source.clauses, *reading.clauses))
        )
        conflict_clauses = {
            conflict.clause for conflict in review.conflicts if conflict.clause
        }
        pending = tuple(clause for clause in all_clauses if clause in conflict_clauses)
        if review.conflicts and not pending:
            # 冲突微句没有条款号时，不猜是哪一条，整段待签。
            pending = all_clauses
        publishable = tuple(clause for clause in all_clauses if clause not in pending)
        review.pending_clauses = pending
        review.publishable_clauses = publishable
        if review.conflicts:
            review.status = "partial" if publishable else "pending"
            review.notes.append(
                "原文与译文在要求强弱或数值上矛盾，冲突部分留在待签状态；"
                "能独立解释的部分仍可发布。"
            )
        translations.append(review)

    return Model(
        clarifications=clarifications,
        translations=translations,
        excluded=excluded,
        specs=specs,
        questions=questions,
        by_id=dict(briefs),
        lots=tuple(lots),
    )


def corrections_after(
    model: Model,
    lot: str,
    after: datetime | None,
) -> list[Clarification]:
    found = [
        item
        for item in model.clarifications
        if item.correction
        and item.scope.covers(lot)
        and (after is not None and item.issued_at > after)
    ]
    found.sort(key=lambda item: item.issued_at)
    return found


__all__ = [
    "Clarification",
    "Conflict",
    "ExcludedText",
    "Model",
    "Scope",
    "TranslationReview",
    "build_model",
    "corrections_after",
]
