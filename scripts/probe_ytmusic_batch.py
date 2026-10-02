"""Bounded, paced, cached authenticated YTMusic coverage experiment.

All recording identities remain unverified. No Apple databases are written.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from inspect_music_library import decode_musicdb, parse_tracks
from probe_ytmusic import collect, local_candidates, normalize


SAFE_KEYS = {'videoId', 'title', 'artists', 'name', 'id', 'album', 'length',
             'duration_seconds', 'videoType', 'related', 'tracks', 'contents'}


def sanitize(value):
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if k in SAFE_KEYS}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def save(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def select_seeds(tracks, count):
    # Favor represented artists, one seed per exact artist and album label.
    frequencies = Counter(t.get('artist') for t in tracks)
    ordered = sorted(tracks, key=lambda t: (-frequencies[t.get('artist')],
                                           t.get('artist') or '', t['persistent_id']))
    selected, artists, albums = [], set(), set()
    for track in ordered:
        artist, album = track.get('artist'), track.get('album')
        if not artist or not album or not track.get('title') or not track.get('duration_ms'):
            continue
        title = normalize(track['title'])
        if any(word in title for word in ('off vocal', 'instrumental', 'カラオケ')):
            continue
        if artist in artists or album in albums:
            continue
        selected.append(track)
        artists.add(artist)
        albums.add(album)
        if len(selected) == count:
            break
    return selected


class AuthenticationUnconfirmed(Exception):
    pass


def verify_account(client):
    try:
        account = client.get_account_info()
    except KeyError:
        raise AuthenticationUnconfirmed() from None
    if not account.get('accountName'):
        raise AuthenticationUnconfirmed()
    # Account values deliberately do not leave this function.
    return True


class CachedClient:
    def __init__(self, client, directory):
        self.client, self.directory = client, directory
        self.cache_hits = 0
        self.evidence = []

    def call(self, endpoint, kwargs):
        encoded = json.dumps([endpoint, kwargs], sort_keys=True, ensure_ascii=False).encode()
        path = self.directory / (hashlib.sha256(encoded).hexdigest() + '.json')
        if path.exists():
            self.cache_hits += 1
            stored = json.loads(path.read_text())
            self.evidence.append({k: stored[k] for k in ('endpoint', 'arguments', 'observed_at')} | {'cache_hit': True})
            return stored['response']
        response = sanitize(getattr(self.client, endpoint)(**kwargs))
        evidence = {'endpoint': endpoint, 'arguments': kwargs,
                    'observed_at': datetime.now(timezone.utc).isoformat()}
        save(path, {**evidence, 'response': response})
        self.evidence.append({**evidence, 'cache_hit': False})
        return response

    def search(self, **kwargs):
        return self.call('search', kwargs)

    def get_watch_playlist(self, **kwargs):
        return self.call('get_watch_playlist', kwargs)

    def get_song_related(self, browse_id):
        return self.call('get_song_related', {'browseId': browse_id})


def summary(snapshot):
    rows = [o for o in snapshot['observations'] if not o['is_seed']]
    matched = [o for o in rows if o['local_metadata_candidates']]
    return {
        'persistent_id': snapshot['local_seed']['persistent_id'],
        'observations': len(rows),
        'unique_video_ids': len({o['track']['video_id'] for o in rows}),
        'candidate_video_ids': len({o['track']['video_id'] for o in matched}),
        'candidate_local_ids': sorted({p for o in matched for p in o['local_metadata_candidates']}),
        'ambiguous_observations': sum(len(o['local_metadata_candidates']) > 1 for o in matched),
        'verified_recordings': 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--auth', type=Path, default=Path('../browser.json'))
    parser.add_argument('--output', type=Path, required=True, help='Experiment directory; reuse to resume cached requests')
    parser.add_argument('--plan-only', action='store_true', help='Select local seeds without network access')
    parser.add_argument('--seeds', type=int, default=10)
    parser.add_argument('--interval', type=float, default=5, help='Minimum seconds between HTTP request starts')
    parser.add_argument('--request-budget', type=int, default=45)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(args.bundle.resolve()) or output == args.auth.resolve():
        parser.error('output must be outside the library and auth file')
    if not 1 <= args.seeds <= 20 or args.interval < 2 or not 1 <= args.request_budget <= 100:
        parser.error('seeds: 1..20; interval: >=2; request-budget: 1..100')
    output.mkdir(parents=True, exist_ok=True)
    cache = output / 'requests'
    cache.mkdir(exist_ok=True)
    report = {'schema_version': 1, 'account_authentication_verified': False,
              'status': 'running', 'results': [], 'http_requests': 0,
              'minimum_request_interval_seconds': args.interval,
              'request_budget': args.request_budget, 'identity_status': 'unverified'}
    session = None
    try:
        tracks, _ = parse_tracks(decode_musicdb((args.bundle / 'Library.musicdb').read_bytes()))
        seeds = select_seeds(tracks, args.seeds)
        manifest = {'library_sha256': hashlib.sha256((args.bundle / 'Library.musicdb').read_bytes()).hexdigest(),
                    'seeds': seeds, 'selection': 'distinct_exact_artist_and_album_labels'}
        manifest_path = output / 'seeds.json'
        if manifest_path.exists():
            if json.loads(manifest_path.read_text()) != manifest:
                raise ValueError('Existing experiment seed manifest differs')
        else:
            save(manifest_path, manifest)
        if args.plan_only:
            report['status'] = 'planned'
            report['selected_seed_count'] = len(seeds)
            print(json.dumps({'status': 'planned', 'seeds': len(seeds), 'http_requests': 0}))
            return 0
        import requests
        from ytmusicapi import YTMusic

        class PacedSession(requests.Session):
            def __init__(self):
                super().__init__()
                self.last_start, self.count = None, 0

            def request(self, *positional, **kwargs):
                if self.count >= args.request_budget:
                    raise RuntimeError('Request budget exhausted')
                if self.last_start is not None:
                    time.sleep(max(0, args.interval - (time.monotonic() - self.last_start)))
                self.last_start = time.monotonic()
                self.count += 1
                kwargs['timeout'] = 25
                response = super().request(*positional, **kwargs)
                response.raise_for_status()  # No retries, including auth/rate-limit failures.
                return response

        session = PacedSession()
        client = YTMusic(str(args.auth), requests_session=session, language='en', location='JP')
        report['account_authentication_verified'] = verify_account(client)
        print('Authenticated account response confirmed; no account identity stored.', flush=True)
        cached = CachedClient(client, cache)
        for seed in seeds:
            path = output / (seed['persistent_id'] + '.json')
            if path.exists():
                snapshot = json.loads(path.read_text())
            else:
                cached.evidence = []
                found = cached.search(query=seed['title'] + ' ' + seed['artist'], filter='songs', limit=5)
                candidates = [r for r in found if r.get('videoId') and
                              seed['persistent_id'] in local_candidates(r, tracks)]
                ids = {r['videoId'] for r in candidates}
                if len(ids) != 1:
                    report['results'].append({'persistent_id': seed['persistent_id'],
                                              'status': 'search_unresolved', 'candidate_video_ids': len(ids)})
                    print(json.dumps({'seed': seed['persistent_id'], 'status': 'search_unresolved',
                                      'candidate_video_ids': len(ids)}), flush=True)
                    continue
                snapshot = collect(cached, seed, tracks, next(iter(ids)), 25)
                snapshot['request_evidence'] = cached.evidence.copy()
                # Preserve the original observation times when responses came from cache.
                for evidence in cached.evidence:
                    endpoint, parameters = evidence['endpoint'], evidence['arguments']
                    if endpoint == 'get_watch_playlist':
                        logical_endpoint = 'watch_radio' if parameters.get('radio') else 'watch_seed'
                        relation = 'radio' if parameters.get('radio') else None
                    elif endpoint == 'get_song_related':
                        logical_endpoint, relation = 'song_related', 'related'
                    else:
                        continue
                    for request in snapshot['requests']:
                        if request['endpoint'] == logical_endpoint:
                            request['observed_at'] = evidence['observed_at']
                    if relation:
                        for observation in snapshot['observations']:
                            if observation['relation'] == relation:
                                observation['observed_at'] = evidence['observed_at']
                snapshot['seed_identity_status'] = 'unique_metadata_candidate_unverified'
                snapshot['account_authentication_verified'] = True
                save(path, snapshot)
            row = {'status': 'collected', **summary(snapshot)}
            report['results'].append(row)
            print(json.dumps(row), flush=True)
        report['status'] = 'completed'
        report['cache_hits'] = cached.cache_hits
    except Exception as error:
        report['status'] = 'stopped'
        report['error_type'] = type(error).__name__  # Never store exception text/headers.
        print('Stopped (' + type(error).__name__ + '); no automatic retry.', flush=True)
    finally:
        report['http_requests'] = session.count if session else 0
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        # Separate run summaries retain previous observations on resumed runs.
        save(output / ('summary-' + str(time.time_ns()) + '.json'), report)
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
