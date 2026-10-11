"""Offline safety checks for saved radio-video seed evidence."""
import copy
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap, build_map
from probe_ytmusic_batch import main as batch_main
from seed_video_evidence import (PRODUCER, build_seed_video_evidence,
                                 build_rebound_radio_hints,
                                 build_rebound_catalog_hints,
                                 build_combined_rebound_seed_video_evidence,
                                 combine_seed_video_evidence,
                                 strict_cached_search_match,
                                 select_seed_video_evidence,
                                 validate_album_catalog_evidence,
                                 main as evidence_main,
                                 seed_video_selector_cache_scope)
import seed_video_evidence as seed_evidence_module


LIBRARY_HASH = 'a' * 64
MAP_HASH = 'b' * 64
PID = '0000000000000001'
VIDEO = 'radio000001'
CATALOG_VIDEO = 'catalog0001'
ALBUM_ID = 'MPREsavedalbum01'


def track(pid=PID, title='Song'):
    return {'persistent_id': pid, 'title': title, 'artist': 'Artist',
            'album': 'Album', 'duration_ms': 100000}


def snapshot(video=VIDEO, *, library_hash=LIBRARY_HASH,
             map_hash=MAP_HASH, title='Song', artists=None,
             duration=100, relation='radio'):
    return {
        'schema_version': 1, 'selected_video_id': 'seedvideo01',
        'library_sha256': library_hash, 'identity_map_sha256': map_hash,
        'observations': [{
            'source': 'ytmusic', 'relation': relation, 'is_seed': False,
            'position': 1, 'observed_at': '2026-10-09T00:00:00+00:00',
            'track': {'video_id': video, 'title': title,
                      'artists': artists if artists is not None else [{'name': 'Artist'}],
                      'album': {'name': 'Album'}, 'duration_seconds': duration,
                      'length': '1:40'},
        }, {
            'source': 'ytmusic', 'relation': 'radio', 'is_seed': True,
            'position': 2, 'observed_at': '2026-10-09T00:00:01+00:00',
            'track': {'video_id': 'seedvideo01', 'title': 'Unmatched seed',
                      'artists': [{'name': 'Other Artist'}], 'album': None,
                      'duration_seconds': 100, 'length': '1:40'},
        }],
    }


class PartialMatcher:
    def __init__(self, tracks):
        self.tracks = tracks

    def match(self, remote):
        return [{'persistent_id': PID, 'artist_credit_match': 'partial',
                 'local_metadata': {'title': 'Song'}}]


class SeedVideoEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tracks = [track()]
        self.matcher = IdentityMap(build_map(self.tracks, LIBRARY_HASH),
                                   self.tracks, LIBRARY_HASH)
        self.source_path = self.root / 'radio.json'
        self.write_snapshot(snapshot())
        self.document = build_seed_video_evidence(
            [self.source_path], self.matcher, LIBRARY_HASH, MAP_HASH)
        self.sidecar_path = self.root / 'evidence.json'
        self.save_document(self.document)
        self.seeds = [self.tracks[0]]

    def tearDown(self):
        self.temp.cleanup()

    def write_snapshot(self, document):
        self.source_path.write_text(json.dumps(document), encoding='utf-8')

    def save_document(self, document):
        self.sidecar_path.write_text(json.dumps(document), encoding='utf-8')

    def select(self, document=None, matcher=None, tracks=None):
        return select_seed_video_evidence(
            self.document if document is None else document,
            self.sidecar_path, self.seeds,
            self.tracks if tracks is None else tracks,
            self.matcher if matcher is None else matcher,
            LIBRARY_HASH, MAP_HASH)

    def test_selector_index_reuses_full_validation_for_different_pid_subsets(self):
        self.tracks.append(track('0000000000000002', 'Other Song'))
        # Keep a two-row complete evidence doc; the second PID simply has no hint.
        self.matcher = IdentityMap(build_map(self.tracks, LIBRARY_HASH),
                                   self.tracks, LIBRARY_HASH)
        expected_one = select_seed_video_evidence(
            self.document, self.sidecar_path, [self.tracks[0]], self.tracks,
            self.matcher, LIBRARY_HASH, MAP_HASH)
        expected_empty = select_seed_video_evidence(
            self.document, self.sidecar_path, [self.tracks[1]], self.tracks,
            self.matcher, LIBRARY_HASH, MAP_HASH)
        with seed_video_selector_cache_scope() as stats:
            one = select_seed_video_evidence(
                self.document, self.sidecar_path, [self.tracks[0]], self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            empty = select_seed_video_evidence(
                self.document, self.sidecar_path, [self.tracks[1]], self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            self.assertEqual(one, expected_one)
            self.assertEqual(empty, expected_empty)
            self.assertEqual(stats['full_replays'], 1)
            self.assertEqual(stats['hits'], 1)
            self.assertGreaterEqual(stats['closure_files'], 2)

    def test_selector_index_preserves_multiple_video_row_results_and_order(self):
        second = track('0000000000000002', 'Other Song')
        self.tracks.append(second)
        second_source = self.root / 'radio-second.json'
        second_source.write_text(json.dumps(snapshot(
            video='radio000002', title='Other Song')), encoding='utf-8')
        self.matcher = IdentityMap(build_map(self.tracks, LIBRARY_HASH),
                                   self.tracks, LIBRARY_HASH)
        document = build_seed_video_evidence(
            [self.source_path, second_source], self.matcher, LIBRARY_HASH, MAP_HASH)
        self.save_document(document)
        requests = [[self.tracks[0]], [self.tracks[1], self.tracks[0]]]
        expected = [select_seed_video_evidence(
            document, self.sidecar_path, seeds, self.tracks, self.matcher,
            LIBRARY_HASH, MAP_HASH) for seeds in requests]
        with seed_video_selector_cache_scope() as stats:
            actual = [select_seed_video_evidence(
                document, self.sidecar_path, seeds, self.tracks, self.matcher,
                LIBRARY_HASH, MAP_HASH) for seeds in requests]
            self.assertEqual(actual, expected)
            self.assertEqual(actual[1][self.tracks[0]['persistent_id']]['video_id'], VIDEO)
            self.assertEqual(actual[1][self.tracks[1]['persistent_id']]['video_id'], 'radio000002')
            self.assertEqual(stats['full_replays'], 1)
            self.assertEqual(stats['hits'], 1)

    def test_selector_cache_rehashes_transitive_source_used_by_other_pid_row(self):
        with seed_video_selector_cache_scope():
            select_seed_video_evidence(
                self.document, self.sidecar_path, [self.tracks[0]], self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            changed = snapshot(title='Changed after first PID selection')
            self.write_snapshot(changed)
            with self.assertRaisesRegex(ValueError, 'source changed'):
                select_seed_video_evidence(
                    self.document, self.sidecar_path, [], self.tracks,
                    self.matcher, LIBRARY_HASH, MAP_HASH)

    def test_selector_cache_rehashes_catalog_and_radio_snapshot_transitive_sources(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture()
        radio = build_seed_video_evidence(
            [self.source_path], matcher, LIBRARY_HASH, MAP_HASH)
        combined = combine_seed_video_evidence(
            radio, self.sidecar_path, catalog, catalog_path,
            tracks, matcher, LIBRARY_HASH, MAP_HASH)
        combined_path = self.root / 'combined-cached.json'
        combined_path.write_text(json.dumps(combined), encoding='utf-8')
        with seed_video_selector_cache_scope() as stats:
            first = select_seed_video_evidence(
                combined, combined_path, tracks, tracks, matcher,
                LIBRARY_HASH, MAP_HASH)
            self.assertEqual(first['0000000000000002']['video_id'], CATALOG_VIDEO)
            self.assertEqual(select_seed_video_evidence(
                combined, combined_path, [tracks[0]], tracks, matcher,
                LIBRARY_HASH, MAP_HASH), {PID: first[PID]})
            self.assertEqual(stats['full_replays'], 1)
            self.assertEqual(stats['hits'], 1)
            response_path = Path(catalog['rows'][0]['input_path'])
            response_path.write_text(json.dumps({'tampered': True}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'source changed'):
                select_seed_video_evidence(
                    combined, combined_path, [tracks[0]], tracks, matcher,
                    LIBRARY_HASH, MAP_HASH)

    def test_selector_cache_return_is_deep_copied_and_scope_is_not_persistent(self):
        with seed_video_selector_cache_scope() as stats:
            first = select_seed_video_evidence(
                self.document, self.sidecar_path, self.seeds, self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            first[PID]['video_id'] = 'tampered001'
            second = select_seed_video_evidence(
                self.document, self.sidecar_path, self.seeds, self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            self.assertEqual(second[PID]['video_id'], VIDEO)
            self.assertEqual(stats['hits'], 1)
        with seed_video_selector_cache_scope() as fresh_stats:
            select_seed_video_evidence(
                self.document, self.sidecar_path, self.seeds, self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            self.assertEqual(fresh_stats['full_replays'], 1)
            self.assertEqual(fresh_stats['hits'], 0)

    def test_selector_cache_rechecks_directory_membership_before_hit(self):
        directory = self.root / 'dependency-dir'
        directory.mkdir()
        (directory / 'first.json').write_text('{}', encoding='utf-8')
        original_uncached = seed_evidence_module._select_seed_video_evidence_uncached

        def scan_then_validate(*args, **kwargs):
            list(directory.glob('*.json'))
            return original_uncached(*args, **kwargs)

        with patch.object(seed_evidence_module,
                          '_select_seed_video_evidence_uncached',
                          side_effect=scan_then_validate):
            with seed_video_selector_cache_scope():
                select_seed_video_evidence(
                    self.document, self.sidecar_path, self.seeds, self.tracks,
                    self.matcher, LIBRARY_HASH, MAP_HASH)
                (directory / 'added.json').write_text('{}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'source changed'):
                    select_seed_video_evidence(
                        self.document, self.sidecar_path, self.seeds, self.tracks,
                        self.matcher, LIBRARY_HASH, MAP_HASH)

    def test_selector_cache_rechecks_directory_deletion_before_hit(self):
        directory = self.root / 'dependency-dir-delete'
        directory.mkdir()
        file = directory / 'first.json'
        file.write_text('{}', encoding='utf-8')
        original_uncached = seed_evidence_module._select_seed_video_evidence_uncached

        def scan_then_validate(*args, **kwargs):
            list(directory.glob('*.json'))
            return original_uncached(*args, **kwargs)

        with patch.object(seed_evidence_module,
                          '_select_seed_video_evidence_uncached',
                          side_effect=scan_then_validate):
            with seed_video_selector_cache_scope():
                select_seed_video_evidence(
                    self.document, self.sidecar_path, self.seeds, self.tracks,
                    self.matcher, LIBRARY_HASH, MAP_HASH)
                file.unlink()
                with self.assertRaisesRegex(ValueError, 'source changed'):
                    select_seed_video_evidence(
                        self.document, self.sidecar_path, self.seeds, self.tracks,
                        self.matcher, LIBRARY_HASH, MAP_HASH)

    def test_selector_cache_never_seals_when_source_mutates_during_miss(self):
        original_uncached = seed_evidence_module._select_seed_video_evidence_uncached

        def validate_then_mutate(*args, **kwargs):
            result = original_uncached(*args, **kwargs)
            self.write_snapshot(snapshot(title='Mutated during selector replay'))
            return result

        original_methods = (Path.read_bytes, Path.read_text, Path.open,
                            builtins.open, os.scandir, os.listdir)
        with patch.object(seed_evidence_module,
                          '_select_seed_video_evidence_uncached',
                          side_effect=validate_then_mutate):
            with seed_video_selector_cache_scope():
                with self.assertRaisesRegex(ValueError, 'changed during full validation'):
                    select_seed_video_evidence(
                        self.document, self.sidecar_path, self.seeds, self.tracks,
                        self.matcher, LIBRARY_HASH, MAP_HASH)
                self.assertEqual(len(seed_evidence_module._ACTIVE_SELECTOR_INDEX.get()['entries']), 0)
        self.assertEqual((Path.read_bytes, Path.read_text, Path.open,
                          builtins.open, os.scandir, os.listdir), original_methods)

    def test_selector_returns_uncached_result_when_typed_verifier_removes_scratch(self):
        expected = self.select()
        original_uncached = seed_evidence_module._select_seed_video_evidence_uncached
        removed_paths = []

        def validate_with_temporary_rebuilt_map(*args, **kwargs):
            # Model the typed map verifier's temporary rebuild: the selector's
            # full validation observes/read-hashes the file, then its owner
            # removes the whole scratch directory before closure sealing.
            with tempfile.TemporaryDirectory(
                    prefix='opengenius-title-rebind-', dir=self.root) as raw_dir:
                temp_dir = Path(raw_dir)
                rebuilt_map = temp_dir / 'rebuilt-map.json'
                self.assertTrue(temp_dir.is_dir())
                rebuilt_map.write_text('{"validated": true}', encoding='utf-8')
                self.assertEqual(rebuilt_map.read_text(encoding='utf-8'),
                                 '{"validated": true}')
                removed_paths.append((temp_dir, rebuilt_map))
                return original_uncached(*args, **kwargs)

        with patch.object(seed_evidence_module,
                          '_select_seed_video_evidence_uncached',
                          side_effect=validate_with_temporary_rebuilt_map):
            with seed_video_selector_cache_scope() as stats:
                actual = self.select()
                self.assertEqual(actual, expected)
                self.assertEqual(stats['full_replays'], 1)
                self.assertEqual(stats['uncacheable_replays'], 1)
                self.assertEqual(stats['hits'], 0)
                self.assertEqual(len(seed_evidence_module._ACTIVE_SELECTOR_INDEX.get()['entries']), 0)
                self.assertFalse(removed_paths[-1][0].exists())
                self.assertFalse(removed_paths[-1][1].exists())

    def test_selector_cache_rejects_repeated_state_listing_and_resolution_changes(self):
        original_uncached = seed_evidence_module._select_seed_video_evidence_uncached

        def expect_unstable(observe_twice):
            def validate(*args, **kwargs):
                observe_twice()
                return original_uncached(*args, **kwargs)

            with patch.object(seed_evidence_module,
                              '_select_seed_video_evidence_uncached',
                              side_effect=validate):
                with seed_video_selector_cache_scope() as stats:
                    with self.assertRaisesRegex(ValueError, 'sources changed during full validation'):
                        self.select()
                    self.assertEqual(stats['full_replays'], 0)
                    self.assertEqual(stats['hits'], 0)

        state_path = self.root / 'state-change-during-replay'
        def observe_state_change():
            self.assertFalse(state_path.exists())
            state_path.touch()
            self.assertTrue(state_path.exists())
        expect_unstable(observe_state_change)

        listing_dir = self.root / 'listing-change-during-replay'
        listing_dir.mkdir()
        def observe_listing_change():
            self.assertEqual(list(listing_dir.glob('*.json')), [])
            (listing_dir / 'new.json').write_text('{}', encoding='utf-8')
            self.assertEqual([path.name for path in listing_dir.glob('*.json')], ['new.json'])
        expect_unstable(observe_listing_change)

        target_one = self.root / 'resolution-target-one'
        target_two = self.root / 'resolution-target-two'
        target_one.write_text('one', encoding='utf-8')
        target_two.write_text('two', encoding='utf-8')
        link = self.root / 'resolution-link'
        try:
            link.symlink_to(target_one)
        except (OSError, NotImplementedError):
            self.skipTest('symlink creation is unavailable')
        def observe_resolution_change():
            self.assertEqual(link.resolve(), target_one.resolve())
            link.unlink()
            link.symlink_to(target_two)
            self.assertEqual(link.resolve(), target_two.resolve())
        expect_unstable(observe_resolution_change)

    def test_selector_cache_context_tracks_in_memory_matcher_and_ignores_foreign_reuse(self):
        with seed_video_selector_cache_scope() as stats:
            select_seed_video_evidence(
                self.document, self.sidecar_path, self.seeds, self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            changed_tracks = copy.deepcopy(self.tracks)
            changed_tracks[0]['album'] = 'Changed in memory'
            changed_matcher = IdentityMap(build_map(changed_tracks, LIBRARY_HASH),
                                          changed_tracks, LIBRARY_HASH)
            with self.assertRaisesRegex(ValueError, 'hashes differ'):
                select_seed_video_evidence(
                    self.document, self.sidecar_path, self.seeds,
                    changed_tracks, changed_matcher, LIBRARY_HASH, 'c' * 64)
            self.assertEqual(stats['hits'], 0)

    def test_selector_cache_context_tracks_mutated_matcher_state(self):
        with seed_video_selector_cache_scope() as stats:
            first = select_seed_video_evidence(
                self.document, self.sidecar_path, self.seeds, self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            self.matcher.album_title_aliases['unused remote title'] = 'Album'
            second = select_seed_video_evidence(
                self.document, self.sidecar_path, self.seeds, self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            self.assertEqual(first, second)
            self.assertEqual(stats['misses'], 2)
            self.assertEqual(stats['hits'], 0)

    def test_selector_cache_rejects_symlink_retarget(self):
        alias = self.root / 'radio-alias.json'
        alternate = self.root / 'radio-alternate.json'
        try:
            alias.symlink_to(self.source_path)
        except (OSError, NotImplementedError):
            self.skipTest('symlink creation is unavailable')
        alternate.write_bytes(self.source_path.read_bytes())
        document = copy.deepcopy(self.document)
        document['rows'][0]['input_path'] = str(alias)
        self.save_document(document)
        with seed_video_selector_cache_scope():
            select_seed_video_evidence(
                document, self.sidecar_path, self.seeds, self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            alias.unlink()
            alias.symlink_to(alternate)
            with self.assertRaisesRegex(ValueError, 'source changed'):
                select_seed_video_evidence(
                    document, self.sidecar_path, self.seeds, self.tracks,
                    self.matcher, LIBRARY_HASH, MAP_HASH)

    def test_selector_cache_preserves_requested_seed_validation_errors(self):
        with seed_video_selector_cache_scope():
            with self.assertRaisesRegex(ValueError, 'no persistent ID'):
                select_seed_video_evidence(
                    self.document, self.sidecar_path, [{}], self.tracks,
                    self.matcher, LIBRARY_HASH, MAP_HASH)

    def test_selector_cache_nested_scope_shares_index_and_restores_on_exception(self):
        original_read_bytes = Path.read_bytes
        with seed_video_selector_cache_scope() as outer:
            select_seed_video_evidence(
                self.document, self.sidecar_path, self.seeds, self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            with seed_video_selector_cache_scope() as nested:
                self.assertIs(nested, outer)
                select_seed_video_evidence(
                    self.document, self.sidecar_path, self.seeds, self.tracks,
                    self.matcher, LIBRARY_HASH, MAP_HASH)
            with self.assertRaisesRegex(RuntimeError, 'nested failure'):
                with seed_video_selector_cache_scope():
                    raise RuntimeError('nested failure')
            self.assertIsNotNone(seed_evidence_module._ACTIVE_SELECTOR_INDEX.get())
            select_seed_video_evidence(
                self.document, self.sidecar_path, self.seeds, self.tracks,
                self.matcher, LIBRARY_HASH, MAP_HASH)
            self.assertEqual(outer['nested_scopes'], 2)
            self.assertEqual(outer['hits'], 2)
        self.assertIs(Path.read_bytes, original_read_bytes)

    def test_unrecognized_open_makes_successful_validation_noncacheable(self):
        original_uncached = seed_evidence_module._select_seed_video_evidence_uncached

        def validate_with_extra_open(*args, **kwargs):
            with open(self.source_path, 'rb') as handle:
                handle.read()
            return original_uncached(*args, **kwargs)

        with patch.object(seed_evidence_module,
                          '_select_seed_video_evidence_uncached',
                          side_effect=validate_with_extra_open):
            with seed_video_selector_cache_scope() as stats:
                first = select_seed_video_evidence(
                    self.document, self.sidecar_path, self.seeds, self.tracks,
                    self.matcher, LIBRARY_HASH, MAP_HASH)
                second = select_seed_video_evidence(
                    self.document, self.sidecar_path, self.seeds, self.tracks,
                    self.matcher, LIBRARY_HASH, MAP_HASH)
                self.assertEqual(first, second)
                self.assertEqual(stats['full_replays'], 2)
                self.assertEqual(stats['uncacheable_replays'], 2)
                self.assertEqual(stats['hits'], 0)

    def test_listing_trace_preserves_path_glob_keyword_options(self):
        original_uncached = seed_evidence_module._select_seed_video_evidence_uncached

        def validate_with_optioned_listings(*args, **kwargs):
            list(self.root.glob('*.json', case_sensitive=False))
            list(self.root.rglob('*.json', recurse_symlinks=False))
            return original_uncached(*args, **kwargs)

        with patch.object(seed_evidence_module,
                          '_select_seed_video_evidence_uncached',
                          side_effect=validate_with_optioned_listings):
            with seed_video_selector_cache_scope() as stats:
                first = self.select()
                second = self.select()
                self.assertEqual(first, second)
                self.assertEqual(stats['full_replays'], 1)
                self.assertEqual(stats['hits'], 1)

    def _cached_result(self, *, title='Song', artists=None, album='Album'):
        return [{'videoId': VIDEO, 'title': title,
                 'artists': artists if artists is not None else [{'name': 'Artist'}],
                 'album': album, 'duration_seconds': 100,
                 'length': '1:40'}]

    def test_cached_search_accepts_only_exact_validated_album_alias(self):
        identity = build_map(self.tracks, LIBRARY_HASH)
        identity['album_title_aliases'] = [{
            'enabled': True, 'canonical_library_album': 'Album',
            'exact_remote_album_aliases': ['Official English Edition'],
        }]
        matcher = IdentityMap(identity, self.tracks, LIBRARY_HASH)
        accepted = strict_cached_search_match(
            self._cached_result(album='Official English Edition'),
            self.tracks[0], matcher)
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted['video_id'], VIDEO)
        self.assertTrue(accepted['matches'][0]['album_match'])

        for album in ('Other Album', 'Deluxe Official English Edition'):
            with self.subTest(album=album):
                self.assertIsNone(strict_cached_search_match(
                    self._cached_result(album=album), self.tracks[0], matcher))

    def test_cached_search_album_alias_keeps_credit_and_version_gates(self):
        identity = build_map(self.tracks, LIBRARY_HASH)
        identity['album_title_aliases'] = [{
            'enabled': True, 'canonical_library_album': 'Album',
            'exact_remote_album_aliases': ['Official English Edition'],
        }]
        matcher = IdentityMap(identity, self.tracks, LIBRARY_HASH)
        self.assertIsNone(strict_cached_search_match(
            self._cached_result(album='Official English Edition',
                                artists=[{'name': 'Artist'}, {'name': 'Guest'}]),
            self.tracks[0], matcher))
        self.assertIsNone(strict_cached_search_match(
            self._cached_result(title='Song (Live Version)',
                                album='Official English Edition'),
            self.tracks[0], matcher))

    def test_builds_and_revalidates_a_radio_seed_hint_with_source_provenance(self):
        row, = self.document['rows']
        self.assertEqual(row['persistent_id'], PID)
        self.assertEqual(row['video_id'], VIDEO)
        self.assertEqual(row['source_relation'], 'radio')
        self.assertEqual(row['input_sha256'], hashlib.sha256(self.source_path.read_bytes()).hexdigest())
        hint = self.select()[PID]
        self.assertEqual(hint['video_id'], VIDEO)
        self.assertEqual(hint['source'], 'saved_recommendation_metadata_candidate_unverified')
        self.assertEqual(self.document['audit']['edges_created'], 0)

    def test_rejects_foreign_library_or_identity_map_hash(self):
        for field in ('library_sha256', 'identity_map_sha256'):
            altered = copy.deepcopy(self.document)
            altered[field] = 'c' * 64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'source hashes'):
                self.select(altered)

    def test_rejects_unknown_pid_and_invalid_video_id(self):
        altered = copy.deepcopy(self.document)
        altered['rows'][0]['persistent_id'] = 'FFFFFFFFFFFFFFFF'
        with self.assertRaisesRegex(ValueError, 'Invalid or duplicate'):
            self.select(altered)
        altered = copy.deepcopy(self.document)
        altered['rows'][0]['video_id'] = 'bad-video'
        with self.assertRaisesRegex(ValueError, 'Invalid or duplicate'):
            self.select(altered)

    def test_rejects_malformed_source_hash_or_changed_source_file(self):
        altered = copy.deepcopy(self.document)
        altered['rows'][0]['input_sha256'] = 'bad'
        with self.assertRaisesRegex(ValueError, 'Invalid or duplicate'):
            self.select(altered)
        self.write_snapshot(snapshot(title='Changed source'))
        with self.assertRaisesRegex(ValueError, 'snapshot hash changed'):
            self.select()

    def test_builder_omits_nonunique_duration_supported_candidates(self):
        duplicate_tracks = [track(), track('0000000000000002')]
        matcher = IdentityMap(build_map(duplicate_tracks, LIBRARY_HASH),
                              duplicate_tracks, LIBRARY_HASH)
        result = build_seed_video_evidence(
            [self.source_path], matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['audit']['edges_created'], 0)

    def test_builder_omits_partial_artist_credit_matches(self):
        result = build_seed_video_evidence(
            [self.source_path], PartialMatcher(self.tracks), LIBRARY_HASH, MAP_HASH)
        self.assertEqual(result['rows'], [])

    def test_builder_keeps_version_markers_as_a_match_guard(self):
        self.write_snapshot(snapshot(title='Song (Instrumental)'))
        result = build_seed_video_evidence(
            [self.source_path], self.matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(result['rows'], [])

    def test_rebound_radio_hint_requires_authenticated_old_map_source_and_rematches(self):
        self.write_snapshot(snapshot())
        receipt = {'path': str(self.source_path.resolve()),
                   'sha256': hashlib.sha256(self.source_path.read_bytes()).hexdigest()}
        rows = build_rebound_radio_hints([receipt], self.matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['persistent_id'], PID)
        self.assertEqual(rows[0]['video_id'], VIDEO)
        self.assertEqual(rows[0]['source_relation'], 'rebound_radio_candidate')
        self.assertEqual(build_rebound_radio_hints([], self.matcher, LIBRARY_HASH, MAP_HASH), [])
        audit = {}
        self.assertEqual(build_rebound_radio_hints(
            [receipt], self.matcher, LIBRARY_HASH, 'f' * 64, audit=audit), [])
        self.assertEqual(audit['foreign_source_hash_count'], 1)

    def test_rebound_radio_hint_rejects_source_changed_after_transition_receipt(self):
        receipt = {'path': str(self.source_path.resolve()), 'sha256': 'f' * 64}
        with self.assertRaisesRegex(ValueError, 'changed after transition'):
            build_rebound_radio_hints([receipt], self.matcher, LIBRARY_HASH, MAP_HASH)

    def test_typed_rebound_sidecar_replays_sources_and_rejects_tampering(self):
        from seed_video_evidence import (build_rebound_seed_video_evidence,
                                         select_seed_video_evidence)
        tracks_path = self.root / 'tracks.json'
        tracks_path.write_text(json.dumps({'source_sha256': LIBRARY_HASH,
                                           'tracks': self.tracks}), encoding='utf-8')
        identity = build_map(self.tracks, LIBRARY_HASH)
        old_map_path = self.root / 'old-map.json'
        current_map_path = self.root / 'current-map.json'
        old_map_path.write_text(json.dumps(identity), encoding='utf-8')
        current_map_path.write_text(json.dumps(identity), encoding='utf-8')
        old_hash = hashlib.sha256(old_map_path.read_bytes()).hexdigest()
        current_hash = hashlib.sha256(current_map_path.read_bytes()).hexdigest()
        source = snapshot(map_hash=old_hash)
        self.write_snapshot(source)
        source_receipt = {'path': str(self.source_path.resolve()),
                          'sha256': hashlib.sha256(self.source_path.read_bytes()).hexdigest()}
        transition = {}
        for key, path in (('old_identity_map', old_map_path),
                          ('new_identity_map', current_map_path)):
            transition[key] = {'path': str(path.resolve()),
                               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for key in ('graph_export', 'rebase_report', 'state'):
            path = self.root / (key + '.json')
            path.write_text(json.dumps({'artifact': key}), encoding='utf-8')
            transition[key] = {'path': str(path.resolve()),
                               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        verified = {'old_identity_map_sha256': old_hash,
                    'new_identity_map_sha256': current_hash,
                    'library_sha256': LIBRARY_HASH,
                    'accepted_snapshot_receipts': [source_receipt],
                    'track_snapshot_receipt': {
                        'path': str(tracks_path.resolve()),
                        'sha256': hashlib.sha256(tracks_path.read_bytes()).hexdigest()}}
        with patch('seed_video_evidence.verify_typed_map_transition',
                   return_value=(transition, verified)):
            document = build_rebound_seed_video_evidence(
                track_snapshot=tracks_path, old_identity_map=old_map_path,
                current_identity_map=current_map_path, typed_transition=transition)
            sidecar = self.root / 'rebound.json'
            sidecar.write_text(json.dumps(document), encoding='utf-8')
            matcher = IdentityMap(identity, self.tracks, LIBRARY_HASH)
            selected = select_seed_video_evidence(
                document, sidecar, [self.tracks[0]], self.tracks, matcher,
                LIBRARY_HASH, current_hash)
            self.assertEqual(selected[PID]['video_id'], VIDEO)
            self.assertEqual(document['audit']['recommendation_edges_created'], 0)
            self.assertEqual(document['audit']['outgoing_roots_created'], 0)
            altered = copy.deepcopy(document)
            altered['rows'][0]['video_id'] = 'changed0001'
            with self.assertRaisesRegex(ValueError, 'differs from its typed source replay'):
                select_seed_video_evidence(altered, sidecar, [self.tracks[0]],
                                           self.tracks, matcher, LIBRARY_HASH,
                                           current_hash)

    def test_typed_transition_rejects_mutated_old_or_current_map_receipts(self):
        import types
        from seed_video_evidence import verify_typed_map_transition
        old_map = self.root / 'transition-old-map.json'
        new_map = self.root / 'transition-new-map.json'
        old_map.write_text('{}', encoding='utf-8')
        new_map.write_text('{}', encoding='utf-8')
        paths = {}
        for key in ('graph_export', 'rebase_report', 'state'):
            path = self.root / ('transition-' + key + '.json')
            path.write_text(json.dumps({'key': key}), encoding='utf-8')
            paths[key] = path
        transition = {
            'old_identity_map': {'path': str(old_map),
                                 'sha256': hashlib.sha256(old_map.read_bytes()).hexdigest()},
            'new_identity_map': {'path': str(new_map),
                                 'sha256': hashlib.sha256(new_map.read_bytes()).hexdigest()},
        }
        transition.update({key: {'path': str(path),
                                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                           for key, path in paths.items()})
        fake_merge = types.ModuleType('merge_rebased_radio_growth')
        fake_merge.verify_typed_identity_map_rebinding = lambda **kwargs: {
            'old_identity_map_sha256': transition['old_identity_map']['sha256'],
            'new_identity_map_sha256': transition['new_identity_map']['sha256'],
            'library_sha256': LIBRARY_HASH,
            'accepted_snapshot_receipts': [],
            'track_snapshot_receipt': {'path': str(self.root / 'tracks.json'),
                                       'sha256': 'a' * 64}}
        with patch.dict(sys.modules, {'merge_rebased_radio_growth': fake_merge}):
            old_map.write_text('{"tampered":true}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'input changed'):
                verify_typed_map_transition(
                    transition, transition['old_identity_map']['sha256'],
                    transition['new_identity_map']['sha256'], LIBRARY_HASH)

    def test_typed_transition_cache_rehashes_all_sources_on_each_reuse(self):
        import types
        from seed_video_evidence import (_TYPED_TRANSITION_PROOF_CACHE,
                                         verify_typed_map_transition)
        _TYPED_TRANSITION_PROOF_CACHE.clear()
        files = {}
        for key in ('old_identity_map', 'new_identity_map', 'graph_export',
                    'rebase_report', 'state', 'track_snapshot', 'accepted_snapshot',
                    'nested_alias_decision'):
            path = self.root / ('cached-' + key + '.json')
            path.write_text(json.dumps({'key': key}), encoding='utf-8')
            files[key] = path
        transition = {
            key: {'path': str(files[key].resolve()),
                  'sha256': hashlib.sha256(files[key].read_bytes()).hexdigest()}
            for key in ('old_identity_map', 'new_identity_map', 'graph_export',
                        'rebase_report', 'state')}
        calls = []
        fake_merge = types.ModuleType('merge_rebased_radio_growth')
        def verifier(**kwargs):
            calls.append(kwargs)
            return {
                'verified': True, 'library_sha256': LIBRARY_HASH,
                'old_identity_map_path': str(files['old_identity_map'].resolve()),
                'old_identity_map_sha256': transition['old_identity_map']['sha256'],
                'new_identity_map_path': str(files['new_identity_map'].resolve()),
                'new_identity_map_sha256': transition['new_identity_map']['sha256'],
                'report_path': str(files['rebase_report'].resolve()),
                'report_sha256': transition['rebase_report']['sha256'],
                'graph_export_path': str(files['graph_export'].resolve()),
                'graph_export_sha256': transition['graph_export']['sha256'],
                'state_path': str(files['state'].resolve()),
                'state_file_sha256': transition['state']['sha256'],
                'authenticated_observation_inputs': [{
                    'path': str(files['accepted_snapshot'].resolve()),
                    'sha256': hashlib.sha256(
                        files['accepted_snapshot'].read_bytes()).hexdigest()}],
                'track_snapshot_receipt': {
                    'path': str(files['track_snapshot'].resolve()),
                    'sha256': hashlib.sha256(files['track_snapshot'].read_bytes()).hexdigest()},
                'proof_input_receipts': [
                    {'path': str(path.resolve()),
                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                    for path in files.values()],
            }
        fake_merge.verify_typed_identity_map_rebinding = verifier
        with patch.dict(sys.modules, {'merge_rebased_radio_growth': fake_merge}):
            old_hash = transition['old_identity_map']['sha256']
            new_hash = transition['new_identity_map']['sha256']
            accepted_original = files['accepted_snapshot'].read_bytes()
            verify_typed_map_transition(transition, old_hash, new_hash, LIBRARY_HASH)
            verify_typed_map_transition(transition, old_hash, new_hash, LIBRARY_HASH)
            self.assertEqual(len(calls), 1)
            files['accepted_snapshot'].write_text('{"changed":true}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'cached typed-transition source changed'):
                verify_typed_map_transition(transition, old_hash, new_hash, LIBRARY_HASH)
            self.assertEqual(len(calls), 1)
            files['accepted_snapshot'].write_bytes(accepted_original)
            verify_typed_map_transition(transition, old_hash, new_hash, LIBRARY_HASH)
            self.assertEqual(len(calls), 2)
            proof_original = files['nested_alias_decision'].read_bytes()
            files['nested_alias_decision'].write_text('{"decision_changed":true}',
                                                       encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'cached typed-transition source changed'):
                verify_typed_map_transition(transition, old_hash, new_hash, LIBRARY_HASH)
            self.assertEqual(len(calls), 2)
            files['nested_alias_decision'].write_bytes(proof_original)
            verify_typed_map_transition(transition, old_hash, new_hash, LIBRARY_HASH)
            self.assertEqual(len(calls), 3)
            files['new_identity_map'].write_text('{"alias_tamper":true}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'input changed'):
                verify_typed_map_transition(transition, old_hash, new_hash, LIBRARY_HASH)
            self.assertEqual(len(calls), 3)

    def test_legacy_transition_proof_may_omit_only_replayed_auxiliary_receipts(self):
        from seed_video_evidence import _typed_transition_verification_matches
        current = {'verified': True, 'old_identity_map_sha256': 'a' * 64,
                   'new_identity_map_sha256': 'b' * 64,
                   'proof_input_receipts': [{'path': '/proof.json',
                                             'sha256': 'c' * 64}]}
        legacy = {key: value for key, value in current.items()
                  if key != 'proof_input_receipts'}
        self.assertTrue(_typed_transition_verification_matches(legacy, current))
        self.assertTrue(_typed_transition_verification_matches(current, current))
        self.assertFalse(_typed_transition_verification_matches(
            {'verified': True, 'old_identity_map_sha256': 'a' * 64}, current))
        self.assertFalse(_typed_transition_verification_matches(
            dict(legacy, new_identity_map_sha256='d' * 64), current))
        self.assertFalse(_typed_transition_verification_matches(
            dict(legacy, unverified_extra=True), current))

    def test_rebound_deduplication_preserves_cached_search_release_proof(self):
        from seed_video_evidence import _dedupe_rebound_hints
        radio = {'persistent_id': PID, 'video_id': VIDEO,
                 'source_relation': 'rebound_radio_candidate',
                 'input_path': '/radio.json', 'input_sha256': 'a' * 64}
        cached = {'persistent_id': PID, 'video_id': VIDEO,
                  'source_relation': 'rebound_cached_search_candidate',
                  'input_path': '/search.json', 'input_sha256': 'b' * 64,
                  'historical_result_status': 'search_unresolved',
                  'historical_old_identity_map_sha256': 'c' * 64}
        merged, = _dedupe_rebound_hints([radio, cached])
        self.assertEqual(merged['source_relation'], 'rebound_radio_candidate')
        self.assertEqual(merged['additional_source_evidence'], [cached])

    def test_combined_rebound_builder_preserves_rebound_rows_and_filters_current_conflicts(self):
        from seed_video_evidence import REBOUND_PRODUCER, COMBINED_REBOUND_PRODUCER
        rebound_path = self.root / 'rebound.json'
        rebound_rows = [
            {'persistent_id': PID, 'video_id': VIDEO,
             'source_relation': 'rebound_radio_candidate',
             'input_path': '/old-map-radio.json', 'input_sha256': 'c' * 64},
            {'persistent_id': '0000000000000002', 'video_id': 'other000001',
             'source_relation': 'rebound_album_catalog_candidate',
             'input_path': '/old-map-catalog.json', 'input_sha256': 'd' * 64},
        ]
        rebound_path.write_text(json.dumps({
            'schema_version': 1, 'producer': REBOUND_PRODUCER,
            'library_sha256': LIBRARY_HASH, 'identity_map_sha256': MAP_HASH,
            'rows': rebound_rows,
        }), encoding='utf-8')
        current_path = self.root / 'current-radio.json'
        current_rows = [
            {'persistent_id': PID, 'video_id': 'different000',
             'source_relation': 'radio', 'input_path': '/current-a.json',
             'input_sha256': 'e' * 64},  # same PID, conflicting video
            {'persistent_id': '0000000000000003', 'video_id': 'other000001',
             'source_relation': 'radio', 'input_path': '/current-b.json',
             'input_sha256': 'f' * 64},  # same video, conflicting PID
            {'persistent_id': '0000000000000004', 'video_id': 'safe0000001',
             'source_relation': 'radio', 'input_path': '/current-c.json',
             'input_sha256': '1' * 64},
        ]
        current_path.write_text(json.dumps({
            'schema_version': 1, 'producer': PRODUCER,
            'library_sha256': LIBRARY_HASH, 'identity_map_sha256': MAP_HASH,
            'rows': current_rows,
        }), encoding='utf-8')
        combined = build_combined_rebound_seed_video_evidence(
            rebound_path, current_path, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(combined['producer'], COMBINED_REBOUND_PRODUCER)
        self.assertEqual(combined['rows'], [*rebound_rows, current_rows[2]])
        self.assertEqual(combined['rows'][:2], rebound_rows)
        self.assertEqual(combined['rebound_evidence']['sha256'],
                         hashlib.sha256(rebound_path.read_bytes()).hexdigest())
        self.assertEqual(combined['current_radio_source']['sha256'],
                         hashlib.sha256(current_path.read_bytes()).hexdigest())
        self.assertEqual(combined['audit']['recommendation_edges_created'], 0)
        self.assertEqual(combined['audit']['outgoing_roots_created'], 0)

    def test_combined_selector_rejects_tampered_rebound_or_current_source(self):
        from seed_video_evidence import REBOUND_PRODUCER
        rebound_path = self.root / 'rebound.json'
        rebound_doc = {
            'schema_version': 1, 'producer': REBOUND_PRODUCER,
            'library_sha256': LIBRARY_HASH, 'identity_map_sha256': MAP_HASH,
            'rows': [{'persistent_id': PID, 'video_id': VIDEO,
                      'source_relation': 'rebound_radio_candidate',
                      'input_path': '/radio.json', 'input_sha256': 'a' * 64}],
        }
        rebound_path.write_text(json.dumps(rebound_doc), encoding='utf-8')
        current_path = self.root / 'current-radio.json'
        current_doc = copy.deepcopy(self.document)
        current_doc['rows'][0]['input_path'] = 'radio.json'
        current_path.write_text(json.dumps(current_doc), encoding='utf-8')
        combined = build_combined_rebound_seed_video_evidence(
            rebound_path, current_path, LIBRARY_HASH, MAP_HASH)
        combined_path = self.root / 'combined.json'
        combined_path.write_text(json.dumps(combined), encoding='utf-8')
        with patch('seed_video_evidence._select_rebound_seed_video_evidence',
                   return_value={PID: {'video_id': VIDEO, 'source': 'rebound'}}):
            result = select_seed_video_evidence(
                combined, combined_path, self.seeds, self.tracks, self.matcher,
                LIBRARY_HASH, MAP_HASH)
            self.assertEqual(result[PID]['video_id'], VIDEO)
            rebound_path.write_text(json.dumps({**rebound_doc, 'tampered': True}),
                                    encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'rebound source changed'):
                select_seed_video_evidence(
                    combined, combined_path, self.seeds, self.tracks,
                    self.matcher, LIBRARY_HASH, MAP_HASH)
            rebound_path.write_text(json.dumps(rebound_doc), encoding='utf-8')
            current_path.write_text(json.dumps({**current_doc, 'tampered': True}),
                                    encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'current-radio source changed'):
                select_seed_video_evidence(
                    combined, combined_path, self.seeds, self.tracks,
                    self.matcher, LIBRARY_HASH, MAP_HASH)

    def test_loader_rejects_a_source_that_is_not_saved_radio_evidence(self):
        changed = snapshot(relation='related')
        self.write_snapshot(changed)
        altered = copy.deepcopy(self.document)
        altered['rows'][0]['input_sha256'] = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'no longer has'):
            self.select(altered)

    def test_batch_cli_uses_hint_and_records_evidence_without_search(self):
        identity_path = self.root / 'identity-map.json'
        identity_path.write_text(json.dumps(build_map(self.tracks, LIBRARY_HASH)), encoding='utf-8')
        actual_map_hash = hashlib.sha256(identity_path.read_bytes()).hexdigest()
        source_doc = snapshot(map_hash=actual_map_hash)
        self.write_snapshot(source_doc)
        evidence = build_seed_video_evidence([self.source_path], self.matcher,
                                             LIBRARY_HASH, actual_map_hash)
        evidence_path = self.root / 'batch-evidence.json'
        evidence_path.write_text(json.dumps(evidence), encoding='utf-8')
        tracks_path = self.root / 'tracks.json'
        tracks_path.write_text(json.dumps({'source_sha256': LIBRARY_HASH,
                                           'tracks': self.tracks}), encoding='utf-8')
        plan_path = self.root / 'plan.json'
        plan_path.write_text(json.dumps({
            'schema_version': 1, 'source_library_sha256': LIBRARY_HASH,
            'seeds': [{'persistent_id': PID, 'series': 'fixture',
                       'sample_kind': 'indexed_target_only'}],
        }), encoding='utf-8')
        output = self.root / 'collector-plan'
        argv = ['probe', '--track-snapshot', str(tracks_path),
                '--identity-map', str(identity_path), '--seed-plan', str(plan_path),
                '--seed-video-evidence', str(evidence_path), '--seeds', '1',
                '--output', str(output), '--auth', str(self.root / 'missing-auth'),
                '--plan-only']
        with patch('sys.argv', argv), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(batch_main(), 0)
        manifest = json.loads((output / 'seeds.json').read_text())
        self.assertEqual(manifest['seeds'][0]['seed_video_id'], VIDEO)
        self.assertEqual(manifest['seeds'][0]['seed_video_source'],
                         'saved_recommendation_metadata_candidate_unverified')
        self.assertEqual(manifest['seed_video_evidence_sha256'],
                         hashlib.sha256(evidence_path.read_bytes()).hexdigest())
        self.assertEqual(manifest['seed_video_evidence_sources'][0]['input_path'],
                         str(self.source_path.resolve()))

    def catalog_fixture(self, *, persistent_id='0000000000000002', video=CATALOG_VIDEO,
                        duration=100, album='Album', relation='radio', map_hash=MAP_HASH):
        tracks = [track(), track('0000000000000002', 'Catalog song')]
        matcher = IdentityMap(build_map(tracks, LIBRARY_HASH), tracks, LIBRARY_HASH)
        source = snapshot(map_hash=map_hash)
        source['observations'][0]['track']['album']['id'] = ALBUM_ID
        source['observations'][0]['track']['album']['name'] = 'Album'
        source['observations'][0]['relation'] = relation
        source_path = self.root / 'album-id-source.json'
        source_path.write_text(json.dumps(source), encoding='utf-8')
        source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        response = {'title': album, 'tracks': [{
            'videoId': video, 'title': tracks[1 if persistent_id != PID else 0]['title'],
            'artists': [{'name': 'Artist'}], 'album': album,
            'duration_seconds': duration, 'length': '1:40'}]}
        response_path = self.root / 'album-response.json'
        response_path.write_text(json.dumps({
            'endpoint': 'get_album', 'arguments': {'browseId': ALBUM_ID},
            'observed_at': '2026-10-09T00:00:00+00:00', 'response': response}),
            encoding='utf-8')
        response_sha = hashlib.sha256(response_path.read_bytes()).hexdigest()
        row = {'persistent_id': persistent_id, 'video_id': video,
            'input_path': str(response_path.resolve()), 'input_sha256': response_sha,
            'source_relation': 'album_catalog_candidate', 'album_id': ALBUM_ID,
            'source_key': {'endpoint': 'get_album', 'argument_key': 'browseId',
                           'argument_value': ALBUM_ID, 'track_position': 1},
            'source_album_id_inputs': [{
                'snapshot_path': str(source_path.resolve()), 'snapshot_sha256': source_sha,
                'observation_index': 0, 'source_key': 'observations[0].track.album.id',
                'source_video_id': source['observations'][0]['track']['video_id']}]}
        catalog = {'schema_version': 1,
            'producer': 'opengenius-album-catalog-seed-evidence-v1',
            'library_sha256': LIBRARY_HASH, 'identity_map_sha256': map_hash,
            'source_plan_producer': 'opengenius-saved-radio-album-plan-v1',
            'album_plan_sha256': 'c' * 64, 'rows': [row],
            'catalog_observations': [{'relation': 'album_catalog_candidate'}],
            'audit': {'recommendation_edges_created': 0,
                      'recommendation_observations_created': 0}}
        catalog_path = self.root / 'catalog-evidence.json'
        catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
        return tracks, matcher, catalog, catalog_path

    def test_album_catalog_hint_revalidates_response_and_saved_radio_browse_id(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture()
        validated = validate_album_catalog_evidence(
            catalog, catalog_path, tracks, matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(validated[0]['persistent_id'], '0000000000000002')
        radio_doc = build_seed_video_evidence(
            [self.source_path], matcher, LIBRARY_HASH, MAP_HASH)
        combined = combine_seed_video_evidence(
            radio_doc, self.sidecar_path, catalog, catalog_path,
            tracks, matcher, LIBRARY_HASH, MAP_HASH)
        combined_path = self.root / 'combined.json'
        combined_path.write_text(json.dumps(combined), encoding='utf-8')
        hints = select_seed_video_evidence(
            combined, combined_path, tracks, tracks, matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(hints['0000000000000002']['video_id'], CATALOG_VIDEO)
        self.assertEqual(hints['0000000000000002']['source_relation'], 'album_catalog_candidate')
        self.assertEqual(hints['0000000000000002']['source'],
                         'saved_album_catalog_metadata_candidate_unverified')

    def test_catalog_evidence_alone_can_build_seed_video_sidecar(self):
        tracks = [track(), track('0000000000000002', 'Catalog song')]
        map_path = self.root / 'identity-map.json'
        map_path.write_text(json.dumps(build_map(tracks, LIBRARY_HASH)), encoding='utf-8')
        map_hash = hashlib.sha256(map_path.read_bytes()).hexdigest()
        tracks_path = self.root / 'tracks.json'
        tracks_path.write_text(json.dumps({'source_sha256': LIBRARY_HASH,
                                           'tracks': tracks}), encoding='utf-8')
        _, _, catalog, catalog_path = self.catalog_fixture(map_hash=map_hash)
        output_path = self.root / 'catalog-only-seed-evidence.json'
        argv = ['seed-video-evidence', '--track-snapshot', str(tracks_path),
                '--identity-map', str(map_path), '--catalog-evidence', str(catalog_path),
                '--output', str(output_path)]
        with patch('sys.argv', argv), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(evidence_main(), None)
        output = json.loads(output_path.read_text(encoding='utf-8'))
        self.assertEqual(output['audit']['radio_observation_count'], 0)
        self.assertEqual(output['audit']['edges_created'], 0)
        self.assertEqual(output['audit']['accepted_seed_video_count'], 1)
        hints = select_seed_video_evidence(
            output, output_path, tracks, tracks,
            IdentityMap(build_map(tracks, LIBRARY_HASH), tracks, LIBRARY_HASH),
            LIBRARY_HASH, map_hash)
        self.assertEqual(hints['0000000000000002']['video_id'], CATALOG_VIDEO)
        self.assertEqual(hints['0000000000000002']['source_relation'],
                         'album_catalog_candidate')

    def test_rebound_catalog_hints_revalidate_old_proof_then_match_current_map(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture()
        rows = build_rebound_catalog_hints(
            catalog, catalog_path, tracks, matcher, matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['persistent_id'], '0000000000000002')
        self.assertEqual(row['video_id'], CATALOG_VIDEO)
        self.assertEqual(row['source_relation'], 'rebound_album_catalog_candidate')
        self.assertEqual(row['prior_catalog_evidence_sha256'],
                         hashlib.sha256(catalog_path.read_bytes()).hexdigest())

    def test_album_catalog_hint_accepts_separately_typed_saved_search_provenance(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture()
        query = 'Album Artist'
        search_response = [{'browseId': ALBUM_ID, 'title': 'Album',
                            'artists': [{'name': 'Artist'}]}]
        response_path = self.root / 'saved-search.json'
        response_path.write_text(json.dumps({
            'endpoint': 'search',
            'arguments': {'query': query, 'filter': 'albums', 'limit': 5},
            'observed_at': '2026-10-09T00:00:00+00:00',
            'response': search_response}), encoding='utf-8')
        response_sha = hashlib.sha256(response_path.read_bytes()).hexdigest()
        credits = sorted([list(credit) for credit in matcher.artist_credits('Artist')])
        source = {'source_kind': 'search_album', 'evidence_path': 'search-evidence.json',
            'evidence_sha256': '0' * 64, 'response_path': str(response_path),
            'response_sha256': response_sha, 'source_key': 'response[0].browseId',
            'arguments': {'query': query, 'filter': 'albums', 'limit': 5},
            'result_position': 0}
        evidence = {'schema_version': 1,
            'producer': 'opengenius-unindexed-album-search-evidence-v1',
            'library_sha256': LIBRARY_HASH, 'identity_map_sha256': MAP_HASH,
            'search_album_candidates': [{
                'source_kind': 'search_album', 'browse_id': ALBUM_ID,
                'album_title': 'Album', 'canonical_artists': credits,
                'candidate_pids': ['0000000000000002'],
                'source': {'response_sha256': response_sha, 'result_position': 0,
                           'arguments': source['arguments']}}]}
        evidence_path = self.root / 'search-evidence.json'
        evidence_path.write_text(json.dumps(evidence), encoding='utf-8')
        source['evidence_sha256'] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        catalog['rows'][0]['source_album_id_inputs'] = [source]
        catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
        original_catalog_input = catalog['rows'][0]['input_path']
        original_catalog_hash = catalog['rows'][0]['input_sha256']
        validated = validate_album_catalog_evidence(
            catalog, catalog_path, tracks, matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(validated[0]['persistent_id'], '0000000000000002')
        self.assertEqual(validated[0]['input_path'], str(Path(original_catalog_input).resolve()))
        self.assertEqual(validated[0]['input_sha256'], original_catalog_hash)

    def test_album_catalog_rejects_search_result_browse_id_mismatch(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture()
        response_path = self.root / 'saved-search-mismatch.json'
        response_path.write_text(json.dumps({
            'endpoint': 'search',
            'arguments': {'query': 'Album Artist', 'filter': 'albums', 'limit': 5},
            'observed_at': '2026-10-09T00:00:00+00:00',
            'response': [{'browseId': 'MPREanotheralbum', 'title': 'Album',
                          'artists': [{'name': 'Artist'}]}]}), encoding='utf-8')
        response_sha = hashlib.sha256(response_path.read_bytes()).hexdigest()
        evidence = {'schema_version': 1,
            'producer': 'opengenius-unindexed-album-search-evidence-v1',
            'library_sha256': LIBRARY_HASH, 'identity_map_sha256': MAP_HASH,
            'search_album_candidates': []}
        evidence_path = self.root / 'search-evidence-mismatch.json'
        evidence_path.write_text(json.dumps(evidence), encoding='utf-8')
        catalog['rows'][0]['source_album_id_inputs'] = [{
            'source_kind': 'search_album', 'evidence_path': str(evidence_path),
            'evidence_sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            'response_path': str(response_path), 'response_sha256': response_sha,
            'source_key': 'response[0].browseId',
            'arguments': {'query': 'Album Artist', 'filter': 'albums', 'limit': 5},
            'result_position': 0}]
        catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'browse ID'):
            validate_album_catalog_evidence(
                catalog, catalog_path, tracks, matcher, LIBRARY_HASH, MAP_HASH)

    def test_album_catalog_loader_rejects_duration_and_saved_album_source_mismatches(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture(duration=104)
        with self.assertRaisesRegex(ValueError, 'complete-credit duration'):
            validate_album_catalog_evidence(catalog, catalog_path, tracks, matcher,
                                            LIBRARY_HASH, MAP_HASH)
        tracks, matcher, catalog, catalog_path = self.catalog_fixture(relation='related')
        with self.assertRaisesRegex(ValueError, 'not read from the saved radio'):
            validate_album_catalog_evidence(catalog, catalog_path, tracks, matcher,
                                            LIBRARY_HASH, MAP_HASH)

    def test_album_catalog_loader_rejects_unsanitized_response_fields(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture()
        row = catalog['rows'][0]
        response_path = Path(row['input_path'])
        saved = json.loads(response_path.read_text())
        saved['response']['feedbackTokens'] = {'private': 'value'}
        response_path.write_text(json.dumps(saved), encoding='utf-8')
        row['input_sha256'] = hashlib.sha256(response_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'outside its safe metadata schema'):
            validate_album_catalog_evidence(catalog, catalog_path, tracks, matcher,
                                            LIBRARY_HASH, MAP_HASH)

    def test_combiner_rejects_conflicting_pid_video_hints(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture(
            persistent_id=PID, video='catalog0002')
        radio_doc = build_seed_video_evidence(
            [self.source_path], matcher, LIBRARY_HASH, MAP_HASH)
        with self.assertRaisesRegex(ValueError, 'conflict for one PID'):
            combine_seed_video_evidence(radio_doc, self.sidecar_path,
                catalog, catalog_path, tracks, matcher, LIBRARY_HASH, MAP_HASH)


if __name__ == '__main__':
    unittest.main()
