"""Rebase an accepted synthetic Genius index onto a fresh Music Library copy.

This is an offline artifact builder. It verifies every accepted observation
file before rebinding its wrapper to the zero-ID copy, rematches those raw
observations against the fresh track metadata, and writes only a separate
output directory. It does not launch Music.app or contact a service.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sqlite3

from decrypt_genius import derive_key, decrypt_pages, extract_key_header, validate_database
from evaluate_ytmusic_batch import candidate_graph, validate_observations
from genius_format import pack_similarities, parse_metadata, parse_similarities, sql_id
from inspect_music_library import decode_musicdb, parse_tracks, u32
from music_identity_map import IdentityMap, load_library, version_signature
from plan_genius_update import PRODUCER, _hash, _source
from prepare_music_genius_state import inspect_state_pair
from rewrite_genius import encrypt_pages, table_snapshot
from rewrite_music_ids import encode_library
from update_genius_bundle import CORE, read_bundle, state_rows


def sha(data):
    return hashlib.sha256(data).hexdigest()


def reset_library_ids(expanded):
    """Zero only Genius-ID slots in a supported expanded Library."""
    tracks, _ = parse_tracks(expanded)
    if any('genius_id' not in track for track in tracks):
        raise ValueError('Unsupported Library track header profile')
    start = expanded.find(b'ltma')
    if start < 0:
        raise ValueError('No track list')
    result = bytearray(expanded)
    offset = start + u32(expanded, start + 4)
    cleared = []
    for track in tracks:
        old = int(track['genius_id'], 16)
        if old:
            struct.pack_into('<I', result, offset + 0xCC, 0)
            cleared.append(track['persistent_id'])
        offset += u32(expanded, offset + 8)
    restored = bytearray(result)
    offset = start + u32(expanded, start + 4)
    for track in tracks:
        if int(track['genius_id'], 16):
            restored[offset + 0xCC:offset + 0xD0] = expanded[offset + 0xCC:offset + 0xD0]
        offset += u32(expanded, offset + 8)
    if restored != expanded:
        raise ValueError('Reset changed bytes outside Genius-ID slots')
    after, _ = parse_tracks(result)
    if len(after) != len(tracks):
        raise ValueError('Track count changed while resetting IDs')
    before = {t['persistent_id']: t for t in tracks}
    for track in after:
        original = before[track['persistent_id']]
        expected = {**original, 'genius_id': '0000000000000000'}
        if track != expected:
            raise ValueError('Unexpected field changed while resetting IDs')
    return bytes(result), cleared


def rebase_identity_map(document, tracks, old_library_hash, new_library_hash,
                        original_map_sha256=None):
    """Rebind a map while dropping PID title aliases stale for the new Library."""
    if document.get('library_sha256') != old_library_hash:
        raise ValueError('Identity map does not describe the baseline Library')
    by_pid = {t['persistent_id']: t for t in tracks}
    if len(by_pid) != len(tracks):
        raise ValueError('Duplicate local persistent IDs')
    result = json.loads(json.dumps(document))
    old_titles = result.get('track_title_aliases', [])
    retained, dropped = [], []
    for row in old_titles:
        pid = row.get('persistent_id')
        if pid in by_pid and row.get('original_title') == by_pid[pid].get('title'):
            retained.append(row)
        else:
            dropped.append(pid)
    result['track_title_aliases'] = retained
    result['library_sha256'] = new_library_hash
    if original_map_sha256 is not None:
        result['parent_identity_map_sha256'] = original_map_sha256
    matcher = IdentityMap(result, tracks, new_library_hash)
    artist_names = {matcher.artist_key(t.get('artist') or '') for t in tracks}
    return result, matcher, {
        'input_title_alias_count': len(old_titles),
        'retained_title_alias_count': len(retained),
        'dropped_stale_or_unknown_title_alias_pids': sorted(dropped),
        'enabled_artist_alias_group_count': len(matcher.artist_names),
        'enabled_artist_credit_label_count': len(matcher.credit_sets),
        'artist_aliases_validated_by_identity_map': True,
        'local_artist_key_count': len(artist_names),
        'parent_identity_map_sha256': original_map_sha256 or sha(json.dumps(document, ensure_ascii=False,
            sort_keys=True, separators=(',', ':')).encode()),
    }


def bind_identity_map_to_zero_id_source(document, tracks, uploaded_library_hash,
                                        zero_id_library_hash, source_map_sha256):
    """Bind the final matcher to the exact zero-ID source snapshot bytes."""
    if not isinstance(source_map_sha256, str) or not re.fullmatch('[a-f0-9]{64}', source_map_sha256):
        raise ValueError('Original identity-map source hash is required')
    result = json.loads(json.dumps(document))
    if result.get('library_sha256') not in (uploaded_library_hash, zero_id_library_hash):
        raise ValueError('Identity map is not bound to the uploaded or zero-ID Library')
    result['library_sha256'] = zero_id_library_hash
    result['parent_identity_map_sha256'] = source_map_sha256
    raw_source_map_bytes = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    result['source_rebinding'] = {
        'method': 'verified_zero_id_library_copy',
        'uploaded_original_library_sha256': uploaded_library_hash,
        'zero_id_library_sha256': zero_id_library_hash,
        'source_identity_map_sha256': source_map_sha256,
        'uploaded_source_identity_map_sha256': sha(raw_source_map_bytes),
    }
    matcher = IdentityMap(result, tracks, zero_id_library_hash)
    return result, matcher


def merge_title_alias_decisions(document, tracks, old_library_hash, new_library_hash,
                               original_map_sha256, decision_document,
                               decision_sha256, track_snapshot_sha256):
    """Build a fresh Library-bound map from the old map and scoped title decisions."""
    if (decision_document.get('library_sha256') != new_library_hash
            or decision_document.get('old_library_sha256') != old_library_hash
            or decision_document.get('old_identity_map_sha256') != original_map_sha256
            or decision_document.get('recording_identity') != 'unverified_metadata_candidate'
            or decision_document.get('alias_status') != 'metadata_alias_candidate'
            or decision_document.get('audit', {}).get('ambiguous_alias_collisions') != 0
            or decision_document.get('audit', {}).get('edges_created') != 0
            or decision_document.get('audit', {}).get('recordings_verified') != 0):
        raise ValueError('Title decision provenance does not match this rebase')
    rebound, _, audit = rebase_identity_map(
        document, tracks, old_library_hash, new_library_hash, original_map_sha256)
    by_pid = {track['persistent_id']: track for track in tracks}
    aliases = {row['persistent_id']: row for row in rebound['track_title_aliases']}
    decisions = decision_document.get('map_entries')
    if not isinstance(decisions, list):
        raise ValueError('Title decision sidecar lacks map entries')
    merged_count = 0
    rejected_version_aliases = []
    for entry in decisions:
        pid = entry.get('persistent_id')
        track = by_pid.get(pid)
        if track is None or entry.get('original_title') != track.get('title') or not entry.get('aliases'):
            raise ValueError('Stale or invalid title alias decision')
        existing = aliases.get(pid)
        if existing is None:
            existing = {'persistent_id': pid, 'original_title': track.get('title'), 'aliases': []}
            aliases[pid] = existing
        seen = {item['value'] for item in existing['aliases']}
        for item in entry['aliases']:
            if item.get('enabled') is not True or not isinstance(item.get('value'), str):
                raise ValueError('Title alias decision is not enabled and explicit')
            if version_signature(item['value']) != version_signature(track.get('title') or ''):
                # Retain only aliases that preserve every recognized facet.
                # Sidecars may propose both a complete old title and a suffix;
                # the latter can omit an instrumental/live/version marker.
                rejected_version_aliases.append({'persistent_id': pid,
                    'alias': item['value'], 'reason': 'version_facet_mismatch'})
                continue
            if item['value'] not in seen:
                existing['aliases'].append(item)
                seen.add(item['value'])
        merged_count += 1
    rebound['track_title_aliases'] = [aliases[pid] for pid in sorted(aliases)]
    rebound['library_input'] = {
        'kind': 'track_snapshot', 'snapshot_sha256': track_snapshot_sha256,
        'original_library_hash_verified_this_run': True,
    }
    rebound['title_alias_decision_provenance'] = {
        'sha256': decision_sha256, 'producer': decision_document.get('producer'),
        'alias_entries_merged': merged_count,
        'recording_identity': 'unverified_metadata_candidate',
    }
    matcher = IdentityMap(rebound, tracks, new_library_hash)
    audit.update(title_alias_decision_sha256=decision_sha256,
                 merged_title_alias_entry_count=merged_count,
                 rejected_version_facet_alias_count=len(rejected_version_aliases),
                 rejected_version_facet_aliases=rejected_version_aliases,
                 title_aliases_claim_recording_identity=False)
    return rebound, matcher, audit


def migrate_metadata(state, labels):
    """Keep GIDs; retain bytes for unchanged labels and safely re-key changed groups."""
    def freeze(value):
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        if isinstance(value, dict):
            return tuple(sorted((key, freeze(item)) for key, item in value.items()))
        return value

    old_rows = {row['persistent_id']: row for row in state['rows']}
    if not set(old_rows) <= labels.keys():
        raise ValueError('Fresh Library misses indexed PIDs')
    old_label_to_group = [{}, {}, {}]
    occupied = [set(), set(), set()]
    for row in old_rows.values():
        words = parse_metadata(bytes.fromhex(row['metadata_hex']))
        row_labels = row.get('equivalence_labels')
        if not isinstance(row_labels, list) or len(row_labels) != 3:
            raise ValueError('Invalid baseline equivalence labels')
        for i, (raw_label, gid) in enumerate(zip(row_labels, words[1:])):
            label = freeze(raw_label)
            if label in old_label_to_group[i] and old_label_to_group[i][label] != gid:
                raise ValueError('Baseline label maps to multiple metadata groups')
            if gid in occupied[i] and old_label_to_group[i].get(label) != gid:
                raise ValueError('Baseline metadata group merges distinct labels')
            old_label_to_group[i][label] = gid
            occupied[i].add(gid)
    new_label_to_group = [dict(groups) for groups in old_label_to_group]
    next_group = [max(values, default=0) + 1 for values in occupied]
    for pid in sorted(old_rows):
        for i, label in enumerate(labels[pid]):
            if label not in new_label_to_group[i]:
                if next_group[i] >= 1 << 64:
                    raise ValueError('Metadata group ID space exhausted')
                new_label_to_group[i][label] = next_group[i]
                next_group[i] += 1
    rows, changed, unchanged = [], [], []
    for pid, old in sorted(old_rows.items()):
        new_labels = labels[pid]
        unchanged_labels = tuple(freeze(label) for label in old['equivalence_labels']) == new_labels
        if unchanged_labels:
            metadata_hex = old['metadata_hex']
            unchanged.append(pid)
        else:
            metadata_hex = struct.pack('<4Q', 0, *(new_label_to_group[i][new_labels[i]] for i in range(3))).hex()
            changed.append(pid)
        rows.append({**old, 'metadata_hex': metadata_hex, 'equivalence_labels': new_labels})
    return rows, {
        'indexed_rows': len(rows),
        'unchanged_label_and_metadata_rows': len(unchanged),
        'changed_equivalence_label_rows': len(changed),
        'changed_label_pids': changed,
        'all_genius_ids_preserved': True,
        'new_group_ids_allocated_above_previous_maxima': True,
    }


def extend_index_rows(rows, labels, edges):
    """Assign IDs and consistent metadata to newly matched PIDs."""
    by_pid = {row['persistent_id']: row for row in rows}
    observed = {pid for root, targets in edges.items() for pid in (root, *targets)}
    new_pids = sorted(observed - by_pid.keys())
    if not set(observed) <= labels.keys():
        raise ValueError('Rematched graph refers to a missing new Library PID')
    used_ids = {row['genius_id'] for row in rows}
    cursor = max(used_ids, default=0) + 1
    label_groups = [{}, {}, {}]
    next_group = [1, 1, 1]
    for row in rows:
        groups = parse_metadata(bytes.fromhex(row['metadata_hex']))[1:]
        for i, (raw_label, group) in enumerate(zip(row['equivalence_labels'], groups)):
            label = _freeze_label(raw_label)
            if label in label_groups[i] and label_groups[i][label] != group:
                raise ValueError('Rebased metadata labels have inconsistent group IDs')
            label_groups[i][label] = group
            next_group[i] = max(next_group[i], group + 1)
    for pid in new_pids:
        while cursor in used_ids:
            cursor += 1
        gid = cursor
        cursor += 1
        used_ids.add(gid)
        groups = []
        for i, label in enumerate(labels[pid]):
            if label not in label_groups[i]:
                label_groups[i][label] = next_group[i]
                next_group[i] += 1
            groups.append(label_groups[i][label])
        by_pid[pid] = {
            'persistent_id': pid, 'genius_id': gid,
            'metadata_hex': struct.pack('<4Q', 0, *groups).hex(),
            'similarities_hex': pack_similarities(0, []).hex(),
            'equivalence_labels': labels[pid],
        }
    return [by_pid[pid] for pid in sorted(by_pid)], {
        'newly_indexed_pid_count': len(new_pids),
        'newly_indexed_pids': new_pids,
        'output_indexed_pid_count': len(by_pid),
        'new_pid_assignments_unique': len(used_ids) == len(by_pid),
    }


def _freeze_label(value):
    if isinstance(value, list):
        return tuple(_freeze_label(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze_label(item)) for key, item in value.items()))
    return value


def revalidate_accepted(accepted_wrappers, old_library_hash, new_library_hash,
                       matcher, baseline_state, accepted_provenance):
    """Verify raw source bytes, re-match, then retain only unchanged PID edges."""
    old_ids, _, old_relations = state_rows(baseline_state)
    reverse = {gid: pid for pid, gid in old_ids.items()}
    old_edges = {pid: [reverse[target] for target in parse_similarities(old_relations[gid])[1]]
                 for pid, gid in old_ids.items()}
    supported, skipped = [], []
    verified_inputs = []
    for wrapper in accepted_wrappers:
        entry = wrapper.get('input')
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise ValueError('Accepted graph lacks source observation path')
        path = Path(entry['path'])
        raw = path.read_bytes()
        raw_hash = sha(raw)
        if raw_hash != entry.get('sha256'):
            raise ValueError('Accepted observation hash mismatch: ' + str(path))
        source = json.loads(raw)
        source_library_hash = source.get('library_sha256')
        if source_library_hash not in (old_library_hash, None):
            raise ValueError('Observation wrapper source is not the verified old Library')
        anchored = (entry['path'], entry['sha256']) in set(
            tuple(value) for value in accepted_provenance.get('anchored_observation_inputs', []))
        if source_library_hash is None and not anchored:
            raise ValueError('Unbound observation cannot be transparently rebased')
        # Transparent wrapper: exact original bytes were verified above; only
        # the source hash envelope changes for in-memory rematching.
        rebound = {**source, 'library_sha256': new_library_hash}
        verified_inputs.append({'path': str(path), 'sha256': raw_hash,
                                'old_source_library_sha256': old_library_hash})
        try:
            validate_observations(rebound, new_library_hash)
            graph = candidate_graph(rebound, matcher, new_library_hash)
        except ValueError as error:
            skipped.append({'path': str(path), 'reason': str(error) or type(error).__name__,
                            'old_root_pid': wrapper.get('root_pid')})
            continue
        if graph['root_pid'] != wrapper.get('root_pid') or graph['root_pid'] not in old_edges:
            skipped.append({'path': str(path), 'reason': 'root_pid_changed_or_unindexed',
                            'old_root_pid': wrapper.get('root_pid'), 'new_root_pid': graph['root_pid']})
            continue
        old_targets = set(old_edges[graph['root_pid']])
        matched_targets = set(graph['ordered_target_pids'])
        if matched_targets:
            supported.append(graph)
        dropped = old_targets - matched_targets
        if dropped:
            skipped.append({'path': str(path), 'reason': 'target_pid_no_longer_matches_new_metadata',
                            'root_pid': graph['root_pid'], 'dropped_target_pids': sorted(dropped)})
    evidence_by_root = {}
    evidence_order_by_root = {}
    for graph in supported:
        root = graph['root_pid']
        evidence_by_root.setdefault(root, set()).update(graph['ordered_target_pids'])
        ordered = evidence_order_by_root.setdefault(root, [])
        for pid in graph['ordered_target_pids']:
            if pid not in ordered:
                ordered.append(pid)
    output_edges = {}
    retained_old_count = 0
    new_observed_count = 0
    for root, targets in old_edges.items():
        confirmed = evidence_by_root.get(root, set())
        stable_old = [pid for pid in targets if pid in confirmed]
        added = [pid for pid in evidence_order_by_root.get(root, []) if pid not in set(targets)]
        retained_old_count += len(stable_old)
        new_observed_count += len(added)
        output_edges[root] = [*stable_old, *added]
    old_edge_count = sum(len(v) for v in old_edges.values())
    new_edge_count = sum(len(v) for v in output_edges.values())
    return output_edges, {
        'accepted_wrapper_count': len(accepted_wrappers),
        'source_observation_count': len(verified_inputs),
        'all_original_observation_hashes_verified': len(verified_inputs) == len(accepted_wrappers),
        'rematched_wrapper_count': len(supported),
        'rematched_root_count': len(evidence_by_root),
        'old_directed_edge_count': old_edge_count,
        'retained_old_directed_edge_count': retained_old_count,
        'new_rematched_directed_edge_count': new_observed_count,
        'output_directed_edge_count': new_edge_count,
        'dropped_old_directed_edge_count': old_edge_count - retained_old_count,
        'roots_with_changed_or_missing_edges': sum(output_edges[pid] != targets for pid, targets in old_edges.items()),
        'all_retained_edges_same_pid_and_fresh_metadata_match': True,
        'new_matched_edge_count': new_observed_count,
        'missing_library_hash_observations_bound_to_accepted_manifest':
            sum(1 for item in verified_inputs
                if (Path(item['path']).is_file()
                    and json.loads(Path(item['path']).read_text()).get('library_sha256') is None
                    and (item['path'], item['sha256']) in set(
                        tuple(value) for value in accepted_provenance.get('anchored_observation_inputs', [])))),
        'accepted_input_manifest': accepted_provenance,
        'raw_input_provenance': verified_inputs,
        'skipped_or_dropped': skipped,
    }


def _make_state(rows, edges, labels, source):
    by_pid = {row['persistent_id']: row for row in rows}
    ids = {pid: row['genius_id'] for pid, row in by_pid.items()}
    for row in rows:
        targets = edges.get(row['persistent_id'], [])
        row['similarities_hex'] = pack_similarities(0, [ids[pid] for pid in targets]).hex()
    state = {'schema_version': 1, 'producer': PRODUCER, 'source': source,
             'rows': rows}
    state['state_sha256'] = _hash(state)
    return state


def rebase_sql(clear, baseline, next_state):
    """Apply explicitly audited metadata/relation changes to the old DB rows."""
    old_ids, old_metadata, old_relations = state_rows(baseline)
    ids, metadata, relations = state_rows(next_state)
    if any(ids.get(pid) != gid for pid, gid in old_ids.items()):
        raise ValueError('Rebase changed an established Genius ID')
    validate_database(clear)
    with sqlite3.connect(':memory:') as connection:
        connection.deserialize(clear)
        before = table_snapshot(connection)
        schema = connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        if any(rows for name, rows in before.items()
               if name not in {'genius_metadata', 'genius_similarities', 'genius_config'}):
            raise ValueError('Nonempty auxiliary SQL data cannot be rebased')
        for name, rows in (('genius_metadata', old_metadata), ('genius_similarities', old_relations)):
            if before[name] != [(sql_id(gid), 1, blob) for gid, blob in sorted(rows.items())]:
                raise ValueError('Baseline SQL rows differ from sealed state: ' + name)
        old_gids = set(old_metadata)
        if old_gids - set(metadata) or old_gids - set(relations):
            raise ValueError('Rebase dropped established SQL rows')
        config = connection.execute('SELECT id,version,data FROM genius_config ORDER BY id').fetchall()
        if len(config) != 1 or config[0][:2] != (1, 2):
            raise ValueError('Unsupported Genius configuration')
        expected = dict(before)
        expected['genius_metadata'] = [(sql_id(gid), 1, blob) for gid, blob in sorted(metadata.items())]
        expected['genius_similarities'] = [(sql_id(gid), 1, blob) for gid, blob in sorted(relations.items())]
        with connection:
            connection.executemany('UPDATE genius_metadata SET data=? WHERE genius_id=?',
                                   [(metadata[gid], sql_id(gid)) for gid in old_gids])
            connection.executemany('INSERT INTO genius_metadata VALUES(?,1,?)',
                                   [(sql_id(gid), metadata[gid]) for gid in sorted(set(metadata) - old_gids)])
            connection.executemany('UPDATE genius_similarities SET data=? WHERE genius_id=?',
                                   [(relations[gid], sql_id(gid)) for gid in old_gids])
            connection.executemany('INSERT INTO genius_similarities VALUES(?,1,?)',
                                   [(sql_id(gid), relations[gid]) for gid in sorted(set(relations) - old_gids)])
            if table_snapshot(connection) != expected:
                raise ValueError('Unexpected SQL rows after rebase')
            if connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall() != schema:
                raise ValueError('SQL schema changed during rebase')
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Rebased SQL integrity check failed')
        return connection.serialize(), expected


def build_rebased_bundle(new_bundle_path, baseline_bundle_path, baseline_state,
                         accepted_wrappers, identity_document, output_path, executable,
                         original_map_sha256=None, old_track_snapshot=None,
                         new_identity_document=None, new_map_sha256=None,
                         allow_unverified_profile=False,
                         title_alias_decisions=None, title_alias_decision_sha256=None,
                         accepted_provenance=None):
    new_bundle_path = Path(new_bundle_path).resolve()
    baseline_bundle_path = Path(baseline_bundle_path).resolve()
    output_path = Path(output_path).resolve()
    if output_path.exists() or output_path.is_relative_to(new_bundle_path) or output_path.is_relative_to(baseline_bundle_path):
        raise ValueError('Output must be new and separate from source bundles')
    raw_files = read_bundle(new_bundle_path)
    base_files = read_bundle(baseline_bundle_path)
    new_raw_library = raw_files['Library.musicdb']
    original_hash = sha(new_raw_library)
    expanded_raw = decode_musicdb(new_raw_library)
    raw_tracks, _ = parse_tracks(expanded_raw)
    zero_expanded, cleared_pids = reset_library_ids(expanded_raw)
    zero_library = encode_library(zero_expanded, new_raw_library,
                                  allow_unverified_profile=allow_unverified_profile)
    zero_hash = sha(zero_library)
    tracks, _ = parse_tracks(zero_expanded)
    if original_map_sha256 is None or original_map_sha256 != baseline_state['source']['identity_map_sha256']:
        raise ValueError('Identity map bytes do not match baseline state provenance')
    if old_track_snapshot is None:
        raise ValueError('Verified old track snapshot is required for metadata comparison')
    old_tracks, old_hash, _ = load_library(track_snapshot=Path(old_track_snapshot))
    if old_hash != baseline_state['source']['library_sha256']:
        raise ValueError('Old track snapshot does not match baseline state Library hash')
    old_matcher = IdentityMap(identity_document, old_tracks, old_hash)
    old_source, _ = _source(old_matcher, old_hash, original_map_sha256)
    if old_source != baseline_state['source']:
        raise ValueError('Baseline state source hashes do not match snapshot and identity map')
    if new_identity_document is None and title_alias_decisions is not None:
        raw_snapshot = {'schema_version': 1, 'source_sha256': original_hash, 'tracks': raw_tracks}
        raw_snapshot_bytes = (json.dumps(raw_snapshot, ensure_ascii=False, indent=2) + '\n').encode()
        matcher_doc, matcher, map_audit = merge_title_alias_decisions(
            identity_document, raw_tracks, baseline_state['source']['library_sha256'],
            original_hash, original_map_sha256, title_alias_decisions,
            title_alias_decision_sha256, sha(raw_snapshot_bytes))
        new_identity_document = matcher_doc
        new_map_sha256 = sha((json.dumps(matcher_doc, ensure_ascii=False, indent=2) + '\n').encode())
    elif new_identity_document is None:
        matcher_doc, matcher, map_audit = rebase_identity_map(
            identity_document, tracks, baseline_state['source']['library_sha256'], zero_hash,
            original_map_sha256)
    else:
        if new_map_sha256 is None or new_identity_document.get('library_sha256') != original_hash:
            raise ValueError('Fresh identity map must be hash-provenanced to the uploaded Library')
        matcher_doc, matcher, map_audit = rebase_identity_map(
            new_identity_document, tracks, original_hash, zero_hash, new_map_sha256)
        alias_provenance = matcher_doc.get('title_alias_decision_provenance')
        if isinstance(alias_provenance, dict):
            map_audit.update(title_alias_decision_provenance=alias_provenance,
                             title_aliases_claim_recording_identity=False)
    raw_library_matcher_doc = json.loads(json.dumps(matcher_doc))
    matcher_doc, matcher = bind_identity_map_to_zero_id_source(
        matcher_doc, tracks, original_hash, zero_hash, original_map_sha256)
    map_audit.update(source_binding_verified=True,
                     source_identity_map_sha256=original_map_sha256,
                     uploaded_source_identity_map_sha256=matcher_doc['source_rebinding']['uploaded_source_identity_map_sha256'],
                     zero_id_library_sha256=zero_hash)
    snapshot = {'schema_version': 1, 'source_sha256': zero_hash, 'tracks': tracks}
    snapshot_bytes = (json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n').encode()
    matcher_doc['library_input'] = {
        'kind': 'track_snapshot', 'snapshot_sha256': sha(snapshot_bytes),
        'original_library_hash_verified_this_run': True,
        'uploaded_original_library_sha256': original_hash,
    }
    map_bytes = (json.dumps(matcher_doc, ensure_ascii=False, indent=2) + '\n').encode()
    map_hash = sha(map_bytes)
    from plan_genius_update import _source as source_labels
    source, labels = source_labels(matcher, zero_hash, map_hash)
    if set(row['persistent_id'] for row in baseline_state['rows']) - set(labels):
        raise ValueError('New zero-ID Library misses indexed PIDs')
    rows, metadata_audit = migrate_metadata(baseline_state, labels)
    edges, edge_audit = revalidate_accepted(
        accepted_wrappers, baseline_state['source']['library_sha256'], zero_hash,
        matcher, baseline_state, accepted_provenance or {})
    rows, index_growth_audit = extend_index_rows(rows, labels, edges)
    old_track_map = {t['persistent_id']: t for t in old_tracks}
    new_track_map = {t['persistent_id']: t for t in tracks}
    indexed_pids = {row['persistent_id'] for row in baseline_state['rows']}
    common_indexed = indexed_pids & set(old_track_map) & set(new_track_map)
    duration_deltas = {pid: (new_track_map[pid].get('duration_ms') or 0) -
                       (old_track_map[pid].get('duration_ms') or 0) for pid in common_indexed}
    duration_changes = [abs(delta) for delta in duration_deltas.values() if delta]
    edge_duration_changes = sum(bool(duration_deltas.get(root, 0) or duration_deltas.get(target, 0))
                                for root, targets in edges.items() for target in targets)
    next_state = _make_state(rows, edges, labels, source)

    # The fresh uploaded Library is used for output. Preserve its original 286
    # nonzero IDs where present, and add assignments for its currently zero IDs.
    from rewrite_music_ids import patch_ids
    by_pid = {t['persistent_id']: t for t in raw_tracks}
    old_ids, _, _ = state_rows(baseline_state)
    rebased_ids, _, _ = state_rows(next_state)
    assignments = [{'persistent_id': pid, 'genius_id': f'{rebased_ids[pid]:016X}'}
                   for pid in sorted(rebased_ids) if int(by_pid[pid].get('genius_id', '0'), 16) == 0]
    patched_expanded, id_changes = patch_ids(expanded_raw, assignments) if assignments else (expanded_raw, [])
    # Existing nonzero IDs must already agree with accepted PID assignments.
    for pid, track in by_pid.items():
        gid = int(track.get('genius_id', '0'), 16)
        if gid and (pid not in rebased_ids or gid != rebased_ids[pid]):
            raise ValueError('Uploaded nonzero Genius ID conflicts with accepted state: ' + pid)
    output_library = encode_library(patched_expanded, new_raw_library,
                                    allow_unverified_profile=allow_unverified_profile)
    saved_tracks, _ = parse_tracks(decode_musicdb(output_library))
    if any(int(t.get('genius_id', '0'), 16) != rebased_ids.get(t['persistent_id'], 0) for t in saved_tracks):
        raise ValueError('Final Library ID assignment differs from rebased state')

    pair = inspect_state_pair(raw_files)
    if not pair['key_headers_match']:
        raise ValueError('Uploaded Library and Genius preferences do not share key headers')
    old_key, _ = derive_key(executable, extract_key_header(base_files['Library Preferences.musicdb']))
    old_clear = decrypt_pages(base_files['Genius.itdb'], old_key)
    clear, expected_tables = rebase_sql(old_clear, baseline_state, next_state)
    new_key, _ = derive_key(executable, extract_key_header(raw_files['Library Preferences.musicdb']))
    encrypted = encrypt_pages(clear, new_key)
    recovered = decrypt_pages(encrypted, new_key)
    counts = validate_database(recovered)
    with sqlite3.connect(':memory:') as connection:
        connection.deserialize(recovered)
        if table_snapshot(connection) != expected_tables:
            raise ValueError('Re-encrypted Genius database differs from planned rows')
    output_files = {**raw_files, 'Library.musicdb': output_library, 'Genius.itdb': encrypted}
    if inspect_state_pair(output_files) != pair:
        raise ValueError('Uploaded local Genius preferences changed')
    report = {
        'schema_version': 1,
        'uploaded_original_library_sha256': original_hash,
        'legacy_library_profile': new_raw_library[16:48].split(b'\0', 1)[0].decode('ascii', 'replace'),
        'allow_unverified_profile_override_used': allow_unverified_profile,
        'legacy_rewrite_may_be_reset_by_music': allow_unverified_profile,
        'zero_id_library_sha256': zero_hash,
        'final_library_sha256': sha(output_library),
        'baseline_state_sha256': baseline_state['state_sha256'],
        'next_state_sha256': next_state['state_sha256'],
        'new_source_file_sha256': {name: sha(blob) for name, blob in raw_files.items()},
        'baseline_bundle_file_sha256': {name: sha(blob) for name, blob in base_files.items()},
        'identity_map_sha256': map_hash,
        'track_count': len(saved_tracks),
        'indexed_tracks': len(next_state['rows']),
        'index_growth': index_growth_audit,
        'cleared_uploaded_id_count_for_zero_id_snapshot': len(cleared_pids),
        'retained_uploaded_preexisting_id_count': len(cleared_pids),
        'newly_assigned_id_count': len(id_changes),
        'baseline_to_new_indexed_metadata': {
            'common_indexed_pids': len(common_indexed),
            'title_differences': sum(old_track_map[p].get('title') != new_track_map[p].get('title') for p in common_indexed),
            'artist_differences': sum(old_track_map[p].get('artist') != new_track_map[p].get('artist') for p in common_indexed),
            'album_differences': sum(old_track_map[p].get('album') != new_track_map[p].get('album') for p in common_indexed),
            'duration_differences': len(duration_changes),
            'duration_differences_within_3s': sum(delta <= 3000 for delta in duration_changes),
            'duration_differences_over_3s': sum(delta > 3000 for delta in duration_changes),
            'maximum_duration_delta_ms': max(duration_changes, default=0),
            'retained_edges_with_duration_change_on_an_endpoint': edge_duration_changes,
        },
        'all_prior_genius_ids_preserved': all(
            old_ids[row['persistent_id']] == row['genius_id'] for row in baseline_state['rows']),
        'final_library_preserves_original_track_metadata': all(
            {k: v for k, v in track.items() if k != 'genius_id'} ==
            {k: v for k, v in by_pid[track['persistent_id']].items() if k != 'genius_id'}
            for track in saved_tracks),
        'rebase_identity_map': map_audit,
        'metadata_rebase': metadata_audit,
        'relation_revalidation': edge_audit,
        'source_auxiliary_files_preserved': all(output_files[k] == raw_files[k]
                                                for k in raw_files if k not in ('Library.musicdb', 'Genius.itdb')),
        'config_and_auxiliary_sql_preserved': True,
        'encrypted_round_trip_verified': True,
        'sql_integrity_check': 'ok',
        'table_counts': counts,
        'network_requests': 0,
        'music_app_acceptance_verified': False,
        'ipod_acceptance_verified': False,
    }
    if read_bundle(new_bundle_path) != raw_files or read_bundle(baseline_bundle_path) != base_files:
        raise ValueError('Source bundle changed while the rebase was being prepared')
    for entry in edge_audit['raw_input_provenance']:
        if sha(Path(entry['path']).read_bytes()) != entry['sha256']:
            raise ValueError('Accepted observation changed while the rebase was being prepared')
    output_path.mkdir(mode=0o700)
    zero_dir = output_path / 'zero-id-source'
    zero_dir.mkdir(mode=0o700)
    (zero_dir / 'Library.musicdb').write_bytes(zero_library)
    (zero_dir / 'track-snapshot.json').write_bytes(snapshot_bytes)
    (zero_dir / 'raw-library-identity-map.json').write_text(
        json.dumps(raw_library_matcher_doc, ensure_ascii=False, indent=2) + '\n')
    (zero_dir / 'identity-map.json').write_bytes(map_bytes)
    out_bundle = output_path / 'Music Library.musiclibrary'
    out_bundle.mkdir(mode=0o700)
    for name, blob in output_files.items():
        target = out_bundle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    for name, document in (('state.json', next_state), ('report.json', report)):
        (output_path / name).write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('new-bundle', 'baseline-bundle', 'baseline-state', 'accepted-snapshots',
                 'accepted-run-inputs', 'identity-map', 'old-track-snapshot', 'executable', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--new-identity-map', type=Path)
    parser.add_argument('--title-alias-decisions', type=Path)
    parser.add_argument('--accepted-provenance-snapshot', type=Path,
                        help='Earlier accepted snapshot file bound by accepted-run-inputs')
    parser.add_argument('--allow-unverified-profile', action='store_true',
                        help='Explicitly allow writing a legacy 1.5.6.11 Library copy that Music may reset')
    args = parser.parse_args()
    state = json.loads(args.baseline_state.read_text())
    accepted = json.loads(args.accepted_snapshots.read_text())
    run_inputs = json.loads(args.accepted_run_inputs.read_text())
    mapping = json.loads(args.identity_map.read_text())
    map_hash = sha(args.identity_map.read_bytes())
    accepted_hash = sha(args.accepted_snapshots.read_bytes())
    old_snapshot_hash = sha(args.old_track_snapshot.read_bytes())
    input_hashes = run_inputs.get('input_sha256', {})
    provenance_snapshot_path = args.accepted_provenance_snapshot or args.accepted_snapshots
    provenance_snapshot = json.loads(provenance_snapshot_path.read_text())
    provenance_snapshot_hash = sha(provenance_snapshot_path.read_bytes())
    if (input_hashes.get('accepted_snapshots') != provenance_snapshot_hash
            or input_hashes.get('identity_map') != map_hash
            or run_inputs.get('library_input', {}).get('snapshot_sha256') != old_snapshot_hash):
        parser.error('Accepted snapshots manifest does not bind the baseline snapshot/map/input bytes')
    anchored_inputs = []
    for wrapper in provenance_snapshot:
        source = wrapper.get('input', {})
        if isinstance(source.get('path'), str) and isinstance(source.get('sha256'), str):
            anchored_inputs.append([source['path'], source['sha256']])
    current_inputs = {(wrapper.get('input', {}).get('path'), wrapper.get('input', {}).get('sha256'))
                      for wrapper in accepted}
    if not set(map(tuple, anchored_inputs)) <= current_inputs:
        parser.error('Current accepted snapshots omit inputs bound by the source manifest')
    accepted_provenance = {
        'accepted_snapshots_manifest_sha256': sha(args.accepted_run_inputs.read_bytes()),
        'current_accepted_snapshots_sha256': accepted_hash,
        'manifest_accepted_snapshots_sha256': provenance_snapshot_hash,
        'old_track_snapshot_sha256': old_snapshot_hash,
        'old_identity_map_sha256': map_hash,
        'anchored_observation_inputs': anchored_inputs,
    }
    new_map_path = args.new_identity_map
    new_mapping = json.loads(new_map_path.read_text()) if new_map_path else None
    new_map_hash = sha(new_map_path.read_bytes()) if new_map_path else None
    decision_path = args.title_alias_decisions
    decisions = json.loads(decision_path.read_text()) if decision_path else None
    decisions_hash = sha(decision_path.read_bytes()) if decision_path else None
    report = build_rebased_bundle(args.new_bundle, args.baseline_bundle, state, accepted,
                                  mapping, args.output, args.executable, map_hash,
                                  args.old_track_snapshot, new_mapping, new_map_hash,
                                  args.allow_unverified_profile, decisions, decisions_hash,
                                  accepted_provenance)
    print(json.dumps({k: report[k] for k in ('track_count', 'indexed_tracks', 'final_library_sha256',
                                             'zero_id_library_sha256', 'relation_revalidation')}))


if __name__ == '__main__':
    main()
