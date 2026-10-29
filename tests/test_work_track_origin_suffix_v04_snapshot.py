"""Offline guards for the exact reviewed 40-track origin-suffix extension."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path('/mnt/data/mizoyuzu/Projects/personal/opengenius')
sys.path.insert(0, str(ROOT / 'data/ytmusic/work-video-runtime-backup-20261016-v04/scripts'))

import work_track_video_bindings as bindings  # noqa: E402


CONTEXT = ROOT / 'data/ytmusic/post870-kirby-context-bindings-20261015-v01'
PLAYLIST = ROOT / (
    'data/ytmusic/post870-kirby-playlist-direct-20261015-v04/requests/'
    'ae16d1c9d68a4b5a7cea47b4e9b9a1a1523c7a11d4781d5e02a59c7a6bb3ffd3.json')
TRACK_SNAPSHOT = ROOT / (
    'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json')


class OriginSuffixBindingV04Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc, cls.report, cls.official, cls.local_tracks, cls.input_receipts = (
            bindings.load_binding_inputs(
                CONTEXT / 'candidate-bindings.json', CONTEXT / 'report.json',
                CONTEXT / 'hallab-official-page.html', TRACK_SNAPSHOT, repo=ROOT))
        cls.legacy, cls.legacy_receipt = bindings.load_frontier_playlist_cache(
            PLAYLIST, cls.official, cls.local_tracks)
        cls.extended, cls.extension_receipt = bindings.load_frontier_playlist_cache_v04(
            PLAYLIST, cls.official, cls.local_tracks)
        cls.recipe_context = bindings.load_reviewed_recipe_context(
            cls.official, cls.local_tracks)
        cls.raw_doc = json.loads(PLAYLIST.read_bytes())

    def test_legacy_packet_and_99_row_recipe_remain_unchanged(self):
        self.assertEqual(len(self.doc['bindings']), 25)
        self.assertEqual(self.legacy_receipt['strict_metadata_candidate_count'], 99)
        self.assertEqual(len(self.legacy), 99)
        self.assertEqual(self.extension_receipt['legacy_v03_strict_candidate_count'], 99)
        self.assertTrue(all(item['policy'] == bindings.POLICY for item in self.legacy))

    def test_exact_40_addition_is_metadata_only_and_unique(self):
        self.assertEqual(len(self.extended), 139)
        self.assertEqual(self.extension_receipt['origin_suffix_v04_candidate_count'], 40)
        self.assertEqual(len({x['persistent_id'] for x in self.extended}), 139)
        self.assertEqual(len({x['video_id'] for x in self.extended}), 139)
        extension = [x for x in self.extended if x['policy'] == bindings.ORIGIN_SUFFIX_POLICY]
        self.assertEqual(len(extension), 40)
        for item in extension:
            self.assertFalse(item['recording_identity_verified'])
            self.assertEqual(item['origin_suffix_evidence']['credit'], 0)
            self.assertEqual(item['source_game_suffix'],
                             item['origin_suffix_evidence']['source_game_suffix'])
            self.assertEqual(item['disc_track'], item['origin_suffix_evidence']['ordinal'])
            self.assertEqual(self.official[item['disc_track']],
                             item['official_japanese_title'])
            self.assertTrue(item['official_japanese_title'].endswith(
                f"（{item['source_game_suffix']}）")
                or item['official_japanese_title'].endswith(
                    f"({item['source_game_suffix']})"))
            source_row = self.raw_doc['response']['tracks'][
                item['origin_suffix_evidence']['playlist_row'] - 1]
            self.assertEqual(item['remote_title'], source_row['title'])
            self.assertIn('(', item['remote_title'])

    def test_parenthesized_remote_title_is_exactly_reused_on_watch_and_radio(self):
        expected = next(x for x in self.extended
                        if x['policy'] == bindings.ORIGIN_SUFFIX_POLICY
                        and '(' in x['remote_title'])
        row = next(x for x in self.raw_doc['response']['tracks']
                   if x.get('videoId') == expected['video_id'])
        bound = bindings.bind_video_row_with_origin_suffix(
            row, self.official, self.local_tracks, [expected])
        self.assertIsNotNone(bound)
        self.assertEqual(bound['remote_title'], row['title'])
        self.assertEqual(bound['disc_track'], expected['disc_track'])
        self.assertEqual(bound['source_game_suffix'], expected['source_game_suffix'])
        self.assertEqual(bound['origin_suffix_evidence'], expected['origin_suffix_evidence'])
        targets, stats = bindings.ordered_radio_targets_with_origin_suffix(
            [row], 'FFFFFFFFFFFFFFFF', self.official, self.local_tracks, [expected])
        self.assertEqual(len(targets), 1)
        self.assertEqual(stats['ordered_unique_target_count'], 1)

    def test_exported_context_reloads_pins_and_binds_raw_radio_rows(self):
        self.assertEqual(len(self.recipe_context.frontier_bindings), 139)
        self.assertEqual(len(self.recipe_context.legacy_bindings), 99)
        self.assertEqual(len(self.recipe_context.origin_suffix_bindings), 40)
        self.assertEqual(self.recipe_context.receipt['sha256'],
                         bindings.ORIGIN_SUFFIX_PLAYLIST_SHA256)
        expected = next(x for x in self.recipe_context.origin_suffix_bindings
                        if '(' in x['remote_title'])
        row = next(x for x in self.raw_doc['response']['tracks']
                   if x.get('videoId') == expected['video_id'])
        bound, stats = self.recipe_context.bind_rows([row])
        self.assertEqual(len(bound), 1)
        self.assertEqual(bound[0]['remote_title'], expected['remote_title'])
        self.assertEqual(stats['unique_strict_bindings'], 1)

    def test_changed_title_video_channel_or_ordinal_is_rejected(self):
        expected = next(x for x in self.extended
                        if x['policy'] == bindings.ORIGIN_SUFFIX_POLICY
                        and '(' in x['remote_title'])
        row = next(x for x in self.raw_doc['response']['tracks']
                   if x.get('videoId') == expected['video_id'])
        cases = []
        changed = dict(row); changed['title'] += ' (Extended Version)'; cases.append(changed)
        changed = dict(row); changed['videoId'] = 'AAAAAAAAAAA'; cases.append(changed)
        changed = dict(row); changed['title'] = changed['title'].replace(
            expected['disc_track'] + '.', '1-99.', 1); cases.append(changed)
        changed = dict(row); changed['artists'] = [{'id': 'UCOYQDzmJEWJHIuNosA2rxkX',
                                                     'name': 'lookalike'}]; cases.append(changed)
        for altered in cases:
            with self.subTest(title=altered.get('title'), video=altered.get('videoId')):
                self.assertIsNone(bindings.bind_video_row_with_origin_suffix(
                    altered, self.official, self.local_tracks, [expected]))

    def test_tampered_candidate_pid_ordinal_video_or_suffix_fails(self):
        proposal = json.loads(bindings.ORIGIN_SUFFIX_PROPOSAL_PATH.read_bytes())
        candidate = proposal['candidates'][0]
        position = candidate['playlist_row'] - 1
        row = bindings._project_playlist_rows(self.raw_doc['response']['tracks'])[position]
        source_digest = bindings.sha256(PLAYLIST.read_bytes())
        for key, bad in (
                ('persistent_id', '0000000000000000'),
                ('ordinal', '1-99'),
                ('video_id', 'AAAAAAAAAAA'),
                ('source_game_suffix', 'unreviewed title text')):
            altered = copy.deepcopy(candidate)
            altered[key] = bad
            with self.subTest(field=key):
                with self.assertRaises(ValueError):
                    bindings.validate_origin_suffix_candidate(
                        altered, row, self.official, self.local_tracks,
                        response_position=position, source_path=PLAYLIST,
                        source_sha256=source_digest)

    def test_legacy_parser_does_not_gain_generic_qualifier_support(self):
        self.assertIsNone(bindings.parse_work_title(
            "1-14. Green Greens (Kirby's Dream Land) - " + bindings.WORK_LABEL))
        self.assertIsNone(bindings.parse_work_title(
            '1-14. Green Greens (Extended Version) - ' + bindings.WORK_LABEL))


if __name__ == '__main__':
    unittest.main()
