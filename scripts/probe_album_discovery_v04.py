"""Bounded exact-title album discovery, with no recommendation credit.

Reuse the observed paced transport and sanitized first-page receipt format.
The intent binds exact queries and input files; no continuation or retry is sent.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from urllib.parse import urlsplit
from music_identity_map import normalize
from probe_ytmusic_batch import verify_account
from probe_ytmusic_session_observer_v02 import _request_parts, create_observed_paced_session
from probe_official_album_discovery_v03 import (
    _atomic_json, _safe_failure, _BlockedContinuation, _validated_transport,
    _validate_safe_search_receipt,
)
REPO = Path(__file__).resolve().parents[1]
OUTPUT = None

# These code-bearing intent roles must identify the very modules used by this
# process. File hashes alone do not detect a same-named module preloaded from a
# different path before this script was imported.
CODE_INPUT_MODULES = {
    'trusted_probe_script': None,
    'trusted_transport_and_receipt_helper': 'probe_official_album_discovery_v03',
    'authenticated_account_verifier': 'probe_ytmusic_batch',
    'paced_request_observer': 'probe_ytmusic_session_observer_v02',
    'title_normalizer': 'music_identity_map',
}
CODE_INPUT_PATHS = {
    'trusted_probe_script': 'probe_album_discovery_v04.py',
    'trusted_transport_and_receipt_helper': 'probe_official_album_discovery_v03.py',
    'authenticated_account_verifier': 'probe_ytmusic_batch.py',
    'paced_request_observer': 'probe_ytmusic_session_observer_v02.py',
    'title_normalizer': 'music_identity_map.py',
}

def sha(data):
    return hashlib.sha256(data).hexdigest()


def _verify_loaded_code_input_origins(inputs, repo=REPO, loaded_modules=None,
                                      runner_source=None):
    """Require the declared code files to be the modules this process loaded."""
    modules = sys.modules if loaded_modules is None else loaded_modules
    runner_path = Path(__file__).resolve() if runner_source is None else Path(runner_source).resolve()
    script_dir = (Path(repo) / 'scripts').resolve()
    rows = [row for row in inputs if isinstance(row, dict)
            and row.get('role') in CODE_INPUT_MODULES]
    by_role = {row['role']: row for row in rows}
    if (len(rows) != len(CODE_INPUT_MODULES)
            or len(by_role) != len(CODE_INPUT_MODULES)
            or set(by_role) != set(CODE_INPUT_MODULES)):
        raise ValueError('code input role set differs from the loaded-module allowlist')
    for role, module_name in CODE_INPUT_MODULES.items():
        row = by_role[role]
        expected_path = (script_dir / CODE_INPUT_PATHS[role]).resolve()
        declared_path = Path(row.get('path', '')).resolve()
        if expected_path.parent != script_dir or declared_path != expected_path:
            raise ValueError('code input path differs from the fixed scripts allowlist')
        try:
            expected_hash = row['sha256']
            if sha(expected_path.read_bytes()) != expected_hash:
                raise ValueError('code input bytes differ from the sealed receipt')
        except (KeyError, OSError, TypeError):
            raise ValueError('code input receipt is malformed or missing') from None
        loaded_path = runner_path if module_name is None else getattr(
            modules.get(module_name), '__file__', None)
        if loaded_path is None or Path(loaded_path).resolve() != expected_path:
            raise ValueError('loaded code module resolved outside its sealed source path')
        if sha(Path(loaded_path).read_bytes()) != expected_hash:
            raise ValueError('loaded code module bytes differ from the sealed receipt')

def validate_intent(intent, intent_bytes, expected_sha, repo=REPO):
    if sha(intent_bytes) != expected_sha or json.loads(intent_bytes) != intent:
        raise ValueError('Intent digest differs')
    if intent['producer'] != 'opengenius-bounded-album-discovery-v04':
        raise ValueError('Unexpected intent producer')
    if Path(intent['output']).resolve() != OUTPUT or OUTPUT.exists():
        raise ValueError('Output must be fresh and match intent')
    for ref in intent['inputs']:
        if sha(Path(ref['path']).read_bytes()) != ref['sha256']:
            raise ValueError('Discovery input changed')
    _verify_loaded_code_input_origins(intent['inputs'], repo=repo)
    titles = intent['titles']
    if not 1 <= len(titles) <= 20 or len({r['album'] for r in titles}) != len(titles):
        raise ValueError('Queries must be unique and bounded')
    for row in titles:
        if row['search'] != {'arguments': {'query': row['album'], 'filter': 'albums', 'limit': 1}}:
            raise ValueError('Only exact-title first-page search is allowed')
    if intent['limits'] != {'minimum_interval_seconds': 3, 'total_http_requests_max': len(titles)+2}:
        raise ValueError('Request limits differ')
    if intent['outgoing_roots_credited'] != 0:
        raise ValueError('Discovery cannot credit roots')

def run(intent, intent_bytes, expected_sha, auth_path, *, client_factory,
        session_factory, output=OUTPUT, repo=REPO):
    validate_intent(intent, intent_bytes, expected_sha, repo=repo)
    if output != OUTPUT or output.exists():
        raise ValueError('output namespace is not fresh')
    output.mkdir(mode=0o700, parents=False)
    title_dir = output / 'first-pages'
    title_dir.mkdir(mode=0o700)
    state = {'active_index': None, 'per_title_search_requests': {},
             'blocked_continuations': []}
    title_rows = intent['titles']

    def persist_search_receipt(record):
        _validate_safe_search_receipt(record)
        index = state['active_index']
        if type(index) is not int or not 0 <= index < len(title_rows):
            raise RuntimeError('search response arrived outside a declared title query')
        row = title_rows[index]
        receipt = {'schema_version': 1,
            'producer': 'opengenius-official-album-search-first-page-v03',
            'intent_sha256': expected_sha, 'query_ordinal': index,
            'local_album_title': row['album'], 'observed_at': datetime.now(timezone.utc).isoformat(),
            'response': record}
        _atomic_json(title_dir / f'{index:02d}-first-page.json', receipt)

    limits = intent['limits']
    session = session_factory(limits['minimum_interval_seconds'],
                              limits['total_http_requests_max'],
                              search_response_callback=persist_search_receipt)
    original_request = session.request
    report = {'schema_version': 1,
        'producer': 'opengenius-official-album-first-page-discovery-result-v03',
        'intent_sha256': expected_sha, 'status': 'running',
        'account_authentication_verified': False, 'http_requests': 0,
        'attempted_pids': 0, 'pid_bindings_created': 0,
        'recording_identities_created': 0, 'outgoing_roots_created': 0,
        'recommendation_edges_created': 0, 'titles': [],
        'blocked_continuations': state['blocked_continuations']}

    def guarded_request(*args, **kwargs):
        method, path, url, body = _request_parts(args, kwargs)
        if urlsplit(url).hostname != 'music.youtube.com':
            raise RuntimeError('host is outside the sealed diagnostic allowlist')
        active_title = None
        if type(state['active_index']) is int:
            active_title = title_rows[state['active_index']]['album']
        if method == 'GET' and path == '/':
            category = 'visitor_id_bootstrap'
        elif method == 'POST' and path == '/youtubei/v1/account/account_menu':
            category = 'account_preflight'
        elif method == 'POST' and path == '/youtubei/v1/search':
            category = 'search_page'
        else:
            raise RuntimeError('endpoint is outside the sealed diagnostic allowlist')
        if category == 'search_page':
            index = state['active_index']
            if type(index) is not int:
                raise RuntimeError('search request arrived outside a declared title query')
            count = state['per_title_search_requests'].get(index, 0)
            if count >= 1:
                state['blocked_continuations'].append({
                    'query_ordinal': index, 'method': method,
                    'endpoint_path_without_query': path,
                    'request_number': None, 'sent': False,
                })
                raise _BlockedContinuation('second search request blocked before transport')
            state['per_title_search_requests'][index] = count + 1
        _category, response = _validated_transport(
            method, path, url, body, active_title,
            lambda: original_request(*args, **kwargs))
        return response

    session.request = guarded_request
    stage = 'client_initialization'
    try:
        client = client_factory(auth_path, session)
        stage = 'account_preflight'
        report['account_authentication_verified'] = verify_account(client)
        if (report['account_authentication_verified'] is not True
                or session.count != 2
                or dict(session.request_categories) != {
                    'visitor_id_bootstrap': 1, 'account_preflight': 1}):
            raise RuntimeError('authenticated preflight request sequence differs')
        for index, row in enumerate(title_rows):
            state['active_index'] = index
            call = {'query_ordinal': index, 'local_album_title': row['album'],
                    'query': row['search']['arguments']['query'],
                    'status': 'running', 'parsed_result_count': None,
                    'first_page_receipt_sha256': None,
                    'exact_title_candidate_count': None, 'failure': None}
            stage = f'album_search_{index:02d}'
            requests_before = session.request_categories.get('search_page', 0)
            try:
                parsed = client.search(**row['search']['arguments'])
                call['parsed_result_count'] = len(parsed) if isinstance(parsed, list) else None
                search_requests = session.request_categories.get('search_page', 0) - requests_before
                receipt_path = title_dir / f'{index:02d}-first-page.json'
                if search_requests != 1 or not receipt_path.is_file():
                    call['status'] = 'stopped_incomplete'
                    call['failure'] = {
                        'exception_type': 'MissingFirstPageTransportOrReceipt',
                        'safe_module_basename': Path(__file__).name,
                        'function_name': 'run',
                        'line_number': None,
                    }
                else:
                    call['status'] = 'first_page_search_completed'
            except _BlockedContinuation as error:
                call['status'] = 'incomplete_continuation_blocked'
                call['failure'] = _safe_failure(error)
            except Exception as error:
                call['status'] = 'stopped_incomplete'
                call['failure'] = _safe_failure(error)
            receipt_path = title_dir / f'{index:02d}-first-page.json'
            if receipt_path.is_file():
                receipt_bytes = receipt_path.read_bytes()
                call['first_page_receipt_sha256'] = sha(receipt_bytes)
                candidates = json.loads(receipt_bytes)['response']['sanitized_search_json']['album_candidates']
                call['exact_title_candidate_count'] = sum(
                    normalize(item['title']) == normalize(row['album']) for item in candidates)
            report['titles'].append(call)
            state['active_index'] = None
            if call['status'] == 'stopped_incomplete':
                # An unexpected transport/parser failure stops the diagnostic without retry.
                break
        expected_categories = {'visitor_id_bootstrap': 1, 'account_preflight': 1}
        search_count = sum(state['per_title_search_requests'].values())
        if search_count:
            expected_categories['search_page'] = search_count
        if (session.count > limits['total_http_requests_max']
                or dict(session.request_categories) != expected_categories):
            raise RuntimeError('actual endpoint category/count differs from sealed profile')
        report['status'] = (
            'bounded_diagnostic_completed'
            if all(row['status'] == 'first_page_search_completed' for row in report['titles'])
            else 'bounded_diagnostic_partial'
        )
    except Exception as error:
        report['status'] = 'stopped_incomplete'
        report['failure_stage'] = stage
        report['failure'] = _safe_failure(error)
    finally:
        report['http_requests'] = session.count if type(session.count) is int else None
        report['request_categories'] = dict(session.request_categories)
        report['request_events'] = list(session.request_events)
        report['blocked_continuations'] = list(state['blocked_continuations'])
        if hasattr(session, 'close'):
            session.close()
        _atomic_json(output / 'summary.json', report)
    return report


def main():
    global OUTPUT
    from ytmusicapi import YTMusic
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intent', type=Path, required=True)
    parser.add_argument('--intent-sha256', required=True)
    parser.add_argument('--auth', type=Path, required=True)
    args = parser.parse_args()
    raw = args.intent.read_bytes()
    intent = json.loads(raw)
    OUTPUT = Path(intent['output']).resolve()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        result = run(intent, raw, args.intent_sha256, str(args.auth),
            client_factory=lambda auth, session: YTMusic(auth, requests_session=session, language='en', location='JP'),
            session_factory=create_observed_paced_session, output=OUTPUT)
    print(json.dumps({k: result.get(k) for k in ('status', 'http_requests', 'account_authentication_verified', 'titles', 'failure')}, ensure_ascii=False))

if __name__ == '__main__':
    main()
