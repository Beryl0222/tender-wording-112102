"""用合成资料覆盖样例数据没有直接触发的几种处置。"""

import unittest

from tender_clarify import Clock, review


def make_brief(identifier, kind, text, **extra):
    row = {"id": identifier, "kind": kind, "text": text, "language": "zh"}
    row.update(extra)
    return row


def base_briefs():
    return {
        "S1": make_brief(
            "S1",
            "spec",
            "4.2 东区墙盒可用到场联合样替代复检，单块不适用。",
            lot="东区",
        ),
        "C1": make_brief(
            "C1",
            "clarification",
            "东区 4.2 联合装配组合须提供原件证明；4.4 不变。",
            decision="issued",
            issued_at="2026-10-18T10:00:00+08:00",
            lot="东区",
        ),
    }


def base_submissions(proof_usable=("P墙盒", "P芯层")):
    return {
        "P1": {
            "id": "P1",
            "kind": "proof",
            "usable_for": list(proof_usable),
            "text": "P 墙盒与芯层共用一份联合装配到场样，无法拆分。",
        },
        "T1": {
            "id": "T1",
            "lot": "东区",
            "supplier": "企业甲",
            "option": "联合样P",
            "choices": ["P墙盒", "P芯层"],
            "proof": ["P1"],
            "filed": "2026-10-15",
        },
        "H1": {
            "id": "H1",
            "kind": "tender_clock",
            "deadline": "2026-10-20T17:00:00+08:00",
            "reconfirm_until": "2026-10-24T17:00:00+08:00",
        },
    }


CLOCK = Clock("2026-10-20T17:00:00+08:00", "2026-10-24T17:00:00+08:00")


class ScenarioTest(unittest.TestCase):
    def test_tightened_requirement_gives_reproof_and_whole_group_reconfirmation(self):
        result = review(base_briefs(), base_submissions(), CLOCK)
        t1 = result.by_submission("T1")
        self.assertEqual(t1.status, "action_required")
        kinds = {notice.kind for notice in t1.notices}
        self.assertIn("re_proof", kinds)
        # 两个构件共用同一份不可拆证明，改一项就要整组确认。
        self.assertIn("reconfirm", kinds)
        self.assertTrue(any("整组" in note or "牵动" in note for note in t1.notes))
        self.assertFalse(t1.whole_return)

    def test_unchanged_clause_does_not_generate_notice(self):
        result = review(base_briefs(), base_submissions(), CLOCK)
        t1 = result.by_submission("T1")
        for notice in t1.notices:
            self.assertNotIn("4.4", notice.clauses)

    def test_scope_limited_translation_cannot_widen(self):
        briefs = base_briefs()
        briefs["T9"] = make_brief(
            "T9",
            "translation",
            "Original documents are required for assembled sets in the east lot.",
            pair="C1",
            lot="西区",
            language="en",
            received_at="2026-10-19T09:00:00+08:00",
        )
        result = review(briefs, base_submissions(), CLOCK)
        answer = result.answers["T9"]
        self.assertEqual(answer["status"], "out_of_scope")
        self.assertEqual(result.model.translations[0].scope.lots, ())

    def test_numeric_conflict_holds_only_that_clause(self):
        briefs = base_briefs()
        briefs["C1"]["text"] = "东区 4.2 联合装配组合须提供 635 mm 到场样；4.4 不变。"
        briefs["T8"] = make_brief(
            "T8",
            "translation",
            "East lot: assembled sets need a 650 mm on-site specimen; 4.4 unchanged.",
            pair="C1",
            lot="东区",
            language="en",
        )
        result = review(briefs, base_submissions(), CLOCK)
        answer = result.answers["T8"]
        self.assertEqual(answer["status"], "partial")
        self.assertEqual(answer["pending_clauses"], ["4.2"])
        self.assertEqual(answer["publishable_clauses"], ["4.4"])
        kinds = {conflict["kind"] for conflict in answer["conflicts"]}
        self.assertIn("numeric", kinds)

    def test_unissued_clarification_does_not_apply(self):
        briefs = base_briefs()
        briefs["C1"]["decision"] = "draft"
        result = review(briefs, base_submissions(), CLOCK)
        self.assertNotIn("C1", {item.id for item in result.model.clarifications})
        self.assertEqual(result.by_submission("T1").status, "clear")

    def test_proof_shared_but_scope_missing_in_one_place(self):
        submissions = base_submissions(proof_usable=("P墙盒",))
        result = review(base_briefs(), submissions, CLOCK)
        t1 = result.by_submission("T1")
        use = t1.proof_uses[0]
        self.assertEqual(use.uncovered, ("P芯层",))
        self.assertTrue(
            any(effect.kind == "proof" for effect in t1.effects)
        )

    def test_requirement_new_to_the_lot_is_asked_about_not_enforced(self):
        briefs = base_briefs()
        # 原文只规定单块拼件，没提联合装配组合；释义才第一次提出要求。
        briefs["S2"] = make_brief(
            "S2", "spec", "4.2 西区单块拼件各件单独提供原件。", lot="西区"
        )
        briefs["C2"] = make_brief(
            "C2",
            "clarification",
            "西区 4.2 联合装配组合须提供原件证明。",
            decision="issued",
            issued_at="2026-10-18T10:00:00+08:00",
            lot="西区",
        )
        submissions = base_submissions(proof_usable=("Q墙盒", "Q芯层"))
        submissions["T2"] = {
            "id": "T2",
            "lot": "西区",
            "supplier": "企业乙",
            "option": "联合样Q",
            "choices": ["Q墙盒", "Q芯层"],
            "proof": ["P1"],
            "filed": "2026-10-15",
        }
        result = review(briefs, submissions, CLOCK)
        t2 = result.by_submission("T2")
        self.assertEqual(t2.status, "pending_answer")
        self.assertIn("先向采购组询问", " ".join(
            effect.reason for effect in t2.effects
        ))
        self.assertFalse(t2.whole_return)

    def test_no_q_private_tokens_appear_in_any_public_answer(self):
        briefs = base_briefs()
        briefs["Q1"] = make_brief(
            "Q1",
            "question",
            "能否把东区 A 组合的到场联合样换成原件？",
            supplier="企业甲",
            private_tokens=["某独家供应商", "800 mm"],
            lot="东区",
        )
        result = review(briefs, base_submissions(), CLOCK)
        question = result.questions[0]
        blob = repr(question.as_dict())
        self.assertNotIn("某独家供应商", blob)
        self.assertNotIn("800 mm", blob)


if __name__ == "__main__":
    unittest.main()
