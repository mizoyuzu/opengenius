"""Diagnostic summaries never disclose raw Music log contents."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import summarize_macos_genius_log as logs


class GeniusLogTests(unittest.TestCase):
    def test_known_counts_and_unknown_account_values_are_redacted(self):
        secret = 'private-account-CUID-key-value'
        messages = ['genius corruption detected; deleting db',
                    'Assertion failure libraryPrefs->geniusCUID.NotEmpty()',
                    'GeniusCreateClusterContext err -50',
                    'genius produced 1 tracks < 25 mintracks',
                    'geniusAccountID: ' + secret,
                    'geniusKeyHeader contents: ' + secret]
        document = [{'eventMessage': message, 'processImagePath': '/System/Applications/Music.app/Contents/MacOS/Music', 'private': secret} for message in messages]
        document.append({'eventMessage': messages[0], 'processImagePath': '/bin/Other'})
        result = logs.summarize_log_json(json.dumps(document).encode())
        self.assertTrue(result['complete'])
        self.assertEqual(result['matched_events'], 6)
        self.assertEqual(result['unclassified_events'], 2)
        self.assertEqual(result['counts']['genius_database_deleted_as_corrupt'], 1)
        self.assertEqual(result['counts']['genius_cuid_assertion'], 1)
        self.assertEqual(result['counts']['genius_too_few_tracks'], 1)
        serialized = json.dumps(result)
        self.assertNotIn(secret, serialized)
        self.assertNotIn('eventMessage', serialized)
        self.assertNotIn('geniusAccountID', serialized)

    def test_invalid_or_partial_json_does_not_echo_input(self):
        for raw in (b'private secret', b'[{"eventMessage":"secret"}', b'{}', b'\xff'):
            result = logs.summarize_log_json(raw)
            self.assertEqual(result['status'], 'invalid_json')
            self.assertFalse(result['complete'])
            self.assertNotIn('secret', json.dumps(result))

    def test_capture_size_bound_discards_partial_document(self):
        result = logs._capture_summary([sys.executable, '-c', 'import sys; sys.stdout.write("x" * 4096)'], max_bytes=100, timeout=2)
        self.assertEqual(result['status'], 'truncated')
        self.assertFalse(result['complete'])
        self.assertNotIn('xxx', json.dumps(result))

    def test_timeout_discards_output(self):
        result = logs._capture_summary([sys.executable, '-c', 'import time; print("secret",flush=True); time.sleep(2)'], timeout=0.1)
        self.assertEqual(result['status'], 'timeout')
        self.assertNotIn('secret', json.dumps(result))

    def test_successful_capture_returns_only_safe_summary(self):
        result = logs._capture_summary([sys.executable, '-c', 'import json; print(json.dumps([{ "eventMessage": "genius corruption detected; deleting db SECRET-CUID" }]))'], timeout=2)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['counts']['genius_database_deleted_as_corrupt'], 1)
        self.assertNotIn('SECRET-CUID', json.dumps(result))

    def test_native_command_is_fixed_and_failed_capture_is_redacted(self):
        with patch.object(logs, '_capture_summary', return_value=logs._empty('nonzero_exit', returncode=1, truncated=False)) as capture:
            result = logs.summarize_macos_genius_log()
        command = capture.call_args.args[0]
        self.assertEqual(command[:7], ['/usr/bin/log', 'show', '--last', '10m', '--style', 'json', '--info'])
        self.assertIn(logs.PREDICATE, command)
        self.assertFalse(result['complete'])
        self.assertNotIn('secret', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
