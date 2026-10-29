import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import audit_reviewed_work_video_stage as subject


class ProfileTests(unittest.TestCase):
    def check(self, *, budget=5130, mismatched_pins=False, wrong_hash=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / 'wrapper.py'
            pins = {'/example/source.json': 'a' * 64}
            wrapper.write_text('import json\nCONFIG = json.loads(' +
                               repr(json.dumps({'input_pins': pins})) + ')\n')
            profile = {'kind': 'root-reviewed-work-radio-capture-v1',
                       'root_credit': 0, 'database_written': False,
                       'http_before': budget, 'http_cap': 6500,
                       'runtime_manifest_sha256': 'b' * 64,
                       'wrapper_sha256': 'c' * 64,
                       'capture_input_pins': {} if mismatched_pins else pins,
                       **{key: str(root / key) for key in
                          ('root', 'runtime', 'stage', 'supervisor')},
                       'wrapper': str(wrapper)}
            path = root / 'review.json'
            path.write_text(json.dumps(profile))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            return subject.checked_profile(path, '0' * 64 if wrong_hash else digest)

    def test_reviewed_profile(self):
        self.assertEqual(self.check()['http_before'], 5130)

    def test_wrong_profile_hash(self):
        with self.assertRaisesRegex(subject.audit.AuditError, 'hash differs'):
            self.check(wrong_hash=True)

    def test_cumulative_budget_rejected(self):
        with self.assertRaisesRegex(subject.audit.AuditError, 'budget'):
            self.check(budget=6400)

    def test_capture_pin_mismatch_rejected(self):
        with self.assertRaisesRegex(subject.audit.AuditError, 'input pins differ'):
            self.check(mismatched_pins=True)
