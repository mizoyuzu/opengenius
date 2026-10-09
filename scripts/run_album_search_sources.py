"""Run a bounded authenticated album search plan and save sanitized evidence."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import os

from music_identity_map import IdentityMap, load_library
from probe_ytmusic_batch import create_paced_session, verify_account
from search_unindexed_album_sources import (build_album_search_evidence,
    sanitize_album_search_response, validate_album_search_plan)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write_new(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def run_album_searches(*, plan_path, track_snapshot, identity_map_path, state_path,
                       auth_path, output_dir, interval=2, request_budget=21,
                       deadline='2026-10-09T13:30:00+09:00',
                       client_factory=None, session_factory=None):
    if type(interval) not in (int, float) or not math.isfinite(interval) or interval < 2:
        raise ValueError('interval must be finite and at least 2 seconds')
    plan_path, output_dir = Path(plan_path).resolve(), Path(output_dir).resolve()
    auth_path = Path(auth_path).resolve()
    if output_dir.exists() or output_dir == auth_path or output_dir.is_relative_to(auth_path):
        raise ValueError('Output must be new and separate from auth material')
    tracks, library_hash, _ = load_library(track_snapshot=Path(track_snapshot))
    map_bytes = Path(identity_map_path).read_bytes()
    map_hash = _sha(map_bytes)
    matcher = IdentityMap(json.loads(map_bytes), tracks, library_hash)
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    validate_album_search_plan(plan, library_hash, map_hash)
    state = json.loads(Path(state_path).read_text(encoding='utf-8'))
    if state.get('state_sha256') != plan.get('state_sha256'):
        raise ValueError('Album search plan index state is stale')
    if len(plan['albums']) > 20 or request_budget < len(plan['albums']) + 1:
        raise ValueError('Request budget must cover account verification and every planned query')
    if session_factory is None:
        session_factory = create_paced_session
    if client_factory is None:
        from ytmusicapi import YTMusic
        client_factory = YTMusic
    output_dir.mkdir(parents=True, mode=0o700)
    response_dir = output_dir / 'responses'
    response_dir.mkdir(mode=0o700)
    session = session_factory(interval, request_budget, deadline=deadline)
    report = {'schema_version': 1, 'status': 'running',
        'producer': 'opengenius-paced-album-search-run-v1',
        'plan_sha256': _sha(plan_bytes), 'library_sha256': library_hash,
        'identity_map_sha256': map_hash, 'state_sha256': state['state_sha256'],
        'account_authentication_verified': False, 'minimum_request_interval_seconds': interval,
        'request_budget': request_budget, 'deadline': deadline, 'http_requests': 0,
        'results': [], 'recommendation_edges_created': 0}
    try:
        # The client SDK may print account/request diagnostics. Keep all such
        # output private while still reporting only a boolean auth result.
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            client = client_factory(str(auth_path), requests_session=session,
                                    language='en', location='JP')
            report['account_authentication_verified'] = verify_account(client)
        responses = []
        for album in plan['albums']:
            request = {'query': album['query'], 'filter': 'albums', 'limit': 5}
            try:
                raw_response = client.search(**request)
                safe_response = sanitize_album_search_response(raw_response)
                envelope = {'endpoint': 'search', 'arguments': request,
                    'observed_at': datetime.now(timezone.utc).isoformat(),
                    'response': safe_response}
                path = response_dir / ('search-%02d.json' % album['query_ordinal'])
                _write_new(path, envelope)
                responses.append({'query': album['query'], 'path': str(path),
                    'sha256': _sha(path.read_bytes()), 'response': safe_response})
                report['results'].append({'query_ordinal': album['query_ordinal'],
                    'response_file': str(path), 'result_count': len(safe_response)})
            except Exception as error:
                report['status'] = 'stopped'
                report['error_type'] = type(error).__name__
                break
        report['http_requests'] = getattr(session, 'count', None)
        evidence = build_album_search_evidence(plan, responses, tracks, matcher,
                                               library_hash, map_hash)
        evidence['search_plan_sha256'] = report['plan_sha256']
        _write_new(output_dir / 'album-search-evidence.json', evidence)
        report.update(status='completed' if report['status'] == 'running' else report['status'],
            saved_search_response_count=len(responses),
            candidate_count=evidence['audit']['candidate_count'],
            evidence_path=str(output_dir / 'album-search-evidence.json'),
            evidence_sha256=_sha((output_dir / 'album-search-evidence.json').read_bytes()))
        _write_new(output_dir / 'summary.json', report)
        return report
    except Exception as error:
        report['status'] = 'stopped'
        report['error_type'] = type(error).__name__
        report['http_requests'] = getattr(session, 'count', None)
        _write_new(output_dir / 'summary.json', report)
        return report
    finally:
        session.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--auth', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--budget', type=int, default=21)
    parser.add_argument('--deadline', default='2026-10-09T13:30:00+09:00')
    args = parser.parse_args(argv)
    try:
        result = run_album_searches(plan_path=args.plan,
            track_snapshot=args.track_snapshot, identity_map_path=args.identity_map,
            state_path=args.state, auth_path=args.auth, output_dir=args.output,
            interval=args.interval, request_budget=args.budget, deadline=args.deadline)
    except Exception as error:
        parser.exit(1, 'Album search stopped (' + type(error).__name__ + '). No retry.\n')
    print(json.dumps({key: result.get(key) for key in
        ('status', 'http_requests', 'saved_search_response_count', 'candidate_count',
         'account_authentication_verified', 'error_type')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
