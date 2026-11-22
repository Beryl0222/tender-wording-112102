"""把已公开的释义落到每份仍然有效的递交上。

对每份尚有效的递交，依据已公开（已签发、未被更正取代）的释义和原先
声明的构件组合逐项复核：

- 先分辨该递交针对的是联合装配组合还是单块拼件，再看证据实际采用的
  检验方式（到场联合样、同批、原件等），避免把「单块不能」的结论误
  套到联合组合上。
- 证明的 usable_for 须覆盖该证明在这份递交里被用来支撑的每个构件；
  同一份证明用在两处时，只有适用范围足够的那处继续有效。
- 若干候选构件共用一份不能拆开的证明时，只改其中一个会牵动整个组合，
  这类递交按整组重新确认，而不是单件补交。
- 只在公开变更确实影响某项方案时生成补证或重新确认；影响无法判定时
  先询问，不能把没受影响的递交整份退回。
- 截止后出现的文本若把两种检验方式译反，受影响的方案暂缓评审至统一
  补正；已到期且无关的部分仍按原截止记录。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import text as textmod
from .loader import Clock
from .public import answer_questions
from .rules import Model, build_model

CHECK_CLAUSES = ("4.2", "4.4")
MODULAR = textmod.MODULAR
SINGLE = textmod.SINGLE

TREATMENTS = {
    "ok": "无需动作",
    "re_proof": "补证",
    "reconfirm": "重新确认",
    "inquire": "先询问",
    "hold": "暂缓评审",
    # 与公开变更无关、复核时发现的既有缺件，单独归类，避免混作变更通知。
    "evidence_gap": "证据补齐",
}

_STATUS_LABEL = {
    "clear": "继续有效",
    "action_required": "需补证/重新确认",
    "pending_answer": "待追问答复",
    "hold": "暂缓评审",
}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _in_time(filed: str | None, deadline: str | None) -> bool:
    """递交日期是否不晚于原截止。日期与带时区的时间按日历日比较。"""

    filed_time = _parse(filed)
    cutoff = _parse(deadline)
    if filed_time is None or cutoff is None:
        return False
    return filed_time.date() <= cutoff.date()


def _label(modality: str) -> str:
    return {
        "may": "可用等效替代",
        "must": "须原件",
        "may_not": "不适用该方式",
        "unknown": "未判定",
    }.get(modality, modality)


def _dedupe(items):
    return list(dict.fromkeys(items))


def _requirement(fact: textmod.Fact | None) -> tuple[str, tuple[str, ...]]:
    if fact is None:
        return "unknown", ()
    return fact.modality, tuple(sorted(fact.methods))


@dataclass
class ProofUse:
    proof_id: str
    declared: tuple[str, ...]
    claimed: tuple[str, ...]
    covered: tuple[str, ...]
    uncovered: tuple[str, ...]
    shared_with: tuple[str, ...]
    indivisible: bool
    text: str

    @property
    def sufficient(self) -> bool:
        return not self.uncovered and bool(self.covered)

    def describe(self) -> str:
        if self.uncovered:
            return (
                f"{self.proof_id} 只覆盖 {'、'.join(self.claimed) or '（无）'}，"
                f"支撑不了 {'、'.join(self.uncovered)}"
            )
        return f"{self.proof_id} 覆盖 {'、'.join(self.covered)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "proof": self.proof_id,
            "declared": list(self.declared),
            "claimed": list(self.claimed),
            "covered": list(self.covered),
            "uncovered": list(self.uncovered),
            "shared_with": list(self.shared_with),
            "indivisible": self.indivisible,
            "valid_here": self.sufficient,
            "text": self.text,
        }


@dataclass
class Effect:
    clause: str
    source: str | None
    modality: str
    treatment: str
    reason: str
    kind: str = "modality"            # modality | proof | indivisible | correction

    def as_dict(self) -> dict[str, Any]:
        return {
            "clause": self.clause,
            "source": self.source,
            "modality": self.modality,
            "treatment": self.treatment,
            "treatment_label": TREATMENTS.get(self.treatment, self.treatment),
            "kind": self.kind,
            "reason": self.reason,
        }


@dataclass
class Notice:
    id: str
    submission: str
    kind: str
    reason: str
    clauses: tuple[str, ...]
    answers: tuple[str, ...]
    due: str | None
    basis: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "submission": self.submission,
            "kind": self.kind,
            "kind_label": TREATMENTS.get(self.kind, self.kind),
            "reason": self.reason,
            "clauses": list(self.clauses),
            "answers": list(self.answers),
            "due": self.due,
            "basis": list(self.basis),
        }


@dataclass
class SubmissionReview:
    id: str
    lot: str
    supplier: str
    option: str
    choices: tuple[str, ...]
    filed: str
    answered_in_time: bool
    subject: str
    evidence_method: str
    valid: bool
    status: str
    proof_uses: list[ProofUse] = field(default_factory=list)
    effects: list[Effect] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)
    reviewable: bool = True
    notes: list[str] = field(default_factory=list)
    invalid_proof_uses: list[str] = field(default_factory=list)

    @property
    def not_affected(self) -> bool:
        return self.status == "clear"

    @property
    def whole_return(self) -> bool:
        """只有整份递交赖以成立的证明链彻底不能用时，才整份退回。"""

        return self.status == "hold" and bool(self.effects) and all(
            effect.treatment == "hold" for effect in self.effects
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "lot": self.lot,
            "supplier": self.supplier,
            "option": self.option,
            "choices": list(self.choices),
            "filed": self.filed,
            "answered_in_time": self.answered_in_time,
            "subject": self.subject,
            "evidence_method": self.evidence_method,
            "status": self.status,
            "status_label": _STATUS_LABEL.get(self.status, self.status),
            "reviewable": self.reviewable,
            "whole_return": self.whole_return,
            "proof_uses": [use.as_dict() for use in self.proof_uses],
            "effects": [effect.as_dict() for effect in self.effects],
            "notices": [notice.as_dict() for notice in self.notices],
            "invalid_proof_uses": list(self.invalid_proof_uses),
            "notes": list(self.notes),
        }


@dataclass
class ReviewResult:
    model: Model
    submissions: list[SubmissionReview]
    notices: list[Notice]
    late_correction: dict[str, Any] | None
    deadlines: dict[str, Any]
    lots: dict[str, Any]
    answers: dict[str, Any]
    questions: list[dict[str, Any]]
    warnings: list[str]

    def by_submission(self, identifier: str) -> SubmissionReview:
        for item in self.submissions:
            if item.id == identifier:
                return item
        raise KeyError(identifier)

    def by_notice(self, identifier: str) -> Notice:
        for item in self.notices:
            if item.id == identifier:
                return item

    def as_dict(self) -> dict[str, Any]:
        return {
            "lots": self.lots,
            "deadlines": self.deadlines,
            "answers": self.answers,
            "questions": [question.as_dict() for question in self.questions],
            "notices": [notice.as_dict() for notice in self.notices],
            "submissions": [item.as_dict() for item in self.submissions],
            "late_correction": self.late_correction,
            "warnings": list(self.warnings),
        }


def submission_subject(record: dict[str, Any]) -> str:
    """该项递交针对的是联合装配组合还是一块一块的单件。"""

    choices = tuple(record.get("choices", ()))
    option = record.get("option", "")
    if any(word in option for word in ("联合", "联装", "组合")):
        return MODULAR
    if any(word in option for word in ("拼件", "单块", "单件")):
        return SINGLE
    if len(choices) > 1:
        return MODULAR
    return SINGLE


def evidence_method(record: dict[str, Any], submissions: dict[str, dict[str, Any]]) -> str:
    """从证据文字判断实际用的检验方式。

    一份递交里若同时有原件证明和到场样，按原件证明对待（更严格者）；
    只有独立块/单件而没有联合样时记为 single_piece，供追问时区分。
    """

    texts = " ".join(
        submissions.get(proof_id, {}).get("text", "")
        for proof_id in record.get("proof", ())
    ).lower()
    if "原件" in texts or "original" in texts:
        return "original"
    if "同批" in texts or "same batch" in texts or "same-batch" in texts:
        return "batch"
    if "到场" in texts or "现场" in texts or "on-site" in texts or "onsite" in texts:
        return "on_site"
    if any(word in texts for word in ("独立块", "单块", "单件", "单独")):
        return "single_piece"
    return "unknown"


def _single_verdict(method: str, fact: textmod.Fact | None) -> bool | None:
    """单条要求下，现证据是否满足。

    True 满足、False 不满足、None 该条只说了别的检验方式、无法判定。
    原件是最严格的形式，任何条款下都成立；独立块连联合装配样都不是，
    不满足要求联合样/原件的条款。
    """

    if fact is None or fact.modality == "unknown":
        return None
    if method == "original":
        return True
    if method == "unknown":
        return None
    if fact.modality == "may":
        if method == "single_piece":
            return None if not fact.methods else False
        if not fact.methods:
            return True
        return method in fact.methods
    if fact.modality == "must":
        if method == "single_piece":
            return False
        if fact.methods:
            return method in fact.methods
        # 未点名方式的「须提供」按原件理解，其他方式都不满足。
        return False
    # may_not：只否定被点名的方式
    if method in fact.methods:
        return False
    return None


def _aggregate(verdicts: list[bool | None]) -> bool | None:
    """多条公开要求合看：有一条明确可用即满足，否则能说明就说明，剩下未知。"""

    if any(verdict is True for verdict in verdicts):
        return True
    if any(verdict is False for verdict in verdicts):
        return False
    return None


def _current_verdict(
    model: Model,
    lot: str,
    clause: str,
    subject: str,
    method: str,
) -> bool | None:
    """按现行公开文本判定。

    已签发释义存在时以释义为准，不再拿原文的较宽口径来兜底；只有在
    该条款/对象上没有释义时，才回落到采购文件原文。
    """

    requirements = model.current_requirements(lot, clause, subject)
    clarified = [entry for entry in requirements if entry[2] == "clarification"]
    selected = clarified or [
        entry for entry in requirements if entry[2] == "spec"
    ]
    return _aggregate(
        [_single_verdict(method, fact) for fact, _, _ in selected]
    )


def _baseline_verdict(
    model: Model,
    lot: str,
    clause: str,
    subject: str,
    method: str,
) -> bool | None:
    fact, _ = model.baseline_fact(lot, clause, subject)
    return _single_verdict(method, fact)


def _method_label(method: str) -> str:
    return {
        "on_site": "到场样",
        "batch": "同批检测",
        "original": "原件",
        "single_piece": "独立块",
        "unknown": "未判定方式",
    }.get(method, method)


def _proof_uses(
    record: dict[str, Any],
    submissions: dict[str, dict[str, Any]],
) -> list[ProofUse]:
    choices = tuple(record.get("choices", ()))
    uses: list[ProofUse] = []
    for proof_id in record.get("proof", ()):
        proof = submissions.get(proof_id, {})
        claimed = tuple(proof.get("usable_for", ()))
        # 证据文字自己缩小的范围，以更窄者为准。
        narrowed = textmod.proof_scope(proof)
        effective_claim = claimed
        if narrowed != set(claimed):
            effective_claim = tuple(item for item in claimed if item in narrowed)
        covered = tuple(choice for choice in choices if choice in effective_claim)
        uncovered = tuple(choice for choice in choices if choice not in effective_claim)
        shared = tuple(
            other_id
            for other_id, other in submissions.items()
            if other_id != record["id"]
            and other.get("kind") is None
            and proof_id in other.get("proof", ())
        )
        text = proof.get("text", "")
        joint_test = ("联合" in text) and not any(
            marker in text
            for marker in ("独立块", "还没见到", "未见到", "未见")
        )
        # 「共用」不等于「不可拆」：只有证据本身是联合整体、或文字明说
        # 不能拆分时，才按整组处理；被多个递交引用不改变可拆性。
        indivisible = len(choices) > 1 and not uncovered and (
            "无法拆分" in text or "不可拆分" in text or joint_test
        )
        uses.append(
            ProofUse(
                proof_id=proof_id,
                declared=claimed,
                claimed=effective_claim,
                covered=covered,
                uncovered=uncovered,
                shared_with=shared,
                indivisible=indivisible,
                text=text,
            )
        )
    return uses


def _proof_doubt(use: ProofUse) -> str | None:
    """证明本身还缺什么。只描述缺件，不把别处递交的问题算到这里。"""

    text = use.text
    if "还没见到" in text or "未见到" in text or "未见" in text:
        return f"{use.proof_id} 所述联合样尚未见到，需向供应商追问后才补得齐"
    return None


def _public_change(
    model: Model,
    record: dict[str, Any],
    clause: str,
    subject: str,
) -> tuple[bool, str | None, textmod.Fact | None, textmod.Fact | None]:
    """公开释义是否真的改变了适用于本组合的要求（强度或检验方式）。"""

    effective, source, origin = model.effective_fact(record["lot"], clause, subject)
    baseline, _ = model.baseline_fact(record["lot"], clause, subject)
    if origin != "clarification" or effective is None:
        return False, source, effective, baseline
    return _requirement(effective) != _requirement(baseline), source, effective, baseline


def review_submissions(
    model: Model,
    submissions: dict[str, dict[str, Any]],
    clock: Any,
) -> tuple[list[SubmissionReview], list[Notice]]:
    notices: list[Notice] = []
    reviews: list[SubmissionReview] = []

    for record in submissions.values():
        if record.get("kind") is not None:
            continue
        subject = submission_subject(record)
        method = evidence_method(record, submissions)
        uses = _proof_uses(record, submissions)
        review = SubmissionReview(
            id=record["id"],
            lot=record["lot"],
            supplier=record["supplier"],
            option=record["option"],
            choices=tuple(record.get("choices", ())),
            filed=record.get("filed", ""),
            answered_in_time=_in_time(record.get("filed"), clock.deadline),
            subject=subject,
            evidence_method=method,
            valid=True,
            status="clear",
            proof_uses=uses,
        )

        for clause in CHECK_CLAUSES:
            changed, source, effective, baseline = _public_change(
                model, record, clause, subject
            )
            if not changed:
                review.effects.append(
                    Effect(
                        clause=clause,
                        source=source,
                        modality=effective.modality if effective else "unknown",
                        treatment="ok",
                        reason=(
                            f"公开释义未改变 {clause} 上适用于本组合的要求"
                            if effective is not None
                            else f"原文与已签发释义均未就 {clause} 对本组合提出要求"
                        ),
                    )
                )
                continue

            now_ok = _current_verdict(model, record["lot"], clause, subject, method)
            before_ok = _baseline_verdict(
                model, record["lot"], clause, subject, method
            )
            if not model.has_spec_requirement(record["lot"], clause, subject):
                # 原文未覆盖该标段/组合，签发释义新加的要求不能直接拿来
                # 退回既有递交，先问清是否适用于本方案。
                review.effects.append(
                    Effect(
                        clause=clause,
                        source=source,
                        modality=effective.modality,
                        treatment="inquire",
                        reason=(
                            f"{source} 就 {clause} 提出了要求，但采购文件原文未覆盖"
                            "本标段/组合；能否适用于既有递交无法判定，先向采购组询问"
                        ),
                    )
                )
                continue
            if now_ok is True:
                treatment = "ok"
                if before_ok is False:
                    reason = (
                        f"{source} 就 {clause} 放宽了本组合可用的检验方式，"
                        f"现证据（{_method_label(method)}）转为满足，无需补证"
                    )
                else:
                    reason = f"{source} 就 {clause} 调整了本组合可用的检验方式，现证据仍满足"
            elif now_ok is None:
                treatment = "inquire"
                reason = (
                    f"{source} 就 {clause} 改变了本组合的要求，"
                    "但仅凭公开文字无法判定现证据是否等效，先向采购组询问"
                )
            elif before_ok is False:
                # 公开变更之前就不满足，缺件另有原因，不把账记在这次变更上。
                treatment = "ok"
                reason = (
                    f"{source} 涉及 {clause}，但本方案在该条款上原本就不满足，"
                    "现有缺件按证据核查另行处理，不因本次变更重复退回"
                )
            else:
                treatment = "re_proof"
                if baseline is not None and baseline.modality == effective.modality:
                    reason = (
                        f"{source} 就 {clause} 收紧了本组合可用的检验方式："
                        f"原为 {_label(baseline.modality)}，现只放开 "
                        f"{'、'.join(_method_label(m) for m in sorted(effective.methods)) or '未指明方式'}；"
                        f"现证据方式（{_method_label(method)}）不再满足"
                    )
                else:
                    before = baseline.modality if baseline else "unknown"
                    reason = (
                        f"{source} 就 {clause} 改变了本组合的要求："
                        f"原为 {_label(before)}，现为 {_label(effective.modality)}"
                        f"（现证据方式：{_method_label(method)}）"
                    )

            review.effects.append(
                Effect(
                    clause=clause,
                    source=source,
                    modality=effective.modality,
                    treatment=treatment,
                    reason=reason,
                )
            )

        # 证据链：同一证明用在不同组合时，只有适用范围足够的那处继续有效。
        covering = [use for use in uses if use.sufficient]
        for use in uses:
            if not use.uncovered:
                continue
            review.invalid_proof_uses.append(
                f"{use.proof_id} 在本递交中不覆盖 {'、'.join(use.uncovered)}"
            )
            if covering:
                # 本递交另有足够的证明，这处引用只是不再有效，不必整份退回。
                review.notes.append(
                    f"{use.describe()}；本递交改由 "
                    f"{'、'.join(use2.proof_id for use2 in covering)} 支撑，"
                    "不足的引用不再沿用"
                )
            else:
                review.effects.append(
                    Effect(
                        clause="4.2",
                        source=None,
                        modality="unknown",
                        treatment="re_proof",
                        kind="proof",
                        reason=f"{use.describe()}；本递交没有其他可覆盖全部构件的证明",
                    )
                )

        for use in uses:
            doubt = _proof_doubt(use)
            if doubt is None:
                continue
            review.effects.append(
                Effect(
                    clause="4.2",
                    source=None,
                    modality="unknown",
                    treatment="re_proof",
                    kind="proof",
                    reason=doubt + "；缺少的证明须由该供应商补齐，不向其他投标人透露",
                )
            )

        # 共用一个不可拆证明的候选构件：只要本组合里有一项要动，就必须
        # 整组重新确认，不能只改单件。能指出是哪一项时按项点名。
        components_to_fix = _components_to_fix(review.effects, review.choices)
        # 影响尚未判定（先询问）时不动整组重提，等答复后再说。
        needs_work = any(
            effect.treatment in {"re_proof", "reconfirm"} for effect in review.effects
        )
        for use in uses:
            if not (use.indivisible and needs_work):
                continue
            touched = "、".join(sorted(components_to_fix)) or "其中任一构件"
            review.effects.append(
                Effect(
                    clause="4.2",
                    source=None,
                    modality="unknown",
                    treatment="reconfirm",
                    kind="indivisible",
                    reason=(
                        f"{use.proof_id} 覆盖本组合全部构件且不能拆分；"
                        f"调整 {touched} 会牵动整个「{record['option']}」，"
                        "须整组重新确认，不能只补单件"
                    ),
                )
            )
            break

        _finish(review, clock)
        notices.extend(review.notices)
        reviews.append(review)

    return reviews, notices


def _components_to_fix(effects: list[Effect], choices: tuple[str, ...]) -> set[str]:
    """从补证/追问的文字里找出被点名的构件。"""

    named: set[str] = set()
    for effect in effects:
        if effect.treatment == "ok":
            continue
        for choice in choices:
            if choice in effect.reason:
                named.add(choice)
    return named


def _finish(review: SubmissionReview, clock: Any) -> None:
    actionable = [effect for effect in review.effects if effect.treatment != "ok"]
    if any(effect.treatment == "hold" for effect in actionable):
        review.status = "hold"
        review.reviewable = False
    elif any(effect.treatment == "inquire" for effect in actionable):
        review.status = "pending_answer"
    elif actionable:
        review.status = "action_required"
    else:
        review.status = "clear"

    per_clause: dict[str, list[Effect]] = {}
    for effect in actionable:
        per_clause.setdefault(effect.clause, []).append(effect)

    answers: list[str] = []
    for clause, effects in per_clause.items():
        groups: dict[str, list[Effect]] = {}
        for effect in effects:
            group = "reconfirm" if effect.kind == "indivisible" else effect.treatment
            groups.setdefault(group, []).append(effect)
        for kind, group in groups.items():
            if kind == "hold":
                continue
            if kind == "re_proof" and all(
                effect.kind == "proof" for effect in group
            ):
                # 公开变更并未触及本方案，缺件是复核发现的既有问题：
                # 记为证据补齐，不冒充补证或重新确认通知。
                kind = "evidence_gap"
            reason = "；".join(_dedupe(effect.reason for effect in group))
            basis = tuple(_dedupe(effect.source for effect in group if effect.source))
            answers.append(_emit(review, clause, kind, reason, basis, clock))

    for notice in review.notices:
        notice.answers = tuple(answers)

    for use in review.proof_uses:
        if use.indivisible:
            review.notes.append(
                f"{use.proof_id} 是本组合不可拆分的证明；一旦改动其中任一构件，"
                f"须将整个「{review.option}」重新确认，不能只改一项"
            )
    proof_deficiencies = [
        effect for effect in actionable if effect.kind == "proof"
    ]
    if review.status == "clear" and not proof_deficiencies:
        review.notes.append("未受公开变更影响，整份递交继续有效，不退回")
    if review.status == "action_required" and proof_deficiencies and not any(
        effect.kind == "modality" for effect in actionable
    ):
        review.notes.append(
            "公开变更未影响本方案；所列缺件系复核时发现的既有问题，"
            "按证据补齐处理，不据此整份退回"
        )
    if any(effect.kind == "indivisible" for effect in actionable):
        review.notes.append(
            "该递交为不可拆替代组合：任一构件变动都按整组处理，不按单件补退"
        )
    if review.status == "pending_answer":
        review.notes.append("影响无法判定的部分先发询问，答复前不整份退回")
    if proof_deficiencies and not any(
        effect.kind in {"modality", "indivisible"} for effect in actionable
    ):
        review.notes.append(
            "现有缺件与本次公开变更无关，按证据核查单独补证；"
            "其余部分不整份退回"
        )


def _emit(
    review: SubmissionReview,
    clause: str,
    kind: str,
    reason: str,
    basis: tuple[str, ...],
    clock: Any,
) -> str:
    notice = Notice(
        id=_notice_id(review.id, clause, kind, reason),
        submission=review.id,
        kind=kind,
        reason=reason,
        clauses=(clause,),
        answers=(),
        due=clock.reconfirm_until,
        basis=basis,
    )
    review.notices.append(notice)
    return notice.id


def _pick_kind(effects: list[Effect]) -> str:
    kinds = {effect.treatment for effect in effects}
    for kind in ("hold", "re_proof", "reconfirm", "inquire"):
        if kind in kinds:
            return kind
    return "inquire"


def _notice_id(submission: str, clause: str, kind: str, reason: str) -> str:
    token = f"{submission}|{clause}|{kind}|{reason}".encode("utf-8")
    digest = 0
    for byte in token:
        digest = (digest * 131 + byte) & 0xFFFFFFFF
    return f"N-{submission}-{clause}-{kind}-{digest:08x}"


def _reversed_requirements(model: Model, lot: str, clause: str) -> tuple[textmod.Fact | None, textmod.Fact | None]:
    """把两种检验方式的归属对调后的要求，用于识别译反并判定影响。"""

    modular, source, origin = model.effective_fact(lot, clause, MODULAR)
    single, _, _ = model.effective_fact(lot, clause, SINGLE)
    if origin != "clarification" or modular is None or single is None:
        return None, None
    swapped_modular = textmod.Fact(
        subject=MODULAR,
        modality=single.modality,
        clause=clause,
        clauses=(clause,),
        methods=single.methods,
        text="(译反)",
    )
    swapped_single = textmod.Fact(
        subject=SINGLE,
        modality=modular.modality,
        clause=clause,
        clauses=(clause,),
        methods=modular.methods,
        text="(译反)",
    )
    return swapped_modular, swapped_single


def _reverse_sensitive(
    model: Model,
    submissions: dict[str, dict[str, Any]],
    record: dict[str, Any],
    clause: str,
) -> bool:
    """译反前后，本递交在该条款上的处置是否会改变。"""

    subject = submission_subject(record)
    method = evidence_method(record, submissions)
    effective, _, origin = model.effective_fact(record["lot"], clause, subject)
    swapped_modular, swapped_single = _reversed_requirements(
        model, record["lot"], clause
    )
    swapped = swapped_modular if subject == MODULAR else swapped_single
    if origin != "clarification" or effective is None or swapped is None:
        return False
    # 只比较已签发释义本身的方向：译反后，本组合在两种读法下能否成立
    # 是否改变。采购文件原文在两读法下不变，不参与比较。
    before_ok = _single_verdict(method, effective) is True
    after_ok = _single_verdict(method, swapped) is True
    return before_ok != after_ok


def _late_correction(
    model: Model,
    submissions: dict[str, dict[str, Any]],
    clock: Any,
) -> dict[str, Any] | None:
    """截止后收到的文本把两种检验方式译反时，确定受影响的范围。"""

    deadline = _parse(clock.deadline)
    if deadline is None:
        return None

    reversed_texts = []
    for translation in model.translations:
        received = translation.received_at
        if received is None or received <= deadline:
            continue
        modality_conflicts = {
            conflict.subject
            for conflict in translation.conflicts
            if conflict.kind == "modality"
        }
        if {MODULAR, SINGLE} <= modality_conflicts:
            reversed_texts.append(translation)
    if not reversed_texts:
        return None
    reversed_texts.sort(key=lambda item: item.received_at or deadline)
    reversal = reversed_texts[0]

    correction = None
    corrections = [
        item
        for item in model.clarifications
        if item.correction and item.issued_at > (reversal.received_at or deadline)
    ]
    corrections.sort(key=lambda item: item.issued_at)
    for item in corrections:
        if any(
            model.effective_fact(lot, clause, MODULAR)[1] == item.id
            for lot in model.lots
            for clause in CHECK_CLAUSES
        ):
            correction = item
            break
    if correction is None and corrections:
        correction = corrections[0]

    affected: list[str] = []
    reasons: dict[str, str] = {}
    for identifier, record in submissions.items():
        if record.get("kind") is not None:
            continue
        if not reversal.scope.covers(record["lot"]):
            continue
        for clause in CHECK_CLAUSES:
            if clause not in reversal.publishable_clauses and clause not in {
                conflict.clause for conflict in reversal.conflicts
            }:
                continue
            if not _reverse_sensitive(model, submissions, record, clause):
                continue
            affected.append(identifier)
            covered = correction is not None and correction.scope.covers(record["lot"])
            reasons[identifier] = (
                f"{reversal.id} 把两种检验方式译反（原文 {reversal.pair}），"
                f"本方案在 {clause} 上依原方向准备；"
                + (
                    f"待 {correction.id} 统一补正后重核，期间暂缓评审"
                    if covered
                    else "尚无适用于本标段的统一补正，暂缓评审并等待补正"
                )
            )
            break

    if not affected:
        return None

    lots: dict[str, str] = {}
    for lot in model.lots:
        hit = [item for item in affected if submissions[item]["lot"] == lot]
        has_correction = correction is not None and correction.scope.covers(lot)
        if hit:
            lots[lot] = (
                f"暂缓至 {correction.id} 统一补正"
                if has_correction
                else "暂缓评审，等待统一补正"
            )
        else:
            lots[lot] = "按原截止记录继续评审"

    return {
        "reversed_text": reversal.id,
        "reversed_pair": reversal.pair,
        "received_at": (
            reversal.received_at.isoformat() if reversal.received_at else None
        ),
        "deadline": clock.deadline,
        "scope": reversal.scope.describe(),
        "conflicts": [
            {
                "subject": conflict.subject,
                "clause": conflict.clause,
                "detail": conflict.detail,
            }
            for conflict in reversal.conflicts
        ],
        "unified_correction": (
            {
                "id": correction.id,
                "issued_at": correction.issued_at.isoformat(),
                "scope": correction.scope.describe(),
            }
            if correction is not None
            else None
        ),
        "affected": sorted(affected),
        "reasons": reasons,
        "lots": lots,
        "note": (
            "截止后发现文本把两种检验方式译反：受影响的方案暂缓评审至统一补正；"
            "已到期且无关的部分仍按原截止记录，不回溯改期。"
        ),
    }


def review(
    briefs: dict[str, dict[str, Any]],
    submissions: dict[str, dict[str, Any]],
    clock: Any | None = None,
) -> ReviewResult:
    """完整一轮释疑：澄清效力、译文核对、提问隔离、递交复核与通知期限。"""

    if clock is None:
        for row in submissions.values():
            if row.get("kind") == "tender_clock":
                clock = Clock(row["deadline"], row["reconfirm_until"])
                break
    if clock is None:
        raise ValueError("缺少 tender_clock")

    model = build_model(briefs, submissions, clock)
    reviews, notices = review_submissions(model, submissions, clock)

    late = _late_correction(model, submissions, clock)
    if late is not None:
        for identifier in late["affected"]:
            item = next(r for r in reviews if r.id == identifier)
            item.status = "hold"
            item.reviewable = False
            item.notes = [
                note
                for note in item.notes
                if note != "未受公开变更影响，整份递交继续有效，不退回"
            ]
            item.notes.append(late["reasons"][identifier])
            for notice in item.notices:
                if notice.kind in {"re_proof", "reconfirm"}:
                    notice.reason += "；因截止后的译反暂缓，待统一补正后重发"

    questions = answer_questions(model, submissions)
    answers = _answer_index(model, reviews, submissions)
    lots = _lot_index(model, reviews, late, questions)
    deadlines = {
        "original_deadline": clock.deadline,
        "reconfirm_until": clock.reconfirm_until,
        "late_receipts": _late_receipts(briefs, clock),
    }
    warnings = _warnings(model, reviews, questions)

    return ReviewResult(
        model=model,
        submissions=reviews,
        notices=notices,
        late_correction=late,
        deadlines=deadlines,
        lots=lots,
        answers=answers,
        questions=questions,
        warnings=warnings,
    )


def _late_receipts(
    briefs: dict[str, dict[str, Any]], clock: Any
) -> list[dict[str, str]]:
    deadline = _parse(clock.deadline)
    out: list[dict[str, str]] = []
    if deadline is None:
        return out
    for record in briefs.values():
        received = _parse(record.get("received_at"))
        if received and received > deadline:
            out.append(
                {
                    "id": record["id"],
                    "received_at": record["received_at"],
                    "effect": "截止后收到，不得回溯改变既有截止记录",
                }
            )
    return out


def _answer_index(
    model: Model,
    reviews: list[SubmissionReview],
    submissions: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """共同的答疑内容：每份已签发释义覆盖哪些标段、引出哪些通知。"""

    index: dict[str, dict[str, Any]] = {}
    for item in model.clarifications:
        linked = [
            notice.id
            for review_item in reviews
            for notice in review_item.notices
            if item.id in notice.basis
        ]
        index[item.id] = {
            "status": "issued",
            "issued_at": item.issued_at.isoformat(),
            "scope": item.scope.describe(),
            "clauses": list(item.clauses),
            "unchanged": list(item.unchanged),
            "correction": item.correction,
            "supersedes": list(item.supersedes),
            "notices": _dedupe(linked),
            "public_text": item.text,
        }
    for excluded in model.excluded:
        index[excluded.id] = {
            "status": "excluded",
            "reason": excluded.reason,
        }
    for translation in model.translations:
        index[translation.id] = {
            "status": translation.status,
            "pair": translation.pair,
            "scope": translation.scope.describe(),
            "publishable_clauses": list(translation.publishable_clauses),
            "pending_clauses": list(translation.pending_clauses),
            "conflicts": [
                {
                    "subject": conflict.subject,
                    "clause": conflict.clause,
                    "kind": conflict.kind,
                    "detail": conflict.detail,
                }
                for conflict in translation.conflicts
            ],
            "notes": list(translation.notes),
        }
    return index


def _lot_index(
    model: Model,
    reviews: list[SubmissionReview],
    late: dict[str, Any] | None,
    questions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    lots: dict[str, dict[str, Any]] = {}
    for lot in model.lots:
        in_lot = [item for item in reviews if item.lot == lot]
        lots[lot] = {
            "submissions": [item.id for item in in_lot],
            "reviewable": [item.id for item in in_lot if item.reviewable],
            "held": [item.id for item in in_lot if not item.reviewable],
            "clear": [item.id for item in in_lot if item.not_affected],
            "notices": [notice.id for item in in_lot for notice in item.notices],
            "due": {
                item.id: [notice.due for notice in item.notices] for item in in_lot
            },
            "latest_issued": {
                clause: (
                    model.issued_for(lot, clause)[0].id
                    if model.issued_for(lot, clause)
                    else None
                )
                for clause in CHECK_CLAUSES
            },
            "late_correction": late["lots"].get(lot) if late else "无",
            "open_questions": [
                question.id for question in questions if question.scope_covers(lot)
            ],
        }
    return lots


def _warnings(
    model: Model,
    reviews: list[SubmissionReview],
    questions: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    for review_item in reviews:
        if review_item.status == "pending_answer":
            warnings.append(
                f"{review_item.id} 的影响无法判定，已发出询问，答复前不作退回"
            )
    for translation in model.translations:
        if translation.status in {"partial", "pending"}:
            warnings.append(
                f"{translation.id} 与 {translation.pair} 存在冲突，"
                f"待签条款：{'、'.join(translation.pending_clauses) or '全部'}"
            )
        if translation.status == "out_of_scope":
            warnings.append(
                f"{translation.id} 超出原文明确指向的范围，不能借译文扩大效力"
            )
    for excluded in model.excluded:
        warnings.append(f"{excluded.id} 不产生效力：{excluded.reason}")
    for question in questions:
        if question.private_followup:
            warnings.append(
                f"{question.id} 需不公开追问，追问内容不得提前答给其中一家"
            )
    return warnings


__all__ = [
    "Effect",
    "Notice",
    "ProofUse",
    "ReviewResult",
    "SubmissionReview",
    "TREATMENTS",
    "evidence_method",
    "review",
    "review_submissions",
    "submission_subject",
]
