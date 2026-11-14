import unittest
from case_matcher import rank_matches, similarity, rank_case_matches, score_case_attributes, explain_case_match, find_case_conflicts

class CaseMatcherTests(unittest.TestCase):
    def test_same_stem_prompt_and_image_rank_high(self):
        items=[{"path":"set01/jacket_front.png","name":"jacket_front.png","media_type":"image"},{"path":"set01/jacket_front.txt","name":"jacket_front.txt","media_type":"text"},{"path":"other/shoes.mp4","name":"shoes.mp4","media_type":"video"}]
        results=rank_matches(items); self.assertEqual(len(results),1); self.assertEqual(results[0]["asset_path"],"set01/jacket_front.png"); self.assertGreaterEqual(results[0]["score"],0.9)
    def test_directory_alone_does_not_create_match(self):
        items=[{"path":"folder/alpha.txt","name":"alpha.txt","media_type":"text"},{"path":"folder/zzzzzz.png","name":"zzzzzz.png","media_type":"image"}]; self.assertEqual(rank_matches(items),[])
    def test_matching_is_only_a_suggestion(self):
        items=[{"path":"x/look_01.txt","name":"look_01.txt","media_type":"text"},{"path":"x/look_01.mp4","name":"look_01.mp4","media_type":"video"}]
        results=rank_matches(items); self.assertEqual(results[0]["status"],"suggested"); self.assertIn("reason",results[0])
    def test_similarity_is_case_and_extension_insensitive(self): self.assertGreater(similarity("Jacket-Front.PNG","jacket_front.txt"),0.8)
    def setUp(self):
        self.case={"id":"case-1","name":"黑色立领无袖马甲","status":"已验证成功","sku":"LL-WZWY10278","category":"上装","subcategory":"无袖马甲","material":"柔软哑光高密度面料","color":"黑色","details":["立领","双拉头拉链","无袖"],"tags":["黑色","马甲","哑光","双拉头"],"body":"严格锁定人物动作，仅替换服装。黑色立领无袖马甲，柔软哑光面料，双拉头拉链。"}
    def test_exact_sku_is_perfect_match(self):
        result=score_case_attributes(self.case,"款号 LL-WZWY10278"); self.assertEqual(result["score"],1.0); self.assertIn("款号精确匹配",result["reasons"])
    def test_exact_sku_retrieves_verified_case_at_100_percent(self):
        results = rank_case_matches([self.case], "LL-WZWY10278")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["case"]["id"], "case-1")
        self.assertEqual(results[0]["match_percent"], 100)
        self.assertIn("款号精确匹配", results[0]["reasons"])

    def test_legacy_style_number_and_product_code_are_supported(self):
        for field in ("style_number", "product_code"):
            case = dict(self.case, sku=None)
            case[field] = "LEGACY-7788"
            result = score_case_attributes(case, "LEGACY-7788")
            self.assertEqual(result["score"], 1.0, field)
            self.assertIn("款号精确匹配", result["reasons"])

    def test_structured_garment_fields_contribute_to_attribute_matching(self):
        case = dict(self.case, silhouette="宽松", fit="宽松", fabric="尼龙",
                    texture="哑光", craftsmanship="双针走线",
                    constraints="锁定人物动作")
        result = rank_case_matches([case], "宽松尼龙哑光双针走线", threshold=0.8)
        self.assertEqual(len(result), 1)
        self.assertGreaterEqual(result[0]["score"], 0.8)

    def test_material_synonyms_match_canonical_values(self):
        case = dict(self.case, material="尼龙")
        result = score_case_attributes(case, "锦纶马甲")
        self.assertGreaterEqual(result["score"], 0.8)

    def test_color_synonyms_match_canonical_values(self):
        case = dict(self.case, color="蓝色")
        result = score_case_attributes(case, "藏青色马甲")
        self.assertGreaterEqual(result["score"], 0.8)

    def test_individual_outfit_item_attributes_are_searchable_from_full_outfit_case(self):
        case = dict(self.case, category="整套搭配", subcategory="冬季穿搭",
                    outfit_items=[
                        {"category": "上装", "subcategory": "无袖马甲",
                         "name": "黑色立领马甲", "material": "尼龙",
                         "color": "黑色", "details": ["双拉头拉链"]},
                        {"category": "下装", "subcategory": "微喇裤",
                         "name": "深灰灯芯绒裤", "material": "灯芯绒",
                         "color": "深灰", "details": ["微喇"]},
                    ])
        result = rank_case_matches([case], "深灰灯芯绒微喇裤", threshold=0.8)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["case"]["id"], "case-1")
        self.assertEqual(result[0]["matched_item"], "深灰灯芯绒裤")

    def test_attribute_query_returns_verified_case_over_threshold(self):
        result=rank_case_matches([self.case],"黑色立领无袖马甲 柔软哑光面料 双拉头拉链"); self.assertEqual(len(result),1); self.assertGreaterEqual(result[0]["score"],0.8)
    def test_unverified_cases_are_excluded_by_default(self):
        self.assertEqual(rank_case_matches([dict(self.case,status="待验证")],"黑色立领无袖马甲",threshold=0.1),[])
    def test_threshold_excludes_weak_matches(self): self.assertEqual(rank_case_matches([self.case],"红色皮革长裤",threshold=0.8),[])
    def test_unverified_can_be_included_explicitly(self):
        self.assertEqual(len(rank_case_matches([dict(self.case,status="待验证")],"黑色立领无袖马甲",threshold=0.1,include_unverified=True)),1)
    def test_explanation_reports_matched_and_missing_attributes(self):
        details = explain_case_match(self.case, "黑色立领马甲 红色皮革")
        by_name = {item["attribute"]: item for item in details}
        self.assertIn("color", by_name)
        self.assertTrue(by_name["color"]["matched_terms"])
        self.assertTrue(by_name["color"]["missing_terms"])
        self.assertIn("material", by_name)
        self.assertTrue(by_name["material"]["missing_terms"])

    def test_ranked_result_includes_explanation_without_changing_legacy_fields(self):
        results = rank_case_matches([self.case], "黑色立领无袖马甲 柔软哑光面料 双拉头拉链")
        self.assertEqual(len(results), 1)
        self.assertIn("reasons", results[0])
        self.assertIn("attribute_details", results[0])
        self.assertTrue(results[0]["attribute_details"])
    def test_hard_category_conflict_excludes_wrong_garment_type(self):
        case = dict(
            self.case, name="黑色微喇裤", category="下装",
            subcategory="微喇裤", details=["隐藏抽绳"],
        )
        conflicts = find_case_conflicts(case, "黑色立领无袖马甲")
        self.assertTrue(any(item["attribute"] == "category" for item in conflicts))
        self.assertEqual(
            rank_case_matches([case], "黑色立领无袖马甲", threshold=0.1),
            [],
        )

    def test_full_outfit_item_breakdown_allows_matching_a_specific_garment(self):
        case = dict(
            self.case, category="整套搭配", subcategory="冬季穿搭",
            outfit_items=[
                {"category": "上装", "subcategory": "无袖马甲", "name": "黑色马甲"},
                {"category": "下装", "subcategory": "微喇裤", "name": "深灰灯芯绒裤"},
            ],
        )
        query = "深灰灯芯绒微喇裤"
        self.assertEqual(find_case_conflicts(case, query), [])
        results = rank_case_matches([case], query, threshold=0.8)
        self.assertEqual(len(results), 1)
        self.assertGreaterEqual(results[0]["score"], 0.8)
        self.assertEqual(results[0]["matched_item"], "深灰灯芯绒裤")
        self.assertIn("面料/材质匹配", results[0]["reasons"])
        self.assertIn("颜色匹配", results[0]["reasons"])

    def test_full_outfit_color_and_material_conflicts_use_item_breakdown(self):
        case = dict(
            self.case, category="整套搭配", color="黑色", material="尼龙",
            outfit_items=[
                {"category": "上装", "name": "黑色马甲", "color": "黑色", "material": "尼龙"},
                {"category": "下装", "name": "深灰灯芯绒裤", "color": "深灰", "material": "灯芯绒"},
            ],
        )
        self.assertEqual(find_case_conflicts(case, "深灰灯芯绒裤"), [])

    def test_full_outfit_without_breakdown_is_not_assumed_to_miss_an_item(self):
        case = dict(self.case, category="整套搭配", subcategory="冬季穿搭")
        self.assertEqual(find_case_conflicts(case, "黑色立领无袖马甲"), [])

    def test_explicit_color_conflict_is_detected(self):
        conflicts = find_case_conflicts(self.case, "红色立领马甲")
        self.assertTrue(any(x["attribute"] == "color" for x in conflicts))

    def test_mixed_materials_are_not_treated_as_conflict(self):
        case = dict(self.case, material="尼龙与棉混纺", tags=["混纺"])
        self.assertEqual(find_case_conflicts(case, "纯棉面料马甲"), [])

    def test_multicolor_query_is_not_auto_rejected(self):
        self.assertEqual(find_case_conflicts(self.case, "黑红拼接马甲"), [])

    def test_unstructured_body_text_does_not_create_color_conflict(self):
        case = dict(self.case, color=None, color_details=None, body="红色背景中的黑色马甲")
        self.assertEqual(find_case_conflicts(case, "红色马甲"), [])

    def test_explicit_material_conflict_is_detected(self):
        conflicts = find_case_conflicts(dict(self.case, material="皮革"), "尼龙面料黑色马甲")
        self.assertTrue(any(x["attribute"] == "material" for x in conflicts))

    def test_material_conflict_does_not_reject_blended_fabrics(self):
        case = dict(self.case, material="尼龙与棉混纺")
        self.assertEqual(find_case_conflicts(case, "纯棉面料马甲"), [])

    def test_material_conflict_does_not_use_prompt_body_or_tags(self):
        case = dict(self.case, material=None, fabric=None, texture=None,
                    material_details=None, body="尼龙外套背景中的黑色马甲")
        self.assertEqual(find_case_conflicts(case, "尼龙面料马甲"), [])

    def test_cashmere_and_wool_are_distinct_material_values(self):
        case = dict(self.case, material="羊毛")
        conflicts = find_case_conflicts(case, "羊绒马甲")
        self.assertTrue(any(x["attribute"] == "material" for x in conflicts))

    def test_polyester_and_acrylic_aliases_are_recognized(self):
        case = dict(self.case, material="聚酯纤维")
        conflicts = find_case_conflicts(case, "腈纶面料")
        self.assertTrue(any(x["attribute"] == "material" for x in conflicts))

    def test_conflicting_case_is_excluded_by_default(self):
        results = rank_case_matches([self.case], "红色立领无袖马甲", threshold=0.1)
        self.assertEqual(results, [])

    def test_conflicting_case_can_be_included_for_manual_review(self):
        results = rank_case_matches([self.case], "红色立领无袖马甲", threshold=0.1, include_conflicts=True)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["conflicts"])

    def test_curated_verified_case_retrieval_benchmark(self):
        cases = [
            {"id": "vest", "name": "黑色立领无袖马甲", "status": "已验证成功",
             "category": "上装", "subcategory": "无袖马甲", "material": "尼龙",
             "color": "黑色", "silhouette": "直筒", "fit": "宽松",
             "details": ["立领", "双拉头拉链"], "tags": ["马甲"]},
            {"id": "trousers", "name": "深灰灯芯绒微喇裤", "status": "已验证成功",
             "category": "下装", "subcategory": "微喇裤", "material": "灯芯绒",
             "color": "深灰", "silhouette": "微喇", "fit": "宽松",
             "details": ["长裤", "隐藏抽绳"], "tags": ["裤子"]},
            {"id": "coat", "name": "藏青羊毛连帽外套", "status": "已验证成功",
             "category": "上装", "subcategory": "外套", "material": "羊毛",
             "color": "蓝色", "silhouette": "长款", "fit": "宽松",
             "details": ["连帽", "拉链"], "tags": ["外套"]},
            {"id": "jacket", "name": "棕色皮革短款夹克", "status": "已验证成功",
             "category": "上装", "subcategory": "夹克", "material": "皮革",
             "color": "棕色", "silhouette": "短款", "fit": "标准",
             "details": ["立领", "拉链"], "tags": ["夹克"]},
        ]
        labelled_queries = [
            ("黑色尼龙立领无袖马甲 双拉头", "vest"),
            ("深灰灯芯绒微喇裤", "trousers"),
            ("藏青羊毛连帽外套", "coat"),
            ("棕色皮革短款夹克", "jacket"),
        ]
        for query, expected_id in labelled_queries:
            with self.subTest(query=query):
                results = rank_case_matches(cases, query, threshold=0.8)
                self.assertTrue(results, query)
                self.assertEqual(results[0]["case"]["id"], expected_id)
                self.assertGreaterEqual(results[0]["match_percent"], 80)
        self.assertEqual(
            rank_case_matches(cases, "红色羊绒羽绒服", threshold=0.8), []
        )


    def test_missing_requested_material_is_zero_coverage_and_reduces_the_score(self):
        case = dict(self.case, material="")
        query = "黑色尼龙马甲"
        result = score_case_attributes(case, query)
        self.assertAlmostEqual(result["score"], 0.6667, places=3)
        details = {item["attribute"]: item for item in explain_case_match(case, query)}
        self.assertEqual(details["material"]["coverage"], 0.0)
        self.assertIn("尼龙", details["material"]["missing_terms"])
        self.assertEqual(rank_case_matches([case], query, threshold=0.8), [])

    def test_unrequested_missing_attributes_do_not_reduce_score(self):
        case = dict(self.case, material="", color="", details=[], model="", scene="")
        result = score_case_attributes(case, "黑色马甲")
        self.assertEqual(result["score"], 1.0)

    def test_english_garment_alias_requires_a_word_boundary(self):
        case = dict(self.case, name="black vest", subcategory="vest", body="", tags=[])
        result = score_case_attributes(case, "investment notes")
        self.assertEqual(result["score"], 0.0)

    def test_common_footwear_and_accessory_subcategories_are_searchable(self):
        boots = dict(
            self.case, id="boots", name="棕色皮革短靴", category="鞋履",
            subcategory="短靴", material="皮革", color="棕色", details=["系带"],
        )
        backpack = dict(
            self.case, id="backpack", name="黑色尼龙双肩包", category="配饰",
            subcategory="双肩包", material="尼龙", color="黑色", details=["双肩","拉链"],
        )
        boot_results = rank_case_matches([boots], "棕色皮革短靴", threshold=0.8)
        bag_results = rank_case_matches([backpack], "黑色尼龙双肩包", threshold=0.8)
        self.assertEqual([item["case"]["id"] for item in boot_results], ["boots"])
        self.assertEqual([item["case"]["id"] for item in bag_results], ["backpack"])

    def test_retrieval_benchmark_reports_precision_at_1_and_recall_at_5(self):
        cases = [
            {"id":"vest","name":"黑色立领无袖马甲","status":"已验证成功","category":"上装","subcategory":"无袖马甲","material":"尼龙","color":"黑色","details":["立领","双拉头拉链"],"tags":["马甲"]},
            {"id":"trousers","name":"深灰灯芯绒微喇裤","status":"已验证成功","category":"下装","subcategory":"微喇裤","material":"灯芯绒","color":"深灰","details":["微喇","宽松"],"tags":["裤子"]},
            {"id":"coat","name":"藏青羊毛连帽外套","status":"已验证成功","category":"上装","subcategory":"外套","material":"羊毛","color":"蓝色","details":["连帽","拉链"],"tags":["外套"]},
            {"id":"jacket","name":"棕色皮革短款夹克","status":"已验证成功","category":"上装","subcategory":"夹克","material":"皮革","color":"棕色","details":["短款","立领"],"tags":["夹克"]},
            {"id":"sneakers","name":"白色尼龙运动鞋","status":"已验证成功","category":"鞋履","subcategory":"运动鞋","material":"尼龙","color":"白色","details":["网面","系带"],"tags":["鞋履"]},
            {"id":"backpack","name":"黑色尼龙双肩包","status":"已验证成功","category":"配饰","subcategory":"双肩包","material":"尼龙","color":"黑色","details":["双肩","拉链"],"tags":["背包"]},
            {"id":"hoodie","name":"灰色棉质连帽卫衣","status":"已验证成功","category":"上装","subcategory":"卫衣","material":"棉","color":"灰色","details":["连帽","抽绳"],"tags":["卫衣"]},
            {"id":"shirt","name":"白色棉质衬衫","status":"已验证成功","category":"上装","subcategory":"衬衫","material":"棉","color":"白色","details":["纽扣","牛津纺"],"tags":["衬衫"]},
            {"id":"shorts","name":"卡其尼龙运动短裤","status":"已验证成功","category":"下装","subcategory":"运动短裤","material":"尼龙","color":"卡其","details":["抽绳","宽松"],"tags":["短裤"]},
            {"id":"scarf","name":"红色羊毛围巾","status":"已验证成功","category":"配饰","subcategory":"围巾","material":"羊毛","color":"红色","details":["针织"],"tags":["围巾"]},
            {"id":"puffer","name":"黑色聚酯连帽羽绒服","status":"已验证成功","category":"上装","subcategory":"羽绒服","material":"聚酯","color":"黑色","details":["连帽","横向绗缝"],"tags":["羽绒服"]},
            {"id":"jeans","name":"蓝色牛仔直筒裤","status":"已验证成功","category":"下装","subcategory":"直筒裤","material":"牛仔","color":"蓝色","details":["直筒"],"tags":["牛仔裤"]},
            {"id":"boots","name":"棕色皮革短靴","status":"已验证成功","category":"鞋履","subcategory":"短靴","material":"皮革","color":"棕色","details":["系带"],"tags":["短靴"]},
            {"id":"skirt","name":"米色亚麻半身裙","status":"已验证成功","category":"下装","subcategory":"半身裙","material":"亚麻","color":"米色","details":["A字"],"tags":["半身裙"]},
            {"id":"failed","name":"黑色尼龙马甲失败样本","status":"失败案例","category":"上装","subcategory":"无袖马甲","material":"尼龙","color":"黑色","details":["立领"],"tags":["马甲"]},
            {"id":"untested","name":"黑色尼龙马甲待验证样本","status":"待验证","category":"上装","subcategory":"无袖马甲","material":"尼龙","color":"黑色","details":["立领"],"tags":["马甲"]},
        ]
        labelled_queries = [
            ("黑色尼龙无袖立领马甲 双拉头拉链","vest"),
            ("深灰灯芯绒微喇裤 宽松版型","trousers"),
            ("藏青羊毛连帽外套","coat"),
            ("棕色皮革短款夹克","jacket"),
            ("白色尼龙运动鞋 网面","sneakers"),
            ("黑色尼龙双肩包","backpack"),
            ("灰色棉质连帽卫衣 抽绳","hoodie"),
            ("白色棉质衬衫 纽扣","shirt"),
            ("卡其尼龙运动短裤 抽绳","shorts"),
            ("红色羊毛围巾","scarf"),
            ("黑色聚酯连帽羽绒服 横向绗缝","puffer"),
            ("蓝色牛仔直筒裤","jeans"),
            ("棕色皮革短靴","boots"),
            ("米色亚麻半身裙","skirt"),
        ]
        hits_at_1 = 0
        hits_at_5 = 0
        returned_at_5 = 0
        expected_ids = {expected for _, expected in labelled_queries}
        for query, expected_id in labelled_queries:
            with self.subTest(query=query):
                results = rank_case_matches(cases, query, threshold=0.8, limit=5)
                ranked_ids = [item["case"]["id"] for item in results]
                if ranked_ids and ranked_ids[0] == expected_id:
                    hits_at_1 += 1
                if expected_id in ranked_ids:
                    hits_at_5 += 1
                returned_at_5 += len(ranked_ids)
        precision_at_1 = hits_at_1 / len(labelled_queries)
        recall_at_5 = hits_at_5 / len(expected_ids)
        precision_at_5 = hits_at_5 / max(1, returned_at_5)
        self.assertGreaterEqual(precision_at_1, 0.90)
        self.assertGreaterEqual(recall_at_5, 0.90)
        self.assertGreaterEqual(precision_at_5, 0.50)
        result_ids = {
            item["case"]["id"]
            for item in rank_case_matches(cases, "黑色尼龙无袖立领马甲", threshold=0.8, limit=20)
        }
        self.assertNotIn("failed", result_ids)
        self.assertNotIn("untested", result_ids)


    def test_full_outfit_does_not_mix_attributes_from_different_items(self):
        case = dict(
            self.case,
            name="黑色尼龙马甲与红色皮革裤整套穿搭",
            category="整套搭配",
            subcategory="冬季穿搭",
            material="尼龙",
            color="黑色",
            outfit_items=[
                {
                    "category": "上装",
                    "subcategory": "无袖马甲",
                    "name": "黑色尼龙马甲",
                    "material": "尼龙",
                    "color": "黑色",
                },
                {
                    "category": "下装",
                    "subcategory": "微喇裤",
                    "name": "红色皮革裤",
                    "material": "皮革",
                    "color": "红色",
                },
            ],
        )
        results = rank_case_matches([case], "黑色皮革马甲", threshold=0.8)
        self.assertEqual(results, [])

if __name__=="__main__": unittest.main(verbosity=2)
