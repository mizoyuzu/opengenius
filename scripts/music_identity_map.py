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


_VERSION_MARKERS = {
    'off_vocal': r'\boff[ _-]?vocal\b',
    'karaoke': r'\bkaraoke\b|カラオケ|からおけ',
    'instrumental': r'\binstrument(?:al)?\b',
    'game_size': r'game[ _-]?size',
    'tv_size': r'tv[ _-]?size',
    'short_version': r'\bshort\s+(?:ver(?:sion)?|edit)\b',
    'edit': r'\b(?:tv\s+)?(?:short\s+)?edit(?:ed)?\b',
    'live': r'\blive\b|ライブ',
    'remix': r'\bremix\b|\brmx\b|リミックス',
    'remaster': r'\bremaster(?:ed|ing)?\b|再マスタリング',
    'piano': r'\bpiano\b|ピアノ',
    'orchestral': r'\borchestr(?:al|a)\b|オーケストラ',
}
_VERSION_MARKER_RE = {name: re.compile(pattern, re.IGNORECASE)
                      for name, pattern in _VERSION_MARKERS.items()}
_YEAR_RE = re.compile(r'(?<!\d)((?:19|20)\d{2})(?!\d)')
_QUALIFIER_RE = re.compile(r'\(([^()]*)\)')
_BRACKET_QUALIFIER_RE = re.compile(r'\[([^\[\]]*)\]|【([^【】]*)】')


def version_signature(title):
    """Return recognized title facets used only as a spelling-alias guard."""
    title = normalize(title).replace('_', ' ')
    flags = {name for name, pattern in _VERSION_MARKER_RE.items() if pattern.search(title)}
    flags.update('year:' + year for year in _YEAR_RE.findall(title))
    return flags


def version_facets(title):
    """Describe title markers without asserting recording identity.

    Qualifier text that is not explained by a recognized marker is retained as
    unknown. This is diagnostic metadata; callers must preserve the source title.
    """
    original = title or ''
    normalized = normalize(original).replace('_', ' ')
    signature = version_signature(original)
    markers = sorted(value for value in signature if not value.startswith('year:'))
    years = sorted(value[5:] for value in signature if value.startswith('year:'))
    unknown = set()
    qualifier_matches = list(_QUALIFIER_RE.finditer(normalized)) + list(_BRACKET_QUALIFIER_RE.finditer(normalized))
    for match in qualifier_matches:
        raw = next((value for value in match.groups() if value is not None), '')
        text = normalize(raw).replace('_', ' ')
        text = re.sub(r'【[^【】]*】|\[[^\[\]]*\]', ' ', text)
        if not text:
            continue
        remainder = text
        for pattern in _VERSION_MARKER_RE.values():
            remainder = pattern.sub(' ', remainder)
        remainder = _YEAR_RE.sub(' ', remainder)
        remainder = re.sub(r'\b(?:ver|version|size|for|the)\b', ' ', remainder)
        remainder = re.sub(r'[^\w\u3040-\u30ff\u3400-\u9fff]+', ' ', remainder)
        remainder = ' '.join(remainder.split())
        if remainder:
            unknown.add(remainder)
    kinds = sorted(set(markers) & {'off_vocal', 'instrumental', 'karaoke'})
    return {'original_title': original, 'markers': markers, 'years': years,
            'kinds': kinds, 'unknown_qualifiers': sorted(unknown)}


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


def apply_artist_credit_alias_review(document, decisions, proposals, tracks, library_hash,
                                    identity_map_sha256, proposal_sha256):
    """Apply reviewed remote whole-credit labels without changing local credits."""
    IdentityMap(document, tracks, library_hash)
    if decisions.get('schema_version') != 1:
        raise ValueError('Unsupported artist credit review schema')
    if decisions.get('library_sha256') != library_hash:
        raise ValueError('Review decisions do not match this Library')
    if decisions.get('identity_map_sha256') != identity_map_sha256:
        raise ValueError('Review decisions target a different map revision')
    if decisions.get('proposal_sha256') != proposal_sha256:
        raise ValueError('Review decisions target a different proposal document')
    source_report = proposals.get('source_report') or {}
    if decisions.get('source_report_sha256') != source_report.get('sha256'):
        raise ValueError('Review decisions target a different source report')
    proposal_rows = {}
    for row in proposals.get('proposals', []):
        key = (normalize(row.get('remote_credit') or ''), normalize(row.get('local_credit') or ''),
               row.get('persistent_id'))
        if row.get('status') == 'verified' and row.get('activation_recommendation') == 'root_review_only':
            proposal_rows[key] = row
    by_pid = {track['persistent_id']: track for track in tracks}
    result = copy.deepcopy(document)
    aliases = result.setdefault('artist_credit_aliases', [])
    seen_sources = {normalize(row['remote_credit']) for row in aliases if row.get('enabled')}
    for decision in decisions.get('decisions', []):
        remote = decision.get('remote_credit')
        local = decision.get('local_credit')
        pid = decision.get('target_persistent_id')
        reason = decision.get('reason')
        sources = decision.get('evidence_sources')
        if not all(isinstance(value, str) and normalize(value) for value in (remote, local)):
            raise ValueError('Credit review needs complete remote and local credit strings')
        if not isinstance(pid, str) or pid not in by_pid or by_pid[pid].get('artist') != local:
            raise ValueError('Credit review target must be an exact existing local artist credit')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Credit review needs a reason')
        if not isinstance(sources, list) or not sources or any(
                not isinstance(url, str) or not url.startswith('https://') for url in sources):
            raise ValueError('Credit review needs HTTPS evidence sources')
        key = (normalize(remote), normalize(local), pid)
        proposal = proposal_rows.get(key)
        if proposal is None:
            raise ValueError('Credit review does not match a verified proposal')
        if normalize(remote) == normalize(local):
            raise ValueError('Credit alias source and target are already equivalent')
        if normalize(remote) in {normalize(track.get('artist') or '') for track in tracks}:
            raise ValueError('Credit alias source is already a local artist credit')
        if normalize(remote) in seen_sources:
            raise ValueError('Duplicate or overlapping remote credit alias')
        proposal_urls = {source.get('url') for source in proposal.get('sources', [])}
        if not set(sources).issubset(proposal_urls):
            raise ValueError('Review evidence sources are not bound to the proposal')
        seen_sources.add(normalize(remote))
        aliases.append({'remote_credit': remote, 'local_credit': local, 'enabled': True,
                        'status': 'reviewed_whole_credit_alias',
                        'evidence': {'reason': reason.strip(), 'sources': sources,
                                     'proposal_sha256': proposal_sha256,
                                     'source_report_sha256': source_report['sha256']}})
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
        # Album aliases are exact, directed metadata mappings.  They only change
        # album_match; canonical Library album fields and recording identity stay
        # untouched.  Requiring targets to be unique existing Library values
        # prevents chains, cycles, and ambiguous destinations.
        local_album_values = {}
        for track in tracks:
            album = track.get('album') or ''
            if normalize(album):
                local_album_values.setdefault(normalize(album), set()).add(album)
        self.album_title_aliases = {}
        normalized_album_aliases = {}
        for declaration in document.get('album_title_aliases', []):
            if not isinstance(declaration, dict):
                raise ValueError('Invalid album title alias declaration')
            if not declaration.get('enabled'):
                continue
            canonical = declaration.get('canonical_library_album')
            aliases = declaration.get('exact_remote_album_aliases')
            if (not isinstance(canonical, str) or not normalize(canonical)
                    or not isinstance(aliases, list) or not aliases):
                raise ValueError('Invalid whole album title alias')
            canonical_key = normalize(canonical)
            target_values = local_album_values.get(canonical_key, set())
            if len(target_values) != 1:
                raise ValueError('Album alias target is not one existing canonical Library album')
            canonical_value = next(iter(target_values))
            if canonical_value != canonical:
                raise ValueError('Album alias target must use the exact canonical Library album value')
            for alias in aliases:
                if not isinstance(alias, str) or not normalize(alias):
                    raise ValueError('Invalid exact remote album alias')
                alias_key = normalize(alias)
                if alias_key == canonical_key:
                    raise ValueError('Album alias source and canonical target are equivalent')
                existing_album_values = local_album_values.get(alias_key, set())
                if existing_album_values:
                    raise ValueError('Album alias shadows an existing local album value')
                if alias in self.album_title_aliases or alias_key in normalized_album_aliases:
                    raise ValueError('Duplicate or conflicting whole album title aliases')
                self.album_title_aliases[alias] = canonical_value
                normalized_album_aliases[alias_key] = canonical_value
        if any(normalize(target) in normalized_album_aliases for target in self.album_title_aliases.values()):
            raise ValueError('Album alias chains or cycles are unsupported')
        self.artist_credit_aliases = {}
        local_artist_keys = {self.artist_key(track.get('artist') or '') for track in tracks}
        for declaration in document.get('artist_credit_aliases', []):
            if not declaration.get('enabled'):
                continue
            remote, local = declaration.get('remote_credit'), declaration.get('local_credit')
            if not isinstance(remote, str) or not normalize(remote) or not isinstance(local, str) or not normalize(local):
                raise ValueError('Invalid whole artist credit alias')
            remote_key, local_key = self.artist_key(remote), self.artist_key(local)
            if remote_key == local_key:
                raise ValueError('Whole artist credit alias source and target are equivalent')
            if remote_key in local_artist_keys and remote_key != local_key:
                raise ValueError('Whole artist credit alias overlaps a local artist credit')
            if local_key not in local_artist_keys:
                raise ValueError('Whole artist credit alias target is not a local artist credit')
            if remote_key in self.artist_credit_aliases:
                raise ValueError('Duplicate or overlapping whole artist credit aliases')
            self.artist_credit_aliases[remote_key] = local_key
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
        # The Library and alias document are fixed inputs for this matcher.
        # Narrow by title before the recording/version/credit/duration checks;
        # retain Library order when multiple local recordings share a title.
        self._active_title_indices = {}
        self._preview_title_indices = {}
        for index, track in enumerate(tracks):
            pid = track['persistent_id']
            active = {normalize(track.get('title') or ''), *self.titles.get(pid, [])}
            for value in active:
                self._active_title_indices.setdefault(value, set()).add(index)
            for value in active | set(self.proposed_titles.get(pid, [])):
                self._preview_title_indices.setdefault(value, set()).add(index)

    def _matching_track_indices(self, remote_titles, include_proposed):
        lookup = self._preview_title_indices if include_proposed else self._active_title_indices
        return sorted({index for title in remote_titles for index in lookup.get(title, ())})

    def artist_key(self, name):
        normalized = normalize(name)
        if normalized in self.artist_names:
            return ('alias_group', self.artist_names[normalized])
        return ('literal', normalized)

    def artist_credits(self, name):
        key = self.artist_key(name)
        if key in self.artist_credit_aliases:
            key = self.artist_credit_aliases[key]
        return self.credit_sets.get(key, frozenset({key}))

    def match(self, remote, include_proposed=False):
        title = normalize(remote.get('title') or '')
        artists = {credit for artist in (remote.get('artists') or []) if artist.get('name')
                   for credit in self.artist_credits(artist['name'])}
        remote_declared_credits = any(self.artist_key(artist['name']) in self.credit_sets or
                                      self.artist_key(artist['name']) in self.artist_credit_aliases
                                      for artist in (remote.get('artists') or []) if artist.get('name'))
        remote_has_credit_alias = any(self.artist_key(artist['name']) in self.artist_credit_aliases
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
        for index in self._matching_track_indices(remote_titles, include_proposed):
            local = self.tracks[index]
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
            album_match = None
            if remote_album:
                remote_album_target = self.album_title_aliases.get(remote_album, remote_album)
                album_match = normalize(remote_album_target) == normalize(local.get('album') or '')
            candidates.append({'persistent_id': pid,
                               'local_metadata': {key: local.get(key) for key in ('title', 'artist', 'album', 'duration_ms')},
                               'title_match': 'exact' if exact_title else ('proposed_alias' if pending_title else 'explicit_alias'),
                               'alias_review_status': 'needs_review' if pending_title else 'active_metadata_candidate',
                               'artist_match': ('explicit_artist_credit_alias' if remote_has_credit_alias else
                                                'explicit_credit_set' if credit_match else
                                                'exact' if exact_artist else 'explicit_alias'),
                               # A literal singer match may omit an additional remote
                               # credit. Report that evidence without assuming its role.
                               'artist_credit_match': 'complete' if self.artist_credits(local_artist) == artists else 'partial',
                               'duration_difference_seconds': round(delta, 3),
                               'album_match': album_match,
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
            'complete_credit_supported': set(), 'partial_credit_supported': set(),
        })
        group['observation_indices'].append(index)
        group['all_candidates'].update(row['exact_candidates'])
        for candidate in row['alias_candidates']:
            pid = candidate['persistent_id']
            group['all_candidates'].add(pid)
            group['duration_supported'].add(pid)
            if candidate['album_match'] is True:
                group['album_supported'].add(pid)
            if candidate.get('artist_credit_match') == 'complete':
                group['complete_credit_supported'].add(pid)
            elif candidate.get('artist_credit_match') == 'partial':
                group['partial_credit_supported'].add(pid)
    result = []
    for group in grouped.values():
        duration_supported = group.pop('duration_supported')
        album_supported = group.pop('album_supported')
        candidates = group.pop('all_candidates')
        complete_credits = group.pop('complete_credit_supported')
        partial_credits = group.pop('partial_credit_supported')
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
                     complete_credit_supported_pids=sorted(complete_credits),
                     partial_credit_supported_pids=sorted(partial_credits),
                     preferred_artist_credit_match=('complete' if preferred in complete_credits else
                                                    'partial' if preferred in partial_credits else
                                                    'unknown' if preferred is not None else None),
                     preferred_relies_on_partial_artist_credits=(preferred in partial_credits and preferred not in complete_credits),
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
