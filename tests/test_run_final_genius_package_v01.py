import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import run_final_genius_package_v01 as runner


def write_json(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding='utf-8')
    return path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lineage_paths(campaign, runtime_manifest):
    preservation_script = campaign / 'preservation-script.py'
    return {
        'SUPERVISOR_PROCESS': campaign / 'v13-continuation-supervisor-process-v05.json',
        'CONTINUATION_INVOCATION': campaign / 'v13-continuation-invocation-v05.json',
        'CONTINUATION_CHILD': campaign / 'v13-continuation-child-v05.json',
        'RECOVERY_PROCESS': campaign / 'v13-recovery-process-v01.json',
        'RECOVERY_TERMINAL': campaign / 'v13-recovery-terminal-v01.json',
        'RECOVERY_PROGRESS_CHECKPOINT': campaign / 'v13-accepted826-progress-checkpoint-v01.json',
        'PRESERVATION_AUDIT': campaign / 'independent-preservation-audit-v03.json',
        'RUNTIME_MANIFEST': runtime_manifest,
        'RUNTIME_ROOT': runtime_manifest.parent,
        'RECOVERY_SCRIPT': campaign / 'recovery-script.py',
        'RECOVERY_SCRIPT_SHA256': digest(campaign / 'recovery-script.py'),
        'SUPERVISOR_SCRIPT': campaign / 'supervisor-script.py',
        'SUPERVISOR_SCRIPT_SHA256': digest(campaign / 'supervisor-script.py'),
        'PRESERVATION_SCRIPT': preservation_script,
        'PRESERVATION_SCRIPT_SHA256': digest(preservation_script),
    }


class FinalPackageOrchestratorTests(unittest.TestCase):
    def fixture_campaign(self, root, roots=1000, status='target_reached'):
        campaign = root / 'campaign'
        graph_dir = campaign / 'batch-0001/growth'
        graph_dir.mkdir(parents=True)
        graph_path = write_json(graph_dir / 'accepted-graphs.json', {
            'accepted_roots': [
                {'root_pid': f'{i:016X}', 'ordered_target_pids': [f'{i + 1:016X}']}
                for i in range(roots)
            ]
        })
        state_path = write_json(graph_dir / 'state.json', {'rows': []})
        progress_path = write_json(campaign / 'progress.json', {
            'status': status,
            'successful_outgoing_roots': roots,
            'batches': [{'growth_output': str(graph_dir),
                         'growth_receipt': {'path': str(graph_path), 'sha256': digest(graph_path)}}],
        })
        runtime_root = root / 'runtime'
        (runtime_root / 'scripts').mkdir(parents=True)
        expander = runtime_root / 'scripts/expand_genuine_roots.py'
        expander.write_text('# exact fixture entrypoint\n', encoding='utf-8')
        runtime = runtime_root / 'runtime-manifest.json'
        runtime.write_text(json.dumps({'modules': [{'path': 'scripts/expand_genuine_roots.py',
                                                   'sha256': digest(expander)}]}), encoding='utf-8')
        recovery_script = campaign / 'recovery-script.py'
        recovery_script.write_text('# exact fixture recovery script\n', encoding='utf-8')
        supervisor_script = campaign / 'supervisor-script.py'
        supervisor_script.write_text('# exact fixture supervisor script\n', encoding='utf-8')
        preservation_script = campaign / 'preservation-script.py'
        preservation_script.write_text('# exact fixture preservation script\n', encoding='utf-8')
        cfg = {
            'auth_path': '/private/auth.json',
            'track_snapshot': {'path': '/inputs/tracks.json'},
            'identity_map': {'path': '/inputs/map.json'},
            'baseline_state': {'path': '/inputs/base-state.json'},
            'baseline_graph_export': {'path': '/inputs/base-graph.json'},
            'rebase_report': {'path': '/inputs/rebase.json'},
            'external_seed_video_evidence': {'path': '/inputs/seeds.json'},
            'prior_attempt_ledger': {'path': '/inputs/ledger.json'},
            'target_outgoing_roots': 1000, 'global_request_budget': 6500,
            'max_attempts': 2800, 'batch_size': 44, 'batch_request_budget': 200,
            'candidate_limit': 100, 'interval': 3.0,
            'source_policy': 'authenticated-watch-radio-only-v1',
            'collector_collection_mode': 'radio-only-v1',
            'authorized_retry_release_pids': [],
        }
        terminal_path = write_json(campaign / 'v13-continuation-supervisor-terminal-v05.json', {
            'status': 'completed', 'returncode': 0,
            'progress_sha256': digest(progress_path),
            'elapsed_seconds': 123.0,
        })
        progress_doc = json.loads(progress_path.read_text())
        progress_doc['config'] = cfg
        progress_path.write_text(json.dumps(progress_doc), encoding='utf-8')
        terminal_path.write_text(json.dumps({'status': 'completed', 'returncode': 0,
                                             'progress_sha256': digest(progress_path),
                                             'elapsed_seconds': 123.0}), encoding='utf-8')
        checkpoint = write_json(campaign / 'v13-accepted826-progress-checkpoint-v01.json', {'status': 'target_reached'})
        recovery_terminal = write_json(campaign / 'v13-recovery-terminal-v01.json', {
            'status': 'completed', 'returncode': 0, 'new_http_requests': 0,
            'baseline_substitutions': 0, 'runtime_manifest_sha256': digest(runtime),
            'progress_sha256': digest(checkpoint),
        })
        recovery_script_sha = digest(recovery_script)
        supervisor_script_sha = digest(supervisor_script)
        write_json(campaign / 'v13-recovery-process-v01.json', {
            'worker_pid': 101, 'runtime_manifest_sha256': digest(runtime),
            'collector_invocations_permitted': 0, 'baseline_substitutions': 0,
            'script_sha256': recovery_script_sha,
        })
        write_json(campaign / 'independent-preservation-audit-v03.json', {
            'status': 'passed_postterminal_preservation_checks_uncredited_pending_final_whole_source_audit',
            'worker': {'progress_sha256': digest(checkpoint)},
            'runtime_manifest': {'sha256': digest(runtime)},
        })
        write_json(campaign / 'v13-continuation-supervisor-process-v05.json', {
            'supervisor_pid': 102, 'recovery_worker_pid': 101,
            'normal_recovery_terminal_required': True, 'extra_metadata_calls': 0,
            'runtime_manifest_sha256': digest(runtime),
        })
        args = []
        for flag, key in (
            ('--auth', 'auth_path'), ('--track-snapshot', 'track_snapshot'),
            ('--identity-map', 'identity_map'), ('--baseline-state', 'baseline_state'),
            ('--baseline-graph-export', 'baseline_graph_export'),
            ('--rebase-report', 'rebase_report'),
            ('--seed-video-evidence', 'external_seed_video_evidence'),
            ('--prior-attempt-ledger', 'prior_attempt_ledger'),
        ):
            value = cfg[key]
            args += [flag, value if isinstance(value, str) else value['path']]
        for flag, key in (
            ('--target-outgoing-roots', 'target_outgoing_roots'),
            ('--global-request-budget', 'global_request_budget'), ('--max-attempts', 'max_attempts'),
            ('--batch-size', 'batch_size'), ('--batch-request-budget', 'batch_request_budget'),
            ('--candidate-limit', 'candidate_limit'), ('--source-policy', 'source_policy'),
            ('--collector-collection-mode', 'collector_collection_mode'), ('--interval', 'interval'),
        ):
            args += [flag, str(cfg[key])]
        args += ['--stop-after-batches', '20', '--output', str(campaign)]
        invocation_path = campaign / 'v13-continuation-invocation-v05.json'
        write_json(invocation_path, {
            'argv': [str(runner.ROOT / '.venv/bin/python'), str(expander), *args],
            'normal_recovery_terminal_sha256': digest(recovery_terminal),
            'progress_before_sha256': digest(checkpoint),
            'preserved826_progress_sha256': digest(checkpoint),
            'independent_preservation_audit_sha256': digest(campaign / 'independent-preservation-audit-v03.json'),
            'preservation_auditor_sha256': digest(preservation_script),
            'maximum_completed_batches': 20, 'global_http_cap': 6500, 'no_limits_raised': True,
        })
        write_json(campaign / 'v13-continuation-child-v05.json', {
            'worker_pid': 103, 'invocation_sha256': digest(invocation_path),
        })
        terminal_path.write_text(json.dumps({
            'status': 'completed', 'returncode': 0, 'progress_sha256': digest(progress_path),
            'runtime_manifest_sha256': digest(runtime), 'invocation_sha256': digest(invocation_path),
            'accepted_graphs_sha256': digest(graph_path), 'elapsed_seconds': 123.0,
        }), encoding='utf-8')
        assert json.loads(terminal_path.read_text())['runtime_manifest_sha256'] == digest(runtime)
        return campaign, progress_path, terminal_path, runtime, graph_dir

    def test_running_or_under_target_refuses_without_artifacts_or_subprocesses(self):
        for roots, status in ((999, 'target_reached'), (1000, 'running')):
            with self.subTest(roots=roots, status=status), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                campaign, progress, terminal, runtime, _ = self.fixture_campaign(root, roots, status)
                package = root / 'package'
                receipt = root / 'receipt.json'
                called = []
                with patch.multiple(
                    runner,
                    RUNTIME_MANIFEST=runtime,
                    RUNTIME_MANIFEST_SHA256=digest(runtime),
                    AUDIT_OUTPUT=root / 'audits/final-source-audit-v21.json',
                    AUDIT_DIR=root / 'audits',
                    PACKAGE_DIR=package,
                    ORCHESTRATOR_RECEIPT=receipt,
                ):
                    before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
                    with self.assertRaises(runner.GateError):
                        runner.orchestrate(progress_path=progress, terminal_path=terminal,
                                           runner=lambda *args, **kwargs: called.append(args))
                    after = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
                self.assertEqual(before, after)
                self.assertEqual(called, [])
                self.assertFalse(package.exists())
                self.assertFalse(receipt.exists())

    def test_failed_whole_source_audit_never_invokes_updater_or_writes_package(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            campaign, progress, terminal, runtime, _ = self.fixture_campaign(root)
            original = root / 'original.musiclibrary'
            original.mkdir()
            inventory = {}
            for index in range(12):
                file = original / f'file-{index:02d}.bin'
                file.write_bytes(f'original-{index}'.encode())
                inventory[file.name] = digest(file)
            baseline_report = write_json(root / 'baseline-report.json', {
                'physical_sql_matches_baseline': True,
                'source_bundle_unchanged_on_disk': True,
                'physical_bundle': {'file_sha256': inventory},
            })
            baseline_state = write_json(root / 'baseline-state.json', {'rows': []})
            files = {}
            for name in ('auditor', 'preflight', 'updater', 'selector', 'index', 'topology', 'manifest',
                         'snapshot', 'map', 'executable'):
                path = root / f'{name}.bin'
                path.write_bytes(name.encode())
                files[name] = path
            package = root / 'package'
            audit_dir = root / 'audits'
            audit_output = audit_dir / 'final-source-audit-v21.json'
            receipt = root / 'orchestrator-receipt.json'
            called = []

            def fake_runner(argv, **kwargs):
                called.append(argv)
                return runner.subprocess.CompletedProcess(argv, 1, stdout='', stderr='fixture audit failure')

            values = {
                'RUNTIME_MANIFEST': runtime,
                'RUNTIME_MANIFEST_SHA256': digest(runtime),
                'AUDITOR': files['auditor'], 'PREFLIGHT': files['preflight'],
                'UPDATER': files['updater'], 'SAMPLE_SELECTOR': files['selector'],
                'INDEX_AUDITOR': files['index'], 'TOPOLOGY_AUDITOR': files['topology'],
                'MANIFEST_WRITER': files['manifest'],
                'BASELINE_PROJECTOR_REPORT': baseline_report,
                'BASELINE_PROJECTOR_REPORT_SHA256': digest(baseline_report),
                'BASELINE_STATE': baseline_state,
                'BASELINE_STATE_SHA256': digest(baseline_state),
                'ORIGINAL_BUNDLE': original,
                'TRACK_SNAPSHOT': files['snapshot'], 'IDENTITY_MAP': files['map'],
                'MUSIC_EXECUTABLE': files['executable'],
                'PACKAGE_DIR': package, 'AUDIT_OUTPUT': audit_output, 'AUDIT_DIR': audit_dir,
                'ORCHESTRATOR_RECEIPT': receipt,
                'PREFLIGHT_OUTPUT': package / 'package-preflight-v20.json',
            }
            values.update(lineage_paths(campaign, runtime))
            before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
            with patch.multiple(runner, **values):
                with self.assertRaisesRegex(runner.GateError, 'whole-source audit failed'):
                    runner.orchestrate(progress_path=progress, terminal_path=terminal,
                                       runner=fake_runner)
            after = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
            self.assertEqual(before, after)
            self.assertEqual(len(called), 1)
            self.assertIn(str(files['auditor']), called[0])
            self.assertNotIn(str(files['updater']), called[0])
            self.assertFalse(package.exists())
            self.assertFalse(audit_output.exists())
            self.assertFalse(receipt.exists())
            self.assertFalse(audit_dir.exists())

    def test_default_source_audit_output_is_outside_frozen_campaign(self):
        self.assertFalse(runner.AUDIT_OUTPUT.resolve().is_relative_to(runner.CAMPAIGN.resolve()))
        self.assertEqual(runner.AUDIT_OUTPUT.parent, runner.AUDIT_DIR)

    def test_audit_output_inside_campaign_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            campaign, progress, terminal, runtime, _ = self.fixture_campaign(root)
            audit_dir = campaign / 'audit-output'
            audit_output = audit_dir / 'final-source-audit-v21.json'
            package = root / 'package'
            receipt = root / 'orchestrator.json'
            called = []
            values = {
                'RUNTIME_MANIFEST': runtime,
                'RUNTIME_MANIFEST_SHA256': digest(runtime),
                'AUDIT_OUTPUT': audit_output,
                'AUDIT_DIR': audit_dir,
                'PACKAGE_DIR': package,
                'ORCHESTRATOR_RECEIPT': receipt,
            }
            values.update(lineage_paths(campaign, runtime))
            before = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
            with patch.multiple(runner, **values):
                with self.assertRaisesRegex(runner.GateError, 'outside the frozen live campaign'):
                    runner.orchestrate(progress_path=progress, terminal_path=terminal,
                                       runner=lambda *args, **kwargs: called.append(args))
            after = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
            self.assertEqual(before, after)
            self.assertEqual(called, [])

    def test_successful_audit_flow_reaches_copy_only_after_receipt_checks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            campaign, progress, terminal, runtime, growth = self.fixture_campaign(root)
            original = root / 'original.musiclibrary'
            original.mkdir()
            inventory = {}
            for index in range(12):
                file = original / f'file-{index:02d}.bin'
                file.write_bytes(f'original-{index}'.encode())
                inventory[file.name] = digest(file)
            baseline_report = write_json(root / 'baseline-report.json', {
                'physical_sql_matches_baseline': True,
                'source_bundle_unchanged_on_disk': True,
                'physical_bundle': {'file_sha256': inventory},
            })
            baseline_state = write_json(root / 'baseline-state.json', {'rows': []})
            inputs = {}
            for name in ('auditor', 'preflight', 'updater', 'selector', 'index', 'topology',
                         'manifest', 'snapshot', 'map', 'executable'):
                path = root / f'{name}.bin'
                path.write_bytes(name.encode())
                inputs[name] = path
            audit_dir = root / 'source-audit'
            audit_output = audit_dir / 'final-source-audit-v21.json'
            audit_receipt = audit_output.with_name(audit_output.stem + '.receipt.json')
            package = root / 'package'
            orchestrator_receipt = root / 'orchestrator.json'
            samples_path = package / 'musiccore-sample-roots.json'
            index_path = package / 'audit-index-expansion.json'
            topology_path = package / 'graph-topology-audit.json'
            manifest_path = package / 'final-hash-manifest.json'
            launch_order = []

            def arg_value(argv, option):
                return argv[argv.index(option) + 1]

            def fake_runner(argv, **kwargs):
                stage = Path(argv[1]).name
                launch_order.append(stage)
                if stage == 'auditor.bin':
                    doc = {
                        'status': 'passed',
                        'independent_full_chain_replay': {'status': 'passed', 'checkpoint': str(growth)},
                        'source_root_origin': {'goal_metric': 'total-genuine',
                                               'counts': {'total_active': 1000, 'unknown': 0}},
                        'final_files_sha256': {'progress': digest(progress), 'state': digest(growth / 'state.json')},
                        'source_closure_sha256': {},
                    }
                    write_json(audit_output, doc)
                    write_json(audit_receipt, {
                        'source_audit_sha256': digest(audit_output),
                        'source_audit_path': str(audit_output.resolve()),
                        'checkpoint_path': str(growth.resolve()),
                        'accepted_graphs_sha256': digest(growth / 'accepted-graphs.json'),
                        'state_file_sha256': digest(growth / 'state.json'),
                        'runtime_manifest_sha256': digest(runtime),
                    })
                    stdout = {'status': 'passed', 'outgoing_root_count': 1000}
                elif stage == 'preflight.bin' and '--baseline-only' in argv:
                    stdout = {'library_track_count': 3004, 'baseline_assignments': 515,
                              'baseline_ordered_edges': 3341}
                elif stage == 'preflight.bin':
                    stdout = {'status': 'ready_for_separate_copy_packaging',
                              'qualifying_roots': 1000, 'unknown_root_origins': 0}
                elif stage == 'updater.bin':
                    package.mkdir()
                    (package / 'report.json').write_text('{}')
                    (package / 'plan.json').write_text('{}')
                    (package / 'state.json').write_text('{}')
                    (package / 'Music Library.musiclibrary').mkdir()
                    stdout = {'status': 'updated'}
                elif stage == 'selector.bin':
                    pids = [f'{i:016X}' for i in range(5)]
                    write_json(Path(arg_value(argv, '--output')), {'sample_pids': pids, 'sample_count': 5})
                    stdout = {'sample_pids': pids}
                elif stage == 'index.bin':
                    write_json(Path(arg_value(argv, '--output')), {
                        'sqlite_integrity': {'baseline': 'ok', 'output': 'ok'},
                        'baseline_library_track_count': 3004, 'output_library_track_count': 3004,
                        'existing_pid_count': 515, 'existing_pid_gid_and_metadata_preserved': True,
                        'sync_revision_mismatch_count': 0, 'music_core_sample': [{'pid': str(i)} for i in range(5)],
                    })
                    stdout = {'status': 'passed'}
                elif stage == 'topology.bin':
                    write_json(Path(arg_value(argv, '--output')), {
                        'status': 'passed', 'accepted_pid_vectors_match_output_state_and_sql': True,
                        'state_only_assignments_are_empty': True, 'active_graph_roots': 1000,
                    })
                    stdout = {'status': 'passed'}
                elif stage == 'manifest.bin':
                    write_json(Path(arg_value(argv, '--output')), {'status': 'sealed'})
                    stdout = {'status': 'sealed'}
                else:
                    self.fail('unexpected child command ' + stage)
                return runner.subprocess.CompletedProcess(argv, 0, stdout=json.dumps(stdout), stderr='')

            patched = {
                'AUDITOR': inputs['auditor'], 'PREFLIGHT': inputs['preflight'],
                'UPDATER': inputs['updater'], 'SAMPLE_SELECTOR': inputs['selector'],
                'INDEX_AUDITOR': inputs['index'], 'TOPOLOGY_AUDITOR': inputs['topology'],
                'MANIFEST_WRITER': inputs['manifest'],
                'BASELINE_PROJECTOR_REPORT': baseline_report,
                'BASELINE_PROJECTOR_REPORT_SHA256': digest(baseline_report),
                'BASELINE_STATE': baseline_state, 'BASELINE_STATE_SHA256': digest(baseline_state),
                'ORIGINAL_BUNDLE': original, 'TRACK_SNAPSHOT': inputs['snapshot'],
                'IDENTITY_MAP': inputs['map'], 'MUSIC_EXECUTABLE': inputs['executable'],
                'AUDIT_DIR': audit_dir, 'AUDIT_OUTPUT': audit_output, 'PACKAGE_DIR': package,
                'PREFLIGHT_OUTPUT': package / 'package-preflight-v20.json',
                'SAMPLES_OUTPUT': samples_path, 'INDEX_OUTPUT': index_path,
                'TOPOLOGY_OUTPUT': topology_path, 'PACKAGE_MANIFEST_OUTPUT': manifest_path,
                'ORCHESTRATOR_RECEIPT': orchestrator_receipt,
                'RUNTIME_MANIFEST_SHA256': digest(runtime),
            }
            patched.update(lineage_paths(campaign, runtime))
            with patch.multiple(runner, **patched):
                summary = runner.orchestrate(progress_path=progress, terminal_path=terminal,
                                             runner=fake_runner)
            self.assertEqual(summary['status'], 'completed')
            self.assertLess(launch_order.index('auditor.bin'), launch_order.index('updater.bin'))
            self.assertLess(launch_order.index('preflight.bin'), launch_order.index('updater.bin'))
            self.assertTrue(orchestrator_receipt.is_file())

    def test_cli_reports_partial_artifacts_after_late_gate_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audit_dir = root / 'audits'
            package = root / 'package'
            receipt = root / 'orchestrator.json'

            def failing_orchestrate():
                write_json(audit_dir / 'source-audit.json', {'status': 'passed'})
                package.mkdir()
                raise runner.GateError('later SQL check failed')

            values = {
                'AUDIT_DIR': audit_dir,
                'AUDIT_OUTPUT': audit_dir / 'source-audit.json',
                'PACKAGE_DIR': package,
                'ORCHESTRATOR_RECEIPT': receipt,
            }
            output = StringIO()
            with patch.multiple(runner, **values), patch.object(runner, 'orchestrate', failing_orchestrate):
                with redirect_stdout(output):
                    code = runner.main()
            self.assertEqual(code, 2)
            document = json.loads(output.getvalue())
            self.assertGreater(document['artifact_write_count'], 0)
            self.assertIn(str((audit_dir / 'source-audit.json').resolve()), document['artifact_writes'])
            self.assertIn(str(package.resolve()), document['artifact_writes'])

    def test_child_invocation_is_structured_argv_with_shell_disabled(self):
        captured = {}

        def run(argv, **kwargs):
            captured['argv'] = argv
            captured['kwargs'] = kwargs
            return runner.subprocess.CompletedProcess(argv, 0, stdout='{}', stderr='')

        runner._run_json_command(['/python', '/script.py', '--value', 'a b'],
                                 cwd=Path('/tmp'), runner=run, stage='test')
        self.assertEqual(captured['argv'], ['/python', '/script.py', '--value', 'a b'])
        self.assertNotIn('shell', captured['kwargs'])
        self.assertIsInstance(captured['argv'], list)


if __name__ == '__main__':
    unittest.main()
