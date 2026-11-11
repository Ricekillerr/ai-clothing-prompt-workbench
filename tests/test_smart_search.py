import unittest

from smart_search import rank_case_matches, score_case


class SmartSearchTests(unittest.TestCase):
    def setUp(self):
        self.case = {
            "id": "case-1", "name": "黑色立领无袖马甲",
            "status": "已验证成功", "sku": "LL-WZWY10278",
            "category": "上装", "subcategory": "无袖马甲",
            "material": "柔软哑光高密度面料", "color": "黑色",
            "details": ["立领", "双拉头拉链", "无袖"],
            "tags": ["黑色", "马甲", "哑光", "双拉头"],
            "body": "严格锁定人物动作，仅替换服装。黑色立领无袖马甲，柔软哑光面料，双拉头拉链。",
        }

    def test_exact_sku_is_perfect_match(self):
        result = score_case(self.case, "款号 LL-WZWY10278")
        self.assertEqual(result["score"], 1.0)
        self.assertIn("款号精确匹配", result["reasons"])

    def test_attribute_query_returns_verified_case_over_threshold(self):
        result = rank_case_matches([self.case], "黑色立领无袖马甲 柔软哑光面料 双拉头拉链")
        self.assertEqual(len(result), 1)
        self.assertGreaterEqual(result[0]["score"], 0.8)

    def test_unverified_cases_are_excluded_by_default(self):
        pending = dict(self.case, status="待验证")
        self.assertEqual(rank_case_matches([pending], "黑色立领无袖马甲", threshold=0.1), [])

    def test_threshold_excludes_weak_matches(self):
        self.assertEqual(rank_case_matches([self.case], "红色皮革长裤", threshold=0.8), [])

    def test_unverified_can_be_included_explicitly(self):
        pending = dict(self.case, status="待验证")
        self.assertEqual(len(rank_case_matches(
            [pending], "黑色立领无袖马甲", threshold=0.1, include_unverified=True
        )), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
