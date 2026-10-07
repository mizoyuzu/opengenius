"""Validated, Library-bound recommendation controls for individual seed roots."""
from __future__ import annotations

import re

from music_clusters import scope_graphs


_HASH = re.compile(r'^[0-9a-f]{64}$')
_TOP_FIELDS = {'schema_version', 'library_sha256', 'root_preferences'}
_ENTRY_FIELDS = {
    'root_pid', 'name', 'tags', 'kind', 'excluded_kinds', 'cluster_mode',
    'profile', 'artist_preference', 'fallback_tags', 'fallback_limit', 'note',
}
_REQUIRED_ENTRY_FIELDS = _ENTRY_FIELDS - {'fallback_tags', 'fallback_limit', 'note'}
_PROFILES = {'without-compatible-genre', 'artist-album-minimum-one', 'relations-only'}
_ARTIST_PREFERENCES = {'neutral', 'slight', 'moderate'}


def _strings(value, label):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f'{label} must be a list of strings')
    if any(not item.strip() for item in value):
        raise ValueError(f'{label} cannot contain empty values')
    if len(set(value)) != len(value):
        raise ValueError(f'{label} cannot contain duplicates')
    return list(value)


def _track_ids(tracks):
    if not isinstance(tracks, (list, tuple)):
        raise ValueError('Tracks must be a list or tuple')
    ids = set()
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError('Invalid Library track')
        pid = track.get('persistent_id')
        if not isinstance(pid, str) or not pid or pid in ids:
            raise ValueError('Missing or duplicate Library persistent ID')
        ids.add(pid)
    return ids


def _scope_is_valid(root_pid, tags, kind, excluded_kinds, mode, classifications):
    # The standard scope implementation enforces known tags, valid kinds,
    # exclusions, and whether the seed survives the requested mode.
    scope_graphs([{'root_pid': root_pid, 'ordered_target_pids': []}], classifications,
                 tags, kind, excluded_kinds, mode=mode)


def validate_root_preferences(doc, tracks, library_hash, classifications, root_pids=None):
    """Validate and detach per-root criteria against this Library and labels.

    ``root_pids`` may restrict entries to seeds with usable observations. In
    ``prefer`` mode a seed may be outside its preferred scope; in ``only`` mode
    the seed must satisfy the primary scope.
    """
    if not isinstance(library_hash, str) or _HASH.fullmatch(library_hash) is None:
        raise ValueError('Invalid Library SHA256')
    known_pids = _track_ids(tracks)
    if not isinstance(classifications, dict) or set(classifications) != known_pids:
        raise ValueError('Classifications must cover exactly the Library PIDs')
    if known_pids:
        _scope_is_valid(next(iter(known_pids)), [], None, [], 'prefer', classifications)
    if root_pids is not None:
        if (not isinstance(root_pids, (list, tuple, set))
                or any(not isinstance(pid, str) or pid not in known_pids for pid in root_pids)):
            raise ValueError('Invalid known root PID set')
        allowed_roots = set(root_pids)
    else:
        allowed_roots = known_pids
    if not isinstance(doc, dict) or set(doc) != _TOP_FIELDS:
        raise ValueError('Invalid root preference document fields')
    if type(doc.get('schema_version')) is not int or doc['schema_version'] != 1:
        raise ValueError('Unsupported root preference schema')
    if doc.get('library_sha256') != library_hash:
        raise ValueError('Root preferences belong to a different Library')
    entries = doc.get('root_preferences')
    if not isinstance(entries, list):
        raise ValueError('root_preferences must be a list')

    seen = set()
    detached = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - _ENTRY_FIELDS:
            raise ValueError('Invalid root preference fields')
        if not _REQUIRED_ENTRY_FIELDS.issubset(entry):
            raise ValueError('Missing root preference fields')
        root = entry['root_pid']
        if not isinstance(root, str) or root not in known_pids:
            raise ValueError('Root preference references an unknown Library PID')
        if root not in allowed_roots:
            raise ValueError('Root preference PID is not an evaluated root')
        if root in seen:
            raise ValueError('Duplicate root preference PID')
        seen.add(root)
        name = entry['name']
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Root preference name must be nonempty')
        tags = _strings(entry['tags'], 'tags')
        excluded = _strings(entry['excluded_kinds'], 'excluded_kinds')
        fallback_present = 'fallback_tags' in entry or 'fallback_limit' in entry
        if fallback_present and not {'fallback_tags', 'fallback_limit'}.issubset(entry):
            raise ValueError('Fallback tags and limit must be supplied together')
        if 'note' in entry and not isinstance(entry['note'], str):
            raise ValueError('Root preference note must be a string')
        mode = entry['cluster_mode']
        if mode not in ('only', 'prefer'):
            raise ValueError('Invalid root preference cluster mode')
        kind = entry['kind']
        if kind is not None and not isinstance(kind, str):
            raise ValueError('Invalid root preference kind')
        if not isinstance(entry['profile'], str) or entry['profile'] not in _PROFILES:
            raise ValueError('Invalid root preference profile')
        if (not isinstance(entry['artist_preference'], str)
                or entry['artist_preference'] not in _ARTIST_PREFERENCES):
            raise ValueError('Invalid root preference artist preference')
        _scope_is_valid(root, tags, kind, excluded, mode, classifications)

        result = {
            'root_pid': root, 'name': name, 'tags': tags, 'kind': kind,
            'excluded_kinds': excluded, 'cluster_mode': mode,
            'profile': entry['profile'], 'artist_preference': entry['artist_preference'],
        }
        if fallback_present:
            if mode != 'prefer' or not (tags or kind is not None):
                raise ValueError('Fallbacks require a constrained primary prefer scope')
            fallback_tags = _strings(entry['fallback_tags'], 'fallback_tags')
            fallback_limit = entry['fallback_limit']
            if type(fallback_limit) is not int or not 0 <= fallback_limit <= 25:
                raise ValueError('fallback_limit must be an integer from 0 through 25')
            _scope_is_valid(root, fallback_tags, None, excluded, 'prefer', classifications)
            result['fallback_tags'] = fallback_tags
            result['fallback_limit'] = fallback_limit
        if 'note' in entry:
            result['note'] = entry['note']
        detached.append(result)
    return {'schema_version': 1, 'library_sha256': library_hash, 'root_preferences': detached}


def defaults_for_root(doc, pid):
    """Return only evaluation controls for one PID, excluding descriptive fields."""
    if not isinstance(doc, dict) or not isinstance(doc.get('root_preferences'), list):
        raise ValueError('Invalid validated root preferences')
    for entry in doc['root_preferences']:
        if isinstance(entry, dict) and entry.get('root_pid') == pid:
            return {key: list(value) if isinstance(value, list) else value
                    for key, value in entry.items()
                    if key in {'tags', 'kind', 'excluded_kinds', 'cluster_mode', 'profile',
                               'artist_preference', 'fallback_tags', 'fallback_limit'}}
    return None
