"""Relocate stored file URLs conservatively and export partial M3U8 playlists."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from export_real_playlists import export_playlists, media_index, parse_track_locations, resolve_location


def expanded_library(urls):
    records = []
    for index, url in enumerate(urls, 1):
        encoded = url.encode()
        body = struct.pack('<4I', 2, len(encoded), 0, 0) + encoded
        child = struct.pack('<5I', int.from_bytes(b'boma', 'little'), 20, 20 + len(body), 11, 0) + body
        record = bytearray(376)
        struct.pack_into('<4IQ', record, 0, int.from_bytes(b'itma', 'little'), 376, 376 + len(child), 1, index)
        records.append(bytes(record) + child)
    return struct.pack('<3I', int.from_bytes(b'ltma', 'little'), 12, len(records)) + b''.join(records)


def playlist_report():
    return {'results': [{'root_pid': '0000000000000001', 'playlist': [
        {'persistent_id': '0000000000000001', 'title': '曲一', 'artist': '歌手'},
        {'persistent_id': '0000000000000002', 'title': '曲二', 'artist': '歌手'},
    ]}]}


class PlaylistExportTests(unittest.TestCase):
    def test_foreign_library_report_rejected_before_export(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bundle, media = base / 'Library.musiclibrary', base / 'media'
            bundle.mkdir(); media.mkdir()
            (bundle / 'Library.musicdb').write_bytes(b'original')
            report = {'source_library_sha256': '0' * 64, 'results': []}
            output = base / 'out'
            with self.assertRaisesRegex(ValueError, 'different source'):
                export_playlists(bundle, media, report, output)
            self.assertFalse(output.exists())

    def test_observed_utf8_boma11_location_and_bad_length(self):
        url = 'file:///Volumes/Old/Media.localized/Music/' + quote('歌手/アルバム/曲.m4a')
        data = expanded_library([url])
        tracks, locations = parse_track_locations(data)
        self.assertEqual(tracks[0]['persistent_id'], '0000000000000001')
        self.assertEqual(locations['0000000000000001'], [url])
        corrupted = bytearray(data)
        struct.pack_into('<I', corrupted, 12 + 376 + 20 + 4, 1)
        with self.assertRaisesRegex(ValueError, 'URL length'):
            parse_track_locations(corrupted)

    def test_relative_media_path_wins_over_same_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory)
            target = media / 'Music/歌手/アルバム/曲.m4a'
            target.parent.mkdir(parents=True)
            target.touch()
            other = media / 'Other/曲.m4a'
            other.parent.mkdir()
            other.touch()
            url = 'file:///Volumes/Old/Media.localized/' + quote('Music/歌手/アルバム/曲.m4a')
            found = resolve_location([url], media_index(media))
            self.assertEqual(found['path'], str(target))
            self.assertEqual(found['methods'], ['relocated_media_relative_path'])

    def test_duplicate_filename_missing_and_external_symlink_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            media = base / 'media'
            for sub in ('a', 'b'):
                (media / sub).mkdir(parents=True)
                (media / sub / 'same.m4a').touch()
            outside = base / 'outside.m4a'
            outside.touch()
            (media / 'linked.m4a').symlink_to(outside)
            index = media_index(media)
            self.assertEqual(resolve_location(['file:///old/same.m4a'], index)['status'], 'ambiguous')
            self.assertEqual(resolve_location(['file:///old/missing.m4a'], index)['status'], 'missing')
            self.assertEqual(resolve_location(['file:///old/linked.m4a'], index)['status'], 'missing')
            self.assertEqual(resolve_location(['https://example.com/song'], index)['status'], 'missing_supported_file_url')

    def test_export_is_unicode_partial_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bundle = base / 'Library.musiclibrary'
            bundle.mkdir()
            media = base / 'Media.localized'
            target = media / 'Music/歌手/アルバム/曲一.m4a'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'not read as audio')
            urls = ['file:///Volumes/Old/Media.localized/' + quote('Music/歌手/アルバム/曲一.m4a'), 'file:///old/absent.m4a']
            raw = expanded_library(urls)
            (bundle / 'Library.musicdb').write_bytes(raw)
            out = base / 'playlists'
            with patch('export_real_playlists.decode_musicdb', side_effect=lambda data: data):
                report = export_playlists(bundle, media, playlist_report(), out)
                with self.assertRaisesRegex(ValueError, 'new'):
                    export_playlists(bundle, media, playlist_report(), out)
            entry = report['results'][0]
            self.assertTrue(entry['partial'])
            self.assertEqual((entry['requested_count'], entry['exported_count']), (2, 1))
            self.assertEqual([r['status'] for r in entry['entries']], ['resolved', 'missing'])
            playlist = (out / entry['m3u8']).read_text()
            self.assertTrue(playlist.startswith('#EXTM3U\n'))
            self.assertIn(str(target), playlist)
            self.assertEqual((bundle / 'Library.musicdb').read_bytes(), raw)
            self.assertEqual(target.read_bytes(), b'not read as audio')
            self.assertFalse(report['audio_contents_read'])
            self.assertTrue((out / 'export-report.json').is_file())

    def test_invalid_pid_and_source_output_overlap_do_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bundle = base / 'Library.musiclibrary'
            bundle.mkdir()
            media = base / 'media'
            media.mkdir()
            (bundle / 'Library.musicdb').write_bytes(expanded_library(['file:///old/song.m4a']))
            bad = {'results': [{'root_pid': '../unsafe', 'playlist': []}]}
            with patch('export_real_playlists.decode_musicdb', side_effect=lambda data: data):
                with self.assertRaises(ValueError):
                    export_playlists(bundle, media, bad, base / 'out')
                self.assertFalse((base / 'out').exists())
                with self.assertRaises(ValueError):
                    export_playlists(bundle, media, bad, media / 'out')
                self.assertFalse((media / 'out').exists())


if __name__ == '__main__':
    unittest.main()
