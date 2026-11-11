import hashlib
import unittest
from unittest.mock import patch

import ai_prompt_workbench as app


class VerifiedDownloadTests(unittest.TestCase):
    def test_falls_back_to_contents_api_when_raw_bytes_are_wrong(self):
        payload = b"verified exact bytes"
        expected = hashlib.sha256(payload).hexdigest()
        with patch.object(app, "fetch_bytes", return_value=b"proxy-modified bytes"), patch.object(
            app, "fetch_github_api_bytes", return_value=payload
        ):
            self.assertEqual(app.fetch_verified_bytes("sample.py", expected), payload)

    def test_rejects_both_sources_when_hashes_do_not_match(self):
        payload = b"expected bytes"
        expected = hashlib.sha256(payload).hexdigest()
        with patch.object(app, "fetch_bytes", return_value=b"bad raw"), patch.object(
            app, "fetch_github_api_bytes", return_value=b"bad api"
        ):
            with self.assertRaisesRegex(ValueError, "两种下载渠道均未通过校验"):
                app.fetch_verified_bytes("sample.py", expected)


if __name__ == "__main__":
    unittest.main()
