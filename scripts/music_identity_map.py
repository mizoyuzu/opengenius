"""Library-scoped metadata aliases and offline YTMusic candidate matching.

Aliases aid candidate generation; they never establish recording identity.
"""
import argparse
import hashlib
import json
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
        self.titles = {}
        for row in document.get('track_title_aliases', []):
            pid = row['persistent_id']
            if pid not in by_pid or row['original_title'] != by_pid[pid].get('title') or pid in self.titles:
                raise ValueError('Unknown, stale, or duplicate track title alias')
            aliases = []
            for alias in row['aliases']:
                if not isinstance(alias['value'], str) or not normalize(alias['value']):
                    raise ValueError('Invalid title alias')
                if alias.get('enabled'):
                    aliases.append(normalize(alias['value']))
            self.titles[pid] = aliases
        # Recording links are reserved for a later explicit verification workflow.
        if document.get('recording_links'):
            raise ValueError('Recording links are not yet supported')

    def artist_key(self, name):
        normalized = normalize(name)
        if normalized in self.artist_names:
            return ('alias_group', self.artist_names[normalized])
        return ('literal', normalized)

    def match(self, remote):
        title = normalize(remote.get('title') or '')
        artists = {self.artist_key(a.get('name') or '') for a in (remote.get('artists') or []) if a.get('name')}
        duration = remote.get('duration_seconds')
        if duration is None and isinstance(remote.get('length'), str):
            parts = remote['length'].split(':')
            if len(parts) in (2, 3) and all(p.isdigit() for p in parts):
                duration = 0
                for part in parts:
                    duration = duration * 60 + int(part)
        if not title or not artists:
            return []
        candidates = []
        for local in self.tracks:
            pid = local['persistent_id']
            exact_title = title == normalize(local.get('title') or '')
            if not exact_title and title not in self.titles.get(pid, []):
                continue
            if self.artist_key(local.get('artist') or '') not in artists:
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
                               'title_match': 'exact' if exact_title else 'explicit_alias',
                               'artist_match': 'exact' if exact_artist else 'explicit_alias',
                               'duration_difference_seconds': round(delta, 3),
                               'album_match': (normalize(remote_album) == normalize(local.get('album') or '')) if remote_album else None,
                               'identity_status': 'unverified'})
        return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--create', action='store_true')
    action.add_argument('--identity-map', type=Path)
    parser.add_argument('--artist-evidence', type=Path)
    parser.add_argument('--observations', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(args.bundle.resolve()) or output.exists():
        parser.error('output must be new and outside the input library')
    data = (args.bundle / 'Library.musicdb').read_bytes()
    library_hash = hashlib.sha256(data).hexdigest()
    tracks, _ = parse_tracks(decode_musicdb(data))
    if args.create:
        if args.observations:
            parser.error('--observations requires --identity-map')
        evidence = json.loads(args.artist_evidence.read_text())['artist_alias_proposals'] if args.artist_evidence else []
        report = build_map(tracks, library_hash, evidence)
        IdentityMap(report, tracks, library_hash)  # Validate generated aliases before saving.
        summary = {'artist_groups': len(report['artist_alias_groups']),
                   'title_aliases_pending_review': len(report['track_title_aliases'])}
    else:
        if not args.observations or args.artist_evidence:
            parser.error('--identity-map requires --observations and no --artist-evidence')
        document = json.loads(args.identity_map.read_text())
        matcher = IdentityMap(document, tracks, library_hash)
        snapshot = json.loads(args.observations.read_text())
        rows = []
        for observation in snapshot['observations']:
            remote = observation['track']
            rows.append({'source': observation['source'], 'relation': observation['relation'],
                         'video_id': remote['video_id'], 'is_seed': observation['is_seed'],
                         'section': observation.get('section'),
                         'position': observation['position'], 'observed_at': observation['observed_at'],
                         'exact_candidates': local_candidates(remote, tracks),
                         'alias_candidates': matcher.match(remote),
                         'identity_status': 'unverified', 'genius_rank': None})
        report = {'schema_version': 1, 'library_sha256': library_hash,
                  'identity_map_sha256': hashlib.sha256(args.identity_map.read_bytes()).hexdigest(),
                  'observation_file_sha256': hashlib.sha256(args.observations.read_bytes()).hexdigest(),
                  'source_session_id': snapshot.get('session_id'), 'observations': rows}
        nonseed = [row for row in rows if not row['is_seed']]
        summary = {'nonseed_exact_video_ids': len({r['video_id'] for r in nonseed if r['exact_candidates']}),
                   'nonseed_alias_video_ids': len({r['video_id'] for r in nonseed if r['alias_candidates']}),
                   'nonseed_union_video_ids': len({r['video_id'] for r in nonseed if r['exact_candidates'] or r['alias_candidates']}),
                   'new_alias_video_ids': len({r['video_id'] for r in nonseed if r['alias_candidates']} - {r['video_id'] for r in nonseed if r['exact_candidates']}),
                   'verified_recordings': 0}
    if not args.create:
        report['summary'] = summary
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), **summary}))


if __name__ == '__main__':
    main()
