import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import probe_kirby_album_availability_v02 as probe


def receipt(title='星のカービィ スターアライズ オリジナル・サウンドトラック'):
    return {
        'method': 'POST',
        'endpoint_path_without_query': '/youtubei/v1/search',
        'safe_category': 'search_page',
        'status_code': 200,
        'response_content_type': 'application/json; charset=UTF-8',
        'response_body_bytes': 123,
        'sanitized_response_sha256': 'a' * 64,
        'sanitized_search_json': {
            'album_candidates': [{'browseId': 'MPREfixture_123', 'title': title}],
        },
    }


class FakeSession:
    def __init__(self, callback):
        self.count = 0
        self.request_categories = {}
        self.request_events = []
        self.callback = callback

    def close(self):
        pass


class FakeClient:
    def __init__(self, session, fail_after_receipt=False):
        self.session = session
        self.fail_after_receipt = fail_after_receipt

    def get_account_info(self):
        self.session.count = 2
        self.session.request_categories.update({
            'visitor_id_bootstrap': 1, 'account_preflight': 1})
        self.session.request_events.extend([
            {'method': 'GET', 'path': '/', 'category': 'visitor_id_bootstrap',
             'status': 'completed', 'error_type': None, 'request_number': 1},
            {'method': 'POST', 'path': '/youtubei/v1/account/account_menu',
             'category': 'account_preflight', 'status': 'completed',
             'error_type': None, 'request_number': 2},
        ])
        return {'accountName': 'sensitive test account'}

    def search(self, **kwargs):
        self.session.count = 3
        self.session.request_categories['search_page'] = 1
        self.session.request_events.append({
            'method': 'POST', 'path': '/youtubei/v1/search',
            'category': 'search_page', 'status': 'completed',
            'error_type': None, 'request_number': 3,
        })
        self.session.callback(receipt())
        if self.fail_after_receipt:
            raise KeyError('renderer parse failure')
        return [{'title': 'parsed client result'}]


class ProbeRunnerTests(unittest.TestCase):
    def _test_run(self, fail_after_receipt=False):
        original_bytes = probe.PLAN_PATH.read_bytes()
        intent = json.loads(original_bytes)
        with tempfile.TemporaryDirectory(prefix='kirby-probe-v02-test-') as temp:
            output = Path(temp) / 'private'
            intent = copy.deepcopy(intent)
            intent['output']['directory'] = str(output)
            test_bytes = json.dumps(intent, ensure_ascii=False, indent=2).encode() + b'\n'
            test_sha = hashlib.sha256(test_bytes).hexdigest()
            holder = {}

            def session_factory(interval, budget, *, search_response_callback):
                self.assertEqual((interval, budget), (3, 3))
                session = FakeSession(search_response_callback)
                holder['session'] = session
                return session

            def client_factory(auth_path, session):
                self.assertEqual(auth_path, 'fake-auth-path')
                return FakeClient(session, fail_after_receipt)

            with patch.object(probe, 'OUTPUT', output), \
                    patch.object(probe, 'PINNED_INTENT_SHA256', test_sha):
                report = probe.run(
                    intent, test_bytes, test_sha, 'fake-auth-path',
                    client_factory=client_factory, session_factory=session_factory,
                    output=output)

            saved = json.loads((output / 'search-first-page.json').read_text())
            summary = json.loads((output / 'summary.json').read_text())
            self.assertEqual(saved['response']['sanitized_search_json']['album_candidates'],
                             receipt()['sanitized_search_json']['album_candidates'])
            self.assertEqual(summary, report)
            self.assertEqual(report['http_requests'], 3)
            self.assertEqual(report['account_authentication_verified'], True)
            self.assertEqual(report['attempted_pids'], 0)
            self.assertEqual(report['outgoing_roots_created'], 0)
            self.assertNotIn('sensitive test account', repr(report))
            return report

    def test_first_page_receipt_is_persisted_and_never_credits_roots(self):
        report = self._test_run()
        self.assertEqual(report['status'], 'first_page_search_completed')
        self.assertTrue(report['search_page_receipt_saved_before_parser'])
        self.assertEqual(report['exact_title_candidate_count'], 1)

    def test_parser_error_keeps_pre_parser_receipt_but_no_empty_result_claim(self):
        report = self._test_run(fail_after_receipt=True)
        self.assertEqual(report['status'], 'stopped_incomplete')
        self.assertTrue(report['search_page_receipt_saved_before_parser'])
        self.assertEqual(report['exact_title_candidate_count'], 1)
        self.assertIsNone(report['search_result_count'])

    def test_receipt_allowlist_rejects_raw_query_or_headers(self):
        bad = receipt()
        bad['request_url'] = 'https://music.youtube.com/?private-query'
        with self.assertRaisesRegex(ValueError, 'fields differ'):
            probe._validate_safe_search_receipt(bad)


if __name__ == '__main__':
    unittest.main()
