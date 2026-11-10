import unittest

from case_matcher import rank_matches, similarity


class CaseMatcherTests(unittest.TestCase):
    def test_same_stem_prompt_and_image_rank_high(self):
        items = [
            {"path": "set01/jacket_front.png", "name": "jacket_front.png", "media_type": "image"},
            {"path": "set01/jacket_front.txt", "name": "jacket_front.txt", "media_type": "text"},
            {"path": "other/shoes.mp4", "name": "shoes.mp4", "media_type": "video"},
        ]
        results = rank_matches(items)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["asset_path"], "set01/jacket_front.png")
        self.assertGreaterEqual(results[0]["score"], 0.9)

    def test_directory_alone_does_not_create_match(self):
        items = [
            {"path": "folder/alpha.txt", "name": "alpha.txt", "media_type": "text"},
            {"path": "folder/zzzzzz.png", "name": "zzzzzz.png", "media_type": "image"},
        ]
        self.assertEqual(rank_matches(items), [])

    def test_matching_is_only_a_suggestion(self):
        items = [
            {"path": "x/look_01.txt", "name": "look_01.txt", "media_type": "text"},
            {"path": "x/look_01.mp4", "name": "look_01.mp4", "media_type": "video"},
        ]
        results = rank_matches(items)
        self.assertEqual(results[0]["status"], "suggested")
        self.assertIn("reason", results[0])

    def test_similarity_is_case_and_extension_insensitive(self):
        self.assertGreater(similarity("Jacket-Front.PNG", "jacket_front.txt"), 0.8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
