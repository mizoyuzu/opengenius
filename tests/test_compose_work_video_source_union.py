from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from scripts.compose_work_video_source_union import (
    UnionError, compose_documents, global_ambiguities, load_reviewed_stage_pin,
    load_reviewed_work_prefix, merge_root_vectors,
)
from scripts.plan_genius_update import make_synthetic_state


class Matcher:
    def __init__(self):
        self.tracks = [
            {'persistent_id': f'{n:016X}', 'title': f'Track {n}',
             'artist': f'Artist {n % 2}', 'album': 'Album', 'duration_ms': 100000}
            for n in range(1, 8)
        ]

    @staticmethod
    def artist_credits(artist):
        return (artist,)


class ComposeWorkVideoUnionTests(unittest.TestCase):
    def setUp(self):
        self.matcher = Matcher()
        self.pids = [row['persistent_id'] for row in self.matcher.tracks]
        self.library_sha = '1' * 64
        self.map_sha = '2' * 64
        self.old_roots = [
            {'root_pid': self.pids[0], 'ordered_target_pids': [self.pids[1]]},
            {'root_pid': self.pids[1], 'ordered_target_pids': [self.pids[2]]},
            {'root_pid': self.pids[2], 'ordered_target_pids': []},
        ]
        self.old_state = make_synthetic_state(
            self.old_roots, self.matcher, self.library_sha, self.map_sha)
        self.old_graph = {'accepted_roots': copy.deepcopy(self.old_roots)}
        self.native_graph = {'accepted_roots': [copy.deepcopy(self.old_roots[0])]}
        self.source = {'root_pid': self.pids[2], 'video_id': 'video-c',
                       'ordered_target_pids': [self.pids[3]]}
        self.bindings = [
            {'persistent_id': self.pids[2], 'video_id': 'video-c'},
            {'persistent_id': self.pids[3], 'video_id': 'video-d'},
        ]

    def compose(self, sources=None, bindings=None, old_graph=None):
        return compose_documents(
            old_graph=old_graph or self.old_graph, old_state=self.old_state,
            native_graph=self.native_graph,
            source_batches=[{'sources': sources or [self.source],
                             'bindings': bindings or self.bindings}],
            matcher=self.matcher, library_sha=self.library_sha,
            identity_map_sha=self.map_sha, input_receipts={'fixture': 'sealed'},
            expected_native_roots=1, expected_old_yt_roots=1)

    def test_preserves_old_vectors_and_state_while_promoting_only_own_root(self):
        graph, plan, partition = self.compose()
        merged = {row['root_pid']: row['ordered_target_pids']
                  for row in graph['accepted_roots']}
        self.assertEqual(merged[self.pids[0]], [self.pids[1]])
        self.assertEqual(merged[self.pids[1]], [self.pids[2]])
        self.assertEqual(merged[self.pids[2]], [self.pids[3]])
        self.assertNotIn(self.pids[3], merged)  # incoming target is not a root
        self.assertEqual(graph['legacy_empty_rows_promoted'], 1)
        self.assertEqual(graph['candidate_root_rows_appended'], 0)
        self.assertEqual(partition, {'native_roots': 1, 'old_yt_roots': 1,
                                     'work_radio_roots': 1, 'unknown_roots': 0})
        self.assertEqual(plan['audit']['existing_assignments_preserved'], True)
        self.assertEqual(plan['audit']['existing_metadata_bytes_preserved'], True)
        self.assertEqual((graph['root_credit'], graph['edge_credit']), (0, 0))
        self.assertEqual(graph['incoming_root_credit'], graph['catalog_root_credit'], 0)

    def test_missing_legacy_root_row_is_appended_from_own_radio_only(self):
        old = {'accepted_roots': self.old_roots[:2]}
        graph, _, _ = self.compose(old_graph=old)
        by_pid = {row['root_pid']: row['ordered_target_pids']
                  for row in graph['accepted_roots']}
        self.assertEqual(by_pid[self.pids[2]], [self.pids[3]])
        self.assertEqual(graph['candidate_root_rows_appended'], 1)

    def test_target_only_binding_does_not_create_a_root(self):
        extra = {'persistent_id': self.pids[4], 'video_id': 'target-only-video'}
        self.source['ordered_target_pids'] = [self.pids[3], self.pids[4]]
        self.bindings.append(extra)
        graph, _, _ = self.compose()
        roots = {row['root_pid'] for row in graph['accepted_roots']}
        self.assertNotIn(self.pids[4], roots)

    def test_cross_stage_ambiguity_removes_conflicting_target_before_merge(self):
        more = self.bindings + [
            {'persistent_id': self.pids[4], 'video_id': 'video-d'},
            {'persistent_id': self.pids[5], 'video_id': 'video-e'},
        ]
        self.source['ordered_target_pids'] = [self.pids[3], self.pids[5]]
        graph, _, _ = self.compose(bindings=more)
        row = next(row for row in graph['accepted_roots']
                   if row['root_pid'] == self.pids[2])
        self.assertEqual(row['ordered_target_pids'], [self.pids[5]])
        self.assertEqual(graph['summary']['ambiguous_source_videos_rejected'], ['video-d'])
        self.assertEqual(set(graph['summary']['ambiguous_source_pids_rejected']),
                         {self.pids[3], self.pids[4]})

    def test_ambiguous_seed_root_is_dropped_and_old_active_root_cannot_be_overwritten(self):
        conflicting = self.bindings + [
            {'persistent_id': self.pids[2], 'video_id': 'different-video'}]
        graph, _, _ = self.compose(bindings=conflicting)
        root = next(row for row in graph['accepted_roots']
                    if row['root_pid'] == self.pids[2])
        self.assertEqual(root['ordered_target_pids'], [])
        self.assertEqual(graph['summary']['new_own_roots_rejected_by_global_ambiguity'], 1)

        with self.assertRaises(UnionError):
            merge_root_vectors(self.old_roots,
                [{'root_pid': self.pids[0], 'video_id': 'video-a',
                  'ordered_target_pids': [self.pids[3]]}],
                known_pids=set(self.pids), ambiguous_pids=set(),
                ambiguous_videos=set(), binding_by_pair={})

    def test_global_pid_and_video_collisions_are_both_reported(self):
        pid_conflict, video_conflict = global_ambiguities([[
            {'persistent_id': self.pids[0], 'video_id': 'a'},
            {'persistent_id': self.pids[0], 'video_id': 'b'},
            {'persistent_id': self.pids[1], 'video_id': 'b'},
        ]])
        self.assertEqual(pid_conflict, {self.pids[0], self.pids[1]})
        self.assertEqual(video_conflict, {'b'})

    def test_later_stage_pin_requires_external_hash_and_matches_profile_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage, audit, runtime, wrapper = [root / name for name in
                                               ('stage', 'audit', 'runtime', 'wrapper.py')]
            profile_path = root / 'reviewed-profile.json'
            profile = {'runtime_manifest_sha256': '1' * 64,
                       'wrapper_sha256': '2' * 64,
                       'stage': str(stage), 'runtime': str(runtime),
                       'wrapper': str(wrapper)}
            profile_raw = json.dumps(profile, sort_keys=True).encode()
            profile_path.write_bytes(profile_raw)
            pin = {'schema_version': 1,
                   'producer': 'opengenius-reviewed-work-stage-audit-pin-v1',
                   'stage_path': str(stage), 'audit_dir': str(audit),
                   'runtime_path': str(runtime), 'wrapper_path': str(wrapper),
                   'reviewed_profile_path': str(profile_path),
                   'reviewed_profile_sha256': hashlib.sha256(profile_raw).hexdigest(),
                   'stage_graph_sha256': '3' * 64,
                   'audit_receipt_sha256': '4' * 64,
                   'validated_graph_sha256': '5' * 64,
                   'runtime_manifest_sha256': '1' * 64,
                   'capture_wrapper_sha256': '2' * 64}
            pin_path = root / 'stage-audit-pin.json'
            pin_raw = json.dumps(pin, sort_keys=True).encode()
            pin_path.write_bytes(pin_raw)
            expected = hashlib.sha256(pin_raw).hexdigest()
            loaded = load_reviewed_stage_pin(pin_path, expected)
            self.assertEqual(loaded['stage_graph_sha256'], '3' * 64)
            with self.assertRaisesRegex(UnionError, 'pin file hash differs'):
                load_reviewed_stage_pin(pin_path, '0' * 64)

            pin['stage_graph_sha256'] = 'bad'
            tampered = json.dumps(pin).encode()
            pin_path.write_bytes(tampered)
            with self.assertRaisesRegex(UnionError, 'malformed digest'):
                load_reviewed_stage_pin(
                    pin_path, hashlib.sha256(tampered).hexdigest())

    def test_actual_interrupted_prefix_pin_replays_five_pairs_and_sixth_radio_only_for_collisions(self):
        root = Path(__file__).resolve().parents[1]
        profile = root / (
            'data/ytmusic/post870-kirby-playlist-stage2-inputs-20261015-v01/'
            'root-reviewed-capture-profile.json')
        prefix_dir = root / (
            'data/ytmusic/post870-work-video-radio-20261015-v01/'
            'stage-0002-kirby-v01-prefix-audit-v04')
        stage = root / (
            'data/ytmusic/post870-work-video-radio-20261015-v01/stage-0002-kirby-v01')
        supervisor = root / (
            'data/ytmusic/post870-work-video-radio-20261015-v01/'
            'stage-0002-kirby-supervisor-v01')
        runtime = Path('/tmp/opengenius-work-video-capture-runtime-v03')
        wrapper = Path('/tmp/opengenius-run-work-video-stage2-kirby-v01.py')
        profile_sha = '115182969c289ca4eb1a20fed2190ac9abe8848b35116456434d37e91951a594'
        receipt = prefix_dir / 'prefix-audit-receipt.json'
        projection = prefix_dir / 'independently-validated-prefix-candidates.json'
        pin = {
            'schema_version': 1,
            'producer': 'opengenius-reviewed-interrupted-prefix-audit-pin-v1',
            'reviewed_profile_path': str(profile.resolve()),
            'reviewed_profile_sha256': profile_sha,
            'prefix_receipt_path': str(receipt.resolve()),
            'prefix_receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest(),
            'prefix_projection_path': str(projection.resolve()),
            'prefix_projection_sha256': hashlib.sha256(projection.read_bytes()).hexdigest(),
            'stage_path': str(stage.resolve()),
            'stage_process_sha256': hashlib.sha256((stage / 'process.json').read_bytes()).hexdigest(),
            'stage_progress_sha256': hashlib.sha256((stage / 'progress.json').read_bytes()).hexdigest(),
            'supervisor_terminal_sha256': hashlib.sha256((supervisor / 'terminal.json').read_bytes()).hexdigest(),
            'runtime_path': str(runtime.resolve()),
            'runtime_manifest_sha256': hashlib.sha256((runtime / 'runtime-manifest.json').read_bytes()).hexdigest(),
            'wrapper_path': str(wrapper.resolve()),
            'capture_wrapper_sha256': hashlib.sha256(wrapper.read_bytes()).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as tmp:
            pin_path = Path(tmp) / 'reviewed-prefix-pin.json'
            pin_raw = json.dumps(pin, ensure_ascii=False, sort_keys=True).encode()
            pin_path.write_bytes(pin_raw)
            batch = load_reviewed_work_prefix(
                pin_path, hashlib.sha256(pin_raw).hexdigest())
            self.assertEqual(batch['prefix_status'],
                             'interrupted_prefix_validated_candidate_only')
            self.assertEqual(len(batch['sources']), 5)
            self.assertEqual(len(batch['pairs']), 5)
            self.assertEqual(batch['raw_radio_response_count'], 6)
            self.assertEqual(batch['partial_pid'], 'C25E18E3D7DEEA7D')
            self.assertFalse(batch['partial_pair_has_source'])
            self.assertNotIn(batch['partial_pid'], {s['root_pid'] for s in batch['sources']})
            self.assertIn(batch['partial_pid'],
                          {row['persistent_id'] for row in batch['bindings']})

    def test_interrupted_prefix_pin_rejects_changed_projection_hash(self):
        root = Path(__file__).resolve().parents[1]
        profile = root / (
            'data/ytmusic/post870-kirby-playlist-stage2-inputs-20261015-v01/'
            'root-reviewed-capture-profile.json')
        prefix_dir = root / (
            'data/ytmusic/post870-work-video-radio-20261015-v01/'
            'stage-0002-kirby-v01-prefix-audit-v04')
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            bad_projection = json.loads(
                (prefix_dir / 'independently-validated-prefix-candidates.json').read_text())
            bad_projection['candidate_graph_count'] = 4
            projection_path = temp / 'tampered-projection.json'
            projection_path.write_text(json.dumps(bad_projection), encoding='utf-8')
            stage = root / 'data/ytmusic/post870-work-video-radio-20261015-v01/stage-0002-kirby-v01'
            supervisor = root / 'data/ytmusic/post870-work-video-radio-20261015-v01/stage-0002-kirby-supervisor-v01'
            runtime = Path('/tmp/opengenius-work-video-capture-runtime-v03')
            wrapper = Path('/tmp/opengenius-run-work-video-stage2-kirby-v01.py')
            receipt = prefix_dir / 'prefix-audit-receipt.json'
            pin = {
                'schema_version': 1,
                'producer': 'opengenius-reviewed-interrupted-prefix-audit-pin-v1',
                'reviewed_profile_path': str(profile.resolve()),
                'reviewed_profile_sha256': hashlib.sha256(profile.read_bytes()).hexdigest(),
                'prefix_receipt_path': str(receipt.resolve()),
                'prefix_receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest(),
                'prefix_projection_path': str(projection_path.resolve()),
                'prefix_projection_sha256': hashlib.sha256(projection_path.read_bytes()).hexdigest(),
                'stage_path': str(stage.resolve()),
                'stage_process_sha256': hashlib.sha256((stage / 'process.json').read_bytes()).hexdigest(),
                'stage_progress_sha256': hashlib.sha256((stage / 'progress.json').read_bytes()).hexdigest(),
                'supervisor_terminal_sha256': hashlib.sha256((supervisor / 'terminal.json').read_bytes()).hexdigest(),
                'runtime_path': str(runtime.resolve()),
                'runtime_manifest_sha256': hashlib.sha256((runtime / 'runtime-manifest.json').read_bytes()).hexdigest(),
                'wrapper_path': str(wrapper.resolve()),
                'capture_wrapper_sha256': hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            }
            pin_path = temp / 'pin.json'
            raw = json.dumps(pin, sort_keys=True).encode()
            pin_path.write_bytes(raw)
            with self.assertRaisesRegex(UnionError, 'prefix candidate projection differs'):
                load_reviewed_work_prefix(
                    pin_path, hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    unittest.main()
