"""从中文原文与英文译文中抽取可核对的事实。

这里不做通用自然语言理解，只识别招标资料反复出现的那几类措辞：
条款号、联合装配/单块、检验方式（到场/原件/同批）、替代与强制、
例外范围、更正与不变量。识别不了的句子记为「未判定」，不猜测。

判断要求强弱时按微句（逗号也切）归属对象与方式，避免把
「……可用，单块不能」这类句子的结论算到错误的对象或条款下。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CLAUSE_RE = re.compile(r"(?<![\d.])(\d+\.\d+)(?![\d.])")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
LOT_RE = re.compile(r"([一-鿿]{1,2}区)")
NO_CHANGE_LOT_RE = re.compile(r"([一-鿿]{1,2}区)(?:无变化|无變|不变|維持原狀)")

MODULAR = "modular"
SINGLE = "single"

SUBJECT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (SINGLE, (
        "单块", "单个", "单件", "各件", "每件", "拼件", "独立块", "单独",
        "single", "individual module", "each piece", "each unit", "standalone",
    )),
    (MODULAR, (
        "联合装配", "联装样", "联合样", "联合装配样", "到场联合样", "成对", "组合件",
        "assembled", "joint sample", "jointly assembled", "assembled set",
        "module pair", "as a pair", "pair of",
    )),
)

MODALITY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("may_not", (
        "不能", "不可", "不得", "不适用", "不予", "不接受", "禁止", "无需",
        "must not", "may not", "not permitted", "is not accepted", "are not accepted",
    )),
    ("must", (
        "必须", "须提供", "须交", "须单独", "需", "原件", "required", "must ", "shall ",
    )),
    ("may", (
        "可用", "可以", "可采用", "接受", "认可", "等效", "代替", "替代",
        "may use", "can use", "is equivalent", "are equivalent", "accepted",
    )),
)

METHOD_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("on_site", ("到场", "现场", "工地", "on-site", "onsite", "site test", "site sampling")),
    ("original", ("原件", "original")),
    ("batch", ("同批", "出厂件", "same batch", "same-batch")),
)

_UNCHANGED = ("不变", "原文不变", "unchanged", "no change", "no changes")
_CORRECTION = ("更正", "勘误", "订正", "corrected", "correction", "erratum")


@dataclass
class Fact:
    """一条微句中的断言。

    ``clauses`` 只收本微句（或紧邻上文）明确点出的条款；``anchors``
    另收该句所在整句里出现过的条款，用于「同一句后半段省略条款号」的
    情形。要求强度只按 ``clauses`` 归属，避免把 4.4 的句子挂到 4.2。
    """

    subject: str
    modality: str
    clause: str | None
    clauses: tuple[str, ...]
    methods: frozenset[str]
    text: str
    anchors: tuple[str, ...] = ()

    def applies_to(self, clause: str) -> bool:
        return clause in self.clauses

    def mentions(self, clause: str) -> bool:
        return clause in self.clauses or clause in self.anchors

    @property
    def is_restriction(self) -> bool:
        return self.modality in {"must", "may_not"}


@dataclass
class Reading:
    clauses: tuple[str, ...] = ()
    facts: list[Fact] = field(default_factory=list)
    unchanged: tuple[str, ...] = ()
    no_change_lots: tuple[str, ...] = ()
    lots: tuple[str, ...] = ()
    corrections: bool = False
    undetermined: list[str] = field(default_factory=list)

    def facts_for(
        self,
        subject: str,
        clause: str | None = None,
        methods: frozenset[str] | None = None,
    ) -> list[Fact]:
        candidates = [
            fact
            for fact in self.facts
            if fact.subject == subject
            and fact.modality != "unknown"
            and (clause is None or fact.applies_to(clause))
        ]
        if methods:
            overlapping = [fact for fact in candidates if fact.methods & methods]
            candidates = overlapping or [fact for fact in candidates if not fact.methods]
        return candidates


_SENTENCE_END = re.compile(r"(?<=[。！？；;])\s*|(?<!\d)[.!?](?!\d)\s*|\n+")


def _sentences(text: str) -> list[str]:
    """按中英文句读切句，但不切断 4.2 这类条款号。"""

    return [s for s in _SENTENCE_END.split(text) if s and s.strip()]


def micro_clauses(sentence: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"[，,、]\s*", sentence) if chunk.strip()]


def subject_of(clause: str) -> str:
    lowered = clause.lower()
    hits: list[tuple[int, str]] = []
    for name, needles in SUBJECT_PATTERNS:
        for needle in needles:
            index = lowered.find(needle.lower())
            if index >= 0:
                hits.append((index, name))
    if not hits:
        return "unknown"
    hits.sort()
    return hits[0][1]


def modality_of(clause: str) -> str:
    lowered = clause.lower()
    hits: list[tuple[int, str]] = []
    for name, needles in MODALITY_PATTERNS:
        for needle in needles:
            index = lowered.find(needle.lower())
            if index >= 0:
                hits.append((index, name))
    if not hits:
        return "unknown"
    order = {"may_not": 0, "must": 1, "may": 2}
    hits.sort(key=lambda item: (order[item[1]], item[0]))
    return hits[0][1]


def methods_of(clause: str) -> frozenset[str]:
    lowered = clause.lower()
    return frozenset(
        name
        for name, needles in METHOD_PATTERNS
        if any(needle.lower() in lowered for needle in needles)
    )


def read(text: str) -> Reading:
    """解析一段中文或英文文字。"""

    reading = Reading()
    reading.clauses = tuple(dict.fromkeys(CLAUSE_RE.findall(text)))
    reading.corrections = any(token in text.lower() for token in _CORRECTION)
    reading.lots = tuple(dict.fromkeys(LOT_RE.findall(text)))
    reading.no_change_lots = tuple(
        dict.fromkeys(m.group(1) for m in NO_CHANGE_LOT_RE.finditer(text))
    )

    for sentence in _sentences(text):
        inherited: list[str] = []
        inherited_methods: frozenset[str] = frozenset()
        sentence_clauses = tuple(dict.fromkeys(CLAUSE_RE.findall(sentence)))
        for chunk in micro_clauses(sentence):
            found = list(CLAUSE_RE.findall(chunk))
            if found:
                inherited = found
            # 微句开头的条款号是本句主体；句中后续出现的号码视为引用，
            # 不把「4.4 … 可用 4.2 的附件代替」算作 4.2 上的要求。
            clauses = (found[0],) if found else tuple(inherited[:1])

            if any(token in chunk.lower() or token in chunk for token in _UNCHANGED):
                for clause in clauses:
                    if clause not in reading.unchanged:
                        reading.unchanged = (*reading.unchanged, clause)
                continue
            if "无变化" in chunk:
                continue

            methods = methods_of(chunk) or inherited_methods
            if methods and subject_of(chunk) in {MODULAR, SINGLE}:
                inherited_methods = methods

            subject = subject_of(chunk)
            modality = modality_of(chunk)
            if subject == "unknown" and modality == "unknown":
                if clauses and not any(
                    set(fact.clauses) & set(clauses) for fact in reading.facts
                ):
                    reading.undetermined.append(chunk)
                continue
            reading.facts.append(
                Fact(
                    subject=subject,
                    modality=modality,
                    clause=clauses[0] if clauses else None,
                    clauses=clauses,
                    methods=methods,
                    text=chunk,
                    anchors=sentence_clauses,
                )
            )
    return reading


def numbers(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(NUMBER_RE.findall(text)))


DIMENSION_WORDS = ("mm", "cm", "m", "kg", "MPa", "kN", "毫米", "厘米", "米", "公斤", "千牛", "兆帕")


def dimensions(text: str) -> tuple[str, ...]:
    """抽取带单位的数值片段，例如 635 mm。"""

    found: list[str] = []
    for word in DIMENSION_WORDS:
        for pattern in (
            rf"\d+(?:\.\d+)?\s*{re.escape(word)}\b",
            rf"\d+(?:\.\d+)?{re.escape(word)}",
        ):
            found.extend(re.sub(r"\s+", " ", m.group(0)) for m in re.finditer(pattern, text))
    return tuple(dict.fromkeys(found))


CONSTRAINT_WORDS = (
    "成对", "单块", "单件", "各件", "联合装配", "联装样", "联合样", "原件",
    "同批", "出厂件", "复检", "验收", "再生芯层", "墙盒", "芯层", "面件",
    "pair", "single", "assembled", "original", "same batch", "re-inspection",
)


def constraints(text: str) -> tuple[str, ...]:
    """释义必须保留的约束用词（不含供应商、尺寸）。"""

    lowered = text.lower()
    return tuple(dict.fromkeys(w for w in CONSTRAINT_WORDS if w.lower() in lowered))


def redact(text: str, tokens: tuple[str, ...] | list[str]) -> str:
    """去掉提问人独有的供应商名、尺寸等。"""

    out = text
    for token in sorted(tokens, key=len, reverse=True):
        if token:
            out = re.sub(re.escape(token), "某", out)
    return re.sub(r"\s+", " ", out).strip()


def leaks(text: str, tokens: tuple[str, ...] | list[str]) -> list[str]:
    return [token for token in tokens if token and token in text]


def proof_scope(record: dict) -> set[str]:
    """证据文字里「只对 X 组合有结论」之类的自我限缩。"""

    scope = set(record.get("usable_for", ()))
    for match in re.finditer(r"只对\s*([A-Za-z0-9一-鿿]+?)\s*组合", record.get("text", "")):
        prefix = match.group(1)[:1]
        scope = {item for item in scope if item.startswith(prefix)}
    return scope
