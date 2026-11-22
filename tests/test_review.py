"""一轮释疑的核对结果应当与资料中的事实一致。"""

import json
from pathlib import Path
import unittest

from tender_clarify import load, review
from tender_clarify import text as textmod
from tender_clarify.public import classify


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class ReviewFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.briefs, cls.submissions, cls.clock = load(DATA_DIR)
        cls.result = review(cls.briefs, cls.submissions, cls.clock)

    # -- 原文解析 ---------------------------------------------------------
    def test_clause_scoping_does_not_leak_4_4_into_4_2(self):
        reading = textmod.read(self.briefs["Q24"]["text"])
        modular = reading.facts_for("modular", "4.2")
        single = reading.facts_for("single", "4.2")
        self.assertEqual([f.modality for f in modular], ["may"])
        self.assertEqual([f.modality for f in single], ["may_not"])
        self.assertIn("4.4", reading.unchanged)

    def test_conditions_4_4_change_is_recorded_as_unchanged(self):
        item = self.result.model.by_id["Q26"]
        reading = textmod.read(item["text"])
        self.assertEqual(reading.unchanged, ("4.4",))

    # -- 澄清效力与范围 ---------------------------------------------------
    def test_issued_scope_is_lot_limited(self):
        model = self.result.model
        self.assertIsNone(model.by_id["Q24"]["lot"])
        self.assertEqual(model.by_id["Q26"]["lot"], "北区")

    def test_correction_excludes_the_lot_it_says_is_unchanged(self):
        model = self.result.model
        north = [item.id for item in model.issued_for("北区", "4.2")]
        south = [item.id for item in model.issued_for("南区", "4.2")]
        self.assertEqual(north[0], "Q26")
        self.assertEqual(south[0], "Q24")
        self.assertNotIn("Q26", south)

    def test_unissued_reply_has_no_effect(self):
        answer = self.result.answers["Q23"]
        self.assertEqual(answer["status"], "excluded")
        self.assertIn("不产生效力", answer["reason"])
        self.assertNotIn("Q23", {item.id for item in self.result.model.clarifications})

    def test_old_reply_is_not_used_as_current_text(self):
        answer = self.result.answers["Q23"]
        self.assertIn("旧稿", answer["reason"])

    # -- 译文效力 ---------------------------------------------------------
    def test_translation_that_reverses_modalities_is_split(self):
        answer = self.result.answers["Q25"]
        self.assertEqual(answer["status"], "partial")
        self.assertEqual(answer["pending_clauses"], ["4.2"])
        self.assertEqual(answer["publishable_clauses"], ["4.4"])
        kinds = {conflict["kind"] for conflict in answer["conflicts"]}
        self.assertEqual(kinds, {"modality"})

    def test_faithful_translation_is_effective(self):
        answer = self.result.answers["Q27"]
        self.assertEqual(answer["status"], "effective")
        self.assertEqual(answer["conflicts"], [])
        self.assertIn("4.2", answer["publishable_clauses"])

    def test_translation_cannot_widen_lot_scope(self):
        answer = self.result.answers["Q27"]
        self.assertEqual(answer["scope"], "北区（排除南区）")

    # -- 提问归类与私有信息 -----------------------------------------------
    def test_question_kinds_are_told_apart(self):
        self.assertEqual(classify(self.briefs["Q21"]["text"])[0], "inspection_method")
        self.assertEqual(classify(self.briefs["Q22"]["text"])[0], "evidence_count")

    def test_question_about_site_test_lands_under_4_2(self):
        view = next(q for q in self.result.questions if q.id == "Q21")
        self.assertEqual(view.clause, "4.2")
        self.assertIn("4.2", view.cited_clauses)

    def test_public_answer_does_not_leak_private_tokens(self):
        view = next(q for q in self.result.questions if q.id == "Q21")
        self.assertTrue(view.publishable)
        for token in self.briefs["Q21"]["private_tokens"]:
            self.assertNotIn(token, view.public_answer)
            self.assertNotIn(token, json.dumps(view.per_lot, ensure_ascii=False))
        self.assertIn("4.2", view.public_answer)
        self.assertIn("联合装配", view.public_answer)

    def test_question_needing_private_followup_is_not_published(self):
        view = next(q for q in self.result.questions if q.id == "Q22")
        self.assertTrue(view.private_followup)
        self.assertFalse(view.publishable)
        self.assertIsNone(view.public_answer)

    def test_private_followup_does_not_identify_supplier(self):
        view = next(q for q in self.result.questions if q.id == "Q22")
        for token in self.briefs["Q22"]["private_tokens"]:
            self.assertNotIn(token, view.followup_reason)

    # -- 递交复核 ---------------------------------------------------------
    def test_unaffected_submission_is_not_returned(self):
        t4 = self.result.by_submission("T4")
        self.assertEqual(t4.status, "clear")
        self.assertTrue(t4.not_affected)
        self.assertFalse(t4.whole_return)
        self.assertEqual(t4.notices, [])

    def test_shared_proof_valid_only_where_scope_covers(self):
        t2 = self.result.by_submission("T2")
        e1 = next(use for use in t2.proof_uses if use.proof_id == "E1")
        e2 = next(use for use in t2.proof_uses if use.proof_id == "E2")
        self.assertFalse(e1.sufficient)
        self.assertTrue(e2.sufficient)
        self.assertEqual(t2.status, "clear")
        self.assertFalse(t2.whole_return)

    def test_indivisible_combination_moves_as_a_whole(self):
        t1 = self.result.by_submission("T1")
        e1 = next(use for use in t1.proof_uses if use.proof_id == "E1")
        self.assertTrue(e1.indivisible)
        self.assertIn("联合样A", " ".join(t1.notes))

    def test_missing_joint_sample_generates_evidence_gap_not_whole_return(self):
        # E3 缺联合样是复核时发现的既有缺件；Q26 无论按哪种读法都不接受
        # 独立块，因此这不是公开变更造成的影响，不能记成补证通知。
        t3 = self.result.by_submission("T3")
        self.assertEqual(t3.status, "action_required")
        self.assertFalse(t3.whole_return)
        notice = t3.notices[0]
        self.assertEqual(notice.kind, "evidence_gap")
        self.assertEqual(notice.due, self.clock.reconfirm_until)

    def test_notices_carry_shared_answers_and_deadline(self):
        for item in self.result.submissions:
            if not item.notices:
                continue
            self.assertEqual(item.notices[0].due, self.clock.reconfirm_until)
            answers = item.notices[0].answers
            self.assertEqual(set(answers), {n.id for n in item.notices})

    # -- 截止后的译反 -----------------------------------------------------
    def test_late_reversal_holds_only_affected_submission(self):
        late = self.result.late_correction
        self.assertIsNotNone(late)
        self.assertEqual(late["reversed_text"], "Q25")
        self.assertEqual(late["affected"], ["T1"])
        self.assertEqual(late["unified_correction"]["id"], "Q26")
        self.assertIn("暂缓", late["lots"]["北区"])
        self.assertIn("原截止记录", late["lots"]["南区"])

    def test_held_submission_is_not_reviewable(self):
        t1 = self.result.by_submission("T1")
        self.assertEqual(t1.status, "hold")
        self.assertFalse(t1.reviewable)
        self.assertEqual(self.result.lots["北区"]["reviewable"], ["T3", "T4"])

    def test_unrelated_lots_keep_original_deadline_record(self):
        self.assertEqual(self.result.lots["南区"]["held"], [])
        self.assertEqual(
            self.result.deadlines["original_deadline"], self.clock.deadline
        )

    def test_late_translation_is_flagged_not_backdated(self):
        ids = [item["id"] for item in self.result.deadlines["late_receipts"]]
        self.assertIn("Q25", ids)
        self.assertNotIn("Q27", ids)

    # -- 总览 -------------------------------------------------------------
    def test_every_submission_is_accounted_for(self):
        ids = {item.id for item in self.result.submissions}
        self.assertEqual(ids, {"T1", "T2", "T3", "T4"})
        for item in self.result.submissions:
            self.assertTrue(item.valid)

    def test_no_submission_is_returned_whole(self):
        for item in self.result.submissions:
            self.assertFalse(item.whole_return, item.id)


if __name__ == "__main__":
    unittest.main()
