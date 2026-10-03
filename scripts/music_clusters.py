"""User-defined, Library-scoped music tags and explicit vocal/BGM kinds.

Multiple tags may describe families and brands. Recording identity is not inferred.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from music_identity_map import load_library
from probe_ytmusic import normalize

KINDS = frozenset({'vocal', 'bgm', 'off_vocal', 'unknown'})
MATCH_FIELDS = frozenset({'album_contains', 'artist_is', 'title_contains', 'persistent_ids'})


def _strings(value, label, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty) or any(
            not isinstance(item, str) or not normalize(item) for item in value):
        raise ValueError(label + ' must be a list of nonempty strings')
    if len({normalize(item) for item in value}) != len(value):
        raise ValueError('Duplicate ' + label)


def _pid(value):
    return isinstance(value, str) and len(value) == 16 and all(char in '0123456789ABCDEF' for char in value)


def _kind(value):
    if not isinstance(value, str) or value not in KINDS:
        raise ValueError('Invalid music kind')


def validate_config(config, tracks, library_hash):
    if not isinstance(config, dict) or set(config) - {'schema_version', 'source_library_sha256', 'rules', 'overrides'}:
        raise ValueError('Unknown cluster config fields')
    digest = config.get('source_library_sha256')
    if (type(config.get('schema_version')) is not int or config['schema_version'] != 1
            or not isinstance(digest, str) or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest)
            or digest != library_hash):
        raise ValueError('Cluster config must match the source Library SHA-256')
    if not isinstance(tracks, list) or any(not isinstance(track, dict) or not _pid(track.get('persistent_id')) for track in tracks):
        raise ValueError('Invalid local tracks')
    known = {track['persistent_id'] for track in tracks}
    if len(known) != len(tracks):
        raise ValueError('Duplicate local persistent IDs')
    for track in tracks:
        if any(field in track and not isinstance(track[field], str) for field in ('title', 'artist', 'album')):
            raise ValueError('Invalid local text metadata')
    rules = config.get('rules')
    if not isinstance(rules, list):
        raise ValueError('Cluster rules must be a list')
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {'name', 'match', 'tags', 'kind'}:
            raise ValueError('Unknown cluster rule fields')
        if 'name' in rule and (not isinstance(rule['name'], str) or not normalize(rule['name'])):
            raise ValueError('Rule name must be a nonempty string')
        match = rule.get('match')
        if not isinstance(match, dict) or not match or set(match) - MATCH_FIELDS:
            raise ValueError('Need a nonempty match with supported conditions')
        for field, values in match.items():
            _strings(values, field)
            if field == 'persistent_ids' and any(not _pid(pid) or pid not in known for pid in values):
                raise ValueError('Invalid or unknown rule persistent ID')
        _strings(rule.get('tags'), 'tags')
        if 'kind' in rule:
            _kind(rule['kind'])
    overrides = config.get('overrides', {})
    if not isinstance(overrides, dict):
        raise ValueError('Overrides must be an object keyed by persistent ID')
    for pid, override in overrides.items():
        if not _pid(pid) or pid not in known:
            raise ValueError('Invalid or unknown override persistent ID')
        if not isinstance(override, dict) or not override or set(override) - {'tags', 'kind'}:
            raise ValueError('Override needs tags or kind and no other fields')
        if 'tags' in override:
            _strings(override['tags'], 'override tags', allow_empty=True)
        if 'kind' in override:
            _kind(override['kind'])
    return config


def _matches(track, match):
    for field, values in match.items():
        if field == 'persistent_ids':
            satisfied = track['persistent_id'] in values
        elif field == 'artist_is':
            satisfied = normalize(track.get('artist') or '') in {normalize(value) for value in values}
        else:
            actual = normalize(track.get('album' if field == 'album_contains' else 'title') or '')
            satisfied = any(normalize(value) in actual for value in values)
        if not satisfied:
            return False
    return True


def off_vocal_marker(title):
    text = normalize(title)
    for marker in ('カラオケ', 'からおけ'):
        if marker in text:
            return marker
    for marker in ('off vocal', 'offvocal', 'off-vocal', 'off_vocal', 'karaoke'):
        start = 0
        while (index := text.find(marker, start)) >= 0:
            before, after = text[index - 1:index] if index else '', text[index + len(marker):index + len(marker) + 1]
            def english_word(char):
                return bool(char) and char.isascii() and char.isalnum()
            if not english_word(before) and not english_word(after):
                return marker
            start = index + 1
    return None


def classify_tracks(config, tracks, library_hash):
    """Return tags/kind/evidence by PID without mutating config or local tracks."""
    validate_config(config, tracks, library_hash)
    result = {}
    for track in tracks:
        pid = track['persistent_id']
        tags, tag_keys, kind, evidence = [], set(), 'unknown', []
        for index, rule in enumerate(config['rules']):
            if not _matches(track, rule['match']):
                continue
            for tag in rule['tags']:
                key = normalize(tag)
                if key not in tag_keys:
                    tags.append(tag)
                    tag_keys.add(key)
            if 'kind' in rule:
                kind = rule['kind']
            evidence.append({'source': 'rule', 'rule_index': index, 'name': rule.get('name'),
                             'matched_conditions': list(rule['match']), 'tags': list(rule['tags']),
                             'kind': rule.get('kind')})
        marker = off_vocal_marker(track.get('title') or '')
        if marker:
            kind = 'off_vocal'
            evidence.append({'source': 'special_version_marker', 'marker': marker, 'kind': kind})
        override = config.get('overrides', {}).get(pid)
        if override is not None:
            if 'tags' in override:
                tags = list(override['tags'])
            if 'kind' in override:
                kind = override['kind']
            evidence.append({'source': 'persistent_id_override', **copy.deepcopy(override)})
        result[pid] = {'tags': tags, 'kind': kind, 'evidence': evidence}
    return result


def scope_graphs(graphs, classifications, tags=(), kind=None):
    """Restrict all root edges before emulation; retain original observations.

    removed_target_count includes every edge removed with an excluded root.
    An empty tag sequence means no tag condition; empty tag strings are invalid.
    """
    if not isinstance(graphs, list) or not isinstance(classifications, dict):
        raise ValueError('Need graph list and classifications by PID')
    if not isinstance(tags, (list, tuple)):
        raise ValueError('Scope tags must be a list or tuple')
    _strings(list(tags), 'scope tags', allow_empty=True)
    requested = [normalize(tag) for tag in tags]
    if kind is not None:
        _kind(kind)
    known_tags, normalized = set(), {}
    for pid, classification in classifications.items():
        if not isinstance(classification, dict):
            raise ValueError('Invalid PID classification')
        _strings(classification.get('tags'), 'classification tags', allow_empty=True)
        _kind(classification.get('kind'))
        current = frozenset(normalize(tag) for tag in classification['tags'])
        normalized[pid] = (current, classification['kind'])
        known_tags.update(current)
    if not set(requested) <= known_tags:
        raise ValueError('Unknown scope tag')
    # Validate every referenced PID, including graphs that will be excluded.
    for graph in graphs:
        if not isinstance(graph, dict) or not isinstance(graph.get('ordered_target_pids'), list):
            raise ValueError('Invalid candidate graph')
        pids = [graph.get('root_pid'), *graph['ordered_target_pids']]
        if any(not isinstance(pid, str) or pid not in normalized for pid in pids):
            raise ValueError('Graph PID has no classification')
    def accepts(pid):
        current_tags, current_kind = normalized[pid]
        return set(requested) <= current_tags and (kind is None or current_kind == kind)
    filtered, excluded, removed = [], [], 0
    for graph in graphs:
        root = graph['root_pid']
        if not accepts(root):
            if root not in excluded:
                excluded.append(root)
            removed += len(graph['ordered_target_pids'])
            continue
        targets = [pid for pid in graph['ordered_target_pids'] if accepts(pid)]
        removed += len(graph['ordered_target_pids']) - len(targets)
        retained = copy.deepcopy(graph)
        retained['ordered_target_pids'] = targets
        filtered.append(retained)
    if not filtered:
        raise ValueError('No roots remain in the requested cluster scope')
    return filtered, {'excluded_root_pids': excluded, 'removed_target_count': removed,
                      'tags': requested, 'kind': kind}


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or output in {args.track_snapshot.resolve(), args.config.resolve()}:
        parser.error('Output must be new and separate from input files')
    tracks, library_hash, provenance = load_library(track_snapshot=args.track_snapshot)
    config_bytes = args.config.read_bytes()
    config = json.loads(config_bytes, object_pairs_hook=_unique_keys)
    classifications = classify_tracks(config, tracks, library_hash)
    report = {'schema_version': 1, 'source_library_sha256': library_hash, 'library_input': provenance,
              'config_sha256': hashlib.sha256(config_bytes).hexdigest(),
              'track_snapshot_sha256': provenance['snapshot_sha256'], 'classifications': classifications,
              'policy': {'tags': 'Cumulative free labels; AND across conditions, OR within each list',
                         'kind': 'unknown default; later matching rule wins; special-version marker follows rules; PID override last',
                         'overrides': 'Tags replace rather than accumulate; an empty tag list clears tags'},
              'network_requests': 0, 'recording_identity_inferred': False}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output), 'classified_tracks': len(classifications), 'network_requests': 0}))


if __name__ == '__main__':
    main()
