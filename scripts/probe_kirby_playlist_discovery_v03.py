"""Bounded authenticated YTMusic playlist metadata discovery; no graph credit."""
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

from probe_ytmusic_batch import CachedClient, verify_account  # noqa: E402
from probe_ytmusic_session_observer_v02 import create_observed_paced_session  # noqa: E402
from work_track_video_bindings import bind_rows, load_binding_inputs, sha256  # noqa: E402

OUT = ROOT / 'data/ytmusic/post870-kirby-playlist-discovery-20261015-v03'
STAGE1 = ROOT / 'data/ytmusic/post870-work-video-radio-20261015-v01/stage-0001'
STAGE1_SUPERVISOR = ROOT / 'data/ytmusic/post870-work-video-radio-20261015-v01/stage-0001-supervisor-v02'
PACKET = ROOT / 'data/ytmusic/post870-kirby-context-bindings-20261015-v01'
SNAPSHOT = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
AUTH = ROOT / 'data/ytmusic/firefox-nightly-auth-20261004-r5.json'
STAGE1_CUMULATIVE_HTTP = 5115
CAMPAIGN_HTTP_BEFORE = 5120
CAMPAIGN_HTTP_CAP = 6500
MAX_HTTP = 5
INTERVAL = 3
WORK = 'KIRBY STAR ALLIES: THE ORIGINAL SOUNDTRACK'
QUERIES = [WORK, 'Kirby Star Allies Eternal']

# Playlist metadata needs a few fields that the general-purpose collector
# intentionally drops. Keep this allowlist local to this metadata-only probe.
PLAYLIST_METADATA_KEYS = {
    'videoId', 'title', 'artists', 'name', 'id', 'album', 'length',
    'duration_seconds', 'duration', 'videoType', 'related', 'tracks',
    'contents', 'browseId', 'playlistId', 'author', 'authors',
    'itemCount', 'videoCount', 'trackCount', 'count', 'resultType',
    'year', 'category', 'privacy', 'thumbnails',
}


def sanitize_playlist_metadata(value):
    """Retain only playlist lookup and public track metadata fields."""
    if isinstance(value, dict):
        return {key: sanitize_playlist_metadata(child)
                for key, child in value.items() if key in PLAYLIST_METADATA_KEYS}
    if isinstance(value, list):
        return [sanitize_playlist_metadata(child) for child in value]
    return value


class PlaylistMetadataCachedClient(CachedClient):
    """CachedClient with a probe-local, bounded public metadata allowlist."""

    def call(self, endpoint, kwargs):
        encoded = json.dumps([endpoint, kwargs], sort_keys=True,
                             ensure_ascii=False).encode()
        path = self.directory / (hashlib.sha256(encoded).hexdigest() + '.json')
        if path.exists():
            self.cache_hits += 1
            stored = json.loads(path.read_text())
            self.evidence.append({k: stored[k] for k in
                                  ('endpoint', 'arguments', 'observed_at')} |
                                 {'cache_hit': True})
            return stored['response']
        response = sanitize_playlist_metadata(getattr(self.client, endpoint)(**kwargs))
        evidence = {'endpoint': endpoint, 'arguments': kwargs,
                    'observed_at': datetime.now(timezone.utc).isoformat()}
        with path.open('x', encoding='utf-8') as stream:
            json.dump({**evidence, 'response': response}, stream,
                      ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.evidence.append({**evidence, 'cache_hit': False})
        return response


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get('text', ''))
    return ''


def playlist_identity(row: dict) -> tuple[str, str, str, int]:
    title = text(row.get('title'))
    browse_id = row.get('browseId') or row.get('playlistId')
    playlist_id = row.get('playlistId') or browse_id
    authors = row.get('authors') or row.get('author') or []
    if isinstance(authors, dict):
        authors = [authors]
    author_names = ' '.join(text(x.get('name') if isinstance(x, dict) else x)
                            for x in authors)
    count_value = row.get('videoCount') or row.get('itemCount') or row.get('count') or 0
    if isinstance(count_value, str):
        digits = ''.join(ch for ch in count_value if ch.isdigit())
        count_value = int(digits) if digits else 0
    if type(count_value) is not int:
        count_value = 0
    return str(title), str(playlist_id or ''), author_names, count_value


def exact_playlist_rows(search_rows):
    found = []
    for position, row in enumerate(search_rows):
        if not isinstance(row, dict):
            continue
        title, playlist_id, author, count = playlist_identity(row)
        if (WORK.casefold() in title.casefold() and playlist_id
                and ('playlist' in str(row.get('resultType', 'playlist')).casefold())):
            found.append({'response_position': position, 'title': title,
                          'playlist_id': playlist_id, 'author': author,
                          'video_count': count, 'source_row': row})
    return found


def run() -> dict:
    if OUT.exists():
        raise FileExistsError('playlist discovery namespace already exists; no repeated call')
    prior_v01 = ROOT / 'data/ytmusic/post870-kirby-playlist-discovery-20261015-v01/summary.json'
    prior_v02_dir = ROOT / 'data/ytmusic/post870-kirby-playlist-discovery-20261015-v02'
    prior_v02 = prior_v02_dir / 'summary.json'
    prior_v02_intent = prior_v02_dir / 'intent.json'
    if not all(path.is_file() for path in (prior_v01, prior_v02, prior_v02_intent)):
        raise ValueError('prior metadata-only discovery receipts are missing')
    prior1 = json.loads(prior_v01.read_text())
    prior2 = json.loads(prior_v02.read_text())
    if (prior1.get('status') != 'stopped_metadata_only'
            or prior1.get('http_requests') != 1
            or prior1.get('cumulative_http') != 5116
            or prior1.get('root_credit') != 0
            or prior1.get('edge_credit') != 0
            or prior2.get('status') != 'stopped_metadata_only'
            or prior2.get('http_requests') != 4
            or prior2.get('cumulative_http') != 5120
            or prior2.get('root_credit') != 0
            or prior2.get('edge_credit') != 0):
        raise ValueError('prior metadata-only HTTP ledger does not match the v03 budget')
    if CAMPAIGN_HTTP_BEFORE != prior2['cumulative_http']:
        raise ValueError('v03 cumulative starting counter differs from prior terminal')
    prior_v02_caches = []
    for item in prior2.get('request_cache_files', []):
        cache_path = Path(item['path'])
        if (not cache_path.is_file() or digest(cache_path) != item.get('sha256')
                or cache_path.stat().st_size != item.get('bytes')):
            raise ValueError('prior v02 request cache changed')
        prior_v02_caches.append({'path': str(cache_path.resolve()),
                                 'sha256': item['sha256'], 'bytes': item['bytes']})
    if len(prior_v02_caches) != 2:
        raise ValueError('expected the two sealed v02 metadata search receipts')
    terminal_path = STAGE1_SUPERVISOR / 'terminal.json'
    graph_path = STAGE1 / 'candidate-graphs.json'
    process_path = STAGE1 / 'process.json'
    for path in (terminal_path, graph_path, process_path):
        if not path.is_file():
            raise ValueError('stage-1 terminal/graph/process receipt missing')
    stage1_terminal = json.loads(terminal_path.read_text())
    stage1_graph = json.loads(graph_path.read_text())
    stage1_process = json.loads(process_path.read_text())
    if (stage1_terminal.get('status') != 'completed_candidates_only'
            or stage1_terminal.get('http_requests') != 178
            or stage1_terminal.get('cumulative_http') != STAGE1_CUMULATIVE_HTTP
            or stage1_process.get('status') != 'completed'
            or stage1_graph.get('account_authentication_verified') is not True
            or len(stage1_graph.get('results', [])) != 44):
        raise ValueError('unexpected stage-1 terminal or request ledger')
    if CAMPAIGN_HTTP_BEFORE + MAX_HTTP > CAMPAIGN_HTTP_CAP:
        raise ValueError('metadata discovery allowance exceeds campaign budget')

    bindings, binding_report, official, tracks, source_inputs = load_binding_inputs(
        PACKET / 'candidate-bindings.json', PACKET / 'report.json',
        PACKET / 'hallab-official-page.html', SNAPSHOT, repo=ROOT)
    del bindings, binding_report
    attempted_pids = sorted({row['persistent_id'] for row in stage1_graph['results']})
    input_pins = {
        str(prior_v01.resolve()): digest(prior_v01),
        str(prior_v02.resolve()): digest(prior_v02),
        str(prior_v02_intent.resolve()): digest(prior_v02_intent),
        str(terminal_path.resolve()): digest(terminal_path),
        str(graph_path.resolve()): digest(graph_path),
        str(process_path.resolve()): digest(process_path),
        str((STAGE1 / 'progress.json').resolve()): digest(STAGE1 / 'progress.json'),
        str((PACKET / 'candidate-bindings.json').resolve()): digest(PACKET / 'candidate-bindings.json'),
        str((PACKET / 'report.json').resolve()): digest(PACKET / 'report.json'),
        str((PACKET / 'hallab-official-page.html').resolve()): digest(PACKET / 'hallab-official-page.html'),
        str(SNAPSHOT.resolve()): digest(SNAPSHOT),
        str(Path(__file__).resolve()): digest(Path(__file__).resolve()),
    }
    for item in prior_v02_caches:
        input_pins[item['path']] = item['sha256']
    # Every prior radio cache is pinned; the playlist search below is metadata-only.
    previous_radio_caches = []
    for row in stage1_graph['results']:
        key = row['watch_radio']['request_key']
        cache_path = STAGE1 / 'requests' / f'{key}.json'
        expected = row['watch_radio']['receipt_sha256']
        if not cache_path.is_file() or digest(cache_path) != expected:
            raise ValueError('stage-1 radio receipt changed')
        previous_radio_caches.append({'path': str(cache_path.resolve()), 'sha256': expected})
        input_pins[str(cache_path.resolve())] = expected

    OUT.mkdir(mode=0o700)
    (OUT / 'requests').mkdir(mode=0o700)
    intent = {
        'schema_version': 1,
        'producer': 'opengenius-kirby-public-playlist-metadata-discovery-intent-v1',
        'status': 'metadata_only_no_root_credit',
        'queries': [{'query': q, 'filter': 'playlists', 'limit': 20} for q in QUERIES],
        'query2_conditional': 'only if query1 has no full-work playlist candidate',
        'selected_playlist_policy': 'exact complete work label; prefer Eternal as listed curator/uploader; retrieve one best full OST playlist only',
        'expected_video_policy': 'preserve endpoint as playlist metadata; later candidate binding requires exact Eternal channel + disc-track + full OST label + duration gate',
        'stage1_terminal_sha256': digest(terminal_path),
        'stage1_candidate_graph_sha256': digest(graph_path),
        'stage1_http_actual': 178,
        'prior_metadata_discoveries': [
            {'path': str(prior_v01.resolve()), 'sha256': digest(prior_v01),
             'http_requests': 1, 'cumulative_http': 5116},
            {'path': str(prior_v02.resolve()), 'sha256': digest(prior_v02),
             'http_requests': 4, 'cumulative_http': 5120},
        ],
        'prior_v02_request_cache_receipts': prior_v02_caches,
        'campaign_http_before': CAMPAIGN_HTTP_BEFORE,
        'maximum_http_requests_total_including_auth_bootstrap': MAX_HTTP,
        'minimum_interval_seconds': INTERVAL,
        'retries': 0,
        'input_pins': input_pins,
        'prior_radio_cache_receipts': previous_radio_caches,
        'stage1_attempted_seed_pids': attempted_pids,
        'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
        'database_written': False,
    }
    write_new(OUT / 'intent.json', intent)
    session = create_observed_paced_session(INTERVAL, MAX_HTTP)
    report = {'schema_version': 1,
              'producer': 'opengenius-kirby-public-playlist-metadata-discovery-v1',
              'status': 'running', 'account_authentication_verified': False,
              'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
              'playlist_candidates': [], 'strict_fresh_pid_hints': [],
              'attempted_seed_pids_excluded': len(attempted_pids),
              'database_written': False}
    client_cache = None
    try:
        from ytmusicapi import YTMusic
        client = YTMusic(str(AUTH), requests_session=session,
                         language='en', location='JP')
        report['account_authentication_verified'] = verify_account(client) is True
        if report['account_authentication_verified'] is not True:
            raise RuntimeError('account authentication was not verified')
        client_cache = PlaylistMetadataCachedClient(client, OUT / 'requests')
        search_receipts = []
        chosen_rows = []
        used_queries = []
        for query in QUERIES:
            response = client_cache.search(query=query, filter='playlists', limit=20)
            used_queries.append(query)
            rows = response if isinstance(response, list) else []
            matches = exact_playlist_rows(rows)
            search_receipts.append({'query': query, 'result_count': len(rows),
                                    'exact_work_playlist_count': len(matches),
                                    'cache_key': client_cache.evidence[-1]})
            if matches:
                chosen_rows = matches
                break
        if not chosen_rows:
            raise RuntimeError('bounded searches returned no exact full-work playlist')
        chosen_rows.sort(key=lambda row: (
            'eternal' not in row['author'].casefold(), -row['video_count'],
            row['response_position']))
        chosen = chosen_rows[0]
        report['search_receipts'] = search_receipts
        report['queries_used'] = used_queries
        report['playlist_candidates'] = [
            {k: row[k] for k in ('response_position', 'title', 'playlist_id', 'author', 'video_count')}
            for row in chosen_rows]
        report['selected_playlist'] = {k: chosen[k] for k in
                                       ('response_position', 'title', 'playlist_id', 'author', 'video_count')}
        playlist = client_cache.call('get_playlist', {
            'playlistId': chosen['playlist_id'], 'limit': 250})
        if not isinstance(playlist, dict):
            raise RuntimeError('playlist response is not an object')
        tracks_in_playlist = playlist.get('tracks')
        if not isinstance(tracks_in_playlist, list):
            tracks_in_playlist = []
        playlist_request = client_cache.evidence[-1]
        playlist_raw_path = next((p for p in (OUT / 'requests').glob('*.json')
                                  if json.loads(p.read_text()).get('endpoint') == 'get_playlist'
                                  and json.loads(p.read_text()).get('arguments') ==
                                  {'playlistId': chosen['playlist_id'], 'limit': 250}), None)
        if playlist_raw_path is None:
            raise RuntimeError('saved playlist metadata request receipt is missing')
        playlist_raw = json.loads(playlist_raw_path.read_text())
        found, bind_stats = bind_rows(tracks_in_playlist, official, tracks)
        fresh = [row for row in found if row['persistent_id'] not in set(attempted_pids)]
        report['playlist_request'] = {
            'endpoint': 'get_playlist', 'arguments': playlist_raw['arguments'],
            'request_receipt_path': str(playlist_raw_path.resolve()),
            'request_receipt_sha256': digest(playlist_raw_path),
            'source_endpoint_preserved': True,
            'returned_track_rows': len(tracks_in_playlist),
            'strict_bind_stats': bind_stats,
        }
        report['strict_fresh_pid_hints'] = [
            {'persistent_id': row['persistent_id'], 'video_id': row['video_id'],
             'disc_track': row['disc_track'], 'local_title': row['local_title'],
             'remote_title': row['remote_title'], 'remote_duration': row['remote_length'],
             'duration_delta_ms': row['duration_delta_ms'],
             'policy': row['policy'], 'recording_identity_verified': False,
             'root_credit': 0, 'edge_credit': 0}
            for row in fresh]
        report['summary'] = {
            'playlist_track_rows': len(tracks_in_playlist),
            'strict_unique_bindings_before_attempt_exclusion': len(found),
            'strict_fresh_unique_pid_hints': len(fresh),
            'stage1_attempted_pids_excluded': len(attempted_pids),
            'source_or_graph_credit': 0,
        }
        report['status'] = 'completed_metadata_hints_only'
    except Exception as error:
        report['status'] = 'stopped_metadata_only'
        frames = traceback.extract_tb(error.__traceback__)
        frame = frames[-1] if frames else None
        report['error'] = {'exception_type': type(error).__name__,
                           'location': ({'file': Path(frame.filename).name,
                                         'function': frame.name, 'line': frame.lineno}
                                        if frame else None)}
    finally:
        report['http_requests'] = session.count
        report['http_request_categories'] = dict(sorted(session.request_categories.items()))
        report['request_events'] = session.request_events
        report['request_cache_hits'] = client_cache.cache_hits if client_cache else 0
        report['request_cache_files'] = []
        for path in sorted((OUT / 'requests').glob('*.json')):
            report['request_cache_files'].append({'path': str(path.resolve()),
                                                  'sha256': digest(path),
                                                  'bytes': path.stat().st_size})
        report['request_cache_manifest_sha256'] = hashlib.sha256(json.dumps(
            report['request_cache_files'], ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        report['cumulative_http'] = CAMPAIGN_HTTP_BEFORE + session.count
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        if session.count > MAX_HTTP or report['cumulative_http'] > CAMPAIGN_HTTP_CAP:
            report['status'] = 'stopped_budget_guard_failure'
        write_new(OUT / 'summary.json', report)
    return report


if __name__ == '__main__':
    result = run()
    print(json.dumps({'status': result['status'], 'http_requests': result['http_requests'],
                      'cumulative_http': result['cumulative_http'],
                      'strict_fresh_pid_hints': result.get('summary', {}).get('strict_fresh_unique_pid_hints', 0),
                      'root_credit': 0, 'edge_credit': 0, 'source_credit': 0}, ensure_ascii=False))
