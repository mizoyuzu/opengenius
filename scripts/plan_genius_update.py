"""Pure incremental plans for OpenGenius synthetic rows; never Apple DB edits."""
import hashlib
import json
import re
import struct

from evaluate_ytmusic_batch import shared_rows
from genius_format import pack_similarities, parse_metadata, parse_similarities
from music_identity_map import normalize

PRODUCER = 'opengenius-synthetic-rows-v1'
MAX_GID = (1 << 32) - 1
MAX_GROUP_ID = (1 << 64) - 1


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _source(matcher, library_hash, identity_hash):
    if any(not isinstance(value, str) or re.fullmatch('[a-f0-9]{64}', value) is None
           for value in (library_hash, identity_hash)):
        raise ValueError('Source hashes must be SHA-256')
    tracks = {track['persistent_id']: track for track in matcher.tracks}
    if len(tracks) != len(matcher.tracks):
        raise ValueError('Duplicate source PID')
    labels = {}
    for pid, track in tracks.items():
        if not isinstance(pid, str) or re.fullmatch('[A-F0-9]{16}', pid) is None:
            raise ValueError('Invalid source PID')
        credits = tuple(sorted(matcher.artist_credits(track.get('artist') or '')))
        labels[pid] = (credits, normalize(track.get('album') or ''),
                       (credits, normalize(track.get('title') or '')))
    return dict(library_sha256=library_hash, identity_map_sha256=identity_hash,
                tracks_sha256=_hash([tracks[pid] for pid in sorted(tracks)]),
                equivalence_labels_sha256=_hash(labels)), labels


def _roots(roots, labels):
    if not isinstance(roots, (list, tuple)) or not roots:
        raise ValueError('Need an explicit nonempty full root graph')
    edges = {}
    for row in roots:
        if not isinstance(row, dict):
            raise ValueError('Invalid full graph row')
        pid, targets = row.get('root_pid'), row.get('ordered_target_pids')
        if not isinstance(pid, str) or pid not in labels or pid in edges:
            raise ValueError('Unknown or duplicate graph root')
        if not isinstance(targets, (list, tuple)) or any(not isinstance(target, str) or target not in labels for target in targets):
            raise ValueError('Unknown graph target')
        if len(set(targets)) != len(targets):
            raise ValueError('Duplicate graph target')
        edges[pid] = list(targets)
    return edges


def _seal(state):
    return {**state, 'state_sha256': _hash(state)}


def make_synthetic_state(roots, matcher, source_library_sha256, identity_map_sha256):
    """Create a dedicated baseline state from this project's synthetic builder.

    Callers may compare these rows to an existing OpenGenius experiment, but
    this function does not import arbitrary Apple metadata or database files.
    """
    source, labels = _source(matcher, source_library_sha256, identity_map_sha256)
    _roots(roots, labels)
    ids, metadata, similarities, _ = shared_rows(roots, matcher)
    return _seal(dict(schema_version=1, producer=PRODUCER, source=source,
                      rows=[dict(persistent_id=pid, genius_id=gid,
                                 metadata_hex=metadata[gid].hex(), similarities_hex=similarities[gid].hex(),
                                 equivalence_labels=labels[pid]) for pid, gid in sorted(ids.items())]))


def _existing_rows(state, source, labels):
    if (not isinstance(state, dict) or type(state.get('schema_version')) is not int
            or state.get('schema_version') != 1 or state.get('producer') != PRODUCER):
        raise ValueError('Only dedicated OpenGenius synthetic states are supported')
    unsigned = {key: value for key, value in state.items() if key != 'state_sha256'}
    if state.get('state_sha256') != _hash(unsigned):
        raise ValueError('State integrity mismatch')
    if state.get('source') != source:
        raise ValueError('Source changed; regenerate and review instead of incrementing')
    if not isinstance(state.get('rows'), list) or not state['rows']:
        raise ValueError('Existing state needs rows')
    ids, metadata, similarities = {}, {}, {}
    label_groups = [{}, {}, {}]
    group_labels = [{}, {}, {}]
    for row in state['rows']:
        if not isinstance(row, dict):
            raise ValueError('Invalid existing row')
        pid, gid = row.get('persistent_id'), row.get('genius_id')
        if (not isinstance(pid, str) or pid not in labels or pid in ids
                or type(gid) is not int or not 1 <= gid <= MAX_GID or gid in metadata):
            raise ValueError('Invalid or duplicate PID/GID assignment')
        if _hash(row.get('equivalence_labels')) != _hash(labels[pid]):
            raise ValueError('Existing equivalence labels differ from source')
        try:
            blob = bytes.fromhex(row['metadata_hex'])
            relations = bytes.fromhex(row['similarities_hex'])
            words = parse_metadata(blob)
            parse_similarities(relations)
        except (KeyError, TypeError, ValueError):
            raise ValueError('Invalid existing metadata or relation envelope') from None
        if words[0] != 0:
            raise ValueError('Synthetic state requires genre zero')
        for index, (label, group) in enumerate(zip(labels[pid], words[1:])):
            if (not group or label_groups[index].get(label, group) != group
                    or group_labels[index].get(group, label) != label):
                raise ValueError('Metadata group and equivalence label mismatch')
            label_groups[index][label] = group
            group_labels[index][group] = label
        ids[pid], metadata[gid], similarities[gid] = gid, blob, relations
    if any(target not in metadata for blob in similarities.values() for target in parse_similarities(blob)[1]):
        raise ValueError('Existing relation targets an unassigned ID')
    return ids, metadata, similarities, label_groups


def plan_genius_update(state, full_roots, matcher, source_library_sha256, identity_map_sha256):
    """Preserve old assignments/metadata and plan explicit full directed rows.

    Removed roots receive empty outgoing rows. Old metadata-only nodes remain
    assigned. No relationship is carried over merely because it existed before.
    """
    source, labels = _source(matcher, source_library_sha256, identity_map_sha256)
    ids, metadata, old_similarities, groups = _existing_rows(state, source, labels)
    edges = _roots(full_roots, labels)
    new_pids = sorted({pid for root, targets in edges.items() for pid in (root, *targets)} - set(ids))
    old_ids, old_metadata = ids.copy(), metadata.copy()
    occupied = set(ids.values())
    if len(occupied) + len(new_pids) > MAX_GID:
        raise ValueError('Synthetic Genius ID range exhausted')
    cursor = max(occupied) + 1
    if cursor > MAX_GID:
        cursor = 1
    for pid in new_pids:
        while cursor in occupied:
            cursor += 1
            if cursor > MAX_GID:
                cursor = 1
        ids[pid] = cursor
        occupied.add(cursor)
        cursor += 1
        if cursor > MAX_GID:
            cursor = 1
        words = [0]
        for index, label in enumerate(labels[pid]):
            if label not in groups[index]:
                next_group = max(groups[index].values(), default=0) + 1
                if next_group > MAX_GROUP_ID:
                    raise ValueError('Synthetic group ID range exhausted')
                groups[index][label] = next_group
            words.append(groups[index][label])
        metadata[ids[pid]] = struct.pack('<4Q', *words)
    similarities = {gid: pack_similarities(0, [ids[target] for target in edges.get(pid, [])])
                    for pid, gid in ids.items()}
    reverse = {gid: pid for pid, gid in ids.items()}
    deltas = []
    for pid in sorted(ids):
        before = [reverse[gid] for gid in parse_similarities(old_similarities[old_ids[pid]])[1]] if pid in old_ids else []
        after = edges.get(pid, [])
        if before != after or pid not in old_ids:
            deltas.append(dict(root_pid=pid, new_assignment=pid not in old_ids,
                               ordered_targets_before=before, ordered_targets_after=after,
                               added_target_pids=sorted(set(after) - set(before)),
                               removed_target_pids=sorted(set(before) - set(after)),
                               target_order_changed=set(before) == set(after) and before != after))
    next_state = _seal(dict(schema_version=1, producer=PRODUCER, source=source,
                            rows=[dict(persistent_id=pid, genius_id=gid, metadata_hex=metadata[gid].hex(),
                                       similarities_hex=similarities[gid].hex(), equivalence_labels=labels[pid])
                                  for pid, gid in sorted(ids.items())]))
    return dict(schema_version=1, operation='offline_synthetic_incremental_plan',
                previous_state_sha256=state['state_sha256'], next_state=next_state,
                new_pids=new_pids, relation_deltas=deltas,
                audit=dict(existing_assignments_preserved=all(ids[pid] == gid for pid, gid in old_ids.items()),
                           existing_metadata_bytes_preserved=all(metadata[gid] == blob for gid, blob in old_metadata.items()),
                           old_tracks=len(old_ids), added_tracks=len(new_pids), total_tracks=len(ids),
                           directed_edges=sum(len(targets) for targets in edges.values()),
                           source_unchanged=True, reverse_edges_invented=False),
                network_requests=0, files_written=0, music_app_acceptance_verified=False, ipod_acceptance_verified=False)
