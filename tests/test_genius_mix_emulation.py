import os
from collections import Counter
import struct
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from emulate_genius_mix import MixCore, MixSession, NEXT_TRACK, DESTROY_CLUSTER, probe_native_mix_kind
from genius_format import pack_config, pack_similarities

EXECUTABLE = os.environ.get('OPENGENIUS_MUSIC_EXECUTABLE')


class MixInputTests(unittest.TestCase):
    def test_invalid_inputs_rejected_before_native_execution(self):
        core = MixCore.__new__(MixCore)
        for candidates, limit, cap in [([], 25, 100), ([17, 17], 25, 100),
                                       ([0], 25, 100), ([True], 25, 100),
                                       ([1 << 64], 25, 100), ([17], 0, 100),
                                       ([17], True, 100), ([17], 1001, 100),
                                       ([17], 25, 0), ([17], 25, 4097),
                                       (list(range(1, 4098)), 25, 100)]:
            with self.subTest(candidates=candidates[:2], limit=limit, cap=cap):
                with self.assertRaises(ValueError):
                    core.generate_mix(candidates, limit, cap)

    def session(self, returned=17):
        class Core:
            metadata = {17: bytes(32)}
            random_seed = 0
            steps = 0
            calls = Counter()

            def run(self, entry, cluster):
                self.calls[entry] += 1
                self.steps += 1
                return returned if entry == NEXT_TRACK else 0

        core = Core()
        session = MixSession(core, 123, [17], 100, 0, Counter())
        core._active_mix_session = session
        return core, session

    def test_unknown_result_destroys_context_and_blocks_further_reads(self):
        core, session = self.session(returned=99)
        with self.assertRaisesRegex(ValueError, 'unknown Genius ID'):
            session.next_batch(1)
        session.close()
        self.assertEqual(core.calls[DESTROY_CLUSTER], 1)
        self.assertIsNone(core._active_mix_session)
        with self.assertRaisesRegex(ValueError, 'closed'):
            session.next_batch(1)

    def test_cumulative_budget_is_enforced_across_batches(self):
        core, session = self.session()
        with session:
            session.next_batch(600)
            with self.assertRaisesRegex(ValueError, 'budget exceeded'):
                session.next_batch(401)
            self.assertEqual(core.calls[NEXT_TRACK], 600)
            session.next_batch(400)
        self.assertEqual(core.calls[NEXT_TRACK], 1000)

    def test_zero_result_stops_native_reads_and_closed_report_is_frozen(self):
        core, session = self.session(returned=0)
        with session:
            self.assertEqual(session.next_batch(10), [])
            self.assertEqual(session.next_batch(10), [])
            self.assertEqual(core.calls[NEXT_TRACK], 1)
        report = session.report(20)
        core.steps += 100
        core.calls[NEXT_TRACK] += 100
        self.assertEqual(session.report(20), report)
        self.assertTrue(report['exhausted'])
        self.assertTrue(report['context_closed'])


@unittest.skipUnless(EXECUTABLE, 'Set OPENGENIUS_MUSIC_EXECUTABLE for binary integration tests')
class NativeMixCoreTests(unittest.TestCase):
    def core(self, seed=0):
        config = pack_config(dict(version=2,
                                  filters=[dict(type=1, parameters=[20, 50, 10, 10])],
                                  flags=0, result_words=[10, 20]))
        # Relations point OUTSIDE the multi-seed pool on purpose.
        return MixCore(EXECUTABLE, config, {value: bytes(32) for value in (17, 34, 51, 68, 85)},
                       {value: pack_similarities(0, [value, 85])
                        for value in (17, 34, 51, 68)}, random_seed=seed)

    def test_native_playlist_kind_predicate_distinguishes_mix_from_genius_playlist(self):
        result = probe_native_mix_kind(self.core())
        self.assertEqual(result['native_genius_mix_kind'], 32)
        self.assertEqual([row['matches_genius_mix'] for row in result['observations']],
                         [False, False, True, False])
        self.assertFalse(result['storage_kind_mapping_verified'])

    def test_multiseed_context_also_follows_relations_outside_input(self):
        result = self.core().generate_mix([17, 34, 51, 68], 12)
        self.assertEqual(result['result_genius_ids'], [f'{value:016X}' for value in (68, 34, 85, 17, 51)])
        self.assertTrue(result['exhausted'])
        self.assertFalse(result['stopped_at_limit'])
        self.assertEqual(result['duplicate_result_count'], 0)
        self.assertEqual(result['results_outside_input_count'], 1)
        self.assertFalse(result['native_mix_playback_verified'])

    def test_recreation_with_fixed_randomness_is_reproducible(self):
        core = self.core()
        first = core.generate_mix([17, 34, 51, 68], 12)
        second = core.generate_mix([17, 34, 51, 68], 12)
        other = self.core(seed=1).generate_mix([17, 34, 51, 68], 12)
        self.assertEqual(first['result_genius_ids'], second['result_genius_ids'])
        self.assertNotEqual(first['result_genius_ids'], other['result_genius_ids'])
        self.assertEqual(set(first['result_genius_ids']), set(other['result_genius_ids']))

    def test_constructor_cap_limits_seed_draws_not_result_count(self):
        limited = self.core().generate_mix([17, 34, 51, 68], limit=2)
        capped = self.core().generate_mix([17, 34, 51, 68], limit=12, candidate_cap=2)
        self.assertEqual(len(limited['result_genius_ids']), 2)
        self.assertTrue(limited['stopped_at_limit'])
        self.assertFalse(limited['exhausted'])
        self.assertEqual(len(capped['result_genius_ids']), 3)
        self.assertEqual(capped['results_outside_input_count'], 1)
        self.assertTrue(capped['exhausted'])

    def test_single_candidate_delegates_to_existing_seed_core(self):
        ordinary = self.core().generate(17, 12)
        multi = self.core().generate_mix([17], 12)
        self.assertEqual(multi['result_genius_ids'], ordinary['result_genius_ids'])
        # One candidate takes the ordinary path and CAN follow similarities.
        self.assertIn(f'{85:016X}', multi['result_genius_ids'])

    def test_missing_first_random_candidate_rejects_context(self):
        with self.assertRaisesRegex(ValueError, 'rejected candidate context'):
            self.core().generate_mix([17, 99], 12)
        result = self.core().generate_mix([99, 17], 12)
        self.assertEqual(result['result_genius_ids'], [f'{value:016X}' for value in (17, 85)])

    def test_long_run_can_repeat_tracks_in_a_larger_connected_group(self):
        core = self.core()
        pool = list(range(0x70000001, 0x70000041))
        core.metadata = {value: bytes(32) for value in pool}
        core.similarities = {value: pack_similarities(0, pool) for value in pool}
        result = core.generate_mix(pool, limit=80)
        self.assertEqual(len(result['result_genius_ids']), 80)
        self.assertGreater(result['duplicate_result_count'], 0)
        self.assertTrue(result['stopped_at_limit'])
        self.assertFalse(result['exhausted'])
        self.assertEqual(result['results_outside_input_count'], 0)
        with core.open_mix(pool) as session:
            batches = session.next_batch(25) + session.next_batch(25) + session.next_batch(30)
        self.assertEqual(batches, result['result_genius_ids'])
        self.assertEqual(session.report(80)['batch_count'], 3)

    def test_live_context_rejects_recreation_and_continues_without_reset(self):
        core = self.core()
        with core.open_mix([17, 34, 51, 68]) as session:
            first = session.next_batch(2)
            with self.assertRaisesRegex(ValueError, 'already open'):
                core.open_mix([17, 34])
            with self.assertRaisesRegex(ValueError, 'Close the mix context'):
                core.generate(17, 2)
            second = session.next_batch(10)
            self.assertEqual(session.next_batch(10), [])
        combined = first + second
        self.assertEqual(combined, core.generate_mix([17, 34, 51, 68], 12)['result_genius_ids'])
        with self.assertRaisesRegex(ValueError, 'closed'):
            session.next_batch(1)

    def test_artist_distance_state_survives_batch_boundaries(self):
        pool = list(range(17, 33))
        config = pack_config(dict(version=2,
                                  filters=[dict(type=1, parameters=[20, 50, 10, 10]),
                                           dict(type=3, parameters=[1, 2, 6, 0])],
                                  flags=0, result_words=[10, 20]))
        core = MixCore(EXECUTABLE, config,
                       {value: struct.pack('<4Q', 0, 100 + value % 4,
                                          200 + value // 2, 300 + value) for value in pool},
                       {value: pack_similarities(0, pool) for value in pool})
        whole = core.generate_mix(pool, 24)
        with core.open_mix(pool) as session:
            batched = session.next_batch(7) + session.next_batch(7) + session.next_batch(10)
        self.assertEqual(batched, whole['result_genius_ids'])
        self.assertGreater(len(batched), 7)


if __name__ == '__main__':
    unittest.main()
