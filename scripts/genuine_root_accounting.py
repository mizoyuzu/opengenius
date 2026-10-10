"""Derive request and attempt totals from verified progress and orphan receipts."""


def _progress_request_rows(progress, prior_ledger):
    if not isinstance(progress, dict) or not isinstance(prior_ledger, dict):
        raise ValueError('Progress and prior ledger must be objects')
    prior_requests = prior_ledger.get('http_requests')
    checkpoint_requests = progress.get('http_requests')
    if (type(prior_requests) is not int or prior_requests < 0
            or type(checkpoint_requests) is not int or checkpoint_requests < prior_requests):
        raise ValueError('Malformed inherited request totals')

    batches = progress.get('batches')
    if not isinstance(batches, list) or not batches:
        raise ValueError('Progress has no recorded batches')
    rows = []
    for expected_number, batch in enumerate(batches, start=1):
        if not isinstance(batch, dict) or batch.get('batch') != expected_number:
            raise ValueError('Progress batch sequence is not contiguous')
        requests = batch.get('http_requests')
        cap = batch.get('request_budget')
        if (type(requests) is not int or requests < 0 or type(cap) is not int
                or cap < 0 or requests > cap):
            raise ValueError('Malformed progress batch request counts')
        summary = batch.get('collector_summary')
        if not isinstance(summary, dict):
            raise ValueError('Progress batch lacks its collector summary receipt')
        if (summary.get('http_requests') != requests
                or summary.get('request_budget') != cap
                or not isinstance(summary.get('path'), str)
                or not isinstance(summary.get('sha256'), str)):
            raise ValueError('Progress request count differs from its collector receipt')
        rows.append({
            'batch': expected_number,
            'http_requests': requests,
            'request_budget': cap,
            'status': batch.get('status'),
            'collector_summary': summary,
        })

    progress_batch_requests = sum(row['http_requests'] for row in rows)
    if prior_requests + progress_batch_requests != checkpoint_requests:
        raise ValueError('Checkpoint total does not equal prior ledger plus recorded batches')
    attempted_pids = progress.get('attempted_pids')
    if (not isinstance(attempted_pids, list)
            or len(set(attempted_pids)) != len(attempted_pids)):
        raise ValueError('Progress attempted PID list is malformed or duplicated')
    return prior_requests, checkpoint_requests, rows, progress_batch_requests, set(attempted_pids)


def derive_checkpoint_request_accounting(progress, prior_ledger):
    """Account a durable checkpoint whose request total includes its parent ledger."""
    prior_requests, checkpoint_requests, rows, batch_requests, attempted = (
        _progress_request_rows(progress, prior_ledger))
    return {
        'batch_counts': rows,
        'prior_ledger_requests': prior_requests,
        'progress_batch_requests': batch_requests,
        'requests_after_checkpoint': checkpoint_requests,
        'unique_attempted_pid_count': len(attempted),
    }


def derive_request_accounting(progress, prior_ledger, recovered_orphan):
    """Return one cumulative ledger without counting inherited requests twice.

    ``progress`` must already have passed campaign receipt and prior-ledger
    validation. ``recovered_orphan`` must be the result of
    ``recover_orphan_batch`` and contain an authenticated terminal batch.
    """
    if not isinstance(recovered_orphan, dict):
        raise ValueError('Recovered orphan must be an object')
    prior_requests, checkpoint_requests, rows, progress_batch_requests, prior_attempts = (
        _progress_request_rows(progress, prior_ledger))

    orphan_batch = recovered_orphan.get('batch')
    orphan_requests = recovered_orphan.get('http_requests')
    orphan_cap = recovered_orphan.get('request_budget')
    orphan_attempts = recovered_orphan.get('attempted_pids')
    if (orphan_batch != len(rows) + 1 or type(orphan_requests) is not int
            or orphan_requests < 0 or type(orphan_cap) is not int
            or orphan_requests > orphan_cap
            or recovered_orphan.get('status') != 'stopped'
            or recovered_orphan.get('authenticated') is not True
            or not isinstance(recovered_orphan.get('collector_summary'), dict)
            or not isinstance(orphan_attempts, list)
            or len(orphan_attempts) != len(set(orphan_attempts))):
        raise ValueError('Recovered orphan is not a valid next authenticated batch')
    orphan_summary = recovered_orphan['collector_summary']
    if (orphan_summary.get('http_requests') != orphan_requests
            or orphan_summary.get('request_budget') != orphan_cap
            or not isinstance(orphan_summary.get('path'), str)
            or not isinstance(orphan_summary.get('sha256'), str)):
        raise ValueError('Orphan request count differs from its collector receipt')

    if prior_attempts.intersection(orphan_attempts):
        raise ValueError('Recovered orphan repeats an already attempted PID')
    total_requests = checkpoint_requests + orphan_requests
    return {
        'batch_counts': [*rows, {
            'batch': orphan_batch,
            'http_requests': orphan_requests,
            'request_budget': orphan_cap,
            'status': recovered_orphan['status'],
            'collector_summary': orphan_summary,
        }],
        'prior_ledger_requests': prior_requests,
        'requests_before_orphan': checkpoint_requests,
        'orphan_requests': orphan_requests,
        'progress_batch_requests': progress_batch_requests,
        'new_stage_requests': progress_batch_requests + orphan_requests,
        'requests_after_orphan': total_requests,
        'unique_attempted_pid_count_after_orphan': len(prior_attempts) + len(orphan_attempts),
        'attempted_pids_before_orphan': len(prior_attempts),
        'orphan_attempted_pids': len(orphan_attempts),
    }
