"""Offline, Library-bound personal judgments for evaluated playlists.

Feedback is attached to a (seed, target) pair and an evaluation context. It
does not establish recording identity or prove that a target is a good match.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter


_HASH = re.compile(r'^[0-9a-f]{64}$')
_REASONS = {'taste', 'wrong_recording', 'wrong_version', 'too_repetitive', 'out_of_scope'}
_TOP_FIELDS = {'schema_version', 'library_sha256', 'judgments'}
_JUDGMENT_FIELDS = {'root_pid', 'target_pid', 'verdict', 'reason', 'context_sha256', 'note'}
_PAYLOAD_FIELDS = {'root_pid', 'target_pid', 'verdict', 'reason', 'context_sha256', 'note'}


def _valid_hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _track_map(tracks):
    if not isinstance(tracks, (list, tuple)):
        raise ValueError('Tracks must be a list or tuple')
    result = {}
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError('Invalid Library track')
        pid = track.get('persistent_id')
        if not isinstance(pid, str) or not pid or pid in result:
            raise ValueError('Missing or duplicate Library persistent ID')
        result[pid] = track
    return result


def _check_library_hash(library_hash):
    if not _valid_hash(library_hash):
        raise ValueError('Invalid Library SHA256')


def empty_feedback(library_hash):
    """Return an empty schema 1 document bound to one Library snapshot."""
    _check_library_hash(library_hash)
    return {'schema_version': 1, 'library_sha256': library_hash, 'judgments': []}


def validate_feedback(doc, tracks, library_hash):
    """Validate the complete document and return a detached normalized copy."""
    _check_library_hash(library_hash)
    by_pid = _track_map(tracks)
    if not isinstance(doc, dict) or set(doc) != _TOP_FIELDS:
        raise ValueError('Invalid feedback document fields')
    if type(doc.get('schema_version')) is not int or doc['schema_version'] != 1:
        raise ValueError('Unsupported feedback schema')
    if doc.get('library_sha256') != library_hash:
        raise ValueError('Feedback belongs to a different Library')
    if not isinstance(doc.get('judgments'), list):
        raise ValueError('Feedback judgments must be a list')
    seen = set()
    judgments = []
    for judgment in doc['judgments']:
        if not isinstance(judgment, dict) or set(judgment) - _JUDGMENT_FIELDS:
            raise ValueError('Invalid judgment fields')
        required = _JUDGMENT_FIELDS - {'note'}
        if not required.issubset(judgment):
            raise ValueError('Missing judgment fields')
        root, target = judgment['root_pid'], judgment['target_pid']
        if not isinstance(root, str) or root not in by_pid or not isinstance(target, str) or target not in by_pid:
            raise ValueError('Judgment references an unknown Library PID')
        if root == target:
            raise ValueError('A seed cannot be judged against itself')
        pair = (root, target)
        if pair in seen:
            raise ValueError('Duplicate judgment pair')
        seen.add(pair)
        if not isinstance(judgment['verdict'], str) or judgment['verdict'] not in ('fits', 'avoid'):
            raise ValueError('Invalid judgment verdict')
        if not isinstance(judgment['reason'], str) or judgment['reason'] not in _REASONS:
            raise ValueError('Invalid judgment reason')
        if not _valid_hash(judgment['context_sha256']):
            raise ValueError('Invalid judgment context SHA256')
        if 'note' in judgment and not isinstance(judgment['note'], str):
            raise ValueError('Judgment note must be a string')
        judgments.append(dict(judgment))
    return {'schema_version': 1, 'library_sha256': library_hash, 'judgments': judgments}


def record_feedback(doc, payload, tracks, library_hash):
    """Validate then immutably upsert one pair, or clear it with verdict=None."""
    current = validate_feedback(doc, tracks, library_hash)
    by_pid = _track_map(tracks)
    if not isinstance(payload, dict) or set(payload) - _PAYLOAD_FIELDS:
        raise ValueError('Invalid feedback payload fields')
    if 'verdict' not in payload:
        raise ValueError('Missing feedback payload verdict')
    root, target = payload.get('root_pid'), payload.get('target_pid')
    if not isinstance(root, str) or root not in by_pid or not isinstance(target, str) or target not in by_pid:
        raise ValueError('Feedback references an unknown Library PID')
    if root == target:
        raise ValueError('A seed cannot be judged against itself')
    verdict = payload.get('verdict')
    if verdict is None:
        if set(payload) - {'root_pid', 'target_pid', 'verdict'}:
            raise ValueError('Clear payload must only identify the pair')
    else:
        required = {'root_pid', 'target_pid', 'verdict', 'reason', 'context_sha256'}
        if not required.issubset(payload):
            raise ValueError('Missing feedback payload fields')
        if not isinstance(verdict, str) or verdict not in ('fits', 'avoid'):
            raise ValueError('Invalid judgment verdict')
        if not isinstance(payload['reason'], str) or payload['reason'] not in _REASONS:
            raise ValueError('Invalid judgment reason')
        if not _valid_hash(payload['context_sha256']):
            raise ValueError('Invalid judgment context SHA256')
        if 'note' in payload and not isinstance(payload['note'], str):
            raise ValueError('Judgment note must be a string')
    judgments = [j for j in current['judgments']
                 if (j['root_pid'], j['target_pid']) != (root, target)]
    if verdict is not None:
        # Revalidate the candidate before constructing the returned document.
        item = {key: value for key, value in payload.items() if key != 'verdict'}
        item['verdict'] = verdict
        judgments.append(item)
    result = {'schema_version': 1, 'library_sha256': library_hash, 'judgments': judgments}
    return validate_feedback(result, tracks, library_hash)


def feedback_context(evaluation):
    """Hash semantic evaluation inputs, excluding roots and generated results."""
    if not isinstance(evaluation, dict):
        raise ValueError('Evaluation must be an object')
    # Keep provenance that changes the meaning of an evaluation. Paths, timestamps,
    # root subsets, generated playlists, and dynamic counters are intentionally out.
    inputs = evaluation.get('input_sha256', {})
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict):
        raise ValueError('Invalid evaluation input hashes')
    input_hashes = {}
    for key, value in inputs.items():
        if not isinstance(key, str) or not _valid_hash(value):
            raise ValueError('Invalid evaluation input hash')
        input_hashes[key] = value
    observations = evaluation.get('observation_inputs', [])
    observation_hashes = _ordered_observation_hashes(observations)
    provenance = evaluation.get('input_provenance')
    if provenance is not None:
        if not isinstance(provenance, dict):
            raise ValueError('Invalid input provenance')
        provenance_hashes = {}
        for key in ('identity_map_sha256', 'executable_sha256', 'genius_reference_sha256'):
            value = provenance.get(key)
            if value is not None:
                if not _valid_hash(value):
                    raise ValueError('Invalid input provenance hash')
                provenance_hashes[key] = value
        provenance_observations = _ordered_observation_hashes(provenance.get('observations', []))
    else:
        provenance_hashes, provenance_observations = {}, []
    semantic = {
        'library_sha256': evaluation.get('library_sha256'),
        'profile': evaluation.get('profile') or _common_root_value(evaluation, 'profile'),
        'artist_preference': evaluation.get('artist_preference') or _common_root_value(evaluation, 'artist_preference'),
        'cluster_mode': evaluation.get('cluster_mode'),
        'scope': _semantic_scope(evaluation.get('cluster_scope'), evaluation.get('cluster_mode')),
        'cluster_config_semantic_sha256': evaluation.get('cluster_config_semantic_sha256'),
        'controlled_config_sha256': evaluation.get('controlled_config_sha256'),
        'input_sha256': input_hashes,
        'observation_sha256': observation_hashes,
        'input_provenance_sha256': provenance_hashes,
        'input_provenance_observation_sha256': provenance_observations,
    }
    encoded = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _ordered_observation_hashes(observations):
    if observations is None:
        observations = []
    if not isinstance(observations, list):
        raise ValueError('Invalid observation provenance')
    hashes = []
    for entry in observations:
        if not isinstance(entry, dict) or not _valid_hash(entry.get('sha256')):
            raise ValueError('Invalid observation provenance hash')
        hashes.append(entry['sha256'])
    return hashes


def _semantic_scope(scope, cluster_mode=None):
    if scope is None:
        return {'mode': cluster_mode} if cluster_mode is not None else None
    if not isinstance(scope, dict):
        raise ValueError('Invalid cluster scope')
    tags = scope.get('tags', [])
    excluded = scope.get('excluded_kinds', [])
    if not isinstance(tags, (list, tuple)) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError('Invalid cluster scope tags')
    if not isinstance(excluded, (list, tuple)) or any(not isinstance(kind, str) for kind in excluded):
        raise ValueError('Invalid excluded cluster kinds')
    mode = scope.get('mode', cluster_mode)
    kind = scope.get('kind')
    if kind is not None and not isinstance(kind, str):
        raise ValueError('Invalid cluster scope kind')
    result = {'tags': sorted(set(tags)), 'kind': kind,
              'excluded_kinds': sorted(set(excluded)), 'mode': mode}
    if 'fallback_tags' in scope or 'fallback_limit' in scope:
        fallback = scope.get('fallback_tags', [])
        cap = scope.get('fallback_limit')
        if (not isinstance(fallback, (list, tuple)) or any(not isinstance(tag, str) for tag in fallback)
                or cap is not None and (type(cap) is not int or not 0 <= cap <= 25)):
            raise ValueError('Invalid fallback scope')
        result.update(fallback_tags=sorted(set(fallback)), fallback_limit=cap)
    return result


def _common_root_value(evaluation, key):
    rows = evaluation.get('root_results', [])
    values = {row[key] for row in rows if isinstance(row, dict) and key in row}
    return next(iter(values)) if len(values) == 1 else None


def _evaluation_roots(evaluation, by_pid):
    if not isinstance(evaluation, dict) or not isinstance(evaluation.get('root_results'), list):
        raise ValueError('Evaluation must contain root_results')
    roots = {}
    for row in evaluation['root_results']:
        if not isinstance(row, dict):
            raise ValueError('Invalid evaluation root result')
        root = row.get('root_pid')
        playlist = row.get('playlist_pids')
        if not isinstance(root, str) or root not in by_pid or root in roots:
            raise ValueError('Invalid or duplicate evaluated root')
        if not isinstance(playlist, list) or any(not isinstance(pid, str) or pid not in by_pid for pid in playlist):
            raise ValueError('Invalid evaluated playlist PID')
        if len(set(playlist)) != len(playlist):
            raise ValueError('Duplicate evaluated playlist PID')
        roots[root] = [pid for pid in playlist if pid != root]
    return roots


def summarize_feedback(evaluation, feedback, tracks, library_hash):
    """Summarize explicit judgments for evaluated targets, with context kept visible."""
    by_pid = _track_map(tracks)
    if evaluation.get('library_sha256') != library_hash:
        raise ValueError('Evaluation belongs to a different Library')
    validated = validate_feedback(feedback, tracks, library_hash)
    roots = _evaluation_roots(evaluation, by_pid)
    context = feedback_context(evaluation)
    judgments = {(item['root_pid'], item['target_pid']): item for item in validated['judgments']}
    root_summaries = {}
    totals = Counter()
    mismatches = []
    for root, targets in roots.items():
        counts = Counter()
        reason_counts = Counter()
        historical_reason_counts = Counter()
        taste_avoid = 0
        for target in targets:
            item = judgments.get((root, target))
            if item is None:
                counts['unjudged'] += 1
            else:
                counts['all_pair_judged'] += 1
                counts['all_pair_' + item['verdict']] += 1
                historical_reason_counts[item['reason']] += 1
                if item['context_sha256'] != context:
                    counts['context_mismatch'] += 1
                    mismatches.append({'root_pid': root, 'target_pid': target,
                                       'judgment_context_sha256': item['context_sha256'],
                                       'evaluation_context_sha256': context,
                                       'verdict': item['verdict'], 'reason': item['reason']})
                else:
                    counts[item['verdict']] += 1
                    reason_counts[item['reason']] += 1
                    if item['verdict'] == 'avoid' and item['reason'] == 'taste':
                        taste_avoid += 1
        for reason in _REASONS:
            counts['reason_' + reason] = reason_counts[reason]
            counts['all_pair_reason_' + reason] = historical_reason_counts[reason]
        # Only explicit taste-reason dislikes feed taste preference counts.
        counts['taste_avoid'] = taste_avoid
        counts['issue_wrong_recording'] = reason_counts['wrong_recording']
        counts['issue_wrong_version'] = reason_counts['wrong_version']
        counts['judged'] = counts['fits'] + counts['avoid']
        counts['evaluated_targets'] = len(targets)
        root_summaries[root] = dict(counts)
        totals.update(counts)
    return {'library_sha256': library_hash, 'evaluation_context_sha256': context,
            'identity_status': 'unverified', 'interpretation': 'contextual; judgments apply only to this seed-target pair',
            'counts': {key: totals[key] for key in (
                'fits', 'avoid', 'judged', 'unjudged', 'context_mismatch', 'all_pair_judged',
                'all_pair_fits', 'all_pair_avoid', 'evaluated_targets', 'taste_avoid',
                'issue_wrong_recording', 'issue_wrong_version',
                *(('reason_' + reason) for reason in sorted(_REASONS)),
                *(('all_pair_reason_' + reason) for reason in sorted(_REASONS)))},
            'roots': root_summaries, 'context_mismatches': mismatches}


def compare_feedback(baseline, variant, feedback, tracks, library_hash):
    """Compare explicitly judged targets on common evaluated roots only."""
    if baseline.get('library_sha256') != library_hash or variant.get('library_sha256') != library_hash:
        raise ValueError('Evaluations belong to a different Library')
    baseline_roots = _evaluation_roots(baseline, _track_map(tracks))
    variant_roots = _evaluation_roots(variant, _track_map(tracks))
    common = sorted(set(baseline_roots) & set(variant_roots))
    left = dict(baseline, root_results=[row for row in baseline['root_results'] if row['root_pid'] in common])
    right = dict(variant, root_results=[row for row in variant['root_results'] if row['root_pid'] in common])
    left_summary = summarize_feedback(left, feedback, tracks, library_hash)
    right_summary = summarize_feedback(right, feedback, tracks, library_hash)
    return {'library_sha256': library_hash, 'common_roots': common,
            'baseline': left_summary, 'variant': right_summary,
            'winner': None, 'interpretation': 'descriptive comparison only; no global quality winner'}
