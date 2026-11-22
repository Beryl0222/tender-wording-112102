"""提问的归类、公开释义与私有信息隔离。

两个看起来一样的提问要分开：

- 材料/证明的替代（某项材料或证书能否用别的证明顶替）；
- 把送到现场的整套构件换一种检验方式（联合装配组合的检验方式）。

归类依据的是提问指向的对象（联合装配组合 / 单块拼件）、检验方式
（到场、同批、原件）和条款号，而不是「能不能代替」这类字面词。
归类结果要落到正确条款下，避免把答复挂错条款让报价白改。

对外释义：
- 保留必要的量纲与约束（成对、联合装配、原件、同批、4.2 等）；
- 去掉提问人独有的供应商名与尺寸；
- 不引用原提问，避免借释义泄露私有信息；
- 只有不公开追问才补得齐的提问，先不发公开答复，也不能提前答给
  其中一家。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import text as textmod
from .rules import Model

MODULAR = textmod.MODULAR
SINGLE = textmod.SINGLE

QUESTION_KINDS = {
    "material_substitution": "材料/证明替代",
    "inspection_method": "整套构件检验方式",
    "evidence_count": "证明份数/原件",
    "unknown": "无法归类",
}

CLAUSE_FOR_KIND = {
    "material_substitution": "4.4",
    "inspection_method": "4.2",
    "evidence_count": "4.2",
    "unknown": None,
}


@dataclass
class QuestionView:
    id: str
    asker: str | None
    kind: str
    clause: str | None
    cited_clauses: tuple[str, ...]
    subject: str
    method: str
    lots: tuple[str, ...]
    private_tokens: tuple[str, ...]
    public_answer: str | None
    per_lot: dict[str, str]
    private_followup: bool
    followup_reason: str | None
    publishable: bool
    blocked_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def kind_label(self) -> str:
        return QUESTION_KINDS.get(self.kind, self.kind)

    def scope_covers(self, lot: str) -> bool:
        return lot in self.lots

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "asker": self.asker,
            "kind": self.kind,
            "kind_label": self.kind_label,
            "clause": self.clause,
            "cited_clauses": list(self.cited_clauses),
            "subject": self.subject,
            "method": self.method,
            "lots": list(self.lots),
            # 私有词只用于内部脱敏，不随对外释义输出。
            "private_followup": self.private_followup,
            "followup_reason": self.followup_reason,
            "publishable": self.publishable,
            "public_answer": self.public_answer,
            "per_lot": dict(self.per_lot),
            "blocked_reasons": list(self.blocked_reasons),
            "notes": list(self.notes),
        }


def classify(text: str) -> tuple[str, str, str]:
    """返回 (kind, subject, method)。"""

    lowered = text.lower()
    methods = textmod.methods_of(text)
    subject = textmod.subject_of(text)
    if subject == "unknown" and any(
        word in text for word in ("整套", "整组", "构件组", "the whole", "the entire")
    ):
        subject = MODULAR

    material_words = (
        "材料", "芯层", "面件", "墙盒", "再生", "合格证", "证书", "附件",
        "material", "certificate", "core", "panel", "box",
    )
    replacement_words = ("替换", "替代", "代替", "换用", "更换", "顶替", "substitute", "replace")
    inspection_words = ("检验", "检测", "复检", "复验", "验收", "试验", "送检", "inspection", "test", "sampling")
    count_words = ("只交", "一份", "份数", "补交", "one copy", "single copy")

    material = any(word in lowered or word in text for word in material_words)
    replacing = any(word in lowered or word in text for word in replacement_words)
    inspecting = bool(methods) or any(
        word in lowered or word in text for word in inspection_words
    )
    counting = any(word in lowered or word in text for word in count_words)

    # 「把整套构件换一种检验方式」看起来也用了「换」，但被换的是检验方式
    # 而不是材料；只要同时出现检验方式和到场/同批/原件，就按检验方式问。
    method_change = replacing and inspecting and (
        bool(methods) or any(word in text for word in ("检验方式", "检测方式", "验收方式"))
    )
    if counting:
        kind = "evidence_count"
    elif method_change:
        kind = "inspection_method"
    elif replacing and material:
        kind = "material_substitution"
    elif inspecting:
        kind = "inspection_method"
    elif replacing:
        kind = "material_substitution"
    else:
        kind = "unknown"
    if kind == "inspection_method" and any(
        word in text for word in ("整套", "整组", "构件组", "the whole", "the entire")
    ):
        subject = MODULAR

    if subject == "unknown":
        if kind == "inspection_method" and "单块" in text:
            subject = SINGLE
    method = (
        "on_site"
        if "on_site" in methods
        else "batch"
        if "batch" in methods
        else "original"
        if "original" in methods
        else "unknown"
    )
    return kind, subject, method


def _fact_sentence(
    model: Model,
    lot: str,
    clause: str,
    subject: str,
    method: str,
) -> tuple[str, str | None]:
    requirements = model.current_requirements(lot, clause, subject)
    if not requirements:
        return "", None
    who = "联合装配组合" if subject == MODULAR else "单块拼件"
    clauses: list[str] = []
    sources: list[str] = []
    for fact, source, origin in requirements:
        if origin != "clarification":
            continue
        if fact.modality == "may":
            if not fact.methods:
                how = "可用等效证明替代"
            elif method in fact.methods or method == "unknown":
                how = "可用" + "、".join(
                    _method_word(item) for item in sorted(fact.methods)
                ) + "替代复检"
            else:
                how = "只放开 " + "、".join(
                    _method_word(item) for item in sorted(fact.methods)
                ) + "，其余检验方式不适用"
            sentence = f"{who}{how}"
        elif fact.modality == "must":
            if fact.methods & {"on_site"}:
                sentence = f"{who}须以到场联合样验收"
            elif fact.methods & {"batch"}:
                sentence = f"{who}须提供同批检测原件"
            else:
                sentence = f"{who}须提供原件证明"
        elif fact.modality == "may_not":
            sentence = f"{who}不适用该检验方式"
        else:
            sentence = f"{who}的要求以已签发释义为准"
        clauses.append(sentence)
        if source not in sources:
            sources.append(source)
    if not clauses:
        return "", None
    text = f"{lot} {clause}：" + "；".join(dict.fromkeys(clauses))
    return text + f"（依 {'、'.join(sources)}）", "、".join(sources)


def _method_word(method: str) -> str:
    return {
        "on_site": "同批到场联合样",
        "batch": "同批出厂件检测",
        "original": "原件",
        "single_piece": "独立块",
        "unknown": "未判定方式",
    }.get(method, method)


def _unchanged_sentence(model: Model, lot: str, clause: str) -> str:
    for item in model.issued_for(lot, clause):
        if clause in item.unchanged:
            return f"{lot} {clause} 原文不变（依 {item.id}）"
    return ""


def _public_text(
    model: Model,
    kind: str,
    clause: str | None,
    subject: str,
    method: str,
    lots: tuple[str, ...],
) -> tuple[dict[str, str], list[str]]:
    per_lot: dict[str, str] = {}
    sources: list[str] = []
    for lot in lots:
        if clause is None:
            per_lot[lot] = f"{lot}：该提问涉及的对象无法归入现行条款，暂不形成公开释义"
            continue
        sentence, source = _fact_sentence(model, lot, clause, subject, method)
        if not sentence:
            sentence = f"{lot}：{clause} 下未找到适用于该组合的已签发释义"
        unchanged = _unchanged_sentence(model, lot, clause)
        lines = [line for line in (sentence, unchanged) if line]
        per_lot[lot] = "；".join(lines)
        if source:
            sources.append(source)
    return per_lot, list(dict.fromkeys(sources))


def _needs_private_followup(
    record: dict[str, Any],
    kind: str,
    subject: str,
    model: Model,
    lots: tuple[str, ...],
) -> tuple[bool, str | None]:
    """只有不公开追问才补得齐的提问。"""

    text = record.get("text", "")
    tokens = tuple(record.get("private_tokens") or ())
    if not tokens:
        return False, None
    if kind == "evidence_count":
        # 答复取决于该投标人手上缺哪些原件；只有在公开条款能给出
        # 一般答复时，才可直接发布、不必追问。
        has_public_basis = textmod.read(text).clauses and any(
            clause in text for clause in ("4.2", "4.4")
        )
        if not has_public_basis:
            return True, (
                "答复取决于该投标人手上究竟缺哪些原件，需不公开追问补齐；"
                "公开条款已有一般规定，不得提前把结论单独答给这一家"
            )
    # 提问只对某一家独有的构件成立，而现行条款未覆盖该对象时，需追问。
    if subject == "unknown":
        return True, "提问对象无法从公开文本确定，需不公开追问"
    if kind == "material_substitution":
        covered = any(
            model.effective_fact(lot, "4.4", subject)[0] is not None for lot in lots
        )
        if not covered:
            return True, "材料替代的具体对象只能由该投标人补充，需不公开追问"
    return False, None


def answer_questions(
    model: Model,
    submissions: dict[str, dict[str, Any]],
) -> list[QuestionView]:
    views: list[QuestionView] = []
    for record in model.questions:
        text = record.get("text", "")
        tokens = tuple(record.get("private_tokens") or ())
        kind, subject, method = classify(text)
        derived = CLAUSE_FOR_KIND.get(kind)
        cited = textmod.read(text).clauses
        lot = record.get("lot")
        lots = (lot,) if lot else model.lots

        per_lot, sources = _public_text(model, kind, derived, subject, method, lots)
        followup, reason = _needs_private_followup(
            record, kind, subject, model, lots
        )

        # 引错条款不阻止发布，只要求落到正确的条款下；挂错条款才是
        # 会让报价白改的错误。真正不能发布的是私有信息或无法归类。
        misplaced = bool(
            cited
            and derived
            and derived not in cited
            and set(cited) & {"4.2", "4.4"}
        )
        blocked: list[str] = []
        public_answer = None
        if followup:
            blocked.append("需先不公开追问，公开释义暂缓")
        if derived is None:
            blocked.append("无法归入现行条款，不能凭猜测答复")
        if not followup and derived is not None:
            candidate = "\n".join(per_lot[lot] for lot in lots if per_lot.get(lot))
            leaked = textmod.leaks(candidate, tokens)
            if leaked:
                blocked.append(
                    "公开释义仍含提问人私有信息：" + "、".join(leaked)
                )
            else:
                public_answer = candidate

        notes: list[str] = []
        if kind == "inspection_method" and any(
            word in text for word in ("材料", "替换", "代替", "substitute", "replace")
        ):
            notes.append(
                "字面像在问材料替换，实际问的是整套构件的检验方式，应落在 4.2，"
                "不要挂到 4.4 的材料/证明替代下"
            )
        if misplaced:
            notes.append(
                f"归类条款：{derived}；原引条款：{'、'.join(cited)}。"
                "答复只挂在归类条款下，不挂到原引条款"
            )
        if followup:
            notes.append(
                "追问只向提问人进行，追问与答复都不得提前透露给其他投标人，"
                "也不得把结论单独先答给这一家"
            )
        if sources:
            notes.append("公开释义依据：" + "、".join(sources))
        notes.append(
            "公开释义保留必要约束："
            + ("、".join(textmod.constraints(public_answer or "")) or "（无）")
        )

        views.append(
            QuestionView(
                id=record["id"],
                asker=record.get("supplier"),
                kind=kind,
                clause=derived,
                cited_clauses=cited,
                subject=subject,
                method=method,
                lots=lots,
                private_tokens=tokens,
                public_answer=public_answer,
                per_lot=per_lot,
                private_followup=followup,
                followup_reason=reason,
                publishable=public_answer is not None and not blocked,
                blocked_reasons=blocked,
                notes=notes,
            )
        )
    return views


__all__ = ["QuestionView", "answer_questions", "classify"]
