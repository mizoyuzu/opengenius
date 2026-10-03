"""Bounded, paced, cached authenticated YTMusic coverage experiment.

All recording identities remain unverified. No Apple databases are written.
"""
import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from pathlib import Path
import time
import traceback

from music_identity_map import IdentityMap, group_candidate_observations, load_library
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


def select_planned_seeds(plan, tracks, library_hash, count):
    """Select exact current Library records; plans contain labels, never metadata."""
    if (not isinstance(plan, dict) or type(plan.get('schema_version')) is not int
            or plan['schema_version'] != 1 or plan.get('source_library_sha256') != library_hash):
        raise ValueError('Seed plan schema or source Library hash differs')
    rows = plan.get('seeds')
    if not isinstance(rows, list) or not rows or len(rows) != count:
        raise ValueError('Seed plan must contain exactly --seeds records')
    by_pid = {track['persistent_id']: track for track in tracks}
    if len(by_pid) != len(tracks):
        raise ValueError('Duplicate Library persistent IDs')
    chosen, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'persistent_id', 'series', 'sample_kind'}:
            raise ValueError('Seed plan records permit only persistent_id, series and sample_kind')
        pid = row['persistent_id']
        if not isinstance(pid, str) or not re.fullmatch('[0-9A-F]{16}', pid) or pid in seen or pid not in by_pid:
            raise ValueError('Invalid, duplicate or unknown seed plan persistent ID')
        if any(not isinstance(row[field], str) or not row[field].strip() for field in ('series', 'sample_kind')):
            raise ValueError('Seed plan series and sample_kind must be nonempty strings')
        seen.add(pid)
        track = copy.deepcopy(by_pid[pid])
        track.update(seed_series=row['series'], seed_sample_kind=row['sample_kind'])
        chosen.append(track)
    return chosen


def select_observed_seeds(snapshot, matcher, count):
    """Reuse uniquely associated search video IDs; never turn search into edges."""
    rows = []
    for observation in snapshot['observations']:
        if observation.get('source') != 'ytmusic' or observation.get('relation') != 'search_candidate':
            raise ValueError('Seed evidence must contain only YTMusic search candidates')
        track = observation['track']
        video = track.get('video_id')
        if not isinstance(video, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
            raise ValueError('Invalid seed video ID')
        rows.append({'source': 'ytmusic', 'video_id': video, 'is_seed': False,
                     'exact_candidates': [], 'alias_candidates': matcher.match(track)})
    videos_by_pid = {}
    for group in group_candidate_observations(rows):
        pid = group['preferred_metadata_pid']
        if pid is not None and group['duration_supported_pids'] == [pid]:
            videos_by_pid.setdefault(pid, []).append(group['video_id'])
    tracks = {track['persistent_id']: track for track in matcher.tracks}
    buckets = {}
    for pid, videos in videos_by_pid.items():
        if len(videos) != 1:
            continue
        track = tracks[pid]
        # Avoid fragments, long loops and instrumental versions in this seed sample.
        if not 30000 <= (track.get('duration_ms') or 0) <= 900000:
            continue
        if any(word in normalize(track['title']) for word in ('off vocal', 'instrumental', 'カラオケ')):
            continue
        credits = tuple(sorted(matcher.artist_credits(track['artist'])))
        buckets.setdefault(credits, []).append({**track, 'seed_video_id': videos[0],
                                                'seed_identity_status': 'unique_metadata_candidate_unverified'})
    selected = []
    # Round-robin across canonical credits, then add further songs per credit.
    while buckets and len(selected) < count:
        for credits in sorted(list(buckets)):
            selected.append(buckets[credits].pop(0))
            if not buckets[credits]:
                del buckets[credits]
            if len(selected) == count:
                break
    return selected


def resolve_seed_search(found, seed, tracks, matcher):
    candidates = []
    for row in found:
        if not row.get('videoId'):
            continue
        pids = [candidate['persistent_id'] for candidate in matcher.match(row)] if matcher else local_candidates(row, tracks)
        if seed['persistent_id'] in pids:
            candidates.append(row)
    ids = {row['videoId'] for row in candidates}
    if len(ids) == 1:
        return next(iter(ids)), len(ids), 'unique_metadata_candidate_unverified'
    album = normalize(seed.get('album') or '')
    album_ids = {row['videoId'] for row in candidates
                 if album and normalize((row.get('album') or {}).get('name') or '') == album}
    if len(album_ids) == 1:
        return next(iter(album_ids)), len(ids), 'unique_album_metadata_preference_unverified'
    return None, len(ids), 'unresolved'


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


def create_paced_session(interval, request_budget):
    # Keep requests optional for local-only planning.
    import requests

    class PacedSession(requests.Session):
        def __init__(self):
            super().__init__()
            self.last_start, self.count = None, 0

        def request(self, *positional, **kwargs):
            if self.count >= request_budget:
                raise RuntimeError('Request budget exhausted')
            if self.last_start is not None:
                time.sleep(max(0, interval - (time.monotonic() - self.last_start)))
            self.last_start = time.monotonic()
            self.count += 1
            kwargs['timeout'] = 25
            # Redirect sends bypass Session.request(), including its pacing/budget.
            kwargs['allow_redirects'] = False
            response = super().request(*positional, **kwargs)
            if 300 <= response.status_code < 400:
                raise requests.HTTPError('Unexpected redirect response', response=response)
            response.raise_for_status()  # No retries, including auth/rate-limit failures.
            return response

    return PacedSession()


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
        'related_status': snapshot.get('related_status', 'not_recorded'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path, nargs='?')
    parser.add_argument('--track-snapshot', type=Path)
    parser.add_argument('--identity-map', type=Path)
    seed_source = parser.add_mutually_exclusive_group()
    seed_source.add_argument('--seed-observations', type=Path, help='Saved search observations; re-match seeds without new searches')
    seed_source.add_argument('--seed-plan', type=Path, help='Explicit Library PID plan; must contain exactly --seeds records')
    parser.add_argument('--auth', type=Path, default=Path('../browser.json'))
    parser.add_argument('--output', type=Path, required=True, help='Experiment directory; reuse to resume cached requests')
    parser.add_argument('--plan-only', action='store_true', help='Select local seeds without network access')
    parser.add_argument('--language', choices=('ja', 'en'), default='en')
    parser.add_argument('--seeds', type=int, default=10)
    parser.add_argument('--interval', type=float, default=5, help='Minimum seconds between HTTP request starts')
    parser.add_argument('--request-budget', type=int, default=45)
    args = parser.parse_args()
    output = args.output.resolve()
    if (args.bundle is None) == (args.track_snapshot is None):
        parser.error('Specify either bundle or --track-snapshot')
    if (args.bundle and output.is_relative_to(args.bundle.resolve())) or output == args.auth.resolve():
        parser.error('output must be outside the library and auth file')
    if (not 1 <= args.seeds <= 20 or not math.isfinite(args.interval)
            or args.interval < 2 or not 1 <= args.request_budget <= 100):
        parser.error('seeds: 1..20; interval: finite and >=2; request-budget: 1..100')
    if args.language != 'en' and not args.plan_only and not args.seed_observations:
        parser.error('Filtered songs search requires --language en with the current YTMusic adapter; localized shelf headings may be discarded')
    output.mkdir(parents=True, exist_ok=True)
    cache = output / 'requests'
    cache.mkdir(exist_ok=True)
    report = {'schema_version': 1, 'account_authentication_verified': False,
              'status': 'running', 'results': [], 'http_requests': 0,
              'minimum_request_interval_seconds': args.interval,
              'request_budget': args.request_budget, 'identity_status': 'unverified'}
    session = None
    try:
        tracks, library_hash, provenance = load_library(args.bundle, args.track_snapshot)
        matcher, map_hash = None, None
        if args.identity_map:
            map_bytes = args.identity_map.read_bytes()
            matcher = IdentityMap(json.loads(map_bytes), tracks, library_hash)
            map_hash = hashlib.sha256(map_bytes).hexdigest()
        seed_evidence_hash, seed_plan_hash = None, None
        if args.seed_plan:
            plan_bytes = args.seed_plan.read_bytes()
            seeds = select_planned_seeds(json.loads(plan_bytes), tracks, library_hash, args.seeds)
            seed_plan_hash = hashlib.sha256(plan_bytes).hexdigest()
            selection = 'explicit_diverse_library_seed_plan'
        elif args.seed_observations:
            if matcher is None:
                raise ValueError('Seed observations require an identity map')
            seed_bytes = args.seed_observations.read_bytes()
            seed_document = json.loads(seed_bytes)
            if seed_document.get('library_sha256') not in (None, library_hash):
                raise ValueError('Seed observations belong to another Library')
            seeds = select_observed_seeds(seed_document, matcher, args.seeds)
            seed_evidence_hash = hashlib.sha256(seed_bytes).hexdigest()
            selection = 'unique_search_video_round_robin_canonical_credits'
        else:
            seeds = select_seeds(tracks, args.seeds)
            selection = 'distinct_exact_artist_and_album_labels'
        if not seeds:
            raise ValueError('No usable seeds')
        manifest = {'library_sha256': library_hash, 'library_input': provenance,
                    'identity_map_sha256': map_hash,
                    'language': args.language, 'seeds': seeds,
                    'seed_evidence_sha256': seed_evidence_hash,
                    'selection': selection}
        if args.seed_plan:
            manifest['seed_plan_sha256'] = seed_plan_hash
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
        from ytmusicapi import YTMusic

        session = create_paced_session(args.interval, args.request_budget)
        client = YTMusic(str(args.auth), requests_session=session, language=args.language, location='JP')
        report['account_authentication_verified'] = verify_account(client)
        print('Authenticated account response confirmed; no account identity stored.', flush=True)
        cached = CachedClient(client, cache)
        for seed in seeds:
            path = output / (seed['persistent_id'] + '.json')
            if path.exists():
                snapshot = json.loads(path.read_text())
            else:
                cached.evidence = []
                if seed.get('seed_video_id'):
                    ids = {seed['seed_video_id']}
                    candidate_count, seed_status = 1, 'unique_metadata_candidate_unverified'
                else:
                    found = cached.search(query=seed['title'] + ' ' + seed['artist'], filter='songs', limit=5)
                    selected_video, candidate_count, seed_status = resolve_seed_search(found, seed, tracks, matcher)
                    if selected_video is None:
                        report['results'].append({'persistent_id': seed['persistent_id'],
                                                  'status': 'search_unresolved', 'candidate_video_ids': candidate_count})
                        print(json.dumps({'seed': seed['persistent_id'], 'status': 'search_unresolved',
                                          'candidate_video_ids': candidate_count}), flush=True)
                        continue
                    ids = {selected_video}
                snapshot = collect(cached, seed, tracks, next(iter(ids)), 25,
                                   candidate_matcher=matcher.match if matcher else None)
                snapshot['library_sha256'] = library_hash
                snapshot['library_input'] = provenance
                snapshot['identity_map_sha256'] = map_hash
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
                snapshot['seed_identity_status'] = seed_status
                snapshot['seed_search_candidate_video_count'] = candidate_count
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
        frames = traceback.extract_tb(error.__traceback__)
        if frames:
            last = frames[-1]
            report['error_location'] = {'file': Path(last.filename).name,
                                        'function': last.name, 'line': last.lineno}
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        if type(status) is int and 100 <= status <= 599:
            report['http_error_status'] = status
        print('Stopped (' + type(error).__name__ +
              (' HTTP ' + str(status) if 'http_error_status' in report else '') +
              '); no automatic retry.', flush=True)
    finally:
        report['http_requests'] = session.count if session else 0
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        # Separate run summaries retain previous observations on resumed runs.
        save(output / ('summary-' + str(time.time_ns()) + '.json'), report)
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
