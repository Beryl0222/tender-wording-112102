"""读取并校验交换资料。

资料中的人、机构及经营记录是合成的。仅使用标准库。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

BRIEF_KINDS = {"spec", "question", "reply", "clarification", "translation"}
SUB_KINDS = {"proof", "tender_clock"}


class DataError(ValueError):
    """资料字段缺失、类型错误或跨表引用不成立。"""


@dataclass(frozen=True)
class Clock:
    deadline: str
    reconfirm_until: str


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataError(f"缺少资料文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise DataError(f"{path.name} 不是合法 JSON：{exc}") from exc
    if not isinstance(rows, list):
        raise DataError(f"{path.name} 顶层应为数组")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DataError(f"{path.name}[{index}] 应为对象")
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise DataError(f"{path.name}[{index}] 缺少字符串 id")
        if identifier in seen:
            raise DataError(f"{path.name} 中 id 重复：{identifier}")
        seen.add(identifier)
        if "text" in row and not isinstance(row["text"], str):
            raise DataError(f"{path.name}:{identifier} text 应为字符串")
        out.append(row)
    return out


def _require(row: dict[str, Any], field: str, where: str) -> Any:
    if field not in row:
        raise DataError(f"{where}:{row['id']} 缺少字段 {field}")
    return row[field]


def _str_list(row: dict[str, Any], field: str, where: str) -> list[str]:
    value = _require(row, field, where)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DataError(f"{where}:{row['id']} {field} 应为字符串数组")
    return value


def _validate_briefs(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        kind = _require(row, "kind", "briefs")
        if kind not in BRIEF_KINDS:
            raise DataError(f"briefs:{row['id']} 未知 kind：{kind}")
        if "lot" in row and row["lot"] is not None and not isinstance(row["lot"], str):
            raise DataError(f"briefs:{row['id']} lot 应为字符串或 null")
        if "pair" in row and row["pair"] is not None and not isinstance(row["pair"], str):
            raise DataError(f"briefs:{row['id']} pair 应为字符串或 null")
        if kind == "question" and row.get("private_tokens") is not None:
            _str_list(row, "private_tokens", "briefs")
        if kind == "clarification" and row.get("decision") == "issued":
            if not row.get("issued_at"):
                raise DataError(f"briefs:{row['id']} 已签发但缺少 issued_at")
        by_id[row["id"]] = row
    for identifier, row in by_id.items():
        pair = row.get("pair")
        if pair is not None:
            target = by_id.get(pair)
            if target is None:
                raise DataError(f"briefs:{identifier} pair 指向不存在的 {pair}")
            if target["kind"] not in {"clarification", "spec"}:
                raise DataError(f"briefs:{identifier} pair 应指向澄清或原文，而非 {target['kind']}")
    return by_id


def _validate_submissions(
    rows: Iterable[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], Clock]:
    by_id: dict[str, dict[str, Any]] = {}
    clock: Clock | None = None
    for row in rows:
        kind = row.get("kind")
        if kind is not None and kind not in SUB_KINDS:
            # 投标递交没有 kind 字段，其余带 kind 的须是证明或时钟。
            raise DataError(f"submissions:{row['id']} 未知 kind：{kind}")
        if kind == "tender_clock":
            if clock is not None:
                raise DataError("submissions 中 tender_clock 不唯一")
            clock = Clock(
                deadline=_require(row, "deadline", "submissions"),
                reconfirm_until=_require(row, "reconfirm_until", "submissions"),
            )
        by_id[row["id"]] = row

    if clock is None:
        raise DataError("submissions 缺少 tender_clock")

    for identifier, row in by_id.items():
        kind = row.get("kind")
        if kind == "proof":
            _str_list(row, "usable_for", "submissions")
            continue
        if kind == "tender_clock":
            continue
        # 投标递交
        for field in ("lot", "supplier", "option"):
            if not isinstance(_require(row, field, "submissions"), str):
                raise DataError(f"submissions:{identifier} {field} 应为字符串")
        choices = _str_list(row, "choices", "submissions")
        if not choices:
            raise DataError(f"submissions:{identifier} choices 不能为空")
        proofs = _str_list(row, "proof", "submissions")
        for proof_id in proofs:
            proof = by_id.get(proof_id)
            if proof is None:
                raise DataError(f"submissions:{identifier} 引用了不存在的证明 {proof_id}")
            if proof.get("kind") != "proof":
                raise DataError(f"submissions:{identifier} 引用的 {proof_id} 不是证明")
    return by_id, clock


def load(data_dir: str | Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], Clock]:
    """读取 briefs.json 与 submissions.json。

    返回 (briefs, submissions, clock)，两张表都按 id 索引。
    """

    root = Path(data_dir)
    briefs = _validate_briefs(_read(root / "briefs.json"))
    submissions, clock = _validate_submissions(_read(root / "submissions.json"))
    return briefs, submissions, clock
