import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from genuine_root_accounting import (
    derive_checkpoint_request_accounting, derive_request_accounting,
)


def _receipt(batch, requests):
    return {
        'path': f'batch-{batch:04d}/summary.json',
        'sha256': f'{batch:064x}',
        'http_requests': requests,
        'request_budget': 90,
    }


def _fixtures():
    counts = [90, 90, 0, 90, 90, 90, 90, 90]
    progress = {
        'http_requests': 813,
        'attempted_pids': [f'{i:016X}' for i in range(235)],
        'batches': [
            {
                'batch': number,
                'http_requests': requests,
                'request_budget': 90,
                'status': 'preflight_failed' if requests == 0 else 'stopped',
                'collector_summary': _receipt(number, requests),
            }
            for number, requests in enumerate(counts, start=1)
        ],
    }
    orphan = {
        'batch': 9,
        'http_requests': 90,
        'request_budget': 90,
        'status': 'stopped',
        'authenticated': True,
        'attempted_pids': [f'{i + 1000:016X}' for i in range(18)],
        'collector_summary': _receipt(9, 90),
    }
    return progress, {'http_requests': 183}, orphan


class GenuineRootAccountingTests(unittest.TestCase):
    def test_cumulative_requests_include_prior_ledger_once(self):
        progress, prior, orphan = _fixtures()
        result = derive_request_accounting(progress, prior, orphan)
        self.assertEqual([row['batch'] for row in result['batch_counts']], list(range(1, 10)))
        self.assertEqual([row['http_requests'] for row in result['batch_counts']],
                         [90, 90, 0, 90, 90, 90, 90, 90, 90])
        self.assertEqual(result['progress_batch_requests'], 630)
        self.assertEqual(result['requests_before_orphan'], 813)
        self.assertEqual(result['new_stage_requests'], 720)
        self.assertEqual(result['requests_after_orphan'], 903)
        self.assertEqual(result['unique_attempted_pid_count_after_orphan'], 253)

    def test_persisted_nine_batch_checkpoint_recomputes_total(self):
        progress, prior, orphan = _fixtures()
        progress['batches'].append({
            'batch': 9,
            'http_requests': 90,
            'request_budget': 90,
            'status': 'stopped',
            'collector_summary': _receipt(9, 90),
        })
        progress['http_requests'] = 903
        progress['attempted_pids'].extend(orphan['attempted_pids'])
        result = derive_checkpoint_request_accounting(progress, prior)
        self.assertEqual(len(result['batch_counts']), 9)
        self.assertEqual(result['progress_batch_requests'], 720)
        self.assertEqual(result['requests_after_checkpoint'], 903)
        self.assertEqual(result['unique_attempted_pid_count'], 253)

    def test_rejects_missing_batch_or_request_receipt_mismatch(self):
        progress, prior, orphan = _fixtures()
        progress['batches'][2]['collector_summary']['http_requests'] = 90
        with self.assertRaisesRegex(ValueError, 'differs from its collector receipt'):
            derive_request_accounting(progress, prior, orphan)

    def test_rejects_orphan_pid_overlap_or_wrong_sequence(self):
        progress, prior, orphan = _fixtures()
        orphan['attempted_pids'][0] = progress['attempted_pids'][0]
        with self.assertRaisesRegex(ValueError, 'repeats an already attempted PID'):
            derive_request_accounting(progress, prior, orphan)
        progress, prior, orphan = _fixtures()
        orphan['batch'] = 10
        with self.assertRaisesRegex(ValueError, 'next authenticated batch'):
            derive_request_accounting(progress, prior, orphan)


if __name__ == '__main__':
    unittest.main()
