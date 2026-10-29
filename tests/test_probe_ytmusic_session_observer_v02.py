import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from probe_ytmusic_session_observer_v02 import create_observed_paced_session
from probe_ytmusic_batch import create_paced_session
from ytmusicapi import YTMusic
from ytmusicapi.auth.types import AuthType
from probe_ytmusic_batch import verify_account


class FakeResponse:
    status_code = 200
    headers = {}
    url = ''

    def __init__(self, request):
        self.request = request
        self._content = b'{"ok":true}'
        self._content_consumed = True
        self.reason = 'OK'

    @property
    def content(self):
        return self._content

    @property
    def text(self):
        return self._content.decode('utf-8')

    def raise_for_status(self):
        return None


class SessionObserverTests(unittest.TestCase):
    def test_real_ytmusic_client_search_exposes_the_positional_category_bug(self):
        response_body = b'{"context":{}}'
        def send(request, **kwargs):
            response = FakeResponse(request)
            response.status_code = 200
            response.headers['Content-Type'] = 'application/json; charset=UTF-8'
            if request.method == 'GET':
                response._content = b'ytcfg.set({"VISITOR_DATA":"fake-visitor"});'
            else:
                response._content = response_body
            return response

        original = create_paced_session(0, 2)
        fixed = create_observed_paced_session(0, 2)
        try:
            with patch.object(original, 'send', side_effect=send):
                client = YTMusic(requests_session=original, language='en', location='JP')
                self.assertEqual(client.search(
                    query='exact album query', filter='albums', limit=5), [])
            self.assertEqual(original.count, 2)  # visitor-ID bootstrap + search POST
            self.assertEqual(dict(original.request_categories), {'other': 2})

            captured = []
            with patch.object(fixed, 'send', side_effect=send):
                client = YTMusic(requests_session=fixed, language='en', location='JP')
                self.assertEqual(client.search(
                    query='exact album query', filter='albums', limit=5), [])
            self.assertEqual(fixed.count, 2)
            self.assertEqual(dict(fixed.request_categories), {
                'visitor_id_bootstrap': 1, 'search_page': 1})
            search = fixed.search_response_receipts[0]
            self.assertEqual(search['method'], 'POST')
            self.assertEqual(search['endpoint_path_without_query'], '/youtubei/v1/search')
            self.assertEqual(search['status_code'], 200)
            self.assertEqual(search['sanitized_search_json'], {'album_candidates': []})
            self.assertNotIn('exact album query', repr(search))
        finally:
            original.close()
            fixed.close()

    def test_real_authenticated_preflight_is_bootstrap_account_then_search(self):
        account = {
            'actions': [{
                'openPopupAction': {'popup': {'multiPageMenuRenderer': {'header': {
                    'activeAccountHeaderRenderer': {
                        'accountName': {'runs': [{'text': 'Fake Test Account'}]},
                        'accountPhoto': {'thumbnails': [{'url': 'https://example.invalid/photo'}]},
                    }
                }}}}
            }]
        }

        def send(request, **kwargs):
            response = FakeResponse(request)
            response.status_code = 200
            response.headers['Content-Type'] = 'application/json; charset=UTF-8'
            if request.method == 'GET':
                response._content = b'ytcfg.set({"VISITOR_DATA":"fake-visitor"});'
            elif '/account/account_menu' in request.url:
                response._content = __import__('json').dumps(account).encode()
            else:
                response._content = b'{"context":{}}'
            return response

        captured = []
        session = create_observed_paced_session(0, 3, search_response_callback=captured.append)
        try:
            client = YTMusic(requests_session=session, language='en', location='JP')
            client.auth_type = AuthType.BROWSER
            client.sapisid = 'test-only-value'
            client.origin = 'https://music.youtube.com'
            with patch.object(session, 'send', side_effect=send):
                self.assertTrue(verify_account(client))
                self.assertEqual(session.count, 2)
                self.assertEqual(dict(session.request_categories), {
                    'visitor_id_bootstrap': 1, 'account_preflight': 1})
                self.assertEqual(client.search(
                    query='exact album query', filter='albums', limit=5), [])
            self.assertEqual(session.count, 3)
            self.assertEqual(dict(session.request_categories), {
                'visitor_id_bootstrap': 1, 'account_preflight': 1, 'search_page': 1})
            self.assertEqual([event['path'] for event in session.request_events], [
                '/', '/youtubei/v1/account/account_menu', '/youtubei/v1/search'])
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0]['sanitized_search_json'], {'album_candidates': []})
            serialized = repr(session.request_events) + repr(captured)
            self.assertNotIn('Fake Test Account', serialized)
            self.assertNotIn('test-only-value', serialized)
            self.assertNotIn('exact album query', serialized)
        finally:
            session.close()

    def test_search_payload_is_sanitized_before_ytmusic_parser_failure(self):
        raw_response = {
            'contents': {
                'tabbedSearchResultsRenderer': {'tabs': []},
                'safeFixture': {
                    'musicResponsiveListItemRenderer': {
                        'flexColumns': [
                            {'musicResponsiveListItemFlexColumnRenderer': {
                                'text': {'runs': [{'text': 'Exact Test Album'}]}}},
                            {'musicResponsiveListItemFlexColumnRenderer': {
                                'text': {'runs': [
                                    {'text': 'Album'}, {'text': ' • '}, {'text': 'Fixture Artist'}]}}},
                        ],
                        'navigationEndpoint': {
                            'browseEndpoint': {'browseId': 'MPREfixture_123'}
                        },
                    }
                },
            }
        }
        captured = []
        session = create_observed_paced_session(
            0, 2, search_response_callback=captured.append)
        try:
            client = YTMusic(requests_session=session, language='en', location='JP')
            with patch.object(session, 'send', side_effect=lambda request, **kwargs: (
                    self._response(request, raw_response, status=200))):
                with self.assertRaises((IndexError, KeyError)):
                    client.search(query='never persist this query', filter='albums', limit=5)
            self.assertEqual(len(captured), 1)
            receipt = captured[0]
            self.assertEqual(receipt['endpoint_path_without_query'], '/youtubei/v1/search')
            self.assertEqual(receipt['status_code'], 200)
            self.assertEqual(receipt['sanitized_search_json']['album_candidates'], [{
                'browseId': 'MPREfixture_123', 'title': 'Exact Test Album'}])
            self.assertNotIn('never persist this query', repr(receipt))
            self.assertEqual(session.request_events[0]['status'], 'completed')
        finally:
            session.close()

    def test_youtube_search_continuation_stops_at_three_http_requests(self):
        account = {
            'actions': [{'openPopupAction': {'popup': {'multiPageMenuRenderer': {'header': {
                'activeAccountHeaderRenderer': {
                    'accountName': {'runs': [{'text': 'Fake Test Account'}]},
                    'accountPhoto': {'thumbnails': [{'url': 'https://example.invalid/photo'}]},
                }
            }}}}}]
        }
        search_response = {
            'contents': {'sectionListRenderer': {'contents': [{
                'musicShelfRenderer': {
                    'title': {'runs': [{'text': 'Albums'}]},
                    'contents': [{
                        'musicResponsiveListItemRenderer': {
                            'flexColumns': [
                                {'musicResponsiveListItemFlexColumnRenderer': {
                                    'text': {'runs': [{'text': 'Fixture Album'}]}}},
                                {'musicResponsiveListItemFlexColumnRenderer': {
                                    'text': {'runs': [
                                        {'text': 'Album'}, {'text': ' • '}, {'text': 'Fixture Artist'}]}}},
                            ],
                            'navigationEndpoint': {
                                'browseEndpoint': {'browseId': 'MPREfixture_124'}},
                        }
                    }],
                    'continuations': [{
                        'nextContinuationData': {'continuation': 'never-log-this-token'}}],
                }
            }]}}
        }

        def send(request, **kwargs):
            response = FakeResponse(request)
            response.status_code = 200
            response.headers['Content-Type'] = 'application/json; charset=UTF-8'
            if request.method == 'GET':
                response._content = b'ytcfg.set({"VISITOR_DATA":"fake-visitor"});'
            elif '/account/account_menu' in request.url:
                import json
                response._content = json.dumps(account).encode()
            else:
                import json
                response._content = json.dumps(search_response).encode()
            return response

        captured = []
        session = create_observed_paced_session(0, 3, search_response_callback=captured.append)
        try:
            client = YTMusic(requests_session=session, language='en', location='JP')
            client.auth_type = AuthType.BROWSER
            client.sapisid = 'test-only-value'
            client.origin = 'https://music.youtube.com'
            with patch.object(session, 'send', side_effect=send):
                self.assertTrue(verify_account(client))
                with self.assertRaisesRegex(RuntimeError, 'Request budget exhausted'):
                    client.search(query='test query', filter='albums', limit=5)
            self.assertEqual(session.count, 3)
            self.assertEqual(dict(session.request_categories), {
                'visitor_id_bootstrap': 1, 'account_preflight': 1, 'search_page': 1})
            self.assertEqual([event['category'] for event in session.request_events], [
                'visitor_id_bootstrap', 'account_preflight', 'search_page', 'search_continuation'])
            self.assertEqual(session.request_events[-1]['request_number'], None)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0]['sanitized_search_json']['album_candidates'], [{
                'browseId': 'MPREfixture_124', 'title': 'Fixture Album'}])
            self.assertNotIn('never-log-this-token', repr(session.request_events))
            self.assertNotIn('never-log-this-token', repr(captured))
        finally:
            session.close()

    @staticmethod
    def _response(request, payload, status=200):
        import json
        response = FakeResponse(request)
        response.status_code = status
        response.headers['Content-Type'] = 'application/json; charset=UTF-8'
        response._content = json.dumps(payload).encode('utf-8')
        return response

    def test_requests_post_calls_are_classified_from_method_and_url(self):
        session = create_observed_paced_session(0, 3)
        try:
            with patch.object(session, 'send', side_effect=lambda request, **kwargs: FakeResponse(request)):
                session.post('https://music.youtube.com/youtubei/v1/account/account_menu?secret=x',
                             json={'context': {'client': {'clientName': 'WEB_REMIX'}}})
                session.post('https://music.youtube.com/youtubei/v1/search?query=private',
                             json={'query': 'private'})
                session.post('https://music.youtube.com/youtubei/v1/browse?token=private',
                             json={'browseId': 'MPRE_private'})
            self.assertEqual(session.count, 3)
            self.assertEqual(dict(session.request_categories), {
                'account_preflight': 1, 'search_page': 1, 'related_page': 1})
            self.assertEqual([event['category'] for event in session.request_events], [
                'account_preflight', 'search_page', 'related_page'])
            self.assertEqual([event['path'] for event in session.request_events], [
                '/youtubei/v1/account/account_menu',
                '/youtubei/v1/search',
                '/youtubei/v1/browse'])
            serialized = repr(session.request_events)
            self.assertNotIn('secret', serialized)
            self.assertNotIn('private', serialized)
            self.assertNotIn('token=', serialized)
        finally:
            session.close()

    def test_actual_path_is_not_rewritten_when_underlying_call_fails(self):
        session = create_observed_paced_session(0, 1)
        try:
            with patch.object(session, 'send', side_effect=OSError('offline fake')):
                with self.assertRaises(OSError):
                    session.post('https://music.youtube.com/youtubei/v1/search?query=secret',
                                 json={'query': 'secret'})
            self.assertEqual(session.count, 1)
            self.assertEqual(dict(session.request_categories), {'search_page': 1})
            self.assertEqual(session.request_events, [{
                'method': 'POST', 'path': '/youtubei/v1/search',
                'category': 'search_page', 'status': 'error',
                'error_type': 'OSError', 'request_number': 1,
                'status_code': None, 'response_content_type': None,
                'sanitized_response_sha256': None,
            }])
        finally:
            session.close()

    def test_non_youtube_unclassified_path_remains_other(self):
        session = create_observed_paced_session(0, 1)
        try:
            with patch.object(session, 'send', side_effect=lambda request, **kwargs: FakeResponse(request)):
                session.post('https://example.invalid/anything?secret=value', json={})
            self.assertEqual(dict(session.request_categories), {'other': 1})
            self.assertEqual(session.request_events[0]['path'], '/anything')
        finally:
            session.close()


if __name__ == '__main__':
    unittest.main()
