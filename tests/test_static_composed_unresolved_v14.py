import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v14-static-composed-absence-v01/scripts')
REPO = Path('/mnt/data/mizoyuzu/Projects/personal/opengenius')
DATA_ROOT = REPO
CAMPAIGN = REPO / 'data/ytmusic/automatic-genuine-roots-1000-20261014-v15-shiny-ost-radio-live-v01'

sys.path.insert(0, str(RUNTIME))
os.environ.setdefault('OPENGENIUS_TRUSTED_DATA_ROOT', str(DATA_ROOT))
# Several runtime-focused tests load different frozen copies under the same
# public module names. Save and temporarily replace those names so this test
# always exercises the isolated v14 implementation, even in a combined suite.
_MODULE_NAMES = ('expand_genuine_roots', 'merge_rebased_radio_growth',
                 'music_identity_map', 'seed_video_evidence')
_SAVED_MODULES = {name: sys.modules.pop(name) for name in _MODULE_NAMES
                  if name in sys.modules}
import expand_genuine_roots as expander
import merge_rebased_radio_growth as merger
from music_identity_map import IdentityMap, load_library


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class StaticComposedUnresolvedV14Tests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        for name in _MODULE_NAMES:
            if name in _SAVED_MODULES:
                sys.modules[name] = _SAVED_MODULES[name]
            else:
                sys.modules.pop(name, None)

    @classmethod
    def setUpClass(cls):
        cls.batch = CAMPAIGN / 'batch-0002'
        cls.intent_path = cls.batch / 'intent.json'
        cls.intent_bytes = cls.intent_path.read_bytes()
        cls.intent = json.loads(cls.intent_bytes)
        cls.manifest_path = cls.batch / 'collector/seeds.json'
        cls.manifest = json.loads(cls.manifest_path.read_bytes())
        cls.summary_path = next((cls.batch / 'collector').glob('summary-*.json'))
        cls.summary = json.loads(cls.summary_path.read_bytes())
        cls.evidence_path = cls.batch / 'seed-video-evidence.json'
        cls.evidence_bytes = cls.evidence_path.read_bytes()
        cls.evidence = json.loads(cls.evidence_bytes)
        cfg = cls.intent['runner_config']
        cls.tracks, cls.library_hash, _ = load_library(
            track_snapshot=Path(cfg['track_snapshot']['path']))
        map_bytes = Path(cfg['identity_map']['path']).read_bytes()
        cls.identity_hash = _sha(map_bytes)
        cls.matcher = IdentityMap(json.loads(map_bytes), cls.tracks, cls.library_hash)
        cls.parent_dir = CAMPAIGN / 'batch-0001/growth'
        cls.prior = [row['input'] for row in expander._growth_snapshot_receipts(cls.parent_dir)]
        cls.unresolved = [seed for seed, result in zip(
            cls.manifest['seeds'], cls.summary['results'])
            if result.get('status') == 'search_unresolved']

    def prove(self, seed, *, intent=None, config=None, intent_bytes=None,
              evidence_bytes=None, prior=None):
        return merger._typed_static_composed_no_hint(
            seed, intent=intent or self.intent,
            runner_config=config or self.intent['runner_config'],
            intent_bytes=intent_bytes or self.intent_bytes,
            evidence_path=self.evidence_path,
            evidence_bytes=evidence_bytes or self.evidence_bytes,
            prior_snapshot_inputs=self.prior if prior is None else prior,
            tracks=self.tracks, matcher=self.matcher,
            library_hash=self.library_hash, identity_hash=self.identity_hash)

    def test_all_28_actual_unresolved_seeds_replay_zero_credit(self):
        self.assertEqual(len(self.unresolved), 28)
        receipts = [self.prove(seed) for seed in self.unresolved]
        self.assertEqual(len({r['persistent_id'] for r in receipts}), 28)
        self.assertTrue(all(r['producer'] == 'opengenius-static-composed-no-video-hint-v1'
                            and r['credit'] == {'persistent_ids': 0, 'roots': 0, 'edges': 0}
                            and r['selected_hint'] is None for r in receipts))
        selected = merger.select_seed_video_evidence(
            self.evidence, self.evidence_path,
            [{'persistent_id': row['persistent_id']} for row in self.manifest['seeds']],
            self.tracks, self.matcher, self.library_hash, self.identity_hash)
        snapshot_paths = list((self.batch / 'radio-only-source').glob('*.snapshot.json'))
        full = []
        for seed in self.unresolved:
            disposition = merger._unresolved_seed_disposition(
                seed, selected, source_policy='authenticated-watch-radio-only-v1',
                collector_summary=self.summary,
                collector_summary_path=self.summary_path,
                seed_manifest=self.manifest, seed_manifest_path=self.manifest_path,
                seed_video_evidence_path=self.evidence_path,
                snapshot_paths=snapshot_paths,
                library_hash=self.library_hash, identity_hash=self.identity_hash,
                expected_source_paths={
                    'track_snapshot': Path(self.intent['runner_config']['track_snapshot']['path']),
                    'identity_map': Path(self.intent['runner_config']['identity_map']['path'])},
                tracks=self.tracks, matcher=self.matcher,
                prior_snapshot_inputs=self.prior)
            self.assertIsNotNone(disposition)
            self.assertEqual(disposition['status'], 'search_unresolved')
            self.assertEqual(disposition['static_composed_seed_video_absence']['credit'],
                             {'persistent_ids': 0, 'roots': 0, 'edges': 0})
            full.append(disposition)
        self.assertEqual(len(full), 28)

    def test_actual_composed_sidecar_rebuilds_from_parent_and_external(self):
        current = expander.build_seed_video_evidence(
            [{'input': row} for row in self.prior], self.matcher,
            self.library_hash, self.identity_hash)
        external = json.loads(Path(
            self.intent['runner_config']['external_seed_video_evidence']['path']).read_bytes())
        rebuilt, _ = expander._compose_batch_seed_evidence(current, external)
        self.assertEqual(rebuilt, self.evidence)

    def test_rejects_parent_external_sidecar_dynamic_and_intent_tampering(self):
        seed = self.unresolved[0]
        bad_parent = [dict(self.prior[0], sha256='0' * 64), *self.prior[1:]]
        with self.assertRaisesRegex(ValueError, 'parent snapshot changed'):
            self.prove(seed, prior=bad_parent)

        sidecar = json.loads(self.evidence_bytes)
        sidecar['rows'][0]['video_id'] += '_tampered'
        with self.assertRaisesRegex(ValueError, 'does not replay'):
            self.prove(seed, evidence_bytes=json.dumps(sidecar).encode())

        config = dict(self.intent['runner_config'])
        config['dynamic_current_radio_hints'] = True
        with self.assertRaisesRegex(ValueError, 'exact radio-only config'):
            self.prove(seed, config=config)

        with tempfile.TemporaryDirectory() as temp:
            external_path = Path(temp) / 'external.json'
            external_doc = json.loads(Path(
                self.intent['runner_config']['external_seed_video_evidence']['path']).read_bytes())
            external_doc['rows'][0]['video_id'] += '_tampered'
            external_bytes = json.dumps(external_doc).encode()
            external_path.write_bytes(external_bytes)
            config = dict(self.intent['runner_config'])
            config['external_seed_video_evidence'] = {
                'path': str(external_path), 'sha256': _sha(external_bytes)}
            with self.assertRaisesRegex(ValueError, 'does not replay'):
                self.prove(seed, config=config)

        malformed_intent = dict(self.intent, current_radio_evidence={'path': '/bad', 'sha256': '0' * 64})
        with self.assertRaisesRegex(ValueError, 'exact radio-only config'):
            self.prove(seed, intent=malformed_intent)

        malformed_bytes = self.intent_bytes.replace(b'"batch": 2', b'"batch": 99', 1)
        with self.assertRaisesRegex(ValueError, 'object differs from its sealed bytes'):
            self.prove(seed, intent_bytes=malformed_bytes)

    def test_collected_seed_cannot_be_treated_as_unresolved(self):
        seed, result = next((seed, result) for seed, result in zip(
            self.manifest['seeds'], self.summary['results'])
            if result.get('status') == 'collected')
        self.assertIsNone(merger._unresolved_seed_disposition(
            seed, {}, source_policy='authenticated-watch-radio-only-v1',
            collector_summary=self.summary, collector_summary_path=self.summary_path,
            seed_manifest=self.manifest, seed_manifest_path=self.manifest_path,
            seed_video_evidence_path=self.evidence_path,
            snapshot_paths=[], library_hash=self.library_hash,
            identity_hash=self.identity_hash, tracks=self.tracks,
            matcher=self.matcher, prior_snapshot_inputs=self.prior))

    def test_all_14_collected_radio_seeds_keep_their_strict_search_proofs(self):
        cfg = self.intent['runner_config']
        hints = merger.select_seed_video_evidence(
            self.evidence, self.evidence_path,
            [{'persistent_id': row['persistent_id']} for row in self.manifest['seeds']],
            self.tracks, self.matcher, self.library_hash, self.identity_hash)
        snapshots = list((self.batch / 'radio-only-source').glob('*.snapshot.json'))
        self.assertEqual(len(snapshots), 14)
        expected = {
            'track_snapshot': Path(cfg['track_snapshot']['path']),
            'identity_map': Path(cfg['identity_map']['path']),
            'collector_summary': self.summary_path,
            'collector_seed_manifest': self.manifest_path,
            'batch_intent': self.intent_path,
            'seed_plan': self.batch / 'seed-plan.json',
            'seed_video_evidence': self.evidence_path,
        }
        verified = []
        for snapshot in snapshots:
            pid = snapshot.name.removesuffix('.snapshot.json')
            seed = next(row for row in self.manifest['seeds']
                        if row['persistent_id'] == pid)
            paths = dict(expected, collector_snapshot=self.batch / 'collector' / (pid + '.json'))
            hint = merger._validate_collector_seed_video_binding(
                seed, hints, [snapshot], 'authenticated-watch-radio-only-v1',
                self.library_hash, self.identity_hash, paths)
            verified.append((pid, hint['video_id'], hint['source']))
        self.assertEqual(len(verified), 14)
        self.assertTrue(all(source == 'authenticated_search_seed_proof'
                            for _, _, source in verified))


if __name__ == '__main__':
    unittest.main()
