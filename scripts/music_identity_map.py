"""Library-scoped metadata aliases and offline YTMusic candidate matching.

Aliases aid candidate generation; they never establish recording identity.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re

from inspect_music_library import decode_musicdb, parse_tracks
from probe_ytmusic import local_candidates, normalize


def proposed_title_alias(title):
    # Suggestions only: hyphen suffixes can contain essential version information.
    prefix, separator, suffix = title.rpartition(' - ')
    if separator and re.search('[\u3040-\u30ff\u3400-\u9fff]', prefix) and re.search('[A-Za-z]', suffix):
        return {'value': prefix, 'enabled': False,
                'status': 'needs_review', 'reason': 'possible_translation_suffix'}
    return None


def load_library(bundle=None, track_snapshot=None):
    if (bundle is None) == (track_snapshot is None):
        raise ValueError('Specify exactly one Library bundle or track snapshot')
    if bundle is not None:
        data = (bundle / 'Library.musicdb').read_bytes()
        tracks, _ = parse_tracks(decode_musicdb(data))
        return tracks, hashlib.sha256(data).hexdigest(), {'kind': 'library_binary', 'original_library_hash_verified_this_run': True}
    data = track_snapshot.read_bytes()
    document = json.loads(data)
    library_hash = document.get('source_sha256')
    if not isinstance(library_hash, str) or not re.fullmatch('[0-9a-f]{64}', library_hash):
        raise ValueError('Snapshot needs its original Library SHA-256')
    tracks = document.get('tracks')
    if not isinstance(tracks, list) or not tracks:
        raise ValueError('Snapshot needs local tracks')
    seen = set()
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError('Invalid snapshot track')
        pid = track.get('persistent_id')
        if not isinstance(pid, str) or not re.fullmatch('[0-9A-F]{16}', pid) or pid in seen:
            raise ValueError('Invalid or duplicate snapshot persistent ID')
        seen.add(pid)
        for field in ('title', 'artist', 'album'):
            if field in track and not isinstance(track[field], str):
                raise ValueError('Invalid snapshot text metadata')
        duration = track.get('duration_ms')
        if duration is not None and (type(duration) is not int or duration < 0):
            raise ValueError('Invalid snapshot duration')
    return tracks, library_hash, {'kind': 'track_snapshot',
                                 'snapshot_sha256': hashlib.sha256(data).hexdigest(),
                                 'original_library_hash_verified_this_run': False}


def version_signature(title):
    """Conservative guard for common versions; does not prove equivalence."""
    title = normalize(title).replace('_', ' ')
    markers = {
        'instrumental': r'off[ _-]?vocal|\binstrument(?:al)?\b|カラオケ|からおけ',
        'game_size': r'game[ _-]?size',
        'tv_size': r'tv[ _-]?size',
        'live': r'\blive\b|ライブ',
        'remix': r'\bremix\b|\brmx\b|リミックス',
        'piano': r'\bpiano\b|ピアノ',
        'orchestral': r'\borchestr(?:al|a)\b|オーケストラ',
    }
    flags = {name for name, pattern in markers.items() if re.search(pattern, title)}
    flags.update('year:' + year for year in re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', title))
    return flags


def build_map(tracks, library_hash, artist_evidence=()):
    names = {normalize(t.get('artist') or '') for t in tracks}
    groups = []
    for row in artist_evidence:
        aliases = [row['from'], row['to']]
        if not any(normalize(a) in names for a in aliases):
            continue
        groups.append({'aliases': aliases, 'enabled': True,
                       'status': 'metadata_alias_candidate',
                       'evidence': {'method': 'cross_library_exact_title_album_duration_within_1s',
                                    'matching_record_count': row['matching_title_album_duration_records']}})
    title_rows = []
    for track in tracks:
        alias = proposed_title_alias(track.get('title') or '')
        if alias:
            title_rows.append({'persistent_id': track['persistent_id'],
                               'original_title': track['title'], 'aliases': [alias]})
    return {'schema_version': 1, 'library_sha256': library_hash,
            'artist_alias_groups': groups, 'track_title_aliases': title_rows,
            'recording_links': [], 'identity_status': 'unverified'}


def apply_title_review(document, decisions, tracks, library_hash):
    """Apply explicit spelling decisions; no recording identity is confirmed."""
    IdentityMap(document, tracks, library_hash)
    if decisions.get('schema_version') != 1 or decisions.get('library_sha256') != library_hash:
        raise ValueError('Review decisions do not match this Library')
    result = copy.deepcopy(document)
    by_pid = {track['persistent_id']: track for track in tracks}
    rows = {row['persistent_id']: row for row in result['track_title_aliases']}
    seen = set()
    for decision in decisions['decisions']:
        pid = decision['persistent_id']
        if pid not in by_pid or decision['original_title'] != by_pid[pid].get('title'):
            raise ValueError('Unknown or stale title review')
        value = decision['alias']
        if not isinstance(value, str) or not normalize(value) or not isinstance(decision.get('reason'), str) or not decision['reason'].strip():
            raise ValueError('Title review needs an alias and reason')
        key = (pid, normalize(value))
        if key in seen:
            raise ValueError('Duplicate title review')
        seen.add(key)
        if version_signature(value) != version_signature(decision['original_title']):
            raise ValueError('Title alias changes recognized version markers')
        row = rows.get(pid)
        if row is None:
            row = {'persistent_id': pid, 'original_title': decision['original_title'], 'aliases': []}
            result['track_title_aliases'].append(row)
            rows[pid] = row
        alias = next((a for a in row['aliases'] if normalize(a['value']) == normalize(value)), None)
        if alias is None:
            alias = {'value': value}
            row['aliases'].append(alias)
        alias.update(enabled=True, status='reviewed_spelling_alias', reason=decision['reason'],
                     evidence=decision.get('evidence', {}))
    result['identity_status'] = 'unverified'
    IdentityMap(result, tracks, library_hash)
    return result


class IdentityMap:
    def __init__(self, document, tracks, library_hash):
        if document.get('schema_version') != 1 or document.get('library_sha256') != library_hash:
            raise ValueError('Identity map does not match this Library')
        self.tracks = tracks
        by_pid = {t['persistent_id']: t for t in tracks}
        if len(by_pid) != len(tracks):
            raise ValueError('Duplicate local persistent IDs')
        self.artist_names = {}
        for index, group in enumerate(document.get('artist_alias_groups', [])):
            if not group.get('enabled'):
                continue
            aliases = group['aliases']
            if len(aliases) < 2 or any(not isinstance(a, str) or not normalize(a) for a in aliases):
                raise ValueError('Invalid artist aliases')
            for alias in aliases:
                name = normalize(alias)
                if name in self.artist_names and self.artist_names[name] != index:
                    raise ValueError('Overlapping artist alias groups')
                self.artist_names[name] = index
        self.credit_sets = {}
        for declaration in document.get('artist_credit_sets', []):
            if not declaration.get('enabled'):
                continue
            labels, components = declaration['labels'], declaration['components']
            if not isinstance(labels, list) or not isinstance(components, list) or not labels or len(components) < 2 or any(
                    not isinstance(name, str) or not normalize(name) for name in labels + components):
                raise ValueError('Invalid artist credit declaration')
            credits = frozenset(self.artist_key(name) for name in components)
            if len(credits) != len(components):
                raise ValueError('Repeated canonical artist in credit declaration')
            for label in labels:
                key = self.artist_key(label)
                if key in self.credit_sets and self.credit_sets[key] != credits:
                    raise ValueError('Conflicting artist credit declarations')
                self.credit_sets[key] = credits
        if any(component in self.credit_sets for credits in self.credit_sets.values() for component in credits):
            raise ValueError('Nested or cyclic artist credit declarations are unsupported')
        self.titles = {}
        self.proposed_titles = {}
        for row in document.get('track_title_aliases', []):
            pid = row['persistent_id']
            if pid not in by_pid or row['original_title'] != by_pid[pid].get('title') or pid in self.titles:
                raise ValueError('Unknown, stale, or duplicate track title alias')
            aliases, pending = [], []
            for alias in row['aliases']:
                if not isinstance(alias['value'], str) or not normalize(alias['value']):
                    raise ValueError('Invalid title alias')
                if alias.get('enabled'):
                    aliases.append(normalize(alias['value']))
                else:
                    pending.append(normalize(alias['value']))
            self.titles[pid] = aliases
            self.proposed_titles[pid] = pending
        # Recording links are reserved for a later explicit verification workflow.
        if document.get('recording_links'):
            raise ValueError('Recording links are not yet supported')

    def artist_key(self, name):
        normalized = normalize(name)
        if normalized in self.artist_names:
            return ('alias_group', self.artist_names[normalized])
        return ('literal', normalized)

    def artist_credits(self, name):
        key = self.artist_key(name)
        return self.credit_sets.get(key, frozenset({key}))

    def match(self, remote, include_proposed=False):
        title = normalize(remote.get('title') or '')
        artists = {credit for artist in (remote.get('artists') or []) if artist.get('name')
                   for credit in self.artist_credits(artist['name'])}
        remote_declared_credits = any(self.artist_key(artist['name']) in self.credit_sets
                                      for artist in (remote.get('artists') or []) if artist.get('name'))
        duration = remote.get('duration_seconds')
        if duration is None and isinstance(remote.get('length'), str):
            parts = remote['length'].split(':')
            if len(parts) in (2, 3) and all(p.isdigit() for p in parts):
                duration = 0
                for part in parts:
                    duration = duration * 60 + int(part)
        if not title or not artists:
            return []
        if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
            return []
        remote_proposal = proposed_title_alias(remote.get('title') or '') if include_proposed else None
        remote_titles = {title}
        if remote_proposal:
            remote_titles.add(normalize(remote_proposal['value']))
        candidates = []
        for local in self.tracks:
            pid = local['persistent_id']
            exact_title = title == normalize(local.get('title') or '')
            active_title = exact_title or title in self.titles.get(pid, [])
            pending_title = False
            if not active_title:
                possible_local = {normalize(local.get('title') or ''), *self.titles.get(pid, [])}
                if include_proposed:
                    possible_local.update(self.proposed_titles.get(pid, []))
                    pending_title = bool(possible_local & remote_titles)
                if not pending_title:
                    continue
            if not exact_title and version_signature(local.get('title') or '') != version_signature(remote.get('title') or ''):
                continue
            local_artist = local.get('artist') or ''
            local_key = self.artist_key(local_artist)
            credit_match = local_key in self.credit_sets or remote_declared_credits
            if credit_match:
                artist_matches = self.artist_credits(local_artist) == artists
            else:
                artist_matches = local_key in artists
            if not artist_matches:
                continue
            if duration is None or local.get('duration_ms') is None:
                continue  # Alias-assisted matching requires duration evidence.
            delta = abs(local['duration_ms'] / 1000 - duration)
            if delta > 3:
                continue
            exact_artist = normalize(local.get('artist') or '') in {
                normalize(a.get('name') or '') for a in remote.get('artists') or []}
            remote_album = (remote.get('album') or {}).get('name')
            candidates.append({'persistent_id': pid,
                               'local_metadata': {key: local.get(key) for key in ('title', 'artist', 'album', 'duration_ms')},
                               'title_match': 'exact' if exact_title else ('proposed_alias' if pending_title else 'explicit_alias'),
                               'alias_review_status': 'needs_review' if pending_title else 'active_metadata_candidate',
                               'artist_match': 'explicit_credit_set' if credit_match else ('exact' if exact_artist else 'explicit_alias'),
                               'duration_difference_seconds': round(delta, 3),
                               'album_match': (normalize(remote_album) == normalize(local.get('album') or '')) if remote_album else None,
                               'identity_status': 'unverified'})
        return candidates


def group_candidate_observations(observations):
    """Preserve all observations; a metadata preference never confirms a recording."""
    grouped = {}
    for index, row in enumerate(observations):
        if row['is_seed']:
            continue
        key = (row['source'], row['video_id'])
        group = grouped.setdefault(key, {
            'source': key[0], 'video_id': key[1], 'observation_indices': [],
            'all_candidates': set(), 'duration_supported': set(), 'album_supported': set(),
        })
        group['observation_indices'].append(index)
        group['all_candidates'].update(row['exact_candidates'])
        for candidate in row['alias_candidates']:
            pid = candidate['persistent_id']
            group['all_candidates'].add(pid)
            group['duration_supported'].add(pid)
            if candidate['album_match'] is True:
                group['album_supported'].add(pid)
    result = []
    for group in grouped.values():
        duration_supported = group.pop('duration_supported')
        album_supported = group.pop('album_supported')
        candidates = group.pop('all_candidates')
        preferred = None
        if len(album_supported) > 1:
            status = 'conflicting_album_evidence'
        elif len(album_supported) == 1:
            status = 'unique_album_metadata_preference'
            preferred = next(iter(album_supported))
        elif len(duration_supported) == 1:
            status = 'single_duration_supported_candidate'
            preferred = next(iter(duration_supported))
        elif duration_supported:
            status = 'ambiguous_metadata_candidates'
        elif candidates:
            status = 'needs_duration_or_credit_evidence'
        else:
            status = 'unmatched'
        group.update(candidate_pids=sorted(candidates),
                     duration_supported_pids=sorted(duration_supported),
                     album_supported_pids=sorted(album_supported),
                     metadata_status=status, preferred_metadata_pid=preferred,
                     identity_status='unverified')
        result.append(group)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path, nargs='?')
    parser.add_argument('--track-snapshot', type=Path, help='Saved metadata; no access to original HDD')
    parser.add_argument('--review-proposed', action='store_true', help='Report pending title aliases separately; never activate them')
    parser.add_argument('--output', required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--create', action='store_true')
    action.add_argument('--identity-map', type=Path)
    parser.add_argument('--artist-evidence', type=Path)
    parser.add_argument('--observations', type=Path)
    parser.add_argument('--review-decisions', type=Path, help='Explicit spelling decisions; write a new map')
    args = parser.parse_args()
    output = args.output.resolve()
    if (args.bundle is None) == (args.track_snapshot is None):
        parser.error('Specify either bundle or --track-snapshot')
    if (args.bundle and output.is_relative_to(args.bundle.resolve())) or output.exists():
        parser.error('output must be new and outside the input library')
    tracks, library_hash, provenance = load_library(args.bundle, args.track_snapshot)
    if args.create:
        if args.observations or args.review_proposed or args.review_decisions:
            parser.error('Observation/review options require --identity-map')
        evidence = json.loads(args.artist_evidence.read_text())['artist_alias_proposals'] if args.artist_evidence else []
        report = build_map(tracks, library_hash, evidence)
        IdentityMap(report, tracks, library_hash)  # Validate generated aliases before saving.
        summary = {'artist_groups': len(report['artist_alias_groups']),
                   'title_aliases_pending_review': len(report['track_title_aliases'])}
    else:
        if args.artist_evidence or bool(args.observations) == bool(args.review_decisions):
            parser.error('--identity-map requires exactly one of --observations or --review-decisions')
        if args.review_decisions and args.review_proposed:
            parser.error('--review-proposed requires --observations')
        document = json.loads(args.identity_map.read_text())
        if args.review_decisions:
            decisions = json.loads(args.review_decisions.read_text())
            if decisions.get('identity_map_sha256') != hashlib.sha256(args.identity_map.read_bytes()).hexdigest():
                raise ValueError('Review decisions target a different map revision')
            report = apply_title_review(document, decisions, tracks, library_hash)
            report['review_input_sha256'] = hashlib.sha256(args.review_decisions.read_bytes()).hexdigest()
            summary = {'applied_title_decisions': len(decisions['decisions']), 'verified_recordings': 0}
        else:
            matcher = IdentityMap(document, tracks, library_hash)
            snapshot = json.loads(args.observations.read_text())
            rows = []
            for observation in snapshot['observations']:
                remote = observation['track']
                rows.append({'source': observation['source'], 'relation': observation['relation'],
                             'video_id': remote['video_id'], 'is_seed': observation['is_seed'],
                             'remote_track': remote,
                             'search_query': observation.get('search_query'),
                             'request_file_sha256': observation.get('request_file_sha256'),
                             'section': observation.get('section'),
                             'position': observation['position'], 'observed_at': observation['observed_at'],
                             'exact_candidates': local_candidates(remote, tracks),
                             'alias_candidates': matcher.match(remote),
                             'proposed_alias_candidates': [candidate for candidate in matcher.match(remote, include_proposed=True)
                                                           if candidate['alias_review_status'] == 'needs_review'] if args.review_proposed else [],
                             'identity_status': 'unverified', 'genius_rank': None})
            report = {'schema_version': 1, 'library_sha256': library_hash,
                      'identity_map_sha256': hashlib.sha256(args.identity_map.read_bytes()).hexdigest(),
                      'observation_file_sha256': hashlib.sha256(args.observations.read_bytes()).hexdigest(),
                      'source_session_id': snapshot.get('session_id'), 'observations': rows}
            report['input_origin'] = snapshot.get('origin', 'ytmusic_observations')
            report['candidate_groups'] = group_candidate_observations(rows)
            nonseed = [row for row in rows if not row['is_seed']]
            summary = {'nonseed_exact_video_ids': len({r['video_id'] for r in nonseed if r['exact_candidates']}),
                       'nonseed_alias_video_ids': len({r['video_id'] for r in nonseed if r['alias_candidates']}),
                       'nonseed_union_video_ids': len({r['video_id'] for r in nonseed if r['exact_candidates'] or r['alias_candidates']}),
                       'new_alias_video_ids': len({r['video_id'] for r in nonseed if r['alias_candidates']} - {r['video_id'] for r in nonseed if r['exact_candidates']}),
                       'pending_review_video_ids': len({r['video_id'] for r in nonseed if r['proposed_alias_candidates']}),
                       'new_pending_review_video_ids': len({r['video_id'] for r in nonseed if r['proposed_alias_candidates']} - {r['video_id'] for r in nonseed if r['exact_candidates'] or r['alias_candidates']}),
                       'verified_recordings': 0}
            summary['metadata_preferences'] = sum(group['preferred_metadata_pid'] is not None
                                                  for group in report['candidate_groups'])
            summary['metadata_status_counts'] = {
                status: sum(group['metadata_status'] == status for group in report['candidate_groups'])
                for status in sorted({group['metadata_status'] for group in report['candidate_groups']})}
    report['library_input'] = provenance
    if not args.create and not args.review_decisions:
        report['summary'] = summary
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), **summary}))


if __name__ == '__main__':
    main()
