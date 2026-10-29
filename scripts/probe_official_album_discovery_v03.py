"""Bounded first-page YT Music album availability diagnostic; no graph credit."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback
from urllib.parse import urlsplit

REPO = Path('/mnt/data/mizoyuzu/Projects/personal/opengenius')
sys.path.insert(0, str(REPO / 'scripts'))

from music_identity_map import load_library, normalize  # noqa: E402
from probe_ytmusic_batch import verify_account  # noqa: E402
from probe_ytmusic_session_observer_v02 import (  # noqa: E402
    _request_parts, create_observed_paced_session,
)

PLAN_PATH = Path('/tmp/opengenius-official-album-discovery-v03.json')
OUTPUT = Path('/tmp/opengenius-official-album-discovery-v03-private')
AUTH_PATH = REPO / 'data/ytmusic/firefox-nightly-auth-20261004-r5.json'
SNAPSHOT = REPO / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
PILOT_PROGRESS = REPO / 'data/ytmusic/retrieval-query-pilot-20261013-v01/progress.json'
PINNED_INTENT_SHA256 = '6c895671b412e8d5813ab8b12593b6705630f68ee499cdf4897b782af1148029'
PINNED_LIBRARY_SHA256 = '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7'
CODE_INPUTS = {
    'scripts/probe_ytmusic_batch.py': '22f6ec7045bed51a7c7c6b7a0f4d73429db7206243c0b70faec04ca5b6108a28',
    'scripts/music_identity_map.py': 'f1befec3b36108e7904fa97aa9abf6adc43226707018ea07fa1f13e8be66f169',
    'scripts/probe_ytmusic_session_observer_v02.py': '1e7381f68d97789f518113341dc824f0c87b893c544098adb464fc916e387938',
}
MODULES = {
    'scripts/probe_ytmusic_batch.py': 'probe_ytmusic_batch',
    'scripts/music_identity_map.py': 'music_identity_map',
    'scripts/probe_ytmusic_session_observer_v02.py': 'probe_ytmusic_session_observer_v02',
}
RUNTIME_INPUTS = {
    '.venv/lib/python3.13/site-packages/ytmusicapi/ytmusic.py': '0ec8ba6c2a10932198f2aae7e19909d0f537eb33afe3050cb371c84e00399233',
    '.venv/lib/python3.13/site-packages/ytmusicapi/mixins/search.py': '7757644754ce2948aae3724250a4da363e0165cdd5d63df74d4fefcf57436320',
    '.venv/lib/python3.13/site-packages/ytmusicapi/mixins/library.py': '9e067c2ff6d870b7e7fec2c0bca85886f7228be3ee3c7bad6a1903760c1d3fea',
    '.venv/lib/python3.13/site-packages/ytmusicapi/continuations.py': '71a1ec9953e5c41a8eed2ce46ff46cf14f3fa25400cf11697f489a3bd40eea28',
    '.venv/lib/python3.13/site-packages/ytmusicapi/constants.py': '9676f20976316bc1bdb308bc92cedb3c756ef7b17e8ca3ddfbc04f903d9a3845',
    '/usr/lib/python3/dist-packages/requests/sessions.py': 'ca44c8f145864a5b4e7c7d3b1caa25947ee44c11b0e168620556901a67244f0e',
}
RUNTIME_MODULES = {
    '.venv/lib/python3.13/site-packages/ytmusicapi/ytmusic.py': 'ytmusicapi.ytmusic',
    '.venv/lib/python3.13/site-packages/ytmusicapi/mixins/search.py': 'ytmusicapi.mixins.search',
    '.venv/lib/python3.13/site-packages/ytmusicapi/mixins/library.py': 'ytmusicapi.mixins.library',
    '.venv/lib/python3.13/site-packages/ytmusicapi/continuations.py': 'ytmusicapi.continuations',
    '.venv/lib/python3.13/site-packages/ytmusicapi/constants.py': 'ytmusicapi.constants',
    '/usr/lib/python3/dist-packages/requests/sessions.py': 'requests.sessions',
}
TITLE_ORDER = [
    'THE IDOLM@STER SHINY COLORS ORIGINAL SOUNDTRACK',
    'サノバウィッチ オリジナルサウンドトラック',
    '喫茶ステラと死神の蝶 オリジナルサウンドトラック',
    'Charlotte Original Soundtrack',
    'CLANNAD ORIGINAL SOUNDTRACK',
    'Angel Beats!｜ORIGINAL SOUNDTRACK',
]
ALBUM_SEARCH_PARAMS = 'EgWKAQIYAWoMEA4QChADEAQQCRAF'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         indent=2).encode('utf-8') + b'\n'
    temp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise
    return encoded


def _safe_failure(error):
    frames = traceback.extract_tb(error.__traceback__)
    frame = frames[-1] if frames else None
    return {
        'exception_type': type(error).__name__,
        'safe_module_basename': Path(frame.filename).name if frame else None,
        'function_name': frame.name if frame else None,
        'line_number': frame.lineno if frame else None,
    }


class _BlockedContinuation(RuntimeError):
    pass


def _validated_transport(method, path, url, body, active_title, send):
    """Apply the exact host, endpoint, and query allowlist before transport."""
    parsed = urlsplit(url)
    if parsed.hostname != 'music.youtube.com':
        raise RuntimeError('host is outside the sealed diagnostic allowlist')
    if method == 'GET' and path == '/':
        category = 'visitor_id_bootstrap'
    elif method == 'POST' and path == '/youtubei/v1/account/account_menu':
        category = 'account_preflight'
    elif method == 'POST' and path == '/youtubei/v1/search':
        if (not isinstance(body, dict)
                or set(body) != {'context', 'params', 'query'}
                or not isinstance(body.get('context'), dict)
                or body.get('query') != active_title
                or body.get('params') != ALBUM_SEARCH_PARAMS):
            raise RuntimeError('search request does not match the exact active album query')
        category = 'search_page'
    else:
        raise RuntimeError('endpoint is outside the sealed diagnostic allowlist')
    return category, send()


def _validate_safe_search_receipt(record):
    expected = {
        'method', 'endpoint_path_without_query', 'safe_category', 'status_code',
        'response_content_type', 'response_body_bytes',
        'sanitized_response_sha256', 'sanitized_search_json',
    }
    if not isinstance(record, dict) or set(record) != expected:
        raise ValueError('pre-parser receipt fields differ from allowlist')
    if (record.get('method') != 'POST'
            or record.get('endpoint_path_without_query') != '/youtubei/v1/search'
            or record.get('safe_category') != 'search_page'
            or type(record.get('status_code')) is not int or record['status_code'] != 200
            or type(record.get('response_body_bytes')) is not int
            or not 0 <= record['response_body_bytes'] <= 5_000_000
            or not isinstance(record.get('response_content_type'), str)
            or 'json' not in record['response_content_type'].lower()
            or not isinstance(record.get('sanitized_response_sha256'), str)
            or len(record['sanitized_response_sha256']) != 64):
        raise ValueError('pre-parser receipt does not describe a bounded JSON search response')
    projection = record.get('sanitized_search_json')
    if not isinstance(projection, dict) or set(projection) != {'album_candidates'}:
        raise ValueError('sanitized search projection fields differ')
    rows = projection['album_candidates']
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError('sanitized album candidate list is malformed')
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'browseId', 'title'}
                or not isinstance(row.get('browseId'), str)
                or not row['browseId'].startswith('MPRE')
                or not isinstance(row.get('title'), str) or not row['title'].strip()):
            raise ValueError('sanitized album candidate is malformed')


def validate_intent(intent, intent_bytes, expected_sha, repo=REPO):
    if (PINNED_INTENT_SHA256 == 'TO_BE_SEALED'
            or expected_sha != PINNED_INTENT_SHA256 or sha(intent_bytes) != expected_sha):
        raise ValueError('intent digest differs from frozen pin')
    if json.loads(intent_bytes) != intent:
        raise ValueError('decoded intent differs from sealed bytes')
    if not isinstance(intent, dict) or intent.get('producer') != 'opengenius-official-album-first-page-discovery-v03':
        raise ValueError('intent producer differs')
    if intent.get('status') != 'review_pending_no_network':
        raise ValueError('intent is not review-pending')
    titles = intent.get('titles')
    if (not isinstance(titles, list) or [row.get('album') for row in titles] != TITLE_ORDER
            or len(titles) != 6):
        raise ValueError('exact local album title sequence differs')
    for row in titles:
        if row.get('search') != {'endpoint': 'search', 'arguments': {
                'query': row.get('album'), 'filter': 'albums', 'limit': 1}}:
            raise ValueError('search is not the exact-title albums first-page query')
        if (type(row.get('fresh_pid_count')) is not int or row['fresh_pid_count'] <= 0
                or not isinstance(row.get('official_url'), str)
                or not row['official_url'].startswith('https://')):
            raise ValueError('local stock or official reference is malformed')
    expected_limits = {
        'visitor_id_bootstrap_calls': 1, 'auth_preflight_calls': 1,
        'search_calls_max': 6, 'first_page_requests_per_title_max': 1,
        'total_http_requests_max': 8, 'minimum_interval_seconds': 3,
        'retry_count': 0, 'request_cache': 'disabled_fresh_receipts_only',
        'allowed_endpoints': ['visitor_id_bootstrap', 'account_preflight', 'search_page'],
        'get_album_calls': 0, 'follow_on_collection': False,
        'per_title_continuation': 'block before transport and mark only that title incomplete',
    }
    if intent.get('limits') != expected_limits:
        raise ValueError('request limits differ from frozen eight-request profile')
    if intent.get('credit_policy') != {
            'pid_binding': False, 'recording_identity_credit': False,
            'outgoing_root_credit': False, 'recommendation_edge_credit': False,
            'interpretation': 'only bounded first-page exact-title search observations; no claim that an album is absent from YT Music'}:
        raise ValueError('credit policy differs from discovery-only scope')
    if intent.get('output') != {
            'directory': str(OUTPUT), 'must_not_exist_before_run': True,
            'private_candidate_evidence': True} or OUTPUT.exists():
        raise ValueError('output path is not the exact fresh private namespace')

    declared = intent.get('code_inputs')
    declared_map = {row.get('path'): row.get('sha256') for row in declared
                    if isinstance(row, dict)} if isinstance(declared, list) else {}
    if declared_map != CODE_INPUTS or len(declared) != len(CODE_INPUTS):
        raise ValueError('code input receipt set differs from fixed allowlist')
    for rel, expected in CODE_INPUTS.items():
        path = (repo / rel).resolve()
        if path.parent != (repo / 'scripts').resolve() or sha(path.read_bytes()) != expected:
            raise ValueError('pinned helper bytes changed')
        module = sys.modules.get(MODULES[rel])
        if (module is None or Path(module.__file__).resolve() != path
                or sha(Path(module.__file__).read_bytes()) != expected):
            raise ValueError('loaded helper module origin or bytes differ')

    runtime = intent.get('runtime_inputs')
    runtime_map = {row.get('path'): row.get('sha256') for row in runtime
                   if isinstance(row, dict)} if isinstance(runtime, list) else {}
    if runtime_map != RUNTIME_INPUTS or len(runtime) != len(RUNTIME_INPUTS):
        raise ValueError('runtime receipt set differs from fixed allowlist')
    for rel, expected in RUNTIME_INPUTS.items():
        path = (repo / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
        if not path.is_file() or sha(path.read_bytes()) != expected:
            raise ValueError('pinned runtime bytes changed')
        module = sys.modules.get(RUNTIME_MODULES[rel]) or importlib.import_module(RUNTIME_MODULES[rel])
        if Path(module.__file__).resolve() != path or sha(path.read_bytes()) != expected:
            raise ValueError('loaded runtime module origin or bytes differ')

    source = intent.get('source', {})
    snap, progress = source.get('track_snapshot', {}), source.get('pilot_progress', {})
    snap_path, progress_path = repo / snap.get('path', ''), repo / progress.get('path', '')
    if (snap_path != SNAPSHOT or progress_path != PILOT_PROGRESS
            or sha(snap_path.read_bytes()) != snap.get('sha256')
            or sha(progress_path.read_bytes()) != progress.get('sha256')):
        raise ValueError('source snapshot or progress lineage changed')
    tracks, library_hash, _ = load_library(track_snapshot=snap_path)
    if library_hash != PINNED_LIBRARY_SHA256:
        raise ValueError('pinned local library hash differs')
    attempted = set(json.loads(progress_path.read_text(encoding='utf-8')).get('attempted_pids', []))
    for row in titles:
        actual = sum(1 for track in tracks if track.get('album') == row['album']
                     and track.get('persistent_id') not in attempted
                     and track.get('genius_id') in ('0000000000000000', None, ''))
        if actual != row['fresh_pid_count']:
            raise ValueError('fresh local album stock changed')
    prior = source.get('prior_diagnostics')
    if not isinstance(prior, list) or len(prior) != 2:
        raise ValueError('diagnostic lineage set differs')
    for item in prior:
        for path_key, sha_key in (
                ('intent_path', 'intent_sha256'),
                ('summary_path', 'summary_sha256'),
                ('counter_path', 'counter_sha256'),
                ('first_page_receipt_path', 'first_page_receipt_sha256')):
            if item.get(path_key) and item.get(sha_key):
                path = Path(item[path_key])
                if not path.is_file() or sha(path.read_bytes()) != item.get(sha_key):
                    raise ValueError('prior diagnostic receipt changed')
        if type(item.get('charged_http')) is not int or item['attempted_pids'] != 0:
            raise ValueError('prior diagnostic accounting differs')
    cached = source.get('saved_song_for_prism_catalog')
    if not isinstance(cached, dict) or cached.get('network_query_for_this_work') is not False:
        raise ValueError('saved catalog reference is malformed')
    for path_key, hash_key in (
            ('path', 'sha256'), ('album_response_path', 'album_response_sha256'),
            ('current_map_rematch_path', 'current_map_rematch_sha256')):
        path = Path(cached.get(path_key, ''))
        if not path.is_file() or sha(path.read_bytes()) != cached.get(hash_key):
            raise ValueError('saved catalog evidence receipt changed')
    if (type(cached.get('strict_unattempted_nonroot_candidates')) is not int
            or cached['strict_unattempted_nonroot_candidates'] != 14):
        raise ValueError('saved catalog candidate count differs')
    accounting = source.get('accounting_before_probe')
    if accounting != {
            'main_pilot_http_requests': 3935, 'main_pilot_attempted_pids': 1823,
            'main_pilot_roots': 733, 'diagnostic_http_requests_charged': 5,
            'diagnostic_attempted_pids': 0, 'aggregate_http_requests': 3940,
            'aggregate_attempted_pids': 1823, 'aggregate_roots': 733}:
        raise ValueError('historical diagnostics accounting differs')
    return library_hash


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
            try:
                parsed = client.search(**row['search']['arguments'])
                call['parsed_result_count'] = len(parsed) if isinstance(parsed, list) else None
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
    import argparse
    from contextlib import redirect_stderr, redirect_stdout
    from ytmusicapi import YTMusic
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intent-sha256', required=True)
    args = parser.parse_args()
    if args.intent_sha256 != PINNED_INTENT_SHA256:
        parser.error('intent hash differs from frozen pin')
    intent_bytes = PLAN_PATH.read_bytes()
    intent = json.loads(intent_bytes)
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        result = run(intent, intent_bytes, args.intent_sha256, str(AUTH_PATH),
            client_factory=lambda auth, session: YTMusic(
                auth, requests_session=session, language='en', location='JP'),
            session_factory=create_observed_paced_session)
    print(json.dumps({'status': result.get('status'), 'http_requests': result.get('http_requests'),
        'request_categories': result.get('request_categories'),
        'account_authentication_verified': result.get('account_authentication_verified'),
        'title_outcomes': [{'title': x.get('local_album_title'), 'status': x.get('status'),
                            'exact_title_candidate_count': x.get('exact_title_candidate_count')}
                           for x in result.get('titles', [])],
        'attempted_pids': result.get('attempted_pids'), 'failure': result.get('failure')},
        ensure_ascii=False))


if __name__ == '__main__':
    main()
