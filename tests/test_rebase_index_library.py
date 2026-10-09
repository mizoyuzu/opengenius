import struct
import hashlib
import sys
import unittest
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from rebase_index_library import (extend_index_rows, migrate_metadata,
                                  merge_title_alias_decisions, rebase_identity_map,
                                  bind_identity_map_to_zero_id_source)
from music_identity_map import IdentityMap


def _row(pid, gid, groups, labels):
    return {
        'persistent_id': pid,
        'genius_id': gid,
        'metadata_hex': struct.pack('<4Q', 0, *groups).hex(),
        'similarities_hex': '0000000000000000',
        'equivalence_labels': json.loads(json.dumps(labels)),
    }


class RebaseIndexLibraryTests(unittest.TestCase):
    def test_metadata_migration_preserves_stable_rows_and_reuses_group_labels(self):
        stable_labels = (('artist-a',), 'album-a', ('recording-a',))
        old = {
            'rows': [
                _row('0000000000000001', 101, (11, 21, 31), stable_labels),
                _row('0000000000000002', 102, (11, 22, 32), [stable_labels[0], 'album-b', ('recording-b',)]),
            ]
        }
        new_labels = {
            '0000000000000001': stable_labels,
            '0000000000000002': [stable_labels[0], 'album-b', ('recording-new-b',)],
        }

        rows, audit = migrate_metadata(old, new_labels)

        self.assertEqual([row['genius_id'] for row in rows], [101, 102])
        self.assertEqual(rows[0]['metadata_hex'], old['rows'][0]['metadata_hex'])
        self.assertEqual(rows[0]['equivalence_labels'], stable_labels)
        self.assertEqual(rows[1]['equivalence_labels'], new_labels['0000000000000002'])
        self.assertEqual(struct.unpack('<4Q', bytes.fromhex(rows[1]['metadata_hex'])), (0, 11, 22, 33))
        self.assertEqual(audit['unchanged_label_and_metadata_rows'], 1)
        self.assertEqual(audit['changed_equivalence_label_rows'], 1)
        self.assertTrue(audit['all_genius_ids_preserved'])

    def test_rebase_identity_map_drops_stale_title_aliases_and_binds_new_hash(self):
        tracks = [{'persistent_id': '0000000000000001', 'title': 'fresh title', 'artist': 'Artist',
                   'duration_ms': 180000}]
        document = {
            'schema_version': 1,
            'library_sha256': 'a' * 64,
            'artist_alias_groups': [],
            'artist_credit_sets': [],
            'recording_links': [],
            'track_title_aliases': [{
                'persistent_id': tracks[0]['persistent_id'],
                'original_title': 'old title',
                'aliases': [{'value': 'old alias', 'enabled': True}],
            }],
        }

        rebound, matcher, audit = rebase_identity_map(document, tracks, 'a' * 64, 'b' * 64, 'c' * 64)

        self.assertEqual(rebound['library_sha256'], 'b' * 64)
        self.assertEqual(rebound['parent_identity_map_sha256'], 'c' * 64)
        self.assertEqual(rebound['track_title_aliases'], [])
        self.assertEqual(matcher.match({'title': 'old alias', 'artists': [{'name': 'Artist'}],
                                        'duration_seconds': 180}), [])
        self.assertEqual(audit['dropped_stale_or_unknown_title_alias_pids'], [tracks[0]['persistent_id']])

    def test_title_decision_merge_preserves_non_identity_status_and_validates_pid_title(self):
        tracks = [{'persistent_id': '0000000000000001', 'title': 'fresh title', 'artist': 'Artist',
                   'duration_ms': 180000}]
        baseline = {'schema_version': 1, 'library_sha256': 'a' * 64,
                    'artist_alias_groups': [], 'artist_credit_sets': [], 'recording_links': [],
                    'track_title_aliases': []}
        sidecar = {'library_sha256': 'b' * 64, 'old_library_sha256': 'a' * 64,
                   'old_identity_map_sha256': 'c' * 64,
                   'recording_identity': 'unverified_metadata_candidate',
                   'alias_status': 'metadata_alias_candidate',
                   'audit': {'ambiguous_alias_collisions': 0, 'edges_created': 0,
                             'recordings_verified': 0},
                   'producer': 'fixture', 'map_entries': [{
                       'persistent_id': tracks[0]['persistent_id'],
                       'original_title': 'fresh title',
                       'aliases': [{'value': 'old title', 'enabled': True,
                                    'status': 'metadata_alias_candidate'}],
                   }]}

        rebound, matcher, audit = merge_title_alias_decisions(
            baseline, tracks, 'a' * 64, 'b' * 64, 'c' * 64, sidecar, 'd' * 64, 'e' * 64)

        self.assertEqual(rebound['library_sha256'], 'b' * 64)
        self.assertEqual(rebound['track_title_aliases'][0]['aliases'][0]['value'], 'old title')
        self.assertEqual(rebound['title_alias_decision_provenance']['recording_identity'],
                         'unverified_metadata_candidate')
        self.assertTrue(audit['title_aliases_claim_recording_identity'] is False)
        self.assertTrue(matcher.match({'title': 'old title', 'artists': [{'name': 'Artist'}],
                                      'duration_seconds': 180}))

    def test_title_merge_drops_only_alias_values_that_change_version_facets(self):
        tracks = [{'persistent_id': '0000000000000001',
                   'title': 'Song (Instrumental)', 'artist': 'Artist', 'duration_ms': 180000}]
        baseline = {'schema_version': 1, 'library_sha256': 'a' * 64,
                    'artist_alias_groups': [], 'artist_credit_sets': [], 'recording_links': [],
                    'track_title_aliases': []}
        sidecar = {'library_sha256': 'b' * 64, 'old_library_sha256': 'a' * 64,
                   'old_identity_map_sha256': 'c' * 64,
                   'recording_identity': 'unverified_metadata_candidate',
                   'alias_status': 'metadata_alias_candidate',
                   'audit': {'ambiguous_alias_collisions': 0, 'edges_created': 0,
                             'recordings_verified': 0},
                   'producer': 'fixture', 'map_entries': [{
                       'persistent_id': tracks[0]['persistent_id'],
                       'original_title': tracks[0]['title'], 'aliases': [
                           {'value': 'Song (Instrumental) - English', 'enabled': True},
                           {'value': 'English', 'enabled': True}]}]}
        rebound, matcher, audit = merge_title_alias_decisions(
            baseline, tracks, 'a' * 64, 'b' * 64, 'c' * 64, sidecar, 'd' * 64, 'e' * 64)
        self.assertEqual([item['value'] for item in rebound['track_title_aliases'][0]['aliases']],
                         ['Song (Instrumental) - English'])
        self.assertEqual(audit['rejected_version_facet_alias_count'], 1)
        self.assertTrue(matcher.match({'title': 'Song (Instrumental) - English',
                                       'artists': [{'name': 'Artist'}],
                                       'duration_seconds': 180}))
        self.assertEqual(matcher.match({'title': 'English', 'artists': [{'name': 'Artist'}],
                                       'duration_seconds': 180}), [])

    def test_newly_matched_graph_pids_get_new_ids_and_consistent_group_rows(self):
        labels = {
            '0000000000000001': (('artist-a',), 'album-a', ('recording-a',)),
            '0000000000000002': (('artist-a',), 'album-b', ('recording-b',)),
        }
        rows = [_row('0000000000000001', 101, (11, 21, 31), labels['0000000000000001'])]
        output, audit = extend_index_rows(
            rows, labels, {'0000000000000001': ['0000000000000002']})

        self.assertEqual([row['persistent_id'] for row in output],
                         ['0000000000000001', '0000000000000002'])
        self.assertEqual([row['genius_id'] for row in output], [101, 102])
        self.assertEqual(struct.unpack('<4Q', bytes.fromhex(output[1]['metadata_hex'])), (0, 11, 22, 32))
        self.assertEqual(audit['newly_indexed_pid_count'], 1)
        self.assertTrue(audit['new_pid_assignments_unique'])

    def test_final_saved_map_binds_to_zero_id_snapshot_and_source_hash(self):
        tracks = [{'persistent_id': '0000000000000001', 'title': 'fresh title',
                   'artist': 'Artist', 'duration_ms': 180000}]
        document = {'schema_version': 1, 'library_sha256': 'a' * 64,
                    'artist_alias_groups': [], 'artist_credit_sets': [],
                    'recording_links': [], 'track_title_aliases': []}
        rebound, matcher = bind_identity_map_to_zero_id_source(
            document, tracks, 'a' * 64, 'b' * 64, 'c' * 64)
        saved_bytes = (json.dumps(rebound, ensure_ascii=False, indent=2) + '\n').encode()
        saved_hash = hashlib.sha256(saved_bytes).hexdigest()
        self.assertEqual(rebound['library_sha256'], 'b' * 64)
        self.assertEqual(rebound['parent_identity_map_sha256'], 'c' * 64)
        self.assertEqual(matcher.artist_credits('Artist'), frozenset({('literal', 'artist')}))
        self.assertEqual(IdentityMap(json.loads(saved_bytes), tracks, 'b' * 64).artist_credits('Artist'),
                         frozenset({('literal', 'artist')}))
        self.assertEqual(saved_hash, hashlib.sha256(saved_bytes).hexdigest())


if __name__ == '__main__':
    unittest.main()
