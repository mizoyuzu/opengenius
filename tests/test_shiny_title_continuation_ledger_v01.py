import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v10-title-origin-v01')
sys.path.insert(0, str(RUNTIME / 'scripts'))
os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = json.loads((RUNTIME / 'runtime-manifest.json').read_text())['trusted_data_root']
spec = importlib.util.spec_from_file_location('titleledger', ROOT / 'scripts/shiny_title_continuation_ledger_v01.py')
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


class ContinuationAccountingTests(unittest.TestCase):
    def test_real_parent_and_five_metadata_calls_are_accounted_without_new_attempts(self):
        attempted, same_map = ledger.accepted_parent()
        self.assertEqual(len(attempted), 1837)
        self.assertEqual(len(same_map), 110)
        self.assertEqual(ledger.metadata_calls(), 5)

    def test_resealed_parent_counter_is_rejected(self):
        original = ledger.pinned

        def forged(path, expected):
            value = original(path, expected)
            if Path(path).name == 'progress.json':
                value['http_requests'] = 0
            return value

        with patch.object(ledger, 'pinned', forged), self.assertRaisesRegex(ValueError, 'accounting changed'):
            ledger.accepted_parent()

    def test_metadata_cannot_create_a_pid_attempt(self):
        original = ledger.pinned

        def forged(path, expected):
            value = original(path, expected)
            if Path(path).name == 'summary.json':
                value['attempted_pids'] = 1
            return value

        with patch.object(ledger, 'pinned', forged), self.assertRaisesRegex(ValueError, 'accounting changed'):
            ledger.metadata_calls()


if __name__ == '__main__':
    unittest.main()
