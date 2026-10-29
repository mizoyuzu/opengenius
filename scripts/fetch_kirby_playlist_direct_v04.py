"""Fetch one already-selected Kirby playlist as metadata, without another search."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from probe_ytmusic_session_observer_v02 import create_observed_paced_session  # noqa: E402

OUT = ROOT / 'data/ytmusic/post870-kirby-playlist-direct-20261015-v04'
V03 = ROOT / 'data/ytmusic/post870-kirby-playlist-discovery-20261015-v03'
STAGE1 = ROOT / 'data/ytmusic/post870-work-video-radio-20261015-v01/stage-0001'
PACKET = ROOT / 'data/ytmusic/post870-kirby-context-bindings-20261015-v01'
SNAPSHOT = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
AUTH = ROOT / 'data/ytmusic/firefox-nightly-auth-20261004-r5.json'
V03_CODE_SHA256 = 'c60a8b9e7ecee95be270b9ca12426a033f6fd6dae92c5d106c8cf84f65380102'
PLAYLIST_ID = 'VLPLlvbNxiPAuN9SigrXnphMm_xov4fJ5Xya'
PLAYLIST_TITLE = 'KIRBY STAR ALLIES: THE ORIGINAL SOUNDTRACK'
HTTP_BEFORE, HTTP_CAP, MAX_HTTP, INTERVAL = 5125, 6500, 10, 3


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def pinned_inputs():
    v03_script = ROOT / 'scripts/probe_kirby_playlist_discovery_v03.py'
    summary_path, intent_path = V03 / 'summary.json', V03 / 'intent.json'
    if sha(v03_script) != V03_CODE_SHA256:
        raise ValueError('v03 helper code changed')
    summary, intent = json.loads(summary_path.read_text()), json.loads(intent_path.read_text())
    if (summary.get('status') != 'stopped_metadata_only'
            or summary.get('account_authentication_verified') is not True
            or summary.get('http_requests') != 5
            or summary.get('cumulative_http') != HTTP_BEFORE
            or summary.get('root_credit') != 0 or summary.get('edge_credit') != 0
            or intent.get('campaign_http_before') != 5120):
        raise ValueError('v03 summary is not the expected metadata-only terminal')
    for path, expected in intent['input_pins'].items():
        if not Path(path).is_file() or sha(Path(path)) != expected:
            raise ValueError('a v03-pinned input changed')
    caches = summary.get('request_cache_files', [])
    if len(caches) != 1:
        raise ValueError('expected exactly one saved v03 search receipt')
    search_path = Path(caches[0]['path'])
    if not search_path.is_file() or sha(search_path) != caches[0]['sha256']:
        raise ValueError('v03 search cache changed')
    search_doc = json.loads(search_path.read_text())
    expected_args = {'query': PLAYLIST_TITLE, 'filter': 'playlists', 'limit': 20}
    if search_doc.get('endpoint') != 'search' or search_doc.get('arguments') != expected_args:
        raise ValueError('saved v03 search request differs')
    from probe_kirby_playlist_discovery_v03 import exact_playlist_rows
    rows = exact_playlist_rows(search_doc.get('response', []))
    if not any(row['response_position'] == 0 and row['playlist_id'] == PLAYLIST_ID
               for row in rows):
        raise ValueError('saved v03 search row 0 does not bind the selected playlist')
    return summary_path, intent_path, v03_script, search_path, search_doc


def run():
    if OUT.exists():
        raise FileExistsError('new direct-fetch namespace already exists')
    if HTTP_BEFORE + MAX_HTTP > HTTP_CAP:
        raise ValueError('direct-fetch budget exceeds campaign cap')
    summary_path, intent_path, v03_script, search_path, _ = pinned_inputs()
    from probe_kirby_playlist_discovery_v03 import load_binding_inputs
    stage_graph = STAGE1 / 'candidate-graphs.json'
    attempted_graph = json.loads(stage_graph.read_text())
    attempted = sorted({row['persistent_id'] for row in attempted_graph['results']})
    if len(attempted) != 44:
        raise ValueError('stage-1 attempted PID set differs')
    _, _, official, tracks, _ = load_binding_inputs(
        PACKET / 'candidate-bindings.json', PACKET / 'report.json',
        PACKET / 'hallab-official-page.html', SNAPSHOT, repo=ROOT)
    input_pins = {str(path.resolve()): sha(path) for path in (
        summary_path, intent_path, v03_script, search_path, stage_graph, SNAPSHOT,
        PACKET / 'candidate-bindings.json', PACKET / 'report.json',
        PACKET / 'hallab-official-page.html')}
    OUT.mkdir(mode=0o700)
    (OUT / 'requests').mkdir(mode=0o700)
    write_new(OUT / 'intent.json', {
        'schema_version': 1, 'producer': 'opengenius-kirby-direct-playlist-fetch-intent-v1',
        'playlist_endpoint': 'get_playlist', 'playlist_id': PLAYLIST_ID,
        'playlist_title': PLAYLIST_TITLE, 'playlist_limit': 250,
        'search_repeated': False, 'v03_summary_sha256': sha(summary_path),
        'v03_search_receipt_sha256': sha(search_path), 'campaign_http_before': HTTP_BEFORE,
        'maximum_http_including_auth_bootstrap': MAX_HTTP,
        'minimum_interval_seconds': INTERVAL, 'retries': 0, 'input_pins': input_pins,
        'stage1_attempted_seed_pids': attempted,
        'root_credit': 0, 'edge_credit': 0, 'source_credit': 0, 'database_written': False,
    })
    session = create_observed_paced_session(INTERVAL, MAX_HTTP)
    report = {'schema_version': 1,
              'producer': 'opengenius-kirby-direct-playlist-metadata-v1',
              'status': 'running', 'account_authentication_verified': False,
              'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
              'strict_fresh_pid_hints': [], 'database_written': False}
    cache = None
    try:
        from ytmusicapi import YTMusic
        from probe_ytmusic_batch import verify_account
        from probe_kirby_playlist_discovery_v03 import PlaylistMetadataCachedClient, bind_rows
        client = YTMusic(str(AUTH), requests_session=session, language='en', location='JP')
        report['account_authentication_verified'] = verify_account(client) is True
        if report['account_authentication_verified'] is not True:
            raise RuntimeError('account authentication was not verified')
        cache = PlaylistMetadataCachedClient(client, OUT / 'requests')
        playlist = cache.call('get_playlist', {'playlistId': PLAYLIST_ID, 'limit': 250})
        if not isinstance(playlist, dict) or not isinstance(playlist.get('tracks'), list):
            raise RuntimeError('get_playlist returned no track list')
        receipt_path = next((p for p in (OUT / 'requests').glob('*.json')
                             if json.loads(p.read_text()).get('endpoint') == 'get_playlist'), None)
        if receipt_path is None:
            raise RuntimeError('get_playlist cache receipt missing')
        receipt = json.loads(receipt_path.read_text())
        if receipt.get('arguments') != {'playlistId': PLAYLIST_ID, 'limit': 250}:
            raise RuntimeError('get_playlist receipt arguments differ')
        found, stats = bind_rows(playlist['tracks'], official, tracks)
        fresh = [row for row in found if row['persistent_id'] not in set(attempted)]
        report['playlist_request'] = {
            'endpoint': 'get_playlist', 'arguments': receipt['arguments'],
            'request_receipt_path': str(receipt_path.resolve()),
            'request_receipt_sha256': sha(receipt_path), 'source_endpoint_preserved': True,
            'returned_track_rows': len(playlist['tracks']), 'strict_bind_stats': stats}
        report['strict_fresh_pid_hints'] = [
            {'persistent_id': row['persistent_id'], 'video_id': row['video_id'],
             'disc_track': row['disc_track'], 'local_title': row['local_title'],
             'remote_title': row['remote_title'], 'remote_duration': row['remote_length'],
             'duration_delta_ms': row['duration_delta_ms'], 'policy': row['policy'],
             'recording_identity_verified': False, 'root_credit': 0, 'edge_credit': 0}
            for row in fresh]
        report['summary'] = {'playlist_track_rows': len(playlist['tracks']),
                             'strict_bindings': len(found), 'fresh_hints': len(fresh),
                             'attempted_pids_excluded': len(attempted), 'source_credit': 0}
        report['status'] = 'completed_metadata_hints_only'
    except Exception as error:
        report['status'] = 'stopped_metadata_only'
        frame = traceback.extract_tb(error.__traceback__)[-1]
        report['error'] = {'exception_type': type(error).__name__,
                           'location': {'file': Path(frame.filename).name,
                                        'function': frame.name, 'line': frame.lineno}}
    finally:
        report['http_requests'] = session.count
        report['http_request_categories'] = dict(sorted(session.request_categories.items()))
        report['request_events'] = session.request_events
        report['request_cache_hits'] = cache.cache_hits if cache else 0
        report['request_cache_files'] = [
            {'path': str(p.resolve()), 'sha256': sha(p), 'bytes': p.stat().st_size}
            for p in sorted((OUT / 'requests').glob('*.json'))]
        report['request_cache_manifest_sha256'] = hashlib.sha256(json.dumps(
            report['request_cache_files'], ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        report['cumulative_http'] = HTTP_BEFORE + session.count
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_new(OUT / 'summary.json', report)
    return report


if __name__ == '__main__':
    result = run()
    print(json.dumps({'status': result['status'], 'http_requests': result['http_requests'],
                      'cumulative_http': result['cumulative_http'],
                      'strict_fresh_pid_hints': result.get('summary', {}).get('fresh_hints', 0),
                      'root_credit': 0, 'edge_credit': 0, 'source_credit': 0}))
