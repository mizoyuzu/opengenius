#!/usr/bin/env python3
"""Export observed local playlists to M3U8 without copying or changing audio.

Music Library BOMA 11 file URLs are relocated to an explicitly supplied media
root. Ambiguous filenames are omitted and reported, never guessed.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import struct
import unicodedata
from urllib.parse import unquote, urlsplit

from inspect_music_library import decode_musicdb, parse_tracks, u32

PID = re.compile(r'[0-9A-F]{16}\Z')


def parse_track_locations(expanded):
    """Read observed BOMA 11 UTF-8 URLs within validated track boundaries."""
    tracks, _ = parse_tracks(expanded)
    locations = {}
    start = expanded.find(b'ltma')
    offset = start + u32(expanded, start + 4)
    for track in tracks:
        end = offset + u32(expanded, offset + 8)
        child = offset + u32(expanded, offset + 4)
        urls = []
        for _ in range(u32(expanded, offset + 12)):
            header, size, kind = (u32(expanded, child + n) for n in (4, 8, 12))
            body = child + header
            if kind == 11:
                if body + 16 > child + size or u32(expanded, body) != 2:
                    raise ValueError('unsupported BOMA 11 URL encoding')
                length = u32(expanded, body + 4)
                if body + 16 + length != child + size:
                    raise ValueError('invalid BOMA 11 URL length')
                url = expanded[body + 16:body + 16 + length].decode('utf-8')
                if '\x00' in url:
                    raise ValueError('invalid BOMA 11 URL')
                urls.append(url)
            child += size
        if child != end:
            raise ValueError('track child boundaries do not cover record')
        locations[track['persistent_id']] = list(dict.fromkeys(urls))
        offset = end
    return tracks, locations


def normalized(value):
    return unicodedata.normalize('NFC', value)


def media_index(media_root):
    """Read filenames only; symlinks and files outside the media root are excluded."""
    root = Path(media_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('media root must be a directory')
    relative, names = defaultdict(list), defaultdict(list)
    for path in root.rglob('*'):
        if not path.is_file() or path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
            continue
        actual = path.resolve()
        if not actual.is_relative_to(root):
            continue
        key = tuple(normalized(p) for p in path.relative_to(root).parts)
        relative[key].append(actual)
        names[normalized(path.name)].append(actual)
    return root, relative, names


def resolve_location(urls, index):
    root, relative, names = index
    matches = set()
    methods = set()
    ambiguous = set()
    valid_url = False
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost') or parsed.query or parsed.fragment:
            continue
        path = Path(unquote(parsed.path))
        if not path.is_absolute() or any(part == '..' for part in path.parts):
            continue
        valid_url = True
        parts = path.parts
        candidate_keys = []
        for marker in ('Media.localized', 'Media'):
            for i, part in enumerate(parts):
                if part == marker and i + 1 < len(parts):
                    suffix = parts[i + 1:]
                    candidate_keys.append(tuple(normalized(p) for p in suffix))
                    if root.name == 'Music' and suffix[0] == 'Music':
                        candidate_keys.append(tuple(normalized(p) for p in suffix[1:]))
        exact = set(p for key in candidate_keys for p in relative.get(key, []))
        if exact:
            matches.update(exact)
            methods.add('relocated_media_relative_path')
            continue
        # A file URL already referring to this mount is safe to use as-is.
        if path.is_relative_to(root):
            direct = set(relative.get(tuple(normalized(p) for p in path.relative_to(root).parts), []))
            if direct:
                matches.update(direct)
                methods.add('current_media_absolute_path')
                continue
        fallback = set(names.get(normalized(path.name), []))
        if len(fallback) == 1:
            matches.update(fallback)
            methods.add('unique_original_filename')
        elif len(fallback) > 1:
            ambiguous.update(fallback)
    if len(matches) == 1 and not ambiguous:
        result = next(iter(matches))
        if '\n' in str(result) or '\r' in str(result):
            return {'status': 'unsupported_newline_path'}
        return {'status': 'resolved', 'path': str(result), 'methods': sorted(methods)}
    if len(matches) > 1 or ambiguous:
        return {'status': 'ambiguous', 'candidates': sorted(str(p) for p in matches | ambiguous)}
    return {'status': 'missing' if valid_url else 'missing_supported_file_url'}


def validate_report(report, known):
    if not isinstance(report, dict) or not isinstance(report.get('results'), list):
        raise ValueError('report must contain results')
    roots = set()
    for result in report['results']:
        if not isinstance(result, dict) or not isinstance(result.get('root_pid'), str) or not PID.fullmatch(result['root_pid']) or result['root_pid'] in roots:
            raise ValueError('invalid or duplicate playlist root PID')
        roots.add(result['root_pid'])
        if result['root_pid'] not in known or not isinstance(result.get('playlist'), list):
            raise ValueError('playlist root is not in source Library')
        for track in result['playlist']:
            if not isinstance(track, dict) or not isinstance(track.get('persistent_id'), str) or track['persistent_id'] not in known:
                raise ValueError('playlist PID is not in source Library')
            if any(track.get(key) is not None and not isinstance(track[key], str) for key in ('title', 'artist')):
                raise ValueError('invalid playlist text metadata')


def clean_label(value):
    return str(value or '').replace('\r', ' ').replace('\n', ' ')


def export_playlists(bundle, media_root, report, output_directory):
    bundle = Path(bundle).resolve(strict=True)
    output = Path(output_directory).resolve()
    media_root = Path(media_root).resolve(strict=True)
    if (output.exists() or output.is_relative_to(bundle) or output.is_relative_to(media_root)
            or bundle.is_relative_to(output) or media_root.is_relative_to(output)):
        raise ValueError('output must be new and outside source Library and media')
    raw = (bundle / 'Library.musicdb').read_bytes()
    declared_hash = report.get('source_library_sha256', report.get('library_sha256')) if isinstance(report,dict) else None
    if declared_hash is not None and declared_hash != hashlib.sha256(raw).hexdigest():
        raise ValueError('playlist report belongs to a different source Library')
    tracks, locations = parse_track_locations(decode_musicdb(raw))
    known = {track['persistent_id']: track for track in tracks}
    validate_report(report, known)
    index = media_index(media_root)
    resolution = {pid: resolve_location(locations[pid], index)
                  for pid in {t['persistent_id'] for r in report['results'] for t in r['playlist']}}
    exported = {'schema_version': 1, 'source_library_sha256': hashlib.sha256(raw).hexdigest(),
                'media_root': str(media_root), 'audio_contents_read': False, 'network_requests': 0, 'results': []}
    documents = []
    for result in report['results']:
        entries = []
        lines = ['#EXTM3U']
        for track in result['playlist']:
            pid = track['persistent_id']
            found = resolution[pid]
            entries.append({'persistent_id': pid, **found})
            if found['status'] == 'resolved':
                local = known[pid]
                seconds = round(local.get('duration_ms', 0) / 1000) if local.get('duration_ms') is not None else -1
                artist = clean_label(local.get('artist'))
                title = clean_label(local.get('title'))
                lines.extend([f'#EXTINF:{seconds},{artist + " - " if artist else ""}{title}', found['path']])
        count = sum(e['status'] == 'resolved' for e in entries)
        filename = result['root_pid'] + '.m3u8'
        exported['results'].append({'root_pid': result['root_pid'], 'title': known[result['root_pid']].get('title'), 'm3u8': filename, 'requested_count': len(entries),
                                    'exported_count': count, 'partial': count != len(entries), 'entries': entries})
        documents.append((filename, '\n'.join(lines) + '\n'))
    output.mkdir(parents=True, exist_ok=False)
    for filename, text in documents:
        with (output / filename).open('x', encoding='utf-8') as handle:
            handle.write(text)
    with (output / 'export-report.json').open('x', encoding='utf-8') as handle:
        json.dump(exported, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    index_lines = ['# プレイリスト', '']
    index_lines += [f"- [{clean_label(r['title']) or r['root_pid']}]({r['m3u8']})（{r['exported_count']}曲）" for r in exported['results']]
    (output / 'README.md').write_text('\n'.join(index_lines) + '\n')
    return exported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--media-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output-directory', type=Path, required=True)
    args = parser.parse_args()
    report_path = args.report.resolve()
    output = args.output_directory.resolve()
    if report_path == output or report_path.is_relative_to(output):
        parser.error('output overlaps input report')
    try:
        result = export_playlists(args.bundle, args.media_root, json.loads(report_path.read_text()), output)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{type(error).__name__}: {error}\n')
    print(json.dumps({'playlists': len(result['results']), 'partial_playlists': sum(r['partial'] for r in result['results'])}))


if __name__ == '__main__':
    main()
