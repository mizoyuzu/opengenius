"""Fetch three previously observed albums, after the active radio batch ends.

This collects public catalog metadata only; it creates no PID bindings or roots.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from discover_album_catalog_seeds import _safe_catalog
from probe_ytmusic_batch import verify_account
from probe_ytmusic_session_observer_v02 import create_observed_paced_session, _request_parts

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261014-v14-source-union-live'
PLAN = ROOT / 'data/ytmusic/next-fresh-album-offline-plan-20261014-v01/report.json'
OUTPUT = ROOT / 'data/ytmusic/next-three-album-metadata-20261014-v02'
AUTH = ROOT / 'data/ytmusic/firefox-nightly-auth-20261004-r5.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def request_allowed(args, kwargs, active_album, seen):
    method, path, url, body = _request_parts(args, kwargs)
    if urlsplit(url).hostname != 'music.youtube.com':
        raise ValueError('Unexpected metadata host')
    if method == 'GET' and path == '/' and active_album is None:
        category = 'bootstrap'
    elif method == 'POST' and path == '/youtubei/v1/account/account_menu' and active_album is None:
        category = 'account'
    elif (method == 'POST' and path == '/youtubei/v1/browse'
          and isinstance(body, dict) and body.get('browseId') == active_album
          and active_album is not None and 'continuation' not in body):
        category = active_album
    else:
        raise ValueError('Unexpected metadata endpoint or album')
    if category in seen:
        raise ValueError('Repeated metadata request blocked')
    seen.add(category)


def run(client_factory=None):
    terminal = json.loads((CAMPAIGN / 'live-terminal-status-v03.json').read_text())
    # Catalog reads need a completed collector, not successful graph export replay.
    collector = CAMPAIGN / 'batch-0001/collector/summary-1791958653598174840.json'
    if sha(collector) != '4a6098db39ed77b2430731b82bc99ce280e24fc068730ea984324ffeb8c6d626':
        raise ValueError('Completed collector receipt changed')
    summary = json.loads(collector.read_text())
    proof_check = json.loads((CAMPAIGN / 'batch-0001/independent-new-radio-proof-check-v01.json').read_text())
    if (summary.get('status') != 'completed' or summary.get('account_authentication_verified') is not True
            or summary.get('http_requests') != 44 or terminal.get('status') not in {'completed', 'failed'}
            or proof_check.get('status') != 'pass' or proof_check.get('verified_proofs') != 14):
        raise ValueError('Radio collection must terminate and its source proofs must pass first')
    for row in proof_check['proofs']:
        if sha(row['proof_path']) != row['proof_sha256']:
            raise ValueError('Verified radio proof changed')
    plan = json.loads(PLAN.read_text())
    targets = plan['positive_exact_title_metadata_targets']
    if len(targets) != 3 or sum(t['fresh_unattempted_tracks'] for t in targets) != 80:
        raise ValueError('Unexpected metadata cohort')
    expected_ids = ['MPREb_U5HNLV2Dxkg', 'MPREb_1U8rYU18vqX', 'MPREb_CuBEZFL2nUZ']
    for target, album_id in zip(targets, expected_ids):
        receipt = target['search_receipt']
        if sha(receipt['path']) != receipt['sha256'] or target['candidate_album_ids'] != [album_id]:
            raise ValueError('Discovery receipt changed')
    if OUTPUT.exists():
        raise FileExistsError('Metadata namespace already exists; no request repeated')
    OUTPUT.mkdir(mode=0o700)
    intent = {'producer': 'opengenius-three-album-metadata-intent-v1',
              'plan_sha256': sha(PLAN), 'prior_terminal_sha256': sha(CAMPAIGN / 'live-terminal-status-v03.json'),
              'album_ids': expected_ids, 'maximum_http_requests': 5,
              'interval_seconds': 3, 'retries': 0, 'roots_credited': 0}
    write(OUTPUT / 'intent.json', intent)
    session = create_observed_paced_session(3, 5)
    original = session.request
    state = {'active_album': None, 'seen': set()}

    def guarded(*args, **kwargs):
        request_allowed(args, kwargs, state['active_album'], state['seen'])
        return original(*args, **kwargs)

    session.request = guarded
    report = {'producer': 'opengenius-three-album-metadata-result-v1',
              'status': 'running', 'roots_credited': 0, 'attempted_pids': 0,
              'recommendation_edges_created': 0, 'albums': []}
    try:
        if client_factory is None:
            from ytmusicapi import YTMusic
            client_factory = lambda: YTMusic(str(AUTH), requests_session=session, language='en', location='JP')
        client = client_factory()
        if not verify_account(client) or session.count != 2:
            raise ValueError('Unexpected authenticated startup sequence')
        for target, album_id in zip(targets, expected_ids):
            state['active_album'] = album_id
            result = _safe_catalog(client.get_album(album_id))
            path = OUTPUT / (album_id + '.json')
            write(path, {'endpoint': 'get_album', 'arguments': {'browseId': album_id},
                         'observed_at': datetime.now(timezone.utc).isoformat(),
                         'response': result})
            report['albums'].append({'album_id': album_id, 'path': str(path), 'sha256': sha(path),
                                     'track_count': len(result.get('tracks', [])),
                                     'local_album_title': target['album_title']})
        report['status'] = 'completed'
    except Exception as error:
        report['status'] = 'failed'
        report['error_type'] = type(error).__name__
        raise
    finally:
        report['http_requests'] = session.count
        report['request_events'] = session.request_events
        write(OUTPUT / 'summary.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.execute:
        # Parser/auth exceptions may contain sensitive request details.
        try:
            result = run()
            print(json.dumps({'status': result['status'], 'http_requests': result['http_requests']}))
        except Exception as error:
            print(json.dumps({'status': 'stopped', 'error_type': type(error).__name__}))
            raise SystemExit(1)
    else:
        print(json.dumps({'status': 'prepared_no_calls', 'albums': 3, 'max_http_requests': 5,
                          'requires': 'terminated radio campaign; completed authenticated collector and 14 verified source proofs'}))
