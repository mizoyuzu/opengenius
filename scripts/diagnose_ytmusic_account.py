"""Inspect fixed account-menu paths without exporting any response values.

Offline: --response supplied.json. Network access requires explicit --network
and --auth, makes one get_account_info call, and never retries or saves its raw
response. This is a diagnostic, not a replacement authentication policy.

SDK structure: https://github.com/sigma67/ytmusicapi/blob/main/ytmusicapi/mixins/library.py
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import math
import os
from pathlib import Path

from probe_ytmusic_batch import create_paced_session


MENU = ('actions', 0, 'openPopupAction', 'popup', 'multiPageMenuRenderer')
HEADER = (*MENU, 'header', 'activeAccountHeaderRenderer')
MISSING = object()
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def at(value, path):
    """Walk only a caller-provided fixed path; never enumerate response keys."""
    for part in path:
        if isinstance(part, int):
            if not isinstance(value, list) or len(value) <= part:
                return MISSING
            value = value[part]
        else:
            if not isinstance(value, dict) or part not in value:
                return MISSING
            value = value[part]
    return value


def nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def summarize_response(response):
    paths = {
        'actions_list': isinstance(at(response, ('actions',)), list),
        'first_popup_menu': isinstance(at(response, MENU), dict),
        'account_header': isinstance(at(response, HEADER), dict),
        'account_name_text': nonempty_text(at(response, (*HEADER, 'accountName', 'runs', 0, 'text'))),
        'account_photo_url_text': nonempty_text(at(response, (*HEADER, 'accountPhoto', 'thumbnails', 0, 'url'))),
        'channel_handle_text': nonempty_text(at(response, (*HEADER, 'channelHandle', 'runs', 0, 'text'))),
    }
    # Fixed SDK header path at other action indices can reveal an ordering change.
    # Bound the inspection and export only a boolean, never an index or account.
    actions = at(response, ('actions',))
    paths['account_header_in_later_action'] = isinstance(actions, list) and any(
        isinstance(at(action, HEADER[2:]), dict) for action in actions[1:17])
    if not isinstance(response, dict):
        shape = 'unexpected_response_type'
    elif not paths['account_header']:
        shape = 'missing_account_header'
    elif not paths['account_name_text']:
        shape = 'missing_account_name_text'
    elif not paths['account_photo_url_text']:
        shape = 'missing_account_photo_url'
    else:
        shape = 'expected_account_fields'
    return {'response_shape': shape, 'paths': paths}


def error_category(exc):
    """Return a fixed label; exception strings/types/keys are never emitted."""
    import requests
    if isinstance(exc, requests.HTTPError):
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        return {401: 'http_unauthorized', 403: 'http_forbidden',
                429: 'http_rate_limited'}.get(status, 'http_failure')
    if isinstance(exc, requests.Timeout):
        return 'network_timeout'
    if isinstance(exc, requests.ConnectionError):
        return 'network_connection_failure'
    if isinstance(exc, (KeyError, IndexError, TypeError)):
        return 'sdk_shape_error'
    return 'operation_failure'


def empty_report(source):
    return {'source': source, 'response_captured': False,
            'sdk_parser_succeeded': False, 'sdk_account_name_nonempty': False,
            'response_shape': 'response_not_captured', 'paths': {},
            'error_category': 'none'}


def diagnose_client(client, source):
    """Call the installed SDK once; summarize its response before SDK parsing."""
    report = empty_report(source)
    original = client._send_request
    calls = 0

    def capture(endpoint, body, *args, **kwargs):
        nonlocal calls
        if endpoint != 'account/account_menu' or calls:
            raise RuntimeError()  # No second account request or other endpoints.
        calls += 1
        response = original(endpoint, body, *args, **kwargs)
        report.update(summarize_response(response))
        report['response_captured'] = True
        return response

    client._send_request = capture
    try:
        # Discard any SDK print output, including possible private error details.
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            account = client.get_account_info()
        report['sdk_parser_succeeded'] = True
        report['sdk_account_name_nonempty'] = (
            isinstance(account, dict) and nonempty_text(account.get('accountName')))
    except Exception as exc:
        report['error_category'] = error_category(exc)
    finally:
        client._send_request = original
    return report


def diagnose_supplied_response(response):
    # Use the installed SDK parser with transport replaced; no client constructor,
    # auth file, visitor request, or socket is involved in the offline mode.
    from ytmusicapi.mixins.library import LibraryMixin

    class SuppliedClient:
        get_account_info = LibraryMixin.get_account_info

        def _check_auth(self):
            pass  # A fixture has no credentials and makes no authentication claim.

        def _send_request(self, endpoint, body):
            return response

    return diagnose_client(SuppliedClient(), 'supplied_response')


def diagnose_network(auth, interval=5, request_budget=3):
    if (not math.isfinite(interval) or interval < 5 or
            type(request_budget) is not int or not 1 <= request_budget <= 3):
        raise ValueError()
    from ytmusicapi import YTMusic
    session = create_paced_session(interval, request_budget)
    try:
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            client = YTMusic(str(auth), requests_session=session, language='en')
        return diagnose_client(client, 'network')
    except Exception as exc:
        report = empty_report('network')
        report['error_category'] = error_category(exc)
        return report
    finally:
        session.close()


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Do not reflect unexpected arguments, credentials, paths or file errors.
        self.exit(2, '{"error_category": "invalid_arguments"}\n')


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--response', type=Path, help='Local supplied JSON; no network')
    source.add_argument('--network', action='store_true', help='Explicit single SDK account call')
    parser.add_argument('--auth', type=Path, help='Browser auth JSON; requires --network')
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--request-budget', type=int, default=3)
    args = parser.parse_args(argv)
    if (bool(args.auth) != args.network or not math.isfinite(args.interval) or
            args.interval < 5 or not 1 <= args.request_budget <= 3):
        parser.error('invalid configuration')
    if args.response:
        try:
            with args.response.open('rb') as handle:
                encoded = handle.read(MAX_RESPONSE_BYTES + 1)
            if len(encoded) > MAX_RESPONSE_BYTES:
                raise ValueError()
            response = json.loads(encoded)
            report = diagnose_supplied_response(response)
        except Exception:
            report = empty_report('supplied_response')
            report['error_category'] = 'invalid_supplied_response'
    else:
        report = diagnose_network(args.auth, args.interval, args.request_budget)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['sdk_parser_succeeded'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
