"""Bounded authenticated exact-title album search; discovery only."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

REPO = Path('/mnt/data/mizoyuzu/Projects/personal/opengenius')
sys.path.insert(0, str(REPO / 'scripts'))

from music_identity_map import load_library, normalize  # noqa: E402
from probe_ytmusic_batch import verify_account  # noqa: E402
from probe_ytmusic_session_observer_v02 import create_observed_paced_session  # noqa: E402

PLAN_PATH = Path('/tmp/opengenius-kirby-album-probe-v02.json')
OUTPUT = Path('/tmp/opengenius-kirby-album-probe-v02-private')
AUTH_PATH = REPO / 'data/ytmusic/firefox-nightly-auth-20261004-r5.json'
SNAPSHOT = REPO / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
PILOT_PROGRESS = REPO / 'data/ytmusic/retrieval-query-pilot-20261013-v01/progress.json'
LOCAL_ALBUM = '星のカービィ スターアライズ オリジナル・サウンドトラック'

# Filled only after review pins the exact immutable inputs.
PINNED_INTENT_SHA256 = '451abaa354e8f2bff206365e7b0d5e857c25a30bb5203c73dd4043bcd3ace0c9'
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


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _safe_failure(error):
    frames = traceback.extract_tb(error.__traceback__)
    frame = frames[-1] if frames else None
    return {
        'exception_type': type(error).__name__,
        'safe_module_basename': Path(frame.filename).name if frame else None,
        'function_name': frame.name if frame else None,
        'line_number': frame.lineno if frame else None,
    }


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
            or type(record.get('status_code')) is not int
            or record['status_code'] != 200
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
                or not isinstance(row.get('title'), str)
                or not row['title'].strip()):
            raise ValueError('sanitized album candidate is malformed')


def validate_intent(intent, intent_bytes, expected_sha, repo=REPO):
    if (PINNED_INTENT_SHA256 == 'TO_BE_SEALED'
            or expected_sha != PINNED_INTENT_SHA256
            or sha(intent_bytes) != expected_sha):
        raise ValueError('intent digest differs from frozen pin')
    if json.loads(intent_bytes) != intent:
        raise ValueError('decoded intent differs from the sealed bytes')
    if not isinstance(intent, dict) or intent.get('status') != 'review_pending_no_network':
        raise ValueError('intent is not the frozen review-pending probe')
    if intent.get('producer') != 'opengenius-bounded-album-availability-probe-v2':
        raise ValueError('unexpected intent producer')
    target = intent.get('target')
    expected_search = {
        'endpoint': 'search',
        'arguments': {'query': LOCAL_ALBUM, 'filter': 'albums', 'limit': 1},
    }
    if (not isinstance(target, dict) or target.get('local_album') != LOCAL_ALBUM
            or target.get('search') != expected_search
            or 'conditional_catalog' in target):
        raise ValueError('search scope differs from exact one-page title query')
    if intent.get('limits') != {
        'visitor_id_bootstrap_calls': 1,
        'auth_preflight_calls': 1,
        'search_calls': 1,
        'total_http_requests_max': 3,
        'minimum_interval_seconds': 3,
        'retry_count': 0,
        'request_cache': 'disabled_fresh_receipts_only',
        'allowed_endpoints': ['visitor_id_bootstrap', 'account_preflight', 'search_page'],
        'follow_on_collection': False,
        'capture_methods': 'GET and POST method plus path only; no request URL query, headers, cookies, or body',
    }:
        raise ValueError('request limits differ from exact three-request profile')
    if intent.get('credit_policy') != {
        'pid_binding': False,
        'recording_identity_credit': False,
        'outgoing_root_credit': False,
        'recommendation_edge_credit': False,
        'empty_result_interpretation':
            'only the bounded first-page exact-title search was observed; no claim that the work is absent from the catalog',
    }:
        raise ValueError('credit policy differs from discovery-only scope')
    if intent.get('output') != {
        'directory': str(OUTPUT), 'must_not_exist_before_run': True,
        'private_candidate_evidence': True,
    } or OUTPUT.exists():
        raise ValueError('output path is not the exact fresh private namespace')

    declared = intent.get('code_inputs')
    declared_map = {row.get('path'): row.get('sha256') for row in declared
                    if isinstance(row, dict)} if isinstance(declared, list) else {}
    if declared_map != CODE_INPUTS or len(declared) != len(CODE_INPUTS):
        raise ValueError('code input receipt set differs from fixed allowlist')
    for rel, expected in CODE_INPUTS.items():
        path = (repo / rel).resolve()
        if path.parent != (repo / 'scripts').resolve() or sha(path.read_bytes()) != expected:
            raise ValueError('pinned local helper bytes changed')
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
        if (Path(module.__file__).resolve() != path
                or sha(Path(module.__file__).read_bytes()) != expected):
            raise ValueError('loaded runtime module origin differs')

    source = intent.get('source', {})
    snap = source.get('track_snapshot', {})
    progress = source.get('pilot_progress', {})
    snap_path, progress_path = repo / snap.get('path', ''), repo / progress.get('path', '')
    if (snap_path != SNAPSHOT or progress_path != PILOT_PROGRESS
            or sha(snap_path.read_bytes()) != snap.get('sha256')
            or sha(progress_path.read_bytes()) != progress.get('sha256')):
        raise ValueError('source snapshot or pilot progress lineage changed')
    tracks, library_hash, _ = load_library(track_snapshot=snap_path)
    if library_hash != PINNED_LIBRARY_SHA256 or not any(
            row.get('album') == LOCAL_ALBUM for row in tracks):
        raise ValueError('pinned local library album or hash differs')
    for prior in source.get('prior_album_searches', []):
        path = repo / prior['path']
        if not path.is_file() or sha(path.read_bytes()) != prior.get('sha256'):
            raise ValueError('prior search evidence changed')
        doc = json.loads(path.read_text(encoding='utf-8'))
        if (doc.get('endpoint') != 'search'
                or doc.get('arguments', {}).get('filter') != 'albums'
                or doc.get('arguments', {}).get('query') != prior.get('query')
                or doc.get('response') != [] or prior.get('returned_rows') != 0):
            raise ValueError('prior album search receipt differs from declared empty result')
    previous = intent.get('prior_probe_terminal', {})
    for key in ('terminal_summary_path', 'counter_incident_path'):
        path = repo / previous.get(key, '')
        hash_key = 'terminal_summary_sha256' if key == 'terminal_summary_path' else 'counter_incident_sha256'
        if not path.is_file() or sha(path.read_bytes()) != previous.get(hash_key):
            raise ValueError('v01 terminal receipt changed')
    if (previous.get('http_requests_already_charged') != 2
            or previous.get('attempted_pids') != 0
            or previous.get('retry_or_seed_collection') is not False):
        raise ValueError('v01 charged request lineage differs')
    if intent.get('diagnostics_policy') != {
        'request_record_fields': [
            'method', 'endpoint_path_without_query', 'status_code', 'safe_category',
            'response_content_type', 'sanitized_response_sha256'],
        'error_record_fields': [
            'stage', 'exception_type', 'safe_module_basename', 'function_name', 'line_number'],
        'account_response_body_saved': False,
        'search_album_response_body': 'only allowlisted album candidates; callback atomically persists before YTMusic parsing',
        'unknown_non_json_body_saved': False,
        'raw_request_url_headers_cookies_body_saved': False,
    }:
        raise ValueError('diagnostic minimization policy differs')
    if intent.get('ytmusicapi_version') != '1.12.3':
        raise ValueError('YTMusic client version differs from reviewed runtime')
    return library_hash


def run(intent, intent_bytes, expected_sha, auth_path, *, client_factory,
        session_factory, output=OUTPUT, repo=REPO):
    validate_intent(intent, intent_bytes, expected_sha, repo=repo)
    if output != OUTPUT or output.exists():
        raise ValueError('output namespace is not fresh')
    output.mkdir(mode=0o700, parents=False)
    search_receipt_path = output / 'search-first-page.json'

    def persist_search_receipt(record):
        _validate_safe_search_receipt(record)
        payload = {
            'schema_version': 1,
            'producer': 'opengenius-kirby-search-page-receipt-v1',
            'intent_sha256': expected_sha,
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'response': record,
        }
        _atomic_json(search_receipt_path, payload)

    limits = intent['limits']
    session = session_factory(limits['minimum_interval_seconds'],
                              limits['total_http_requests_max'],
                              search_response_callback=persist_search_receipt)
    report = {
        'schema_version': 1,
        'producer': 'opengenius-bounded-album-availability-probe-result-v2',
        'intent_sha256': expected_sha,
        'status': 'running',
        'account_authentication_verified': False,
        'http_requests': 0,
        'attempted_pids': 0,
        'search_page_receipt_saved_before_parser': False,
        'search_result_count': None,
        'exact_title_candidate_count': None,
        'discovery_only': True,
        'pid_bindings_created': 0,
        'recording_identities_created': 0,
        'outgoing_roots_created': 0,
        'recommendation_edges_created': 0,
    }
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
        stage = 'album_search_first_page'
        parsed = client.search(**intent['target']['search']['arguments'])
        if (session.count != 3 or dict(session.request_categories) != {
                'visitor_id_bootstrap': 1, 'account_preflight': 1, 'search_page': 1}):
            raise RuntimeError('search used an unexpected request sequence')
        report['search_result_count'] = len(parsed) if isinstance(parsed, list) else None
        if not search_receipt_path.is_file():
            raise RuntimeError('pre-parser search receipt was not durably saved')
        report['search_page_receipt_saved_before_parser'] = True
        search_doc = json.loads(search_receipt_path.read_text(encoding='utf-8'))
        candidates = search_doc['response']['sanitized_search_json']['album_candidates']
        report['exact_title_candidate_count'] = sum(
            normalize(row['title']) == normalize(LOCAL_ALBUM) for row in candidates)
        report['status'] = 'first_page_search_completed'
    except Exception as error:
        report['status'] = 'stopped_incomplete'
        report['failure_stage'] = stage
        report['failure'] = _safe_failure(error)
        if search_receipt_path.is_file():
            report['search_page_receipt_saved_before_parser'] = True
            search_doc = json.loads(search_receipt_path.read_text(encoding='utf-8'))
            candidates = search_doc['response']['sanitized_search_json']['album_candidates']
            report['exact_title_candidate_count'] = sum(
                normalize(row['title']) == normalize(LOCAL_ALBUM) for row in candidates)
    finally:
        report['http_requests'] = session.count if type(session.count) is int else None
        report['request_categories'] = dict(session.request_categories)
        report['request_events'] = list(session.request_events)
        if search_receipt_path.is_file():
            report['search_page_receipt_sha256'] = sha(search_receipt_path.read_bytes())
        if hasattr(session, 'close'):
            session.close()
        _atomic_json(output / 'summary.json', report)
    return report


def main():
    import argparse
    from ytmusicapi import YTMusic
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intent-sha256', required=True)
    args = parser.parse_args()
    if args.intent_sha256 != PINNED_INTENT_SHA256:
        parser.error('intent hash differs from frozen pin')
    intent_bytes = PLAN_PATH.read_bytes()
    intent = json.loads(intent_bytes)
    from contextlib import redirect_stderr, redirect_stdout
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        result = run(
            intent, intent_bytes, args.intent_sha256, str(AUTH_PATH),
            client_factory=lambda auth, session: YTMusic(
                auth, requests_session=session, language='en', location='JP'),
            session_factory=create_observed_paced_session)
    print(json.dumps({key: result.get(key) for key in (
        'status', 'http_requests', 'request_categories', 'account_authentication_verified',
        'search_page_receipt_saved_before_parser', 'search_result_count',
        'exact_title_candidate_count', 'attempted_pids', 'failure')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
