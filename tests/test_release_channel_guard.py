import os
import subprocess
import sys
import unittest
from pathlib import Path


class ReleaseChannelGuardTests(unittest.TestCase):
    def test_correct_channel_passes_and_incorrect_channel_fails(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / '.github/workflows/release-validation.yml').read_text(encoding='utf-8')
        section = workflow.split('- name: Validate release source version and update channel', 1)[1]
        script = section.split("python - <<'PY'", 1)[1].split('\n          PY', 1)[0]
        script = '\n'.join(line[10:] if line.startswith('          ') else line for line in script.splitlines())
        for ref, expected in [('release/v0.6.5-stabilization', 0), ('release/v0.6.5-wrong', 1)]:
            with self.subTest(ref=ref):
                result = subprocess.run([sys.executable, '-c', script], cwd=root,
                                        env={**os.environ, 'REF_NAME': ref}, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, expected == 0, result.stderr)


if __name__ == '__main__': unittest.main()
