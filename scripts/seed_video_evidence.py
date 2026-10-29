"""Build and validate strict video-ID evidence from saved radio observations.

The sidecar supports seed selection only. It records no recommendation edges
and never upgrades a metadata candidate to a verified recording identity.
"""
import argparse
import builtins
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import hashlib
import io
import inspect
import json
import os
from pathlib import Path
import re
import threading

from evaluate_ytmusic_batch import validate_observations
from music_identity_map import IdentityMap, load_library, normalize, version_signature


PRODUCER = 'opengenius-seed-video-evidence-v1'
CACHED_HINT_PRODUCER = 'opengenius-cached-seed-hint-revalidation-v1'
REBOUND_PRODUCER = 'opengenius-map-transition-rebound-seed-evidence-v1'
COMBINED_REBOUND_PRODUCER = 'opengenius-combined-rebound-seed-evidence-v1'
VIDEO_RE = re.compile(r'[A-Za-z0-9_-]{11}\Z')
SHA_RE = re.compile(r'[a-f0-9]{64}\Z')
ROW_KEYS = {'persistent_id', 'video_id', 'input_path', 'input_sha256', 'source_relation'}
CATALOG_PRODUCER = 'opengenius-album-catalog-seed-evidence-v1'
CATALOG_ROW_KEYS = ROW_KEYS | {
    'album_id', 'source_key', 'source_album_id_inputs',
}
ALBUM_ID_RE = re.compile(r'MPRE[A-Za-z0-9_-]+\Z')
CATALOG_SAFE_KEYS = {'videoId', 'title', 'artists', 'name', 'id', 'album', 'length',
                     'duration_seconds', 'videoType', 'related', 'tracks', 'contents'}
_TYPED_TRANSITION_PROOF_CACHE = {}

# Private-prototype selector cache. It is deliberately separate from the
# typed-transition cache: this memo stores a full selector result only inside
# an explicit synchronous invocation and revalidates its observed filesystem
# dependency closure on every hit.
_ACTIVE_SELECTOR_INDEX = ContextVar('seed_video_selector_index', default=None)
_ACTIVE_SELECTOR_TRACE = ContextVar('seed_video_selector_trace', default=None)
_SELECTOR_IO_LOCK = threading.RLock()
# One campaign can replay up to 140 twenty-seed batches plus the historical
# ancestry. This remains bounded while avoiding deterministic LRU churn across
# those distinct immutable sidecars.
_SELECTOR_CACHE_LIMIT = 256
_PATH_METHODS_ORIGINAL = {}
_IO_METHODS_ORIGINAL = {}
_KNOWN_PATH_READ_DEPTH = ContextVar('known_selector_path_read_depth', default=0)
_KNOWN_PATH_LIST_DEPTH = ContextVar('known_selector_path_list_depth', default=0)
_KNOWN_PATH_STATE_DEPTH = ContextVar('known_selector_path_state_depth', default=0)
_KNOWN_PATH_RESOLVE_DEPTH = ContextVar('known_selector_path_resolve_depth', default=0)


def _typed_transition_verification_matches(recorded, verified):
    """Accept the pre-proof-input-receipt verifier shape after full replay.

    Earlier immutable hint artifacts recorded the complete map-transition
    verification except for ``proof_input_receipts``. The current verifier
    independently rebuilds and hashes those receipts before this comparison;
    only that one additive auxiliary field may be absent from a recorded copy.
    """
    if not isinstance(recorded, dict) or not isinstance(verified, dict):
        return False
    if recorded == verified:
        return True
    if set(verified) != set(recorded) | {'proof_input_receipts'}:
        return False
    return recorded == {key: value for key, value in verified.items()
                        if key != 'proof_input_receipts'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical_sha(value):
    return _sha(json.dumps(value, sort_keys=True, ensure_ascii=False,
                           separators=(',', ':')).encode('utf-8'))


def _selector_context_digest(document, evidence_path, tracks, matcher,
                             library_hash, identity_map_sha256):
    """Return an exact cache context, or None when it cannot be serialized."""
    if type(matcher) is not IdentityMap:
        return None
    try:
        matcher_context = {
            'type': type(matcher).__qualname__,
            'artist_names': matcher.artist_names,
            'credit_sets': sorted((list(key), sorted(value))
                                  for key, value in matcher.credit_sets.items()),
            'album_title_aliases': matcher.album_title_aliases,
            'artist_credit_aliases': sorted(
                (list(key), value)
                for key, value in matcher.artist_credit_aliases.items()),
            'titles': matcher.titles,
            'proposed_titles': matcher.proposed_titles,
            'matcher_tracks': matcher.tracks,
            'active_title_indices': {key: sorted(value)
                                     for key, value in matcher._active_title_indices.items()},
            'preview_title_indices': {key: sorted(value)
                                     for key, value in matcher._preview_title_indices.items()},
        }
        # Reading the top-level document receipt here makes the serialized
        # in-memory document and the source file independently part of context.
        document_path = Path(evidence_path).resolve()
        document_file_sha = _sha(document_path.read_bytes())
        return (
            str(document_path), document_file_sha,
            _canonical_sha(document), _canonical_sha(tracks),
            _canonical_sha(matcher_context), library_hash, identity_map_sha256,
        )
    except Exception:
        return None


def _install_selector_io_trace():
    """Temporarily trace filesystem dependencies of a full selector replay."""
    if _PATH_METHODS_ORIGINAL:
        return
    names = ('read_bytes', 'read_text', 'exists', 'is_file', 'is_dir', 'is_symlink',
             'glob', 'rglob', 'iterdir', 'resolve', 'open')
    for name in names:
        _PATH_METHODS_ORIGINAL[name] = getattr(Path, name)
    _IO_METHODS_ORIGINAL.update({
        'builtins.open': builtins.open,
        'io.open': __import__('io').open,
        'os.scandir': os.scandir,
        'os.listdir': os.listdir,
        'os.stat': os.stat,
        'os.lstat': os.lstat,
        'os.open': os.open,
        'os.read': os.read,
        'os.fstat': os.fstat,
        'os.path.exists': os.path.exists,
        'os.path.lexists': os.path.lexists,
        'os.path.isfile': os.path.isfile,
        'os.path.isdir': os.path.isdir,
        'os.path.realpath': os.path.realpath,
    })

    def record_file(path, digest):
        trace = _ACTIVE_SELECTOR_TRACE.get()
        if trace is None:
            return
        resolved_path = _PATH_METHODS_ORIGINAL['resolve'](path)
        resolved = str(resolved_path)
        record_observation(trace, 'resolutions', str(path), resolved)
        old = trace['files'].setdefault(resolved, digest)
        if old != digest:
            trace['unstable'] = True

    def record_observation(trace, collection, key, value):
        old = trace[collection].setdefault(key, value)
        if old != value:
            trace['unstable'] = True

    def resolved_before_after(path, operation):
        trace = _ACTIVE_SELECTOR_TRACE.get()
        before = _PATH_METHODS_ORIGINAL['resolve'](path)
        if trace is not None:
            record_observation(trace, 'resolutions', str(path), str(before))
        result = operation()
        after = _PATH_METHODS_ORIGINAL['resolve'](path)
        if trace is not None:
            record_observation(trace, 'resolutions', str(path), str(after))
            if before != after:
                trace['unstable'] = True
        return result

    def read_bytes(path):
        if _ACTIVE_SELECTOR_TRACE.get() is None:
            return _PATH_METHODS_ORIGINAL['read_bytes'](path)
        token = _KNOWN_PATH_READ_DEPTH.set(_KNOWN_PATH_READ_DEPTH.get() + 1)
        try:
            data = resolved_before_after(
                path, lambda: _PATH_METHODS_ORIGINAL['read_bytes'](path))
        finally:
            _KNOWN_PATH_READ_DEPTH.reset(token)
        record_file(path, _sha(data))
        return data

    def read_text(path, *args, **kwargs):
        if _ACTIVE_SELECTOR_TRACE.get() is None:
            return _PATH_METHODS_ORIGINAL['read_text'](path, *args, **kwargs)
        token = _KNOWN_PATH_READ_DEPTH.set(_KNOWN_PATH_READ_DEPTH.get() + 1)
        try:
            resolved_before = _PATH_METHODS_ORIGINAL['resolve'](path)
            trace = _ACTIVE_SELECTOR_TRACE.get()
            if trace is not None:
                record_observation(trace, 'resolutions', str(path), str(resolved_before))
            before = _PATH_METHODS_ORIGINAL['read_bytes'](path)
            result = _PATH_METHODS_ORIGINAL['read_text'](path, *args, **kwargs)
            after = _PATH_METHODS_ORIGINAL['read_bytes'](path)
            resolved_after = _PATH_METHODS_ORIGINAL['resolve'](path)
        finally:
            _KNOWN_PATH_READ_DEPTH.reset(token)
        if before != after or resolved_before != resolved_after:
            trace = _ACTIVE_SELECTOR_TRACE.get()
            if trace is not None:
                trace['unstable'] = True
        if trace is not None:
            record_observation(trace, 'resolutions', str(path), str(resolved_after))
        encoding = kwargs.get('encoding', args[0] if len(args) > 0 else None)
        errors = kwargs.get('errors', args[1] if len(args) > 1 else None)
        newline = kwargs.get('newline', args[2] if len(args) > 2 else None)
        try:
            consumed_equivalent = io.TextIOWrapper(
                io.BytesIO(before), encoding=encoding, errors=errors,
                newline=newline).read()
            if consumed_equivalent != result:
                trace = _ACTIVE_SELECTOR_TRACE.get()
                if trace is not None:
                    trace['unstable'] = True
        except (LookupError, UnicodeError, ValueError):
            trace = _ACTIVE_SELECTOR_TRACE.get()
            if trace is not None:
                trace['unstable'] = True
        record_file(path, _sha(after))
        return result

    def record_path_state(path, method, result, resolved):
        trace = _ACTIVE_SELECTOR_TRACE.get()
        if trace is None:
            return
        record_observation(trace, 'resolutions', str(path), str(resolved))
        record_observation(trace, 'states', (str(resolved), method), bool(result))

    def wrap_state(name):
        original = _PATH_METHODS_ORIGINAL[name]
        def checked(path, *args, **kwargs):
            before = _PATH_METHODS_ORIGINAL['resolve'](path)
            trace = _ACTIVE_SELECTOR_TRACE.get()
            if trace is not None:
                record_observation(trace, 'resolutions', str(path), str(before))
            token = _KNOWN_PATH_STATE_DEPTH.set(_KNOWN_PATH_STATE_DEPTH.get() + 1)
            try:
                result = original(path, *args, **kwargs)
            finally:
                _KNOWN_PATH_STATE_DEPTH.reset(token)
            after = _PATH_METHODS_ORIGINAL['resolve'](path)
            if trace is not None and before != after:
                trace['unstable'] = True
            record_path_state(path, name, result, before)
            if trace is not None:
                record_observation(trace, 'resolutions', str(path), str(after))
            return result
        return checked

    def resolve(path, *args, **kwargs):
        token = _KNOWN_PATH_RESOLVE_DEPTH.set(_KNOWN_PATH_RESOLVE_DEPTH.get() + 1)
        try:
            result = _PATH_METHODS_ORIGINAL['resolve'](path, *args, **kwargs)
        finally:
            _KNOWN_PATH_RESOLVE_DEPTH.reset(token)
        trace = _ACTIVE_SELECTOR_TRACE.get()
        if trace is not None:
            record_observation(trace, 'resolutions', str(path), str(result))
        return result

    def wrap_listing(name):
        original = _PATH_METHODS_ORIGINAL[name]
        signature = inspect.signature(original)

        def listed(path, *args, **kwargs):
            bound = signature.bind(path, *args, **kwargs)
            bound.apply_defaults()
            pattern = bound.arguments.get('pattern')
            options = tuple(sorted(
                (key, value) for key, value in bound.arguments.items()
                if key not in ('self', 'pattern')))
            trace = _ACTIVE_SELECTOR_TRACE.get()
            if trace is None:
                return original(path, *args, **kwargs)
            try:
                hash(options)
            except TypeError:
                trace['unknown_io'] = True
                trace.setdefault('unknown_sources', []).append(
                    ('listing_options', name, str(path)))
                options = None
            before = _PATH_METHODS_ORIGINAL['resolve'](path)
            record_observation(trace, 'resolutions', str(path), str(before))
            token = _KNOWN_PATH_LIST_DEPTH.set(_KNOWN_PATH_LIST_DEPTH.get() + 1)
            try:
                values = list(original(path, *args, **kwargs))
            finally:
                _KNOWN_PATH_LIST_DEPTH.reset(token)
            after = _PATH_METHODS_ORIGINAL['resolve'](path)
            if before != after:
                trace['unstable'] = True
            record_observation(trace, 'resolutions', str(path), str(after))
            if options is not None:
                key = (str(before), name,
                       pattern, options)
                record_observation(
                    trace, 'listings', key,
                    tuple(sorted(str(_PATH_METHODS_ORIGINAL['resolve'](item))
                                 for item in values)))
            return iter(values)
        return listed

    def path_open(path, mode='r', *args, **kwargs):
        trace = _ACTIVE_SELECTOR_TRACE.get()
        if (trace is not None and any(flag in mode for flag in ('r', '+'))
                and _KNOWN_PATH_READ_DEPTH.get() == 0):
            trace['unknown_io'] = True
            trace.setdefault('unknown_sources', []).append(('Path.open', str(path), mode, _KNOWN_PATH_READ_DEPTH.get()))
        return _PATH_METHODS_ORIGINAL['open'](path, mode, *args, **kwargs)

    def wrap_external_open(original):
        def opened(file, *args, **kwargs):
            mode = (args[0] if args else kwargs.get('mode', 'r'))
            trace = _ACTIVE_SELECTOR_TRACE.get()
            if (trace is not None and isinstance(mode, str)
                    and any(flag in mode for flag in ('r', '+'))
                    and _KNOWN_PATH_READ_DEPTH.get() == 0):
                trace['unknown_io'] = True
                trace.setdefault('unknown_sources', []).append(('open', str(file), mode, _KNOWN_PATH_READ_DEPTH.get()))
            return original(file, *args, **kwargs)
        return opened

    def scandir(path):
        if (_ACTIVE_SELECTOR_TRACE.get() is not None
                and _KNOWN_PATH_LIST_DEPTH.get() == 0):
            _ACTIVE_SELECTOR_TRACE.get()['unknown_io'] = True
            _ACTIVE_SELECTOR_TRACE.get().setdefault('unknown_sources', []).append(('scandir', str(path)))
        return _IO_METHODS_ORIGINAL['os.scandir'](path)

    def listdir(path='.'):
        if (_ACTIVE_SELECTOR_TRACE.get() is not None
                and _KNOWN_PATH_LIST_DEPTH.get() == 0):
            _ACTIVE_SELECTOR_TRACE.get()['unknown_io'] = True
            _ACTIVE_SELECTOR_TRACE.get().setdefault('unknown_sources', []).append(('listdir', str(path)))
        return _IO_METHODS_ORIGINAL['os.listdir'](path)

    def wrap_os_state(name):
        original = _IO_METHODS_ORIGINAL[name]
        def checked(path):
            try:
                before = _PATH_METHODS_ORIGINAL['resolve'](Path(path))
            except (OSError, TypeError, ValueError):
                before = None
            trace = _ACTIVE_SELECTOR_TRACE.get()
            if trace is not None and before is not None:
                record_observation(trace, 'resolutions', str(path), str(before))
            token = _KNOWN_PATH_STATE_DEPTH.set(_KNOWN_PATH_STATE_DEPTH.get() + 1)
            try:
                result = original(path)
            finally:
                _KNOWN_PATH_STATE_DEPTH.reset(token)
            if trace is not None:
                try:
                    after = _PATH_METHODS_ORIGINAL['resolve'](Path(path))
                except (OSError, TypeError, ValueError):
                    trace['unknown_io'] = True
                else:
                    if before != after:
                        trace['unstable'] = True
                    if before is not None:
                        record_observation(trace, 'resolutions', str(path), str(after))
                    record_observation(
                        trace, 'states', (str(before), name.rsplit('.', 1)[1]),
                        bool(result))
            return result
        return checked

    def realpath(path, *args, **kwargs):
        token = _KNOWN_PATH_RESOLVE_DEPTH.set(_KNOWN_PATH_RESOLVE_DEPTH.get() + 1)
        try:
            result = _IO_METHODS_ORIGINAL['os.path.realpath'](path, *args, **kwargs)
        finally:
            _KNOWN_PATH_RESOLVE_DEPTH.reset(token)
        trace = _ACTIVE_SELECTOR_TRACE.get()
        if trace is not None:
            record_observation(trace, 'resolutions', str(path), str(result))
        return result

    def wrap_unknown_os_call(name):
        original = _IO_METHODS_ORIGINAL[name]
        def called(*args, **kwargs):
            trace = _ACTIVE_SELECTOR_TRACE.get()
            if trace is not None:
                known = (_KNOWN_PATH_READ_DEPTH.get() > 0
                         or _KNOWN_PATH_STATE_DEPTH.get() > 0
                         or _KNOWN_PATH_RESOLVE_DEPTH.get() > 0)
                if not known:
                    trace['unknown_io'] = True
                    trace.setdefault('unknown_sources', []).append((name,))
            return original(*args, **kwargs)
        return called

    Path.read_bytes = read_bytes
    Path.read_text = read_text
    for name in ('exists', 'is_file', 'is_dir', 'is_symlink'):
        setattr(Path, name, wrap_state(name))
    for name in ('glob', 'rglob'):
        setattr(Path, name, wrap_listing(name))
    Path.iterdir = wrap_listing('iterdir')
    Path.resolve = resolve
    Path.open = path_open
    builtins.open = wrap_external_open(_IO_METHODS_ORIGINAL['builtins.open'])
    __import__('io').open = wrap_external_open(_IO_METHODS_ORIGINAL['io.open'])
    os.scandir = scandir
    os.listdir = listdir
    for name in ('os.path.exists', 'os.path.lexists', 'os.path.isfile', 'os.path.isdir'):
        setattr(os.path, name.rsplit('.', 1)[1], wrap_os_state(name))
    os.path.realpath = realpath
    for name in ('os.stat', 'os.lstat', 'os.open', 'os.read', 'os.fstat'):
        setattr(os, name.rsplit('.', 1)[1], wrap_unknown_os_call(name))


def _restore_selector_io_trace():
    if not _PATH_METHODS_ORIGINAL:
        return
    for name, original in _PATH_METHODS_ORIGINAL.items():
        setattr(Path, name, original)
    _PATH_METHODS_ORIGINAL.clear()
    builtins.open = _IO_METHODS_ORIGINAL['builtins.open']
    __import__('io').open = _IO_METHODS_ORIGINAL['io.open']
    os.scandir = _IO_METHODS_ORIGINAL['os.scandir']
    os.listdir = _IO_METHODS_ORIGINAL['os.listdir']
    for name in ('os.path.exists', 'os.path.lexists', 'os.path.isfile', 'os.path.isdir',
                 'os.path.realpath'):
        setattr(os.path, name.rsplit('.', 1)[1], _IO_METHODS_ORIGINAL[name])
    for name in ('os.stat', 'os.lstat', 'os.open', 'os.read', 'os.fstat'):
        setattr(os, name.rsplit('.', 1)[1], _IO_METHODS_ORIGINAL[name])
    _IO_METHODS_ORIGINAL.clear()


def _verify_selector_closure(closure):
    """Rehash every observed file and recheck all state/listing predicates."""
    originals = _PATH_METHODS_ORIGINAL
    read_bytes = originals['read_bytes'] if originals else Path.read_bytes
    token = _ACTIVE_SELECTOR_TRACE.set(None)
    try:
        for path, expected in closure['files'].items():
            try:
                if _sha(read_bytes(Path(path))) != expected:
                    return False
            except OSError:
                return False
        for (path, method), expected in closure['states'].items():
            try:
                if method in ('exists', 'lexists', 'isfile', 'isdir'):
                    current = getattr(os.path, method)(path)
                else:
                    current = getattr(Path(path), method)()
                if bool(current) != expected:
                    return False
            except OSError:
                return False
        for (path, method, pattern, options), expected in closure['listings'].items():
            try:
                directory = Path(path)
                if method == 'iterdir':
                    values = list(directory.iterdir())
                else:
                    values = list(getattr(directory, method)(
                        pattern, **dict(options)))
                current = tuple(sorted(str(item.resolve()) for item in values))
                if current != expected:
                    return False
            except OSError:
                return False
        for path, expected in closure['resolutions'].items():
            try:
                if str(Path(path).resolve()) != expected:
                    return False
            except OSError:
                return False
        return True
    finally:
        _ACTIVE_SELECTOR_TRACE.reset(token)


def _selector_closure(trace):
    return {key: dict(value) if isinstance(value, dict) else value
            for key, value in trace.items() if key != 'unstable'}


@contextmanager
def seed_video_selector_cache_scope():
    """Bound selector-index reuse to one synchronous invocation."""
    active = _ACTIVE_SELECTOR_INDEX.get()
    if active is not None:
        active['stats']['nested_scopes'] += 1
        yield active['stats']
        return
    state = {'entries': OrderedDict(), 'building': False,
             'stats': {'calls': 0, 'hits': 0, 'misses': 0, 'evictions': 0,
                       'full_replays': 0, 'closure_files': 0,
                       'closure_directories': 0, 'nested_scopes': 0,
                       'uncacheable_replays': 0}}
    token = _ACTIVE_SELECTOR_INDEX.set(state)
    try:
        yield state['stats']
    finally:
        _ACTIVE_SELECTOR_INDEX.reset(token)


def verify_typed_map_transition(transition, old_map_hash, new_map_hash,
                                library_hash):
    """Verify the typed old→new map receipt through the merge verifier."""
    required = {'old_identity_map', 'new_identity_map', 'graph_export',
                'rebase_report', 'state'}
    if not isinstance(transition, dict) or set(transition) != required:
        raise ValueError('Cached seed hints require a complete typed map transition')
    checked = {}
    for key, receipt in transition.items():
        if (not isinstance(receipt, dict) or set(receipt) != {'path', 'sha256'}
                or not isinstance(receipt.get('path'), str)
                or not SHA_RE.fullmatch(receipt.get('sha256') or '')):
            raise ValueError('Malformed typed map transition receipt: ' + key)
        path = Path(receipt['path']).resolve()
        data = path.read_bytes()
        if _sha(data) != receipt['sha256']:
            raise ValueError('Typed map transition input changed: ' + key)
        checked[key] = {'path': str(path), 'sha256': receipt['sha256']}
    if (checked['old_identity_map']['sha256'] != old_map_hash
            or checked['new_identity_map']['sha256'] != new_map_hash):
        raise ValueError('Typed map transition does not bind the requested map hashes')
    cache_key = (old_map_hash, new_map_hash, library_hash,
                 tuple((key, checked[key]['path'], checked[key]['sha256'])
                       for key in sorted(checked)))
    cached = _TYPED_TRANSITION_PROOF_CACHE.get(cache_key)
    if cached is not None:
        for receipt in cached['hash_inputs']:
            path = Path(receipt['path'])
            try:
                current_sha = _sha(path.read_bytes())
            except OSError:
                raise ValueError('A cached typed-transition source disappeared') from None
            if current_sha != receipt['sha256']:
                _TYPED_TRANSITION_PROOF_CACHE.pop(cache_key, None)
                raise ValueError('A cached typed-transition source changed')
        return checked, json.loads(json.dumps(cached['verified']))
    try:
        from merge_rebased_radio_growth import verify_typed_identity_map_rebinding
    except ImportError:
        raise ValueError('Typed map transition verifier is unavailable') from None
    verified = verify_typed_identity_map_rebinding(
        graph_export_path=checked['graph_export']['path'],
        rebase_report_path=checked['rebase_report']['path'],
        state_path=checked['state']['path'],
        old_identity_map_path=checked['old_identity_map']['path'],
        new_identity_map_path=checked['new_identity_map']['path'],
        library_sha256=library_hash)
    if (not isinstance(verified, dict)
            or verified.get('verified') is not True
            or verified.get('old_identity_map_sha256') != old_map_hash
            or verified.get('new_identity_map_sha256') != new_map_hash
            or verified.get('library_sha256') != library_hash
            or Path(verified.get('old_identity_map_path', '')).resolve()
               != Path(checked['old_identity_map']['path'])
            or Path(verified.get('new_identity_map_path', '')).resolve()
               != Path(checked['new_identity_map']['path'])
            or Path(verified.get('report_path', '')).resolve()
               != Path(checked['rebase_report']['path'])
            or Path(verified.get('graph_export_path', '')).resolve()
               != Path(checked['graph_export']['path'])
            or Path(verified.get('state_path', '')).resolve()
               != Path(checked['state']['path'])
            or verified.get('report_sha256') != checked['rebase_report']['sha256']
            or verified.get('graph_export_sha256') != checked['graph_export']['sha256']
            or verified.get('state_file_sha256') != checked['state']['sha256']
            or not isinstance(verified.get('authenticated_observation_inputs'), list)
            or not isinstance(verified.get('proof_input_receipts'), list)):
        raise ValueError('Typed map transition verifier returned incomplete source proof')
    accepted = verified['authenticated_observation_inputs']
    normalized, seen = [], set()
    for receipt in accepted:
        if (not isinstance(receipt, dict) or set(receipt) != {'path', 'sha256'}
                or not isinstance(receipt.get('path'), str)
                or not SHA_RE.fullmatch(receipt.get('sha256') or '')):
            raise ValueError('Typed map verifier returned a malformed accepted snapshot receipt')
        path = Path(receipt['path']).resolve()
        pair = (str(path), receipt['sha256'])
        if pair in seen or _sha(path.read_bytes()) != receipt['sha256']:
            raise ValueError('Typed map verifier returned duplicate or changed snapshot input')
        seen.add(pair)
        normalized.append({'path': str(path), 'sha256': receipt['sha256']})
    verified = dict(verified)
    verified['accepted_snapshot_receipts'] = sorted(
        normalized, key=lambda row: (row['path'], row['sha256']))
    track_receipt = verified.get('track_snapshot_receipt')
    if (not isinstance(track_receipt, dict)
            or set(track_receipt) != {'path', 'sha256'}
            or not isinstance(track_receipt.get('path'), str)
            or not SHA_RE.fullmatch(track_receipt.get('sha256') or '')):
        raise ValueError('Typed map verifier omitted its bound Library snapshot receipt')
    track_path = Path(track_receipt['path']).resolve()
    if _sha(track_path.read_bytes()) != track_receipt['sha256']:
        raise ValueError('Typed map verifier returned a changed Library snapshot')
    verified['track_snapshot_receipt'] = {'path': str(track_path),
                                          'sha256': track_receipt['sha256']}
    proof_inputs = []
    for receipt in verified['proof_input_receipts']:
        if (not isinstance(receipt, dict) or set(receipt) != {'path', 'sha256'}
                or not isinstance(receipt.get('path'), str)
                or not SHA_RE.fullmatch(receipt.get('sha256') or '')):
            raise ValueError('Typed map verifier returned malformed transitive proof inputs')
        path = Path(receipt['path']).resolve()
        if _sha(path.read_bytes()) != receipt['sha256']:
            raise ValueError('Typed map verifier returned a changed transitive proof input')
        proof_inputs.append({'path': str(path), 'sha256': receipt['sha256']})
    if len({(row['path'], row['sha256']) for row in proof_inputs}) != len(proof_inputs):
        raise ValueError('Typed map verifier returned duplicate transitive proof inputs')
    proof_pairs = {(row['path'], row['sha256']) for row in proof_inputs}
    required_proof_inputs = list(checked.values()) + verified['accepted_snapshot_receipts'] + [
        verified['track_snapshot_receipt']]
    if any((row['path'], row['sha256']) not in proof_pairs
           for row in required_proof_inputs):
        raise ValueError('Typed map verifier omitted explicit inputs from transitive proof receipts')
    hash_inputs = proof_inputs
    unique_hash_inputs = {}
    for receipt in hash_inputs:
        unique_hash_inputs[(receipt['path'], receipt['sha256'])] = receipt
    hash_inputs = [unique_hash_inputs[key] for key in sorted(unique_hash_inputs)]
    # Detect source replacement during the proof replay before memoizing it.
    for receipt in hash_inputs:
        if _sha(Path(receipt['path']).read_bytes()) != receipt['sha256']:
            raise ValueError('A typed-transition source changed during full verification')
    _TYPED_TRANSITION_PROOF_CACHE[cache_key] = {
        'hash_inputs': hash_inputs,
        'verified': json.loads(json.dumps(verified)),
    }
    return checked, verified


def _candidate_for_video(snapshot, video_id, persistent_id, matcher):
    """Return true only when every saved radio occurrence has one strict match."""
    rows = [row for row in snapshot['observations']
            if row['source'] == 'ytmusic' and row['relation'] == 'radio'
            and row['track']['video_id'] == video_id]
    if not rows:
        return False
    for row in rows:
        candidates = matcher.match(row['track'])
        if (len(candidates) != 1 or candidates[0].get('persistent_id') != persistent_id
                or candidates[0].get('artist_credit_match') != 'complete'):
            return False
        local = candidates[0].get('local_metadata') or {}
        if version_signature(local.get('title') or '') != version_signature(row['track'].get('title') or ''):
            return False
    return True


def _strict_album_metadata_match(remote_album, local_album, candidate, matcher):
    """Require exact album metadata or a validated whole-album map alias."""
    if candidate.get('album_match') is not True:
        return False
    if normalize(local_album or '') == normalize(remote_album or ''):
        return True
    # IdentityMap validates alias targets against unique canonical Library
    # album values. Consult its exact raw source key here; do not infer an
    # alias from normalization, partial text, or the candidate flag alone.
    if not isinstance(matcher, IdentityMap):
        return False
    aliases = matcher.album_title_aliases
    return bool(remote_album and local_album
                and aliases.get(remote_album) == local_album)


def strict_cached_search_match(results, seed, matcher):
    """Resolve cached search rows only on a unique, strict metadata match.

    This resolver is intentionally stricter than the live collector's seed
    resolver: cached alias-rematch hints must carry a complete credit, active
    title, same version signature, duration within the matcher limit, and an
    exact album label or validated whole-album metadata alias. It returns no
    recording verification or graph evidence.
    """
    if not isinstance(results, list):
        return None
    selected = {}
    for position, remote in enumerate(results):
        if not isinstance(remote, dict):
            continue
        video_id = remote.get('videoId')
        if not isinstance(video_id, str) or not VIDEO_RE.fullmatch(video_id):
            continue
        match_row = dict(remote)
        if isinstance(match_row.get('album'), str):
            match_row['album'] = {'name': match_row['album']}
        found = matcher.match(match_row)
        if not found:
            continue
        matching_pids = {candidate.get('persistent_id') for candidate in found}
        if seed.get('persistent_id') not in matching_pids:
            continue
        if (len(found) != 1
                or found[0].get('artist_credit_match') != 'complete'
                or found[0].get('title_match') not in ('exact', 'explicit_alias')
                or found[0].get('duration_difference_seconds') is None
                or found[0]['duration_difference_seconds'] > 3
                or found[0].get('album_match') is not True):
            return None
        local = found[0].get('local_metadata') or {}
        remote_title = remote.get('title')
        remote_album = (match_row.get('album') or {}).get('name')
        if (not isinstance(remote_title, str)
                or version_signature(local.get('title') or '') != version_signature(remote_title)
                or not _strict_album_metadata_match(
                    remote_album, local.get('album'), found[0], matcher)):
            return None
        selected.setdefault(video_id, []).append({
            'position': position,
            'title_match': found[0]['title_match'],
            'artist_match': found[0].get('artist_match'),
            'artist_credit_match': found[0]['artist_credit_match'],
            'duration_difference_seconds': found[0]['duration_difference_seconds'],
            'album_match': found[0]['album_match'],
            'version_signature': sorted(version_signature(remote_title)),
        })
    if len(selected) != 1:
        return None
    video_id, evidence = next(iter(selected.items()))
    return {'video_id': video_id, 'result_positions': [row['position'] for row in evidence],
            'matches': evidence}


def build_seed_video_evidence(graph_paths, matcher, library_hash, identity_map_sha256):
    """Create deterministic PID→radio-video hints from raw snapshot paths.

    Invalid or unbound input files are skipped and counted. Candidate records
    contain their actual source path and content hash for later revalidation.
    """
    if not SHA_RE.fullmatch(library_hash or '') or not SHA_RE.fullmatch(identity_map_sha256 or ''):
        raise ValueError('Library and identity-map hashes must be SHA-256')
    if not isinstance(graph_paths, (list, tuple, set)):
        raise ValueError('Snapshot paths must be a list, tuple, or set')
    candidates = {}
    skipped = []
    radio_count = 0
    for raw_path in graph_paths:
        expected_sha = None
        if isinstance(raw_path, dict):
            source = raw_path.get('input')
            if isinstance(source, dict):
                raw_path, expected_sha = source.get('path'), source.get('sha256')
            else:
                raw_path = None
        if not isinstance(raw_path, (str, Path)):
            skipped.append({'path': None, 'reason': 'invalid_snapshot_path'})
            continue
        path = Path(raw_path).resolve()
        try:
            data = path.read_bytes()
            snapshot = json.loads(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            skipped.append({'path': str(path), 'reason': 'unreadable_or_invalid_snapshot'})
            continue
        try:
            validate_observations(snapshot, library_hash)
        except ValueError as error:
            skipped.append({'path': str(path), 'reason': str(error)})
            continue
        if (snapshot.get('library_sha256') != library_hash
                or snapshot.get('identity_map_sha256') != identity_map_sha256):
            skipped.append({'path': str(path), 'reason': 'source_hash_mismatch'})
            continue
        raw_sha = _sha(data)
        if expected_sha is not None and expected_sha != raw_sha:
            skipped.append({'path': str(path), 'reason': 'graph_input_sha256_mismatch'})
            continue
        by_video = {}
        for observation in snapshot['observations']:
            if observation.get('relation') != 'radio' or observation.get('source') != 'ytmusic':
                continue
            radio_count += 1
            by_video.setdefault(observation['track']['video_id'], []).append(observation)
        for video_id, observations in by_video.items():
            # Match every repeated observation for the video; one partial or
            # conflicting row makes that video unsuitable as a seed hint.
            matching = []
            valid = True
            for observation in observations:
                found = matcher.match(observation['track'])
                if len(found) != 1 or found[0].get('artist_credit_match') != 'complete':
                    valid = False
                    break
                matching.append(found[0]['persistent_id'])
            if not valid or len(set(matching)) != 1:
                continue
            pid = matching[0]
            if not _candidate_for_video(snapshot, video_id, pid, matcher):
                continue
            candidates.setdefault(pid, []).append({
                'persistent_id': pid, 'video_id': video_id,
                'input_path': str(path), 'input_sha256': raw_sha,
                'source_relation': 'radio',
            })
    # One stable video per PID. Repeated saved evidence for the same video is
    # collapsed; ties are resolved by video ID and then source path.
    video_pids = {}
    for pid, hints in candidates.items():
        for hint in hints:
            video_pids.setdefault(hint['video_id'], set()).add(pid)
    ambiguous_videos = {video for video, pids in video_pids.items() if len(pids) != 1}
    rows = []
    for pid in sorted(candidates):
        eligible = [row for row in candidates[pid] if row['video_id'] not in ambiguous_videos]
        if eligible:
            rows.append(min(eligible, key=lambda row: (row['video_id'], row['input_path'])))
    rows.sort(key=lambda row: row['persistent_id'])
    return {
        'schema_version': 1, 'producer': PRODUCER,
        'library_sha256': library_hash,
        'identity_map_sha256': identity_map_sha256,
        'rows': rows,
        'audit': {
            'input_snapshot_count': len(graph_paths),
            'radio_observation_count': radio_count,
            'accepted_seed_video_count': len(rows),
            'ambiguous_video_mapping_count': len(ambiguous_videos),
            'skipped_snapshot_count': len(skipped),
            'skipped_snapshots': skipped,
            'identity_status': 'unverified_metadata_candidate',
            'edges_created': 0,
        },
    }


def build_rebound_radio_hints(snapshot_receipts, matcher, library_hash,
                              old_identity_map_sha256, audit=None):
    """Rematch typed-transition-authenticated old-map snapshots as hints.

    `snapshot_receipts` must come from the typed map verifier's accepted input
    list. Each snapshot remains bound to its old map; only its metadata matches
    are recomputed under the current matcher.
    """
    candidates = {}
    for receipt in snapshot_receipts:
        path = Path(receipt['path']).resolve()
        data = path.read_bytes()
        if _sha(data) != receipt.get('sha256'):
            raise ValueError('Authenticated radio snapshot changed after transition verification')
        try:
            snapshot = json.loads(data)
            if (snapshot.get('library_sha256') != library_hash
                    or snapshot.get('identity_map_sha256') != old_identity_map_sha256):
                if audit is not None:
                    audit['foreign_source_hash_count'] = audit.get('foreign_source_hash_count', 0) + 1
                continue
            validate_observations(snapshot, library_hash)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            if audit is not None:
                audit['invalid_source_snapshot_count'] = audit.get('invalid_source_snapshot_count', 0) + 1
            continue
        by_video = {}
        for observation in snapshot['observations']:
            if observation.get('source') == 'ytmusic' and observation.get('relation') == 'radio':
                by_video.setdefault(observation['track']['video_id'], []).append(observation)
        for video_id, observations in by_video.items():
            matching = []
            for observation in observations:
                found = matcher.match(observation['track'])
                if (len(found) != 1 or found[0].get('artist_credit_match') != 'complete'
                        or found[0].get('title_match') not in ('exact', 'explicit_alias')
                        or version_signature(found[0].get('local_metadata', {}).get('title') or '')
                           != version_signature(observation['track'].get('title') or '')):
                    matching = []
                    break
                matching.append(found[0]['persistent_id'])
            if not matching or len(set(matching)) != 1:
                continue
            pid = matching[0]
            candidates.setdefault((pid, video_id), []).append({
                'persistent_id': pid, 'video_id': video_id,
                'input_path': str(path), 'input_sha256': receipt['sha256'],
                'source_library_sha256': snapshot['library_sha256'],
                'source_identity_map_sha256': snapshot['identity_map_sha256'],
                'source_relation': 'rebound_radio_candidate',
            })
    by_video, by_pid = {}, {}
    for pid, video_id in candidates:
        by_pid.setdefault(pid, set()).add(video_id)
        by_video.setdefault(video_id, set()).add(pid)
    ambiguous_pids = {pid for pid, videos in by_pid.items() if len(videos) != 1}
    ambiguous_videos = {video for video, pids in by_video.items() if len(pids) != 1}
    rows = []
    for (pid, video_id), receipts in candidates.items():
        if pid in ambiguous_pids or video_id in ambiguous_videos:
            continue
        rows.append(min(receipts, key=lambda row: (row['input_path'], row['input_sha256'])))
    return sorted(rows, key=lambda row: (row['persistent_id'], row['video_id']))


def build_rebound_catalog_hints(document, evidence_path, tracks, old_matcher,
                                current_matcher, library_hash, old_map_hash):
    """Revalidate old strict catalog hints under the new matcher."""
    evidence_path = Path(evidence_path).resolve()
    sidecar_dir = evidence_path.parent
    if not isinstance(document, dict):
        raise ValueError('Prior catalog evidence must be an object')
    catalog_document, catalog_path = document, evidence_path
    if document.get('producer') == PRODUCER:
        # Validate the complete prior sidecar first, including any radio rows
        # and the exact catalog document receipt it embeds.
        select_seed_video_evidence(document, evidence_path, [], tracks, old_matcher,
                                   library_hash, old_map_hash)
        source = document.get('catalog_evidence')
        if not isinstance(source, dict):
            return []
        catalog_path = _resolve_source(source['path'], sidecar_dir)
        catalog_document = json.loads(catalog_path.read_text(encoding='utf-8'))
    if catalog_document.get('producer') != CATALOG_PRODUCER:
        raise ValueError('Prior evidence does not contain typed album-catalog hints')
    validated = validate_album_catalog_evidence(
        catalog_document, catalog_path, tracks, old_matcher,
        library_hash, old_map_hash)
    rows_by_pid = {row['persistent_id']: row for row in catalog_document['rows']}
    rebound = []
    for checked in validated:
        source_row = rows_by_pid[checked['persistent_id']]
        response_path = Path(checked['input_path']).resolve()
        response_bytes = response_path.read_bytes()
        if _sha(response_bytes) != checked['input_sha256']:
            raise ValueError('Prior catalog response changed after old-map validation')
        envelope = json.loads(response_bytes)
        position = source_row['source_key']['track_position']
        remote = envelope['response']['tracks'][position - 1]
        match_row = dict(remote)
        if isinstance(match_row.get('album'), str):
            match_row['album'] = {'name': match_row['album']}
        matches = current_matcher.match(match_row)
        if (len(matches) != 1 or matches[0].get('persistent_id') != checked['persistent_id']
                or matches[0].get('artist_credit_match') != 'complete'
                or matches[0].get('title_match') not in ('exact', 'explicit_alias')
                or matches[0].get('duration_difference_seconds') is None
                or matches[0]['duration_difference_seconds'] > 3
                or matches[0].get('album_match') is not True):
            continue
        local = matches[0].get('local_metadata') or {}
        if (normalize(local.get('album') or '') != normalize(envelope['response'].get('title') or '')
                or version_signature(local.get('title') or '')
                   != version_signature(remote.get('title') or '')):
            continue
        rebound.append({
            'persistent_id': checked['persistent_id'],
            'video_id': checked['video_id'],
            'source_relation': 'rebound_album_catalog_candidate',
            'input_path': str(response_path),
            'input_sha256': checked['input_sha256'],
            'album_id': source_row['album_id'],
            'track_position': position,
            'prior_catalog_evidence_path': str(catalog_path),
            'prior_catalog_evidence_sha256': _sha(catalog_path.read_bytes()),
        })
    return sorted(rebound, key=lambda row: (row['persistent_id'], row['video_id']))


def _dedupe_rebound_hints(rows):
    by_pid, by_video = {}, {}
    for row in rows:
        by_pid.setdefault(row['persistent_id'], set()).add(row['video_id'])
        by_video.setdefault(row['video_id'], set()).add(row['persistent_id'])
    ambiguous_pids = {pid for pid, videos in by_pid.items() if len(videos) != 1}
    ambiguous_videos = {video for video, pids in by_video.items() if len(pids) != 1}
    priority = {'rebound_radio_candidate': 0,
                'rebound_album_catalog_candidate': 1,
                'rebound_cached_search_candidate': 2}
    grouped = {}
    for row in rows:
        pid, video = row['persistent_id'], row['video_id']
        if pid in ambiguous_pids or video in ambiguous_videos:
            continue
        grouped.setdefault(pid, []).append(row)
    selected = []
    for pid in sorted(grouped):
        candidates = sorted(grouped[pid], key=lambda row: (
            priority[row['source_relation']], row['input_path'], row['input_sha256']))
        primary = dict(candidates[0])
        if len(candidates) > 1:
            primary['additional_source_evidence'] = candidates[1:]
        selected.append(primary)
    return selected


def build_rebound_seed_video_evidence(*, track_snapshot, old_identity_map,
                                      current_identity_map, typed_transition,
                                      catalog_evidence=None,
                                      cached_hint_evidence=None):
    """Build current-map hints from typed old-map snapshots and cache sources.

    All graph snapshots come from the typed verifier's accepted source-input
    set. Catalog and search hints keep their original immutable evidence and
    are fully revalidated under both maps. The result contains no graph edges.
    """
    tracks, library_hash, library_input = load_library(
        track_snapshot=Path(track_snapshot))
    old_path, current_path = Path(old_identity_map).resolve(), Path(current_identity_map).resolve()
    old_bytes, current_bytes = old_path.read_bytes(), current_path.read_bytes()
    old_hash, current_hash = _sha(old_bytes), _sha(current_bytes)
    old_matcher = IdentityMap(json.loads(old_bytes), tracks, library_hash)
    current_matcher = IdentityMap(json.loads(current_bytes), tracks, library_hash)
    transition_receipts, verified = verify_typed_map_transition(
        typed_transition, old_hash, current_hash, library_hash)
    if (Path(verified['track_snapshot_receipt']['path']).resolve() != Path(track_snapshot).resolve()
            or verified['track_snapshot_receipt']['sha256'] != _sha(Path(track_snapshot).read_bytes())):
        raise ValueError('Typed transition Library snapshot differs from requested input')
    radio_source_audit = {}
    radio_rows = build_rebound_radio_hints(
        verified['accepted_snapshot_receipts'], current_matcher, library_hash,
        old_hash, audit=radio_source_audit)
    catalog_rows = []
    catalog_receipt = None
    if catalog_evidence is not None:
        catalog_path = Path(catalog_evidence).resolve()
        catalog_bytes = catalog_path.read_bytes()
        catalog_document = json.loads(catalog_bytes)
        catalog_rows = build_rebound_catalog_hints(
            catalog_document, catalog_path, tracks, old_matcher, current_matcher,
            library_hash, old_hash)
        catalog_receipt = {'path': str(catalog_path), 'sha256': _sha(catalog_bytes)}
    cached_rows = []
    cached_receipt = None
    if cached_hint_evidence is not None:
        from revalidate_cached_seed_hints import revalidate_cached_seed_hints
        cached_path = Path(cached_hint_evidence).resolve()
        cached_bytes = cached_path.read_bytes()
        cached_doc = json.loads(cached_bytes)
        if (cached_doc.get('schema_version') != 1
                or cached_doc.get('producer') != CACHED_HINT_PRODUCER
                or cached_doc.get('prior_identity_map', {}).get('sha256') != old_hash
                or cached_doc.get('revalidated_identity_map', {}).get('sha256') != current_hash
                or cached_doc.get('typed_map_transition') != transition_receipts
                or not _typed_transition_verification_matches(
                    cached_doc.get('typed_transition_verification'), verified)):
            raise ValueError('Cached search hint artifact is not bound to this typed map transition')
        collector_dirs = list(dict.fromkeys(
            run['collector_dir'] for run in cached_doc.get('source_runs', [])
            if isinstance(run, dict) and isinstance(run.get('collector_dir'), str)))
        replayed = revalidate_cached_seed_hints(
            track_snapshot=track_snapshot, old_identity_map=old_path,
            current_identity_map=current_path, collector_dirs=collector_dirs,
            typed_transition=transition_receipts)
        if (replayed.get('rows') != cached_doc.get('rows')
                or replayed.get('source_runs') != cached_doc.get('source_runs')):
            raise ValueError('Cached search hint artifact differs from its immutable source replay')
        for row in replayed['rows']:
            cached_rows.append({
                'persistent_id': row['persistent_id'], 'video_id': row['video_id'],
                'source_relation': 'rebound_cached_search_candidate',
                'input_path': row['input_path'], 'input_sha256': row['input_sha256'],
                'historical_summary': row['historical_summary'],
                'historical_seed_manifest': row['historical_seed_manifest'],
                'historical_result_status': row['historical_result_status'],
                'historical_old_identity_map_sha256': row['historical_old_identity_map_sha256'],
                'revalidated_identity_map_sha256': row['revalidated_identity_map_sha256'],
                'request_arguments': row['request_arguments'],
                'strict_match_evidence': row['strict_match_evidence'],
                'resolver': row['resolver'],
                'typed_transition_report_sha256': transition_receipts['rebase_report']['sha256'],
            })
        cached_receipt = {'path': str(cached_path), 'sha256': _sha(cached_bytes)}
    rows = _dedupe_rebound_hints(radio_rows + catalog_rows + cached_rows)
    return {
        'schema_version': 1, 'producer': REBOUND_PRODUCER,
        'library_sha256': library_hash, 'library_input': library_input,
        'prior_identity_map_sha256': old_hash,
        'identity_map_sha256': current_hash,
        'track_snapshot': verified['track_snapshot_receipt'],
        'typed_map_transition': transition_receipts,
        'typed_transition_verification': verified,
        'catalog_evidence': catalog_receipt,
        'cached_hint_evidence': cached_receipt,
        'rows': rows,
        'audit': {
            'authenticated_radio_snapshot_count': len(verified['accepted_snapshot_receipts']),
            'foreign_source_hash_snapshot_count': radio_source_audit.get('foreign_source_hash_count', 0),
            'invalid_authenticated_snapshot_count': radio_source_audit.get('invalid_source_snapshot_count', 0),
            'revalidated_radio_seed_count': len(radio_rows),
            'revalidated_catalog_seed_count': len(catalog_rows),
            'revalidated_cached_search_seed_count': len(cached_rows),
            'accepted_seed_video_count': len(rows),
            'ambiguous_pid_or_video_hints_excluded': len(radio_rows) + len(catalog_rows)
                                                 + len(cached_rows) - len(rows),
            'recording_identity_verified': 0,
            'recommendation_observations_created': 0,
            'recommendation_edges_created': 0,
            'outgoing_roots_created': 0,
            'network_requests': 0,
        },
    }


def _resolve_source(path, sidecar_dir):
    source = Path(path)
    return source.resolve() if source.is_absolute() else (sidecar_dir / source).resolve()


def _is_sanitized_catalog(value):
    if isinstance(value, dict):
        return (set(value) <= CATALOG_SAFE_KEYS
                and all(_is_sanitized_catalog(item) for item in value.values()))
    if isinstance(value, list):
        return all(_is_sanitized_catalog(item) for item in value)
    return value is None or isinstance(value, (str, int, float, bool))


def _validate_catalog_seed_row(row, sidecar_dir, tracks, matcher,
                               library_hash, identity_map_sha256):
    if (not isinstance(row, dict) or set(row) != CATALOG_ROW_KEYS
            or row.get('source_relation') != 'album_catalog_candidate'
            or not isinstance(row.get('persistent_id'), str)
            or not re.fullmatch('[0-9A-F]{16}', row['persistent_id'])
            or row['persistent_id'] not in {track.get('persistent_id') for track in tracks}
            or not isinstance(row.get('video_id'), str) or not VIDEO_RE.fullmatch(row['video_id'])
            or not isinstance(row.get('album_id'), str) or not ALBUM_ID_RE.fullmatch(row['album_id'])
            or not isinstance(row.get('input_path'), str)
            or not isinstance(row.get('input_sha256'), str)
            or not SHA_RE.fullmatch(row['input_sha256'])):
        raise ValueError('Invalid or duplicate album-catalog seed evidence row')
    source_key = row.get('source_key')
    if (not isinstance(source_key, dict)
            or set(source_key) != {'endpoint', 'argument_key', 'argument_value', 'track_position'}
            or source_key.get('endpoint') != 'get_album'
            or source_key.get('argument_key') != 'browseId'
            or source_key.get('argument_value') != row['album_id']
            or type(source_key.get('track_position')) is not int
            or source_key['track_position'] < 1):
        raise ValueError('Invalid album-catalog request key provenance')
    catalog_response_path = _resolve_source(row['input_path'], sidecar_dir)
    try:
        response_bytes = catalog_response_path.read_bytes()
        if _sha(response_bytes) != row['input_sha256']:
            raise ValueError('Album-catalog response hash changed')
        cached = json.loads(response_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Album-catalog response is unreadable') from None
    if (not isinstance(cached, dict) or cached.get('endpoint') != 'get_album'
            or cached.get('arguments') != {'browseId': row['album_id']}
            or not isinstance(cached.get('response'), dict)
            or set(cached) != {'endpoint', 'arguments', 'observed_at', 'response'}):
        raise ValueError('Album-catalog response does not match its saved browse ID')
    album_response = cached['response']
    if not _is_sanitized_catalog(album_response):
        raise ValueError('Album-catalog response contains fields outside its safe metadata schema')
    response_rows = album_response.get('tracks')
    position = source_key['track_position']
    if (not isinstance(response_rows, list) or position > len(response_rows)
            or not isinstance(album_response.get('title'), str)):
        raise ValueError('Album-catalog track position is outside the response')
    remote = response_rows[position - 1]
    if (not isinstance(remote, dict) or remote.get('videoId') != row['video_id']
            or not isinstance(remote.get('title'), str)
            or not isinstance(remote.get('artists'), list)):
        raise ValueError('Album-catalog video ID differs from the saved response')
    match_row = dict(remote)
    if isinstance(match_row.get('album'), str):
        match_row['album'] = {'name': match_row['album']}
    candidates = matcher.match(match_row)
    if (len(candidates) != 1 or candidates[0].get('persistent_id') != row['persistent_id']
            or candidates[0].get('artist_credit_match') != 'complete'):
        raise ValueError('Album-catalog row no longer has one complete-credit duration match')
    local = candidates[0].get('local_metadata') or {}
    if (version_signature(local.get('title') or '') != version_signature(remote.get('title') or '')
            or normalize(local.get('album') or '') != normalize(album_response['title'])
            or normalize(local.get('album') or '') != normalize(remote.get('album') or '')):
        raise ValueError('Album-catalog row title version or exact album label changed')
    source_inputs = row.get('source_album_id_inputs')
    if not isinstance(source_inputs, list) or not source_inputs:
        raise ValueError('Album-catalog row lacks saved radio album-ID provenance')
    seen_sources = set()
    for source in source_inputs:
        if isinstance(source, dict) and source.get('source_kind') == 'observed_authenticated_album_first_page_v1':
            from observed_album_search_origin_v01 import validate
            validate(source, row['album_id'], album_response['title'])
            key = (source['response_path'], source['result_position'])
            if key in seen_sources:
                raise ValueError('Duplicate observed album discovery source')
            seen_sources.add(key)
            continue
        if isinstance(source, dict) and source.get('source_kind') == 'search_album':
            required = {'source_kind', 'evidence_path', 'evidence_sha256',
                        'response_path', 'response_sha256', 'source_key',
                        'arguments', 'result_position'}
            if (set(source) != required
                    or not all(isinstance(source.get(key), str)
                               for key in ('evidence_path', 'response_path', 'evidence_sha256',
                                           'response_sha256'))
                    or not SHA_RE.fullmatch(source['evidence_sha256'])
                    or not SHA_RE.fullmatch(source['response_sha256'])
                    or type(source.get('result_position')) is not int
                    or source['result_position'] < 0
                    or source.get('source_key') !=
                       'response[%d].browseId' % source['result_position']
                    or not isinstance(source.get('arguments'), dict)
                    or source['arguments'].get('filter') != 'albums'
                    or source['arguments'].get('limit') != 5
                    or not isinstance(source['arguments'].get('query'), str)):
                raise ValueError('Malformed saved album-search browse-ID provenance')
            evidence_path = _resolve_source(source['evidence_path'], sidecar_dir)
            search_response_path = _resolve_source(source['response_path'], sidecar_dir)
            try:
                evidence_bytes = evidence_path.read_bytes()
                search_response_bytes = search_response_path.read_bytes()
                if (_sha(evidence_bytes) != source['evidence_sha256']
                        or _sha(search_response_bytes) != source['response_sha256']):
                    raise ValueError('Saved album-search source hash changed')
                evidence_doc = json.loads(evidence_bytes)
                response_doc = json.loads(search_response_bytes)
                result = response_doc['response'][source['result_position']]
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
                raise ValueError('Saved album-search source is unreadable') from None
            if (not isinstance(evidence_doc, dict)
                    or evidence_doc.get('schema_version') != 1
                    or evidence_doc.get('producer') != 'opengenius-unindexed-album-search-evidence-v1'
                    or evidence_doc.get('library_sha256') != library_hash
                    or evidence_doc.get('identity_map_sha256') != identity_map_sha256
                    or response_doc.get('endpoint') != 'search'
                    or response_doc.get('arguments') != source['arguments']
                    or not isinstance(response_doc.get('observed_at'), str)
                    or not isinstance(result, dict)
                    or set(result) - {'browseId', 'title', 'artists', 'year'}
                    or result.get('browseId') != row['album_id']
                    or result.get('title') != album_response['title']):
                raise ValueError('Album browse ID was not read from a bound saved album-search result')
            evidence_rows = evidence_doc.get('search_album_candidates')
            matching_search = [candidate for candidate in evidence_rows or []
                if isinstance(candidate, dict)
                and candidate.get('source_kind') == 'search_album'
                and candidate.get('browse_id') == row['album_id']
                and candidate.get('album_title') == result.get('title')
                and candidate.get('source', {}).get('response_sha256') == source['response_sha256']
                and candidate.get('source', {}).get('result_position') == source['result_position']
                and candidate.get('source', {}).get('arguments') == source['arguments']]
            if len(matching_search) != 1:
                raise ValueError('Album-search browse ID is absent from bound candidate evidence')
            search_candidate = matching_search[0]
            local = next(track for track in tracks if track['persistent_id'] == row['persistent_id'])
            remote_artists = result.get('artists')
            if (not isinstance(remote_artists, list) or not remote_artists
                    or not all(isinstance(artist, dict)
                               and isinstance(artist.get('name'), str)
                               and artist['name'].strip() for artist in remote_artists)):
                raise ValueError('Album-search result has incomplete album artist credits')
            remote_credits = {credit for artist in remote_artists
                              for credit in matcher.artist_credits(artist['name'])}
            local_credits = matcher.artist_credits(local.get('artist') or '')
            declared_credits = search_candidate.get('canonical_artists')
            if (normalize(local.get('album') or '') != normalize(result.get('title') or '')
                    or not local_credits or remote_credits != local_credits
                    or sorted([list(credit) for credit in remote_credits]) != declared_credits
                    or row['persistent_id'] not in search_candidate.get('candidate_pids', [])):
                raise ValueError('Album-search artist/title evidence no longer uniquely matches the local track')
            source_identity = (str(search_response_path), source['response_sha256'], source['result_position'])
            if source_identity in seen_sources:
                raise ValueError('Duplicate saved album-search provenance')
            seen_sources.add(source_identity)
            continue
        if (not isinstance(source, dict)
                or set(source) != {'snapshot_path', 'snapshot_sha256', 'observation_index',
                                   'source_key', 'source_video_id'}
                or not isinstance(source.get('snapshot_path'), str)
                or not isinstance(source.get('snapshot_sha256'), str)
                or not SHA_RE.fullmatch(source['snapshot_sha256'])
                or type(source.get('observation_index')) is not int
                or source['observation_index'] < 0
                or source.get('source_key') !=
                   'observations[%d].track.album.id' % source.get('observation_index')):
            raise ValueError('Malformed saved radio album-ID provenance')
        source_path = _resolve_source(source['snapshot_path'], sidecar_dir)
        source_identity = (str(source_path), source['snapshot_sha256'], source['observation_index'])
        if source_identity in seen_sources:
            raise ValueError('Duplicate saved radio album-ID provenance')
        seen_sources.add(source_identity)
        try:
            source_bytes = source_path.read_bytes()
            if _sha(source_bytes) != source['snapshot_sha256']:
                raise ValueError('Saved radio album-ID source hash changed')
            source_snapshot = json.loads(source_bytes)
            validate_observations(source_snapshot, library_hash)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Saved radio album-ID source is unreadable') from None
        if (source_snapshot.get('library_sha256') != library_hash
                or source_snapshot.get('identity_map_sha256') != identity_map_sha256):
            raise ValueError('Saved radio album-ID source has foreign source hashes')
        try:
            observation = source_snapshot['observations'][source['observation_index']]
            source_album = observation['track']['album']
        except (KeyError, IndexError, TypeError):
            raise ValueError('Saved radio album-ID source key no longer exists') from None
        if (observation.get('source') != 'ytmusic' or observation.get('relation') != 'radio'
                or source_album.get('id') != row['album_id']
                or observation['track'].get('video_id') != source.get('source_video_id')):
            raise ValueError('Album browse ID was not read from the saved radio album field')
    return {'persistent_id': row['persistent_id'], 'video_id': row['video_id'],
            'input_path': str(catalog_response_path), 'input_sha256': row['input_sha256'],
            'source_relation': 'album_catalog_candidate', 'album_id': row['album_id']}


def validate_album_catalog_evidence(document, evidence_path, tracks, matcher,
                                    library_hash, identity_map_sha256):
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document.get('schema_version') != 1
            or document.get('producer') != CATALOG_PRODUCER
            or document.get('library_sha256') != library_hash
            or document.get('identity_map_sha256') != identity_map_sha256
            or document.get('source_plan_producer') != 'opengenius-saved-radio-album-plan-v1'
            or not SHA_RE.fullmatch(document.get('album_plan_sha256') or '')
            or not isinstance(document.get('rows'), list)
            or not isinstance(document.get('catalog_observations'), list)):
        raise ValueError('Album-catalog evidence schema or source hashes differ')
    audit = document.get('audit')
    if (not isinstance(audit, dict) or audit.get('recommendation_edges_created') != 0
            or audit.get('recommendation_observations_created') != 0):
        raise ValueError('Album-catalog evidence may not assert recommendation edges')
    sidecar_dir = Path(evidence_path).resolve().parent
    validated, pids, videos = [], set(), set()
    for row in document['rows']:
        output = _validate_catalog_seed_row(row, sidecar_dir, tracks, matcher,
                                            library_hash, identity_map_sha256)
        if output['persistent_id'] in pids or output['video_id'] in videos:
            raise ValueError('Duplicate PID or video ID in album-catalog evidence')
        pids.add(output['persistent_id'])
        videos.add(output['video_id'])
        validated.append(output)
    return validated


def combine_seed_video_evidence(radio_document, radio_evidence_path,
                                catalog_document, catalog_evidence_path,
                                tracks, matcher, library_hash, identity_map_sha256):
    """Combine strict radio and album catalog hints, rejecting PID/video conflicts."""
    radio = select_seed_video_evidence(
        radio_document, radio_evidence_path,
        [{'persistent_id': track['persistent_id']} for track in tracks],
        tracks, matcher, library_hash, identity_map_sha256)
    radio_rows = [row for row in radio_document['rows']]
    catalog_rows = validate_album_catalog_evidence(
        catalog_document, catalog_evidence_path, tracks, matcher,
        library_hash, identity_map_sha256)
    original_catalog_rows = {row['persistent_id']: row for row in catalog_document['rows']}
    by_pid = {row['persistent_id']: row for row in radio_rows}
    by_video = {row['video_id']: row['persistent_id'] for row in radio_rows}
    for row in catalog_rows:
        old = by_pid.get(row['persistent_id'])
        other_pid = by_video.get(row['video_id'])
        if old is not None and old['video_id'] != row['video_id']:
            raise ValueError('Radio and album catalog evidence conflict for one PID')
        if other_pid is not None and other_pid != row['persistent_id']:
            raise ValueError('Radio and album catalog evidence conflict for one video ID')
        if old is None:
            by_pid[row['persistent_id']] = original_catalog_rows[row['persistent_id']]
            by_video[row['video_id']] = row['persistent_id']
    combined = dict(radio_document)
    combined['rows'] = [by_pid[pid] for pid in sorted(by_pid)]
    combined['catalog_evidence'] = {
        'path': str(Path(catalog_evidence_path).resolve()),
        'sha256': _sha(Path(catalog_evidence_path).read_bytes()),
        'producer': CATALOG_PRODUCER,
    }
    combined['audit'] = dict(radio_document.get('audit') or {})
    combined['audit']['radio_seed_video_count'] = len(radio_rows)
    combined['audit']['album_catalog_seed_video_count'] = len(catalog_rows)
    combined['audit']['accepted_seed_video_count'] = len(combined['rows'])
    combined['audit']['edges_created'] = 0
    return combined


def build_combined_rebound_seed_video_evidence(rebound_evidence_path,
                                               current_radio_evidence_path,
                                               library_hash,
                                               identity_map_sha256):
    """Build a typed composite while preserving rebound and current proofs.

    The rebound evidence remains an external immutable receipt. Current-map
    radio evidence is embedded intact and separately bound to its source file.
    Rows are a convenience index only; the selector revalidates both typed
    producers and reconstructs this exact, conflict-filtered union.
    """
    rebound_path = Path(rebound_evidence_path).resolve()
    current_path = Path(current_radio_evidence_path).resolve()
    try:
        rebound_bytes = rebound_path.read_bytes()
        rebound = json.loads(rebound_bytes)
        current_bytes = current_path.read_bytes()
        current = json.loads(current_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Combined seed evidence source is unreadable') from None
    if (not isinstance(rebound, dict)
            or rebound.get('producer') != REBOUND_PRODUCER
            or type(rebound.get('schema_version')) is not int
            or rebound.get('schema_version') != 1
            or rebound.get('library_sha256') != library_hash
            or rebound.get('identity_map_sha256') != identity_map_sha256
            or not isinstance(rebound.get('rows'), list)):
        raise ValueError('Combined evidence requires typed rebound rows for this map')
    if (not isinstance(current, dict)
            or current.get('producer') != PRODUCER
            or type(current.get('schema_version')) is not int
            or current.get('schema_version') != 1
            or current.get('library_sha256') != library_hash
            or current.get('identity_map_sha256') != identity_map_sha256
            or not isinstance(current.get('rows'), list)):
        raise ValueError('Combined evidence requires current-map radio evidence')

    rebound_pids, rebound_videos = set(), set()
    for row in rebound['rows']:
        if (not isinstance(row, dict)
                or not isinstance(row.get('persistent_id'), str)
                or not isinstance(row.get('video_id'), str)):
            raise ValueError('Malformed rebound hint row in combined evidence')
        if (row['persistent_id'] in rebound_pids
                or row['video_id'] in rebound_videos):
            raise ValueError('Rebound evidence contains duplicate PID or video ID')
        rebound_pids.add(row['persistent_id'])
        rebound_videos.add(row['video_id'])

    current_rows = current['rows']
    current_pids, current_videos = set(), set()
    for row in current_rows:
        if (not isinstance(row, dict)
                or not isinstance(row.get('persistent_id'), str)
                or not isinstance(row.get('video_id'), str)):
            raise ValueError('Malformed current-radio hint row in combined evidence')
        if (row['persistent_id'] in current_pids
                or row['video_id'] in current_videos):
            raise ValueError('Current-radio evidence contains duplicate PID or video ID')
        current_pids.add(row['persistent_id'])
        current_videos.add(row['video_id'])

    filtered_current = [row for row in current_rows
                        if row['persistent_id'] not in rebound_pids
                        and row['video_id'] not in rebound_videos]
    return {
        'schema_version': 1,
        'producer': COMBINED_REBOUND_PRODUCER,
        'library_sha256': library_hash,
        'identity_map_sha256': identity_map_sha256,
        'rebound_evidence': {'path': str(rebound_path),
                             'sha256': _sha(rebound_bytes)},
        'current_radio_source': {'path': str(current_path),
                                 'sha256': _sha(current_bytes)},
        'current_radio_evidence': current,
        'rows': rebound['rows'] + filtered_current,
        'audit': {
            'rebound_hint_count': len(rebound['rows']),
            'current_radio_hint_count': len(filtered_current),
            'recommendation_edges_created': 0,
            'recommendation_observations_created': 0,
            'outgoing_roots_created': 0,
        },
    }


def _select_rebound_seed_video_evidence(document, evidence_path, seeds, tracks,
                                        matcher, library_hash, identity_map_sha256):
    if (not isinstance(document, dict) or document.get('producer') != REBOUND_PRODUCER
            or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1
            or document.get('library_sha256') != library_hash
            or document.get('identity_map_sha256') != identity_map_sha256):
        raise ValueError('Rebound seed-video evidence schema or current map hash differs')
    transition = document.get('typed_map_transition')
    if not isinstance(transition, dict):
        raise ValueError('Rebound evidence omits its typed map transition')
    old_map_path = transition.get('old_identity_map', {}).get('path')
    new_map_path = transition.get('new_identity_map', {}).get('path')
    track_snapshot = document.get('track_snapshot')
    if (not isinstance(track_snapshot, dict)
            or set(track_snapshot) != {'path', 'sha256'}
            or not isinstance(track_snapshot.get('path'), str)
            or not SHA_RE.fullmatch(track_snapshot.get('sha256') or '')):
        raise ValueError('Rebound evidence omits its bound Library snapshot')
    snapshot_path = Path(track_snapshot['path']).resolve()
    if _sha(snapshot_path.read_bytes()) != track_snapshot['sha256']:
        raise ValueError('Rebound Library snapshot changed')
    catalog = document.get('catalog_evidence')
    cached = document.get('cached_hint_evidence')
    for label, receipt in (('catalog', catalog), ('cached hints', cached)):
        if receipt is not None and (not isinstance(receipt, dict)
                or set(receipt) != {'path', 'sha256'}
                or not isinstance(receipt.get('path'), str)
                or not SHA_RE.fullmatch(receipt.get('sha256') or '')):
            raise ValueError('Malformed rebound ' + label + ' receipt')
    expected = build_rebound_seed_video_evidence(
        track_snapshot=snapshot_path, old_identity_map=old_map_path,
        current_identity_map=new_map_path, typed_transition=transition,
        catalog_evidence=None if catalog is None else catalog['path'],
        cached_hint_evidence=None if cached is None else cached['path'])
    if document != expected:
        recorded_verification = (document.get('typed_transition_verification')
                                 if isinstance(document, dict) else None)
        replayed_verification = expected.get('typed_transition_verification')
        if (not _typed_transition_verification_matches(
                recorded_verification, replayed_verification)):
            raise ValueError('Rebound seed-video evidence differs from its typed source replay')
        normalized_expected = dict(expected)
        normalized_expected['typed_transition_verification'] = recorded_verification
        if document != normalized_expected:
            raise ValueError('Rebound seed-video evidence differs from its typed source replay')
    requested = {seed.get('persistent_id') for seed in seeds}
    if None in requested:
        raise ValueError('Requested seed has no persistent ID')
    by_pid = {track.get('persistent_id'): track for track in tracks}
    selected = {}
    for row in expected['rows']:
        pid = row['persistent_id']
        if pid not in by_pid or pid in selected:
            raise ValueError('Rebound hint contains unknown or duplicate Library PID')
        if row['source_relation'] == 'rebound_radio_candidate':
            source = 'rebound_authenticated_radio_metadata_candidate_unverified'
        elif row['source_relation'] == 'rebound_album_catalog_candidate':
            source = 'rebound_saved_album_catalog_metadata_candidate_unverified'
        elif row['source_relation'] == 'rebound_cached_search_candidate':
            source = 'rebound_cached_search_metadata_candidate_unverified'
        else:
            raise ValueError('Unsupported rebound seed-video source relation')
        selected[pid] = {
            'video_id': row['video_id'], 'source': source,
            'source_relation': row['source_relation'],
            'input_path': row['input_path'], 'input_sha256': row['input_sha256'],
        }
    return {pid: selected[pid] for pid in requested if pid in selected}


def _select_combined_rebound_seed_video_evidence(document, evidence_path, seeds,
                                                 tracks, matcher, library_hash,
                                                 identity_map_sha256):
    required_keys = {'schema_version', 'producer', 'library_sha256',
                     'identity_map_sha256', 'rebound_evidence',
                     'current_radio_source', 'current_radio_evidence',
                     'rows', 'audit'}
    if (not isinstance(document, dict) or set(document) != required_keys
            or document.get('producer') != COMBINED_REBOUND_PRODUCER
            or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1
            or document.get('library_sha256') != library_hash
            or document.get('identity_map_sha256') != identity_map_sha256):
        raise ValueError('Combined rebound seed-video evidence schema or map hash differs')
    receipt = document.get('rebound_evidence')
    if (not isinstance(receipt, dict) or set(receipt) != {'path', 'sha256'}
            or not isinstance(receipt.get('path'), str)
            or not SHA_RE.fullmatch(receipt.get('sha256') or '')):
        raise ValueError('Combined evidence omits its rebound sidecar receipt')
    rebound_path = _resolve_source(receipt['path'], Path(evidence_path).resolve().parent)
    try:
        rebound_bytes = rebound_path.read_bytes()
        rebound_doc = json.loads(rebound_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Combined rebound source is unreadable') from None
    if _sha(rebound_bytes) != receipt['sha256']:
        raise ValueError('Combined rebound source changed')
    current_receipt = document.get('current_radio_source')
    if (not isinstance(current_receipt, dict)
            or set(current_receipt) != {'path', 'sha256'}
            or not isinstance(current_receipt.get('path'), str)
            or not SHA_RE.fullmatch(current_receipt.get('sha256') or '')):
        raise ValueError('Combined evidence omits its current-radio source receipt')
    current_path = _resolve_source(current_receipt['path'],
                                   Path(evidence_path).resolve().parent)
    try:
        current_bytes = current_path.read_bytes()
        current_source_doc = json.loads(current_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Combined current-radio source is unreadable') from None
    if (_sha(current_bytes) != current_receipt['sha256']
            or current_source_doc != document.get('current_radio_evidence')):
        raise ValueError('Combined current-radio source changed or differs from embedded evidence')
    radio_doc = document.get('current_radio_evidence')
    rebound_rows = rebound_doc.get('rows') if isinstance(rebound_doc, dict) else None
    radio_rows = radio_doc.get('rows') if isinstance(radio_doc, dict) else None
    if not isinstance(rebound_rows, list) or not isinstance(radio_rows, list):
        raise ValueError('Combined evidence lacks typed rebound and current-radio rows')
    requested = {seed.get('persistent_id') for seed in seeds}
    rebound_selected = _select_rebound_seed_video_evidence(
        rebound_doc, rebound_path, seeds, tracks, matcher, library_hash,
        identity_map_sha256)
    radio_selected = select_seed_video_evidence(
        radio_doc, current_path, seeds, tracks, matcher, library_hash,
        identity_map_sha256)
    external_pids = {row.get('persistent_id') for row in rebound_rows
                     if isinstance(row, dict)}
    external_videos = {row.get('video_id') for row in rebound_rows
                       if isinstance(row, dict)}
    filtered_radio_rows = [row for row in radio_rows
                           if row.get('persistent_id') not in external_pids
                           and row.get('video_id') not in external_videos]
    expected_rows = rebound_rows + filtered_radio_rows
    if document.get('rows') != expected_rows:
        raise ValueError('Combined rows differ from their rebound and current-radio source rows')
    audit = document.get('audit')
    if (not isinstance(audit, dict)
            or set(audit) != {'rebound_hint_count', 'current_radio_hint_count',
                              'recommendation_edges_created',
                              'recommendation_observations_created',
                              'outgoing_roots_created'}
            or audit.get('rebound_hint_count') != len(rebound_rows)
            or audit.get('current_radio_hint_count') != len(filtered_radio_rows)
            or audit.get('recommendation_edges_created') != 0
            or audit.get('recommendation_observations_created') != 0
            or audit.get('outgoing_roots_created') != 0):
        raise ValueError('Combined seed-video hints may not assert recommendations or roots')
    selected = dict(rebound_selected)
    for pid, hint in radio_selected.items():
        old = selected.get(pid)
        other_pid = next((other_pid for other_pid, row in selected.items()
                          if row['video_id'] == hint['video_id']), None)
        if old is not None and old['video_id'] != hint['video_id']:
            raise ValueError('Combined evidence has conflicting video IDs for one PID')
        if other_pid is not None and other_pid != pid:
            raise ValueError('Combined evidence maps one video ID to multiple PIDs')
        if old is None:
            selected[pid] = hint
    if None in requested:
        raise ValueError('Requested seed has no persistent ID')
    return {pid: selected[pid] for pid in requested if pid in selected}


def _select_seed_video_evidence_uncached(document, evidence_path, seeds, tracks, matcher,
                                         library_hash, identity_map_sha256):
    """Validate an evidence sidecar and return exact requested seed hints."""
    if isinstance(document, dict) and document.get('producer') == REBOUND_PRODUCER:
        return _select_rebound_seed_video_evidence(
            document, evidence_path, seeds, tracks, matcher, library_hash,
            identity_map_sha256)
    if isinstance(document, dict) and document.get('producer') == COMBINED_REBOUND_PRODUCER:
        return _select_combined_rebound_seed_video_evidence(
            document, evidence_path, seeds, tracks, matcher, library_hash,
            identity_map_sha256)
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1 or document.get('producer') != PRODUCER
            or document.get('library_sha256') != library_hash
            or document.get('identity_map_sha256') != identity_map_sha256
            or not SHA_RE.fullmatch(library_hash or '')
            or not SHA_RE.fullmatch(identity_map_sha256 or '')):
        raise ValueError('Seed-video evidence schema or source hashes differ')
    rows = document.get('rows')
    if not isinstance(rows, list):
        raise ValueError('Seed-video evidence requires rows')
    by_pid = {track.get('persistent_id'): track for track in tracks}
    if len(by_pid) != len(tracks):
        raise ValueError('Duplicate Library persistent IDs')
    requested = {seed.get('persistent_id') for seed in seeds}
    if None in requested:
        raise ValueError('Requested seed has no persistent ID')
    selected, seen_videos = {}, set()
    sidecar_dir = Path(evidence_path).resolve().parent
    catalog_by_pid = {}
    catalog_rows_in_doc = [row for row in rows if isinstance(row, dict)
                           and row.get('source_relation') == 'album_catalog_candidate']
    if catalog_rows_in_doc:
        source = document.get('catalog_evidence')
        if (not isinstance(source, dict)
                or source.get('producer') != CATALOG_PRODUCER
                or not isinstance(source.get('path'), str)
                or not isinstance(source.get('sha256'), str)
                or not SHA_RE.fullmatch(source['sha256'])):
            raise ValueError('Combined evidence lacks album-catalog source provenance')
        catalog_path = _resolve_source(source['path'], sidecar_dir)
        try:
            catalog_bytes = catalog_path.read_bytes()
            if _sha(catalog_bytes) != source['sha256']:
                raise ValueError('Album-catalog evidence source hash changed')
            catalog_doc = json.loads(catalog_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Album-catalog evidence source is unreadable') from None
        validated = validate_album_catalog_evidence(
            catalog_doc, catalog_path, tracks, matcher, library_hash, identity_map_sha256)
        original = {row['persistent_id']: row for row in catalog_doc['rows']}
        catalog_by_pid = {row['persistent_id']: row for row in validated}
        for row in catalog_rows_in_doc:
            if original.get(row.get('persistent_id')) != row:
                raise ValueError('Combined album-catalog row differs from its source evidence')
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Malformed seed-video evidence row')
        relation = row.get('source_relation')
        if relation == 'radio':
            if set(row) != ROW_KEYS:
                raise ValueError('Malformed radio seed-video evidence row')
        elif relation == 'album_catalog_candidate':
            if (set(row) != CATALOG_ROW_KEYS
                    or catalog_by_pid.get(row.get('persistent_id')) is None
                    or catalog_by_pid[row['persistent_id']]['video_id'] != row.get('video_id')):
                raise ValueError('Malformed album-catalog seed-video evidence row')
        else:
            raise ValueError('Unsupported seed-video evidence source relation')
        pid, video_id = row['persistent_id'], row['video_id']
        if (not isinstance(pid, str) or not re.fullmatch('[0-9A-F]{16}', pid)
                or pid not in by_pid or pid in selected
                or not isinstance(video_id, str) or not VIDEO_RE.fullmatch(video_id)
                or video_id in seen_videos
                or not isinstance(row.get('input_path'), str)
                or not isinstance(row.get('input_sha256'), str)
                or not SHA_RE.fullmatch(row['input_sha256'])):
            raise ValueError('Invalid or duplicate seed-video evidence row')
        source_path = Path(row['input_path'])
        if not source_path.is_absolute():
            source_path = sidecar_dir / source_path
        try:
            source_data = source_path.read_bytes()
            if _sha(source_data) != row['input_sha256']:
                raise ValueError('Seed-video source snapshot hash changed')
            snapshot = json.loads(source_data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Seed-video source snapshot is unreadable') from None
        if relation == 'radio':
            validate_observations(snapshot, library_hash)
            if (snapshot.get('library_sha256') != library_hash
                    or snapshot.get('identity_map_sha256') != identity_map_sha256):
                raise ValueError('Seed-video source snapshot has foreign source hashes')
            if not _candidate_for_video(snapshot, video_id, pid, matcher):
                raise ValueError('Seed-video evidence no longer has one complete-credit duration match')
            source_name = 'saved_recommendation_metadata_candidate_unverified'
        else:
            validate_source = catalog_by_pid.get(pid)
            if (validate_source is None or validate_source['video_id'] != video_id
                    or Path(validate_source['input_path']).resolve() != source_path.resolve()
                    or validate_source['input_sha256'] != row['input_sha256']):
                raise ValueError('Album-catalog seed evidence no longer validates')
            source_name = 'saved_album_catalog_metadata_candidate_unverified'
        selected[pid] = {
            'video_id': video_id, 'source': source_name,
            'source_relation': relation,
            'input_path': str(source_path.resolve()), 'input_sha256': row['input_sha256'],
        }
        seen_videos.add(video_id)
    return {pid: selected[pid] for pid in requested if pid in selected}


def select_seed_video_evidence(document, evidence_path, seeds, tracks, matcher,
                               library_hash, identity_map_sha256):
    """Validate once per invocation context, then filter cached all-PID hints.

    With no explicit scope, behavior is the original uncached selector. A cache
    miss still performs the complete validator over every evidence row. Cache
    hits rehash the entire observed filesystem closure before returning.
    """
    cache = _ACTIVE_SELECTOR_INDEX.get()
    if cache is None or cache['building']:
        return _select_seed_video_evidence_uncached(
            document, evidence_path, seeds, tracks, matcher, library_hash,
            identity_map_sha256)
    cache['stats']['calls'] += 1
    requested = {seed.get('persistent_id') for seed in seeds}
    if None in requested:
        raise ValueError('Requested seed has no persistent ID')
    context = _selector_context_digest(document, evidence_path, tracks, matcher,
                                       library_hash, identity_map_sha256)
    if context is None:
        return _select_seed_video_evidence_uncached(
            document, evidence_path, seeds, tracks, matcher, library_hash,
            identity_map_sha256)
    cached = cache['entries'].get(context)
    if cached is not None:
        if not _verify_selector_closure(cached['closure']):
            cache['entries'].pop(context, None)
            raise ValueError('A cached seed-video evidence source changed')
        cache['entries'].move_to_end(context)
        cache['stats']['hits'] += 1
        return {pid: copy.deepcopy(cached['selected'][pid])
                for pid in requested if pid in cached['selected']}

    cache['stats']['misses'] += 1
    trace = {'files': {}, 'states': {}, 'listings': {}, 'resolutions': {},
             'unstable': False, 'unknown_io': False, 'unknown_sources': []}
    cache['building'] = True
    with _SELECTOR_IO_LOCK:
        trace_token = _ACTIVE_SELECTOR_TRACE.set(trace)
        try:
            _install_selector_io_trace()
            # The top-level document is an input even when the caller already
            # supplied its decoded in-memory value.
            Path(evidence_path).read_bytes()
            all_seeds = [{'persistent_id': row.get('persistent_id')}
                         for row in tracks]
            all_selected = _select_seed_video_evidence_uncached(
                document, evidence_path, all_seeds, tracks, matcher,
                library_hash, identity_map_sha256)
            after_context = _selector_context_digest(
                document, evidence_path, tracks, matcher,
                library_hash, identity_map_sha256)
            closure = _selector_closure(trace)
            if trace['unstable'] or after_context != context:
                raise ValueError('Seed-video evidence sources changed during full validation')
            # A completed full replay remains authoritative for this call when
            # it encounters unclassified filesystem plumbing (for example,
            # temporary verifier scratch files). Such a replay cannot be
            # memoized because its dependency closure is incomplete, so do not
            # try to seal/reuse that closure. The next call must replay fully.
            if (not trace['unknown_io']
                    and not _verify_selector_closure(closure)):
                raise ValueError('Seed-video evidence sources changed during full validation')
        finally:
            _ACTIVE_SELECTOR_TRACE.reset(trace_token)
            _restore_selector_io_trace()
            cache['building'] = False
    cache['stats']['full_replays'] += 1
    cache['stats']['closure_files'] += len(closure['files'])
    cache['stats']['closure_directories'] += len(closure['listings'])
    if trace['unknown_io']:
        cache['stats']['uncacheable_replays'] += 1
        return {pid: copy.deepcopy(all_selected[pid])
                for pid in requested if pid in all_selected}
    cache['entries'][context] = {'selected': copy.deepcopy(all_selected),
                                 'closure': closure}
    cache['entries'].move_to_end(context)
    while len(cache['entries']) > _SELECTOR_CACHE_LIMIT:
        cache['entries'].popitem(last=False)
        cache['stats']['evictions'] += 1
    return {pid: copy.deepcopy(all_selected[pid])
            for pid in requested if pid in all_selected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, action='append', default=[])
    parser.add_argument('--snapshot-directory', type=Path, action='append', default=[])
    parser.add_argument('--graph-provenance', type=Path, action='append', default=[],
                        help='Saved raw graph list; extracts each graph input.path')
    parser.add_argument('--catalog-evidence', type=Path,
                        help='Strict saved album-catalog candidate evidence to combine with radio hints')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('Output exists; choose a new path')
    tracks, library_hash, _ = load_library(track_snapshot=args.track_snapshot)
    map_bytes = args.identity_map.read_bytes()
    map_hash = _sha(map_bytes)
    matcher = IdentityMap(json.loads(map_bytes), tracks, library_hash)
    paths = [path.resolve() for path in args.snapshot]
    for directory in args.snapshot_directory:
        paths.extend(sorted(path.resolve() for path in directory.glob('*.json')))
    for graph_file in args.graph_provenance:
        graphs = json.loads(graph_file.read_text(encoding='utf-8'))
        if not isinstance(graphs, list):
            parser.error('--graph-provenance must contain a list of raw graphs')
        for graph in graphs:
            source = graph.get('input') if isinstance(graph, dict) else None
            if not isinstance(source, dict) or not isinstance(source.get('path'), str):
                parser.error('Each raw graph needs input.path provenance')
            source_path = Path(source['path'])
            if not source_path.is_absolute():
                source_path = Path(__file__).resolve().parents[1] / source_path
            paths.append({'input': {**source, 'path': str(source_path.resolve())}})
    if not paths and not args.catalog_evidence:
        parser.error('Supply at least one --snapshot or --snapshot-directory, or --catalog-evidence')
    document = build_seed_video_evidence(paths, matcher, library_hash, map_hash)
    document['identity_map_input_sha256'] = map_hash
    if args.catalog_evidence:
        catalog_bytes = args.catalog_evidence.read_bytes()
        catalog_document = json.loads(catalog_bytes)
        document = combine_seed_video_evidence(
            document, output, catalog_document, args.catalog_evidence,
            tracks, matcher, library_hash, map_hash)
        document['identity_map_input_sha256'] = map_hash
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output),
                      'accepted_seed_video_count': len(document['rows']),
                      'skipped_snapshot_count': document['audit']['skipped_snapshot_count'],
                      'edges_created': 0}))


if __name__ == '__main__':
    main()
