import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import diagnose_ytmusic_account as diagnostic
from probe_ytmusic_batch import create_paced_session


PRIVATE = 'PRIVATE_SENTINEL_NOT_FOR_OUTPUT'


def fixture():
    header = {'accountName': {'runs': [{'text': PRIVATE}]},
              'channelHandle': {'runs': [{'text': PRIVATE}]},
              'accountPhoto': {'thumbnails': [{'url': PRIVATE}]}}
    return {'actions': [{'openPopupAction': {'popup': {'multiPageMenuRenderer': {
        'header': {'activeAccountHeaderRenderer': header}}}}}],
        'responseContext': {'visitorData': PRIVATE}, PRIVATE: {PRIVATE: PRIVATE}}


def header(response):
    return diagnostic.at(response, diagnostic.HEADER)


class Adapter(requests.adapters.BaseAdapter):
    def __init__(self, payload, status=200, clock=None):
        self.payload, self.status, self.clock = payload, status, clock
        self.calls = []

    def send(self, request, **kwargs):
        self.calls.append((request.method, kwargs['timeout'], self.clock[0]))
        response = requests.Response()
        response.status_code = self.status if request.method == 'POST' else 200
        response.request = request
        response.url = request.url
        response._content = (json.dumps(self.payload).encode() if request.method == 'POST' else
                             ('ytcfg.set({"VISITOR_DATA": "' + PRIVATE + '"});').encode())
        return response

    def close(self):
        pass


class AccountDiagnosticTests(unittest.TestCase):
    def assert_private_absent(self, report):
        encoded = json.dumps(report)
        self.assertNotIn(PRIVATE, encoded)
        self.assertNotIn('visitorData', encoded)
        self.assertNotIn('trackingParams', encoded)

    def test_installed_sdk_success_exports_only_fixed_structure(self):
        response = fixture()
        original = copy.deepcopy(response)
        with patch('socket.socket', side_effect=AssertionError('No network')):
            result = diagnostic.diagnose_supplied_response(response)
        self.assertTrue(result['sdk_parser_succeeded'])
        self.assertTrue(result['sdk_account_name_nonempty'])
        self.assertEqual(result['response_shape'], 'expected_account_fields')
        self.assertEqual(set(result['paths']), {
            'actions_list', 'first_popup_menu', 'account_header', 'account_name_text',
            'account_photo_url_text', 'channel_handle_text', 'account_header_in_later_action'})
        self.assertTrue(all(type(value) is bool for value in result['paths'].values()))
        self.assertEqual(response, original)
        self.assert_private_absent(result)

    def test_missing_photo_identifies_sdk_failure_despite_name(self):
        response = fixture()
        del header(response)['accountPhoto']
        result = diagnostic.diagnose_supplied_response(response)
        self.assertFalse(result['sdk_parser_succeeded'])
        self.assertTrue(result['paths']['account_name_text'])
        self.assertEqual(result['response_shape'], 'missing_account_photo_url')
        self.assertEqual(result['error_category'], 'sdk_shape_error')
        self.assert_private_absent(result)

    def test_missing_header_or_name_are_distinct_from_photo_failure(self):
        missing_name = fixture()
        del header(missing_name)['accountName']
        for response, expected in (({}, 'missing_account_header'),
                                   (missing_name, 'missing_account_name_text')):
            with self.subTest(shape=expected):
                result = diagnostic.diagnose_supplied_response(response)
                self.assertFalse(result['sdk_parser_succeeded'])
                self.assertEqual(result['response_shape'], expected)
                self.assert_private_absent(result)

    def test_optional_handle_absent_does_not_break_sdk(self):
        response = fixture()
        del header(response)['channelHandle']
        result = diagnostic.diagnose_supplied_response(response)
        self.assertTrue(result['sdk_parser_succeeded'])
        self.assertFalse(result['paths']['channel_handle_text'])

    def test_later_action_header_reveals_order_change_without_values(self):
        response = fixture()
        response['actions'].insert(0, {'irrelevantSecret': PRIVATE})
        result = diagnostic.diagnose_supplied_response(response)
        self.assertFalse(result['sdk_parser_succeeded'])
        self.assertFalse(result['paths']['account_header'])
        self.assertTrue(result['paths']['account_header_in_later_action'])
        self.assert_private_absent(result)

    def test_malformed_arrays_and_names_are_safe_and_do_not_claim_auth(self):
        malformed = [None, [], {'actions': PRIVATE}, {'actions': []}]
        for field in ('accountName', 'accountPhoto'):
            response = fixture()
            header(response)[field] = {'runs': [], 'thumbnails': []}
            malformed.append(response)
        for response in malformed:
            with self.subTest(response_type=type(response).__name__):
                result = diagnostic.diagnose_supplied_response(response)
                # nav(None, path) returns None in this SDK, so parser success
                # alone is deliberately separate from a nonempty account name.
                self.assertEqual(result['sdk_parser_succeeded'], response is None)
                self.assertFalse(result['sdk_account_name_nonempty'])
                self.assert_private_absent(result)
        response = fixture()
        header(response)['accountName']['runs'][0]['text'] = ''
        result = diagnostic.diagnose_supplied_response(response)
        self.assertTrue(result['sdk_parser_succeeded'])
        self.assertFalse(result['sdk_account_name_nonempty'])
        self.assertEqual(result['response_shape'], 'missing_account_name_text')

    def test_exception_keys_messages_and_sdk_output_are_not_exported(self):
        class Client:
            def _send_request(self, endpoint, body):
                return fixture()

            def get_account_info(self):
                self._send_request('account/account_menu', {})
                print(PRIVATE)
                print(PRIVATE, file=sys.stderr)
                raise KeyError(PRIVATE)

        client = Client()
        previous = client._send_request
        out, err = io.StringIO(), io.StringIO()
        with patch('sys.stdout', out), patch('sys.stderr', err):
            result = diagnostic.diagnose_client(client, 'supplied_response')
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), '')
        self.assertEqual(client._send_request, previous)
        self.assertTrue(result['response_captured'])
        self.assertEqual(result['error_category'], 'sdk_shape_error')
        self.assert_private_absent(result)

    def test_second_sdk_request_is_blocked_without_retry(self):
        class Client:
            calls = 0
            def _send_request(self, endpoint, body):
                self.calls += 1
                return fixture()
            def get_account_info(self):
                self._send_request('account/account_menu', {})
                self._send_request('account/account_menu', {})
        client = Client()
        result = diagnostic.diagnose_client(client, 'supplied_response')
        self.assertEqual(client.calls, 1)
        self.assertFalse(result['sdk_parser_succeeded'])

    def test_network_mode_uses_real_sdk_offline_transport_and_pacing(self):
        self.run_network_fixture(status=200)

    def test_http_rejection_stops_without_body_or_retry(self):
        self.run_network_fixture(status=401)

    def run_network_fixture(self, status):
        clock = [100.0]
        adapter = Adapter(fixture(), status, clock)
        sessions = []
        def session_factory(interval, budget):
            self.assertEqual((interval, budget), (5, 3))
            session = create_paced_session(interval, budget)
            session.mount('https://', adapter)
            sessions.append(session)
            return session
        def sleep(seconds):
            clock[0] += seconds
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / 'auth.json'
            auth.write_text(json.dumps({'authorization': 'SAPISIDHASH synthetic',
                                        'cookie': '__Secure-3PAPISID=' + PRIVATE,
                                        'origin': 'https://music.youtube.com',
                                        'x-goog-authuser': '0'}))
            with patch.object(diagnostic, 'create_paced_session', side_effect=session_factory), \
                    patch('probe_ytmusic_batch.time.monotonic', side_effect=lambda: clock[0]), \
                    patch('probe_ytmusic_batch.time.sleep', side_effect=sleep), \
                    patch('socket.socket', side_effect=AssertionError('No external network')):
                result = diagnostic.diagnose_network(auth)
        self.assertEqual(adapter.calls, [('GET', 25, 100.0), ('POST', 25, 105.0)])
        self.assertEqual(sessions[0].count, 2)
        self.assertEqual(result['sdk_parser_succeeded'], status == 200)
        if status == 401:
            self.assertFalse(result['response_captured'])
            self.assertEqual(result['error_category'], 'http_unauthorized')
        self.assert_private_absent(result)

    def test_cli_default_local_mode_and_invalid_input_never_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'response.json'
            for content, expected_exit in ((json.dumps(fixture()), 0), (PRIVATE, 1)):
                path.write_text(content)
                output = io.StringIO()
                with patch('sys.stdout', output), \
                        patch.object(diagnostic, 'diagnose_network', side_effect=AssertionError('No network')):
                    self.assertEqual(diagnostic.main(['--response', str(path)]), expected_exit)
                report = json.loads(output.getvalue())
                self.assert_private_absent(report)

    def test_invalid_flags_rejected_before_any_credentials_or_network(self):
        for args in ([], ['--auth', PRIVATE], ['--network'],
                     ['--network', '--auth', PRIVATE, '--request-budget', '4'],
                     ['--network', '--auth', PRIVATE, '--interval', 'nan'],
                     ['--network', '--auth', PRIVATE, '--interval', '4'],
                     ['--response', PRIVATE, '--auth', PRIVATE]):
            with self.subTest(args=args), patch('sys.stderr', new=io.StringIO()) as err, \
                    patch.object(diagnostic, 'diagnose_network') as network:
                with self.assertRaises(SystemExit) as stopped:
                    diagnostic.main(args)
                self.assertEqual(stopped.exception.code, 2)
                network.assert_not_called()
                self.assertNotIn(PRIVATE, err.getvalue())


if __name__ == '__main__':
    unittest.main()
