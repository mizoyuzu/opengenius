import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_composed_genius_source import validate_partition, verify_closure, sha


class SourcePartitionTests(unittest.TestCase):
    def setUp(self):
        self.old = [{'root_pid': 'native', 'ordered_target_pids': ['yt']},
                    {'root_pid': 'yt', 'ordered_target_pids': ['native']},
                    {'root_pid': 'incoming', 'ordered_target_pids': []}]
        self.final = copy.deepcopy(self.old) + [
            {'root_pid': 'fresh', 'ordered_target_pids': ['yt', 'native']}]
        self.own = [{'root_pid': 'fresh', 'ordered_target_pids': ['yt', 'native']}]

    def check(self, final=None, own=None, minimum=3):
        return validate_partition(self.old, self.final if final is None else final,
            self.own if own is None else own, native_pids={'native'},
            historical_yt_pids={'yt'}, known_pids={'native', 'yt', 'incoming', 'fresh'},
            minimum_roots=minimum)

    def test_incoming_only_rows_are_not_counted(self):
        self.assertEqual(self.check()['counts']['total_active'], 3)

    def test_unsupported_incoming_root_is_rejected(self):
        final = copy.deepcopy(self.final)
        final[2]['ordered_target_pids'] = ['native']
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            self.check(final)

    def test_changed_historical_order_is_rejected(self):
        final = copy.deepcopy(self.final)
        final[0]['ordered_target_pids'] = ['fresh']
        with self.assertRaisesRegex(ValueError, 'established outgoing'):
            self.check(final)

    def test_changed_fresh_radio_order_is_rejected(self):
        final = copy.deepcopy(self.final)
        final[-1]['ordered_target_pids'].reverse()
        with self.assertRaisesRegex(ValueError, 'own-radio evidence'):
            self.check(final)

    def test_catalog_only_fresh_root_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            self.check(own=[])

    def test_real_target_is_enforced(self):
        with self.assertRaisesRegex(ValueError, 'required total'):
            self.check(minimum=1000)

    def test_archived_code_requires_exact_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            archived = Path(directory) / 'old.py'
            archived.write_text('original code\n')
            digest = sha(archived)
            closure = {str(archived): digest,
                       'receipt-original:/old/worktree/old.py': digest}
            verify_closure(closure)
            archived.write_text('modified code\n')
            with self.assertRaisesRegex(ValueError, 'source file changed'):
                verify_closure(closure)

    def test_missing_historical_code_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory) / 'different.py'
            other.write_text('unrelated\n')
            with self.assertRaisesRegex(ValueError, 'no matching archived'):
                verify_closure({str(other): sha(other),
                                'receipt-original:/old/worktree/old.py': '0' * 64})
