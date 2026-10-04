"""Compare two sanitized iPod audit reports without exposing stored IDs."""
import argparse
import json
import os
from pathlib import Path
import re
import stat

from inspect_ipod_genius import KNOWN_FILES, KNOWN_TABLES, MAX_BYTES

MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_REPORTED_FILES = 256
MAX_SAFE_COUNT = (1 << 53) - 1
KNOWN_FOLDERS = ('iTunes Library.itlp', 'iTunes Library')
KNOWN_REPORT_FILES = {'input_file', *KNOWN_FILES}
KNOWN_REPORT_FILES.update(folder + '/' + name for folder in KNOWN_FOLDERS for name in KNOWN_FILES)
KNOWN_FORMATS = {'itunes_db', 'itunes_cdb', 'sqlite', 'possible_music_encrypted_sqlite', 'unknown'}
KNOWN_STATUSES = {
    'structure_valid', 'invalid_header', 'invalid_bounds', 'unsupported_compressed',
    'section_limit', 'invalid_section_header', 'invalid_section_bounds',
    'invalid_nested_records', 'sqlite_readable', 'sqlite_invalid_or_unsupported',
    'unsupported_encryption', 'unsupported', 'unreadable', 'symlink_rejected',
    'not_regular_file', 'size_limit', 'input_missing',
}

ITUNESDB_METRICS = (
    'track_records', 'track_ids_unique', 'track_ids_duplicate_records',
    'dbid_nonzero_records', 'dbid_unique_values', 'dbid_duplicate_records',
    'dbid2_available_records', 'dbid2_nonzero_records', 'dbid2_unique_values',
    'dbid2_duplicate_records', 'dbid_equal_dbid2_nonzero_records',
    'playlists', 'playlist_members', 'playlist_unique_track_ids_referenced',
    'dangling_playlist_members',
)
RELATION_METRICS = ('examined_edges', 'dangling_edges', 'seeds_missing_metadata', 'unsupported_rows')


def _safe_count(value):
    return value if type(value) is int and 0 <= value <= MAX_SAFE_COUNT else None


def _nested_record_is_validated(section):
    if not isinstance(section, dict):
        return False
    nested = section.get('nested_records')
    return isinstance(nested, dict) and nested.get('status') == 'validated'


def _normalized_profile(entry):
    fmt, status = entry['format'], entry['status']
    if fmt == 'itunes_db' and status == 'structure_valid':
        version = _safe_count(entry.get('database_version'))
        header_bytes = _safe_count(entry.get('header_bytes'))
        sections = entry.get('sections')
        if version is None or header_bytes is None or not isinstance(sections, list) or len(sections) > 1024:
            return None
        signature = []
        cuid_present = cuid_expected32 = cuid_bytes = 0
        for section in sections:
            if not isinstance(section, dict):
                return None
            kind = _safe_count(section.get('type'))
            header = _safe_count(section.get('header_bytes'))
            if kind is None or header is None:
                return None
            signature.append(dict(type=kind, header_bytes=header))
            if kind == 9:
                present = section.get('cuid_present')
                expected = section.get('expected_cuid_length')
                size = _safe_count(section.get('cuid_bytes'))
                if type(present) is not bool or type(expected) is not bool or size is None:
                    return None
                cuid_present += present
                cuid_expected32 += expected
                cuid_bytes += size
        return dict(kind='itunes_db', header_bytes=header_bytes,
                    database_version=version, sections=signature), dict(
            cuid_sections=sum(section['type'] == 9 for section in signature),
            cuid_present_sections=cuid_present,
            cuid_expected_32byte_sections=cuid_expected32,
            cuid_total_bytes=cuid_bytes,
        )
    if fmt == 'sqlite' and status == 'sqlite_readable':
        user_version = _safe_count(entry.get('user_version'))
        schema_version = _safe_count(entry.get('schema_version'))
        table_count = _safe_count(entry.get('table_count'))
        unknown_table_count = _safe_count(entry.get('unknown_table_count'))
        tables = entry.get('tables')
        if any(value is None for value in (user_version, schema_version, table_count, unknown_table_count)):
            return None
        if not isinstance(tables, dict) or len(tables) > len(KNOWN_TABLES):
            return None
        table_counts = {}
        for name, value in tables.items():
            if name in KNOWN_TABLES:
                count = _safe_count(value)
                if count is None:
                    return None
                table_counts[name] = count
        return dict(kind='sqlite', user_version=user_version, schema_version=schema_version,
                    table_count=table_count, unknown_table_count=unknown_table_count,
                    known_tables=sorted(table_counts)), table_counts
    return None


def _normalized_file(raw):
    if not isinstance(raw, dict):
        return dict(valid=False)
    fmt = raw.get('format')
    status = raw.get('status')
    fmt = fmt if isinstance(fmt, str) and fmt in KNOWN_FORMATS else 'other'
    status = status if isinstance(status, str) and status in KNOWN_STATUSES else 'other'
    size = _safe_count(raw.get('bytes'))
    if size is not None and size > MAX_BYTES:
        size = None
    digest = raw.get('sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest):
        digest = None
    entry = dict(valid=True, format=fmt, status=status, bytes=size, sha256=digest.lower() if digest else None,
                 profile=None, cuid=None, itunesdb_metrics=None, sqlite_tables=None, relations=None)
    profile = _normalized_profile({**raw, 'format': fmt, 'status': status})
    if profile is not None:
        entry['profile'], detail = profile
        if fmt == 'itunes_db':
            entry['cuid'] = detail
            raw_sections = raw.get('sections')
            fully_nested = (raw.get('nested_records_validated') is True
                            and raw.get('nested_coverage') == 'libgpod_track_and_playlist_records'
                            and isinstance(raw_sections, list)
                            and any(isinstance(section, dict) and section.get('type') in (1, 2, 3)
                                    for section in raw_sections)
                            and all(_nested_record_is_validated(section)
                                    for section in raw_sections
                                    if isinstance(section, dict) and section.get('type') in (1, 2, 3))
                            and all(isinstance(section, dict) for section in raw_sections))
            if fully_nested:
                metrics = {name: _safe_count(raw.get(name)) for name in ITUNESDB_METRICS}
                if all(value is not None for value in metrics.values()):
                    entry['itunesdb_metrics'] = metrics
        elif fmt == 'sqlite':
            entry['sqlite_tables'] = detail
            relations = raw.get('relations')
            if (isinstance(relations, dict) and relations.get('complete') is True
                    and relations.get('ipod_profile_verified') is False
                    and relations.get('envelope_profile') == 'observed_music_v1_uncompressed'):
                normalized = {name: _safe_count(relations.get(name)) for name in RELATION_METRICS}
                if all(value is not None for value in normalized.values()):
                    entry['relations'] = normalized
    return entry


def normalize_report(report):
    max_file_bytes = _safe_count(report.get('max_file_bytes')) if isinstance(report, dict) else None
    if (not isinstance(report, dict) or type(report.get('report_version')) is not int
            or report.get('report_version') != 1
            or report.get('read_only') is not True
            or report.get('ipod_acceptance_verified') is not False
            or max_file_bytes != MAX_BYTES
            or not isinstance(report.get('files'), dict)
            or len(report['files']) > MAX_REPORTED_FILES):
        raise ValueError('invalid_report')
    files = {}
    unknown_count = 0
    for name, raw in report['files'].items():
        if not isinstance(name, str) or name not in KNOWN_REPORT_FILES:
            unknown_count += 1
            continue
        files[name] = _normalized_file(raw)
    return dict(files=files, unknown_file_count=unknown_count, max_file_bytes=max_file_bytes)


def _metric(before, after):
    if before is None or after is None:
        return dict(status='not_comparable')
    return dict(status='unchanged' if before == after else 'changed', before=before,
                after=after, delta=after - before)


def _profile_comparison(before, after):
    if before is None or after is None:
        return dict(status='not_comparable')
    status = 'unchanged' if before == after else 'drift'
    return dict(status=status, before=before, after=after)


def _compare_metric_sets(before, after, names):
    return {name: _metric(before.get(name), after.get(name)) for name in names}


def _file_comparison(before, after):
    if before is None and after is None:
        return None
    presence = ('added' if before is None else 'removed') if before is None or after is None else 'present_both'
    hash_changed = None
    if before is not None and after is not None and before.get('sha256') and after.get('sha256'):
        hash_changed = before['sha256'] != after['sha256']
    size_delta = None
    if before is not None and after is not None:
        a, b = before.get('bytes'), after.get('bytes')
        if a is not None and b is not None:
            size_delta = b - a

    before_profile = before.get('profile') if before else None
    after_profile = after.get('profile') if after else None
    profile = _profile_comparison(before_profile, after_profile)
    record = dict(presence=presence,
                  format_before=before['format'] if before else None,
                  format_after=after['format'] if after else None,
                  status_before=before['status'] if before else None,
                  status_after=after['status'] if after else None,
                  content_hash_changed=hash_changed, size_delta_bytes=size_delta,
                  profile=profile,
                  cuid=dict(status='not_comparable') if not before or not after
                  else _compare_metric_sets(before.get('cuid') or {}, after.get('cuid') or {},
                                            ('cuid_sections', 'cuid_present_sections',
                                             'cuid_expected_32byte_sections', 'cuid_total_bytes')))

    has_itunesdb = any(item and item.get('format') == 'itunes_db' for item in (before, after))
    if before and after and before.get('itunesdb_metrics') and after.get('itunesdb_metrics'):
        record['itunesdb_aggregates'] = _compare_metric_sets(
            before['itunesdb_metrics'], after['itunesdb_metrics'], ITUNESDB_METRICS)
    else:
        record['itunesdb_aggregates'] = dict(status='not_comparable' if has_itunesdb else 'not_applicable')

    has_sqlite = any(item and item.get('format') == 'sqlite' for item in (before, after))
    if before and after and before.get('sqlite_tables') is not None and after.get('sqlite_tables') is not None:
        tables = {}
        for name in KNOWN_TABLES:
            a = before['sqlite_tables'].get(name, 0)
            b = after['sqlite_tables'].get(name, 0)
            if name in before['sqlite_tables'] or name in after['sqlite_tables']:
                tables[name] = _metric(a, b)
        record['sqlite_table_counts'] = tables
        if before.get('relations') is not None and after.get('relations') is not None:
            record['sqlite_relation_envelope'] = dict(
                interpretation='observed_music_v1_only',
                metrics=_compare_metric_sets(before['relations'], after['relations'], RELATION_METRICS))
        else:
            record['sqlite_relation_envelope'] = dict(status='not_comparable',
                                                      interpretation='observed_music_v1_only')
    else:
        category_status = 'not_comparable' if has_sqlite else 'not_applicable'
        record['sqlite_table_counts'] = dict(status=category_status)
        record['sqlite_relation_envelope'] = dict(status=category_status,
                                                  interpretation='observed_music_v1_only')
    return record


def compare_reports(before_report, after_report):
    before = normalize_report(before_report)
    after = normalize_report(after_report)
    names = sorted(set(before['files']) | set(after['files']))
    files = {name: _file_comparison(before['files'].get(name), after['files'].get(name))
             for name in names}
    files = {name: item for name, item in files.items() if item is not None}
    added = sorted(name for name, item in files.items() if item['presence'] == 'added')
    removed = sorted(name for name, item in files.items() if item['presence'] == 'removed')

    profile_statuses = [item['profile']['status'] for item in files.values()]
    if 'drift' in profile_statuses:
        overall_profile = 'drift'
    elif profile_statuses and all(status == 'unchanged' for status in profile_statuses):
        overall_profile = 'same'
    else:
        overall_profile = 'not_comparable'
    aggregate_incomplete = any(
        item.get('itunesdb_aggregates', {}).get('status') == 'not_comparable'
        or item.get('sqlite_table_counts', {}).get('status') == 'not_comparable'
        or item.get('sqlite_relation_envelope', {}).get('status') == 'not_comparable'
        for item in files.values())
    partial = bool(added or removed or overall_profile == 'not_comparable'
                   or 'not_comparable' in profile_statuses or aggregate_incomplete
                   or before['unknown_file_count'] or after['unknown_file_count'])
    return dict(report_version=1, comparison='sanitized_read_only_snapshot_delta',
                same_db_profile=overall_profile, comparison_partial=partial,
                input_report_limits=dict(before_max_file_bytes=before['max_file_bytes'],
                                         after_max_file_bytes=after['max_file_bytes'],
                                         same=before['max_file_bytes'] == after['max_file_bytes']),
                files_added_after=added, files_missing_after=removed,
                unknown_file_entries=dict(before=before['unknown_file_count'],
                                          after=after['unknown_file_count']),
                files=files,
                interpretation='File/hash/count changes are observations only. They do not prove Genius generation, relation validity, or stock-device acceptance.',
                genius_generation_proven=False, device_acceptance_proven=False)


def read_report(path):
    path = Path(os.path.abspath(path))
    if path.is_symlink():
        raise ValueError('invalid_report')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('invalid_report')
        payload = source.read(MAX_REPORT_BYTES + 1)
    if len(payload) > MAX_REPORT_BYTES:
        raise ValueError('report_size_limit')
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('invalid_report') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--output', type=Path, help='create a new JSON report file; existing paths are refused')
    args = parser.parse_args()
    try:
        before_path = args.before.resolve(strict=True)
        after_path = args.after.resolve(strict=True)
        if args.output:
            output_path = args.output.absolute()
            if output_path.exists() or any(part.is_symlink() for part in (output_path, *output_path.parents)):
                raise ValueError('output_must_be_new')
            if output_path.resolve() in (before_path, after_path):
                raise ValueError('output_must_be_new')
        report = compare_reports(read_report(before_path), read_report(after_path))
        encoded = json.dumps(report, indent=2) + '\n'
        if args.output:
            with args.output.open('x') as destination:
                destination.write(encoded)
        else:
            print(encoded, end='')
    except (ValueError, OSError):
        parser.exit(2, 'Snapshot reports rejected; no source report changed.\n')


if __name__ == '__main__':
    main()
