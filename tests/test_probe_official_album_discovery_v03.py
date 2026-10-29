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

import probe_official_album_discovery_v03 as probe
from probe_ytmusic_session_observer_v02 import create_observed_paced_session
from ytmusicapi import YTMusic
from ytmusicapi.auth.types import AuthType


class FakeResponse:
    status_code = 200
    headers = {}
    url = ''
    reason = 'OK'

    def __init__(self, request, body, content_type='application/json; charset=UTF-8'):
        self.request = request
        self._content = body
        self.headers = {'Content-Type': content_type}
        self._content_consumed = True

    @property
    def content(self):
        return self._content

    @property
    def text(self):
        return self._content.decode('utf-8')

    def raise_for_status(self):
        return None


class OfficialAlbumDiscoveryV03Tests(unittest.TestCase):
    def _run_real_client(self, with_continuations):
        original = probe.PLAN_PATH.read_bytes()
        intent = json.loads(original)
        with tempfile.TemporaryDirectory(prefix='official-album-v03-') as temp:
            output = Path(temp) / 'private-output'
            intent = copy.deepcopy(intent)
            intent['output']['directory'] = str(output)
            intent_bytes = json.dumps(intent, ensure_ascii=False, sort_keys=True,
                                      indent=2).encode() + b'\n'
            intent_sha = hashlib.sha256(intent_bytes).hexdigest()
            sent = []
            held = {}

            def search_payload():
                shelf = {'musicShelfRenderer': {
                    'title': {'runs': [{'text': 'Albums'}]}, 'contents': [{
                        'musicResponsiveListItemRenderer': {
                            'flexColumns': [
                                {'musicResponsiveListItemFlexColumnRenderer': {
                                    'text': {'runs': [{'text': 'Fixture Album'}]}}},
                                {'musicResponsiveListItemFlexColumnRenderer': {
                                    'text': {'runs': [{'text': 'Album'}, {'text': ' • '},
                                                       {'text': 'Fixture Artist'}]}}},
                            ],
                            'navigationEndpoint': {
                                'browseEndpoint': {'browseId': 'MPREfixture_124'}},
                        }
                    }]}}
                if with_continuations:
                    shelf['musicShelfRenderer']['continuations'] = [{
                        'nextContinuationData': {'continuation': 'must-not-be-sent'}}]
                return {'contents': {'sectionListRenderer': {'contents': [shelf]}}}

            account = {'actions': [{'openPopupAction': {'popup': {
                'multiPageMenuRenderer': {'header': {'activeAccountHeaderRenderer': {
                    'accountName': {'runs': [{'text': 'private fake account'}]},
                    'accountPhoto': {'thumbnails': [{'url': 'https://example.invalid/p'}]},
                }}}}}}]}

            def session_factory(interval, budget, *, search_response_callback):
                self.assertEqual((interval, budget), (3, 8))
                session = create_observed_paced_session(
                    0, budget, search_response_callback=search_response_callback)
                held['session'] = session
                return session

            def client_factory(_auth_path, session):
                client = YTMusic(requests_session=session, language='en', location='JP')
                client.auth_type = AuthType.BROWSER
                client.sapisid = 'private fake auth token'
                client.origin = 'https://music.youtube.com'
                if with_continuations:
                    original_search = client.search

                    def request_one_extra_page_to_exercise_guard(**kwargs):
                        # A second desired row makes the fake real-client response
                        # request a continuation; the per-query transport guard must
                        # stop that before it reaches send().
                        kwargs['limit'] = 2
                        return original_search(**kwargs)

                    client.search = request_one_extra_page_to_exercise_guard

                def send(request, **_kwargs):
                    sent.append((request.method, request.url, request.body))
                    if request.method == 'GET':
                        body = b'ytcfg.set({"VISITOR_DATA":"fake-visitor"});'
                        return FakeResponse(request, body, 'text/html; charset=utf-8')
                    if '/account/account_menu' in request.url:
                        return FakeResponse(request, json.dumps(account).encode())
                    if '/youtubei/v1/search' in request.url:
                        return FakeResponse(request, json.dumps(search_payload()).encode())
                    raise AssertionError('unexpected fake transport endpoint')

                session.send = send
                return client

            with patch.object(probe, 'OUTPUT', output), \
                    patch.object(probe, 'PINNED_INTENT_SHA256', intent_sha):
                result = probe.run(intent, intent_bytes, intent_sha, 'fake-auth',
                                   client_factory=client_factory,
                                   session_factory=session_factory, output=output)

            saved = json.loads((output / 'summary.json').read_text())
            self.assertEqual(saved, result)
            self.assertTrue(result['account_authentication_verified'])
            self.assertEqual(result['http_requests'], 8)
            self.assertEqual(dict(result['request_categories']), {
                'visitor_id_bootstrap': 1, 'account_preflight': 1, 'search_page': 6})
            self.assertEqual(len(sent), 8)
            search_bodies = [json.loads(body) for method, url, body in sent
                             if method == 'POST' and '/youtubei/v1/search' in url]
            self.assertEqual([body['query'] for body in search_bodies], probe.TITLE_ORDER)
            self.assertNotIn('must-not-be-sent', repr(result))
            self.assertNotIn('private fake account', repr(result))
            self.assertNotIn('private fake auth token', repr(result))
            self.assertEqual(result['attempted_pids'], 0)
            self.assertEqual(result['outgoing_roots_created'], 0)
            self.assertEqual(result['recommendation_edges_created'], 0)
            self.assertEqual(len(list((output / 'first-pages').glob('*-first-page.json'))), 6)
            return result, sent

    def test_real_ytmusic_client_searches_all_exact_titles_with_one_page_each(self):
        result, _sent = self._run_real_client(with_continuations=False)
        self.assertEqual(result['status'], 'bounded_diagnostic_completed')
        self.assertEqual(len(result['titles']), 6)
        self.assertTrue(all(row['status'] == 'first_page_search_completed'
                            for row in result['titles']))

    def test_real_ytmusic_continuations_are_blocked_before_transport_per_title(self):
        result, sent = self._run_real_client(with_continuations=True)
        self.assertEqual(result['status'], 'bounded_diagnostic_partial')
        self.assertEqual(len(result['blocked_continuations']), 6)
        self.assertTrue(all(row['status'] == 'incomplete_continuation_blocked'
                            for row in result['titles']))
        self.assertEqual(sum('/youtubei/v1/search' in url for _method, url, _body in sent), 6)

    def test_saved_catalog_receipt_tampering_fails_before_client_creation(self):
        intent_bytes = probe.PLAN_PATH.read_bytes()
        intent = json.loads(intent_bytes)
        cached = intent['source']['saved_song_for_prism_catalog']
        cached['strict_unattempted_nonroot_candidates'] = 15
        tampered = json.dumps(intent, ensure_ascii=False, sort_keys=True,
                              indent=2).encode() + b'\n'
        tampered_sha = hashlib.sha256(tampered).hexdigest()
        with patch.object(probe, 'PINNED_INTENT_SHA256', tampered_sha):
            with self.assertRaisesRegex(ValueError, 'saved catalog candidate count differs'):
                probe.validate_intent(intent, tampered, tampered_sha)

    def test_wrong_host_and_wrong_query_are_rejected_before_transport(self):
        calls = []

        def send():
            calls.append('sent')
            return object()

        title = probe.TITLE_ORDER[0]
        body = {'context': {}, 'params': probe.ALBUM_SEARCH_PARAMS, 'query': title}
        with self.assertRaisesRegex(RuntimeError, 'host is outside'):
            probe._validated_transport('POST', '/youtubei/v1/search',
                'https://attacker.invalid/youtubei/v1/search', body, title, send)
        self.assertEqual(calls, [])
        wrong_body = dict(body, query=probe.TITLE_ORDER[1])
        with self.assertRaisesRegex(RuntimeError, 'exact active album query'):
            probe._validated_transport('POST', '/youtubei/v1/search',
                'https://music.youtube.com/youtubei/v1/search', wrong_body, title, send)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
