"""把核对结果排成中文报告或 JSON。"""

from __future__ import annotations

import json
from typing import Any

from .engine import ReviewResult


def _labels(values) -> str:
    return "、".join(values) if values else "无"


def render_text(result: ReviewResult) -> str:
    lines: list[str] = []
    lines.append("== 一轮释疑核对 ==")

    lines.append("")
    lines.append("-- 问答归类与公开释义 --")
    for question in result.questions:
        lines.append(
            f"{question.id}（{question.asker or '匿名'}）"
            f"归类 {question.kind_label} / 条款 {question.clause or '未定'}"
            f" / 对象 {question.subject}"
        )
        if question.cited_clauses and question.clause and question.clause not in question.cited_clauses:
            lines.append(
                f"  注意：原引 {'、'.join(question.cited_clauses)}，应归入 {question.clause}"
            )
        status = "可公开" if question.publishable else "暂缓"
        lines.append(f"  状态：{status}")
        if question.public_answer:
            for line in question.public_answer.splitlines():
                lines.append(f"  释义：{line}")
        for reason in question.blocked_reasons:
            lines.append(f"  暂缓原因：{reason}")
        if question.private_followup:
            lines.append(f"  不公开追问：{question.followup_reason}")

    lines.append("")
    lines.append("-- 已签发释义 --")
    for identifier, answer in result.answers.items():
        if answer.get("status") == "issued":
            lines.append(
                f"{identifier} 签发 {answer['issued_at']} 覆盖 {answer['scope']}"
                f" 条款 {_labels(answer['clauses'])}"
                + ("（更正版）" if answer.get("correction") else "")
                + f" 通知 {_labels(answer['notices'])}"
            )
        elif answer.get("status") == "excluded":
            lines.append(f"{identifier} 不产生效力：{answer['reason']}")
        else:
            lines.append(
                f"{identifier}（{answer.get('pair')} 的译文）状态 {answer['status']}，"
                f"可发布 {_labels(answer.get('publishable_clauses', []))}，"
                f"待签 {_labels(answer.get('pending_clauses', []))}"
            )
            for conflict in answer.get("conflicts", []):
                lines.append(f"  冲突：{conflict['detail']}")

    lines.append("")
    lines.append("-- 递交复核 --")
    for item in result.submissions:
        lines.append(
            f"{item.id} {item.lot} {item.supplier}「{item.option}」"
            f"（{item.subject}，证据方式 {item.evidence_method}）："
            f"{item.as_dict()['status_label']}"
        )
        for use in item.proof_uses:
            lines.append(
                f"  证明 {use.proof_id}：覆盖 {_labels(use.covered)}；"
                f"不足 {_labels(use.uncovered)}"
                + ("；多份递交共用" if use.shared_with else "")
                + ("；不可拆分" if use.indivisible else "")
            )
        for effect in item.effects:
            if effect.treatment != "ok":
                lines.append(
                    f"  {effect.clause} {effect.as_dict()['treatment_label']}：{effect.reason}"
                )
        for notice in item.notices:
            lines.append(
                f"  通知 {notice.id}（{notice.as_dict()['kind_label']}）"
                f" 依据 {_labels(notice.basis)} 期限 {notice.due}"
            )
        for note in item.notes:
            lines.append(f"  说明：{note}")

    lines.append("")
    lines.append("-- 标段与期限 --")
    for lot, info in result.lots.items():
        lines.append(
            f"{lot}：继续有效 {_labels(info['clear'])}；"
            f"可评审 {_labels(info['reviewable'])}；暂缓 {_labels(info['held'])}"
        )
        lines.append(f"  截止后状态：{info['late_correction']}")
    lines.append(
        f"原截止 {result.deadlines['original_deadline']}；"
        f"再确认期限 {result.deadlines['reconfirm_until']}"
    )
    for late in result.deadlines.get("late_receipts", []):
        lines.append(f"  {late['id']} 收到 {late['received_at']}：{late['effect']}")

    if result.late_correction:
        info = result.late_correction
        lines.append("")
        lines.append("-- 截止后的译反 --")
        lines.append(f"{info['reversed_text']}（{info['reversed_pair']} 的译文）把两种检验方式译反")
        lines.append(f"受影响：{_labels(info['affected'])}")
        for lot, state in info["lots"].items():
            lines.append(f"  {lot}：{state}")

    if result.warnings:
        lines.append("")
        lines.append("-- 提示 --")
        for warning in result.warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def render_json(result: ReviewResult, indent: int = 2) -> str:
    payload: dict[str, Any] = result.as_dict()
    return json.dumps(payload, ensure_ascii=False, indent=indent)


__all__ = ["render_json", "render_text"]
