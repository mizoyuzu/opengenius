"""Regression tests for exact graph rows and complete typed rebind receipts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import rebind_radio_growth_alias_chain as alias_chain


class AliasChainConsumerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = ROOT / (
            'data/ytmusic/automatic-genuine-roots-1000-20261011-v07-'
            'alias-chain-rebind-candidate-v03')
        cls.graph = json.loads((cls.candidate / 'accepted-baseline-graphs.json').read_text())
        cls.report = json.loads((cls.candidate / 'identity-map-rebinding-report.json').read_text())
        cls.state = json.loads((cls.candidate / 'state.json').read_text())
        cls.binding = cls.report['identity_map_rebinding']
        cls.parent = alias_chain._load_saved_parent_growth(
            cls.binding['source_growth']['path'], cls.report['zero_id_library_sha256'],
            cls.binding['old_identity_map']['sha256'])
        baseline = cls.binding['source_rebase']
        cls.baseline_graph = json.loads(Path(baseline['graph_export_path']).read_text())
        cls.baseline_state = json.loads(Path(baseline['state_path']).read_text())
        cls.baseline_report = json.loads(Path(baseline['rebase_report_path']).read_text())
        cls.provenance = {
            'graph_export_path': baseline['graph_export_path'],
            'graph_export_sha256': baseline['graph_export_sha256'],
            'rebase_report_path': baseline['rebase_report_path'],
            'rebase_report_sha256': baseline['rebase_report_sha256'],
            'state_path': baseline['state_path'],
            'state_file_sha256': baseline['state_file_sha256'],
            'accepted_input_manifest_sha256': cls.binding['accepted_input_manifest_sha256'],
        }

    def _verify_fixture(self, graph, report, state=None, state_path=None,
                        stub_alias_chain=False, saved_parent=None):
        state = self.state if state is None else state
        state_path = (self.candidate / 'state.json') if state_path is None else state_path
        def replay(*_args, **_kwargs):
            return (self.parent['state'], self.parent['roots'], self.parent['runs'],
                    self.parent['accepted_graphs_sha256'])

        source_patches = [
            patch.object(alias_chain, '_verify_rebase_receipt', return_value=(
                    self.baseline_graph, self.baseline_report, self.baseline_state,
                    self.provenance)),
            patch.object(alias_chain, '_load_saved_parent_growth',
                         return_value=self.parent if saved_parent is None else saved_parent),
            patch.object(alias_chain, '_replay_previous_growth', side_effect=replay),
        ]
        if stub_alias_chain:
            source_patches.append(patch.object(
                alias_chain, 'verify_v04_v11_identity_map_alias_chain',
                return_value=self.binding['reviewed_alias_chain']))
        with source_patches[0], source_patches[1], source_patches[2]:
            if stub_alias_chain:
                source_patches[3].start()
            try:
                return alias_chain.verify_alias_chain_growth_rebinding_report(
                    graph_doc=graph, report=report, state=state,
                    library_hash=self.report['zero_id_library_sha256'],
                    identity_hash=self.report['identity_map_sha256'],
                    state_path=state_path)
            finally:
                if stub_alias_chain:
                    source_patches[3].stop()

    def test_real_candidate_fixture_preserves_exact_691_root_rows_and_complete_receipts(self):
        # This historical child predates policy propagation. Its actual parent
        # is now radio-policy typed, so accepting the untagged child would let
        # it erase source provenance. Keep a separate mocked legacy-parent view
        # below to retain the exact row/receipt regression for the old format.
        with self.assertRaisesRegex(ValueError, 'does not inherit exact parent policy'):
            self._verify_fixture(self.graph, self.report)

        legacy_parent = deepcopy(self.parent)
        legacy_parent['graph'].pop('source_policy', None)
        proof_receipts = self._verify_fixture(
            self.graph, self.report, saved_parent=legacy_parent)
        self.assertEqual(self.graph['accepted_roots'], legacy_parent['roots'])
        self.assertEqual(len(self.graph['accepted_roots']), 691)
        self.assertEqual(sum(bool(row['ordered_target_pids'])
                             for row in self.graph['accepted_roots']), 413)
        self.assertEqual(len(proof_receipts), len(self.binding['proof_input_receipts']))

        reordered = deepcopy(self.graph)
        reordered['accepted_roots'][0], reordered['accepted_roots'][1] = (
            reordered['accepted_roots'][1], reordered['accepted_roots'][0])
        with self.assertRaisesRegex(ValueError, 'root rows/order/counts'):
            self._verify_fixture(reordered, self.report, stub_alias_chain=True,
                                 saved_parent=legacy_parent)

        omitted = deepcopy(self.report)
        omitted['identity_map_rebinding']['proof_input_receipts'].pop()
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary)
            state_path = copy / 'state.json'
            state_path.write_bytes((self.candidate / 'state.json').read_bytes())
            omitted['source_state_path'] = str(state_path.resolve())
            report_path = copy / 'identity-map-rebinding-report.json'
            report_path.write_text(json.dumps(omitted, ensure_ascii=False, indent=2) + '\n',
                                   encoding='utf-8')
            altered_graph = deepcopy(self.graph)
            altered_graph['source_state_sha256'] = hashlib.sha256(state_path.read_bytes()).hexdigest()
            altered_graph['source_identity_map_rebinding_report_sha256'] = hashlib.sha256(
                report_path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'omits or changes required source receipts'):
                self._verify_fixture(altered_graph, omitted, self.state, state_path,
                                     stub_alias_chain=True, saved_parent=legacy_parent)


if __name__ == '__main__':
    unittest.main()
