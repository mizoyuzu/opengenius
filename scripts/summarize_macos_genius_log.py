"""Count known Music Genius diagnostics without retaining raw unified logs."""
import json
import os
import selectors
import subprocess
import time


MAX_LOG_BYTES = 4 * 1024 * 1024
LOG_TIMEOUT_SECONDS = 20
PREDICATE = ('process == "Music" AND '
             '(eventMessage CONTAINS[c] "genius" OR eventMessage CONTAINS[c] "corrupt")')
KNOWN_MESSAGES = {
    'genius_database_deleted_as_corrupt': 'genius corruption detected; deleting db',
    'genius_cluster_creation_error': 'GeniusCreateClusterContext err',
    'genius_track_list_creation_error': 'GeniusCreateTracksListFromClusterContext err',
    'genius_too_few_tracks': 'genius produced',
    'genius_key_header_assertion': 'libraryPrefs->geniusKeyHeader.IsValid()',
    'genius_cuid_assertion': 'libraryPrefs->geniusCUID.NotEmpty()',
    'genius_opted_out_cuid_assertion': 'libraryPrefs->geniusOptedOutCUID.',
    'genius_opt_in_assertion': 'IsGeniusOptedIn()',
}


def _empty(status, **metadata):
    return dict(status=status, complete=False, counts={name: 0 for name in KNOWN_MESSAGES},
                matched_events=0, unclassified_events=0, **metadata)


def summarize_log_json(raw):
    """Return only fixed category names and counts, never log message values."""
    if len(raw) > MAX_LOG_BYTES:
        return _empty('truncated', truncated=True)
    try:
        events = json.loads(raw)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        return _empty('invalid_json', truncated=False)
    if not isinstance(events, list):
        return _empty('invalid_json', truncated=False)
    result = _empty('ok', truncated=False)
    result['complete'] = True
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get('eventMessage'), str):
            continue
        # The command predicate selects Music. Recheck identity when supplied
        # so a mixed test/document cannot attribute another app's diagnostics.
        process = event.get('processImagePath')
        if isinstance(process, str) and process.rsplit('/', 1)[-1] != 'Music':
            continue
        message = event['eventMessage'].casefold()
        if 'genius' not in message and 'corrupt' not in message:
            continue
        result['matched_events'] += 1
        categorized = False
        for name, phrase in KNOWN_MESSAGES.items():
            if phrase.casefold() in message:
                result['counts'][name] += 1
                categorized = True
        if not categorized:
            result['unclassified_events'] += 1
    return result


def _capture_summary(command, max_bytes=MAX_LOG_BYTES, timeout=LOG_TIMEOUT_SECONDS):
    """Bound pipe reads in RAM; stderr is discarded and exceptions are redacted."""
    output = bytearray()
    status = 'ok'
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return _empty('unavailable', truncated=False, returncode=None)
    try:
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = 'timeout'
                    break
                ready = selector.select(min(remaining, 0.25))
                if not ready:
                    continue
                chunk = os.read(process.stdout.fileno(), min(65536, max_bytes + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > max_bytes:
                    status = 'truncated'
                    break
        if status != 'ok':
            process.kill()
        try:
            returncode = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            status = 'timeout'
            returncode = process.returncode
        if status == 'ok' and returncode != 0:
            status = 'nonzero_exit'
        if status != 'ok':
            return _empty(status, truncated=status == 'truncated', returncode=returncode)
        result = summarize_log_json(output)
        result['returncode'] = returncode
        return result
    except (OSError, ValueError):
        return _empty('capture_error', truncated=False, returncode=process.poll())
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def summarize_macos_genius_log():
    """Inspect the preceding ten minutes; call after Music's exit observation."""
    result = _capture_summary([
        '/usr/bin/log', 'show', '--last', '10m', '--style', 'json',
        '--info', '--debug', '--predicate', PREDICATE,
    ])
    result['window_minutes'] = 10
    return result


if __name__ == '__main__':
    print(json.dumps(summarize_macos_genius_log(), sort_keys=True))
