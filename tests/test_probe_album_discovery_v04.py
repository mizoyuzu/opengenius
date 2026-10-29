import hashlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import probe_album_discovery_v04 as probe  # noqa: E402


SEARCH_URL = 'https://music.youtube.com/youtubei/v1/search'
SEARCH_PARAMS = 'EgWKAQIYAWoMEA4QChADEAQQCRAF'


class FakeSession:
    def __init__(self, interval, budget, *, search_response_callback):
        self.interval = interval
        self.budget = budget
        self.callback = search_response_callback
        self.count = 0
        self.request_categories = {}
        self.request_events = []

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        if path == '/':
            category = 'visitor_id_bootstrap'
        elif path == '/youtubei/v1/account/account_menu':
            category = 'account_preflight'
        elif path == '/youtubei/v1/search':
            category = 'search_page'
        else:
            raise AssertionError(f'unexpected fake endpoint: {path}')
        self.count += 1
        self.request_categories[category] = self.request_categories.get(category, 0) + 1
        self.request_events.append({'method': method, 'path': path, 'category': category})
        if category == 'search_page' and self.callback is not None:
            self.callback({
                'method': 'POST',
                'endpoint_path_without_query': path,
                'safe_category': category,
                'status_code': 200,
                'response_content_type': 'application/json',
                'response_body_bytes': 12,
                'sanitized_response_sha256': '0' * 64,
                'sanitized_search_json': {'album_candidates': []},
            })
        return object()

    def close(self):
        pass


class FakeClient:
    def __init__(self, session, *, send_search=True, send_receipt=True,
                 continuation=False):
        self.session = session
        self.send_search = send_search
        self.send_receipt = send_receipt
        self.continuation = continuation

    def search(self, *, query, filter, limit):
        assert filter == 'albums' and limit == 1
        if not self.send_search:
            return []
        callback = self.session.callback
        if not self.send_receipt:
            self.session.callback = None
        body = {'context': {}, 'params': SEARCH_PARAMS, 'query': query}
        self.session.request('POST', SEARCH_URL, json=body)
        self.session.callback = callback
        if self.continuation:
            self.session.request('POST', SEARCH_URL, json=body)
        return []


def make_intent():
    return {
        'titles': [{
            'album': 'Exact test album',
            'search': {'arguments': {'query': 'Exact test album',
                                     'filter': 'albums', 'limit': 1}},
        }],
        'limits': {'minimum_interval_seconds': 3,
                   'total_http_requests_max': 3},
    }


class AlbumDiscoveryV04Tests(unittest.TestCase):
    def _run_fake(self, tmp, *, send_search=True, send_receipt=True,
                  continuation=False):
        output = Path(tmp) / 'private-evidence'
        probe.OUTPUT = output
        intent = make_intent()
        session_holder = {}

        def session_factory(interval, budget, *, search_response_callback):
            session = FakeSession(interval, budget,
                                  search_response_callback=search_response_callback)
            session_holder['session'] = session
            return session

        def client_factory(auth_path, session):
            session.request('GET', 'https://music.youtube.com/')
            session.request('POST',
                            'https://music.youtube.com/youtubei/v1/account/account_menu',
                            json={})
            return FakeClient(session, send_search=send_search,
                              send_receipt=send_receipt,
                              continuation=continuation)

        with patch.object(probe, 'validate_intent'), \
                patch.object(probe, 'verify_account', return_value=True):
            result = probe.run(
                intent, b'{}', '0' * 64, 'auth-path-not-opened',
                client_factory=client_factory, session_factory=session_factory,
                output=output,
            )
        return result, output, session_holder['session']

    def test_completed_title_requires_transport_and_durable_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, output, session = self._run_fake(tmp)
            title = report['titles'][0]
            self.assertEqual(report['status'], 'bounded_diagnostic_completed')
            self.assertEqual(title['status'], 'first_page_search_completed')
            self.assertEqual(session.count, 3)
            self.assertTrue(title['first_page_receipt_sha256'])
            self.assertTrue((output / 'first-pages/00-first-page.json').is_file())

    def test_search_return_without_transport_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, output, session = self._run_fake(tmp, send_search=False)
            self.assertEqual(report['status'], 'bounded_diagnostic_partial')
            self.assertEqual(report['titles'][0]['status'], 'stopped_incomplete')
            self.assertEqual(session.count, 2)
            self.assertFalse((output / 'first-pages/00-first-page.json').exists())

    def test_search_transport_without_receipt_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, output, session = self._run_fake(tmp, send_receipt=False)
            self.assertEqual(report['status'], 'bounded_diagnostic_partial')
            self.assertEqual(report['titles'][0]['status'], 'stopped_incomplete')
            self.assertEqual(session.request_categories.get('search_page'), 1)
            self.assertFalse((output / 'first-pages/00-first-page.json').exists())

    def test_continuation_is_blocked_and_title_remains_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, output, session = self._run_fake(tmp, continuation=True)
            self.assertEqual(report['status'], 'bounded_diagnostic_partial')
            self.assertEqual(report['titles'][0]['status'], 'incomplete_continuation_blocked')
            self.assertEqual(session.request_categories.get('search_page'), 1)
            self.assertEqual(len(report['blocked_continuations']), 1)

    def test_code_receipts_must_match_loaded_module_origins(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            scripts = repo / 'scripts'
            scripts.mkdir()
            inputs = []
            loaded = {}
            runner = None
            for role, module_name in probe.CODE_INPUT_MODULES.items():
                path = scripts / probe.CODE_INPUT_PATHS[role]
                path.write_text(f'# {role}\n')
                inputs.append({'role': role, 'path': str(path),
                               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
                if module_name is None:
                    runner = path
                else:
                    loaded[module_name] = types.SimpleNamespace(__file__=str(path))
            probe._verify_loaded_code_input_origins(
                inputs, repo=repo, loaded_modules=loaded, runner_source=runner)

            loaded['music_identity_map'] = types.SimpleNamespace(
                __file__=str(repo / 'wrong' / 'music_identity_map.py'))
            with self.assertRaises(ValueError):
                probe._verify_loaded_code_input_origins(
                    inputs, repo=repo, loaded_modules=loaded, runner_source=runner)

    def test_code_receipts_reject_changed_module_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            scripts = repo / 'scripts'
            scripts.mkdir()
            inputs = []
            loaded = {}
            runner = None
            for role, module_name in probe.CODE_INPUT_MODULES.items():
                path = scripts / probe.CODE_INPUT_PATHS[role]
                path.write_text(f'# {role}\n')
                inputs.append({'role': role, 'path': str(path),
                               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
                if module_name is None:
                    runner = path
                else:
                    loaded[module_name] = types.SimpleNamespace(__file__=str(path))
            (scripts / 'music_identity_map.py').write_text('# changed\n')
            with self.assertRaises(ValueError):
                probe._verify_loaded_code_input_origins(
                    inputs, repo=repo, loaded_modules=loaded, runner_source=runner)


if __name__ == '__main__':
    unittest.main()
