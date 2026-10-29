import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from scripts.fetch_next_album_metadata_v01 import request_allowed


class MetadataTransportTests(unittest.TestCase):
    def test_only_startup_and_one_declared_album_request_are_allowed(self):
        seen = set()
        request_allowed(('GET', 'https://music.youtube.com/'), {}, None, seen)
        request_allowed(('POST', 'https://music.youtube.com/youtubei/v1/account/account_menu'),
                        {'json': {}}, None, seen)
        args = ('POST', 'https://music.youtube.com/youtubei/v1/browse')
        kwargs = {'data': '{"browseId":"MPREb_test"}'}
        request_allowed(args, kwargs, 'MPREb_test', seen)
        with self.assertRaisesRegex(ValueError, 'Repeated'):
            request_allowed(args, kwargs, 'MPREb_test', seen)

    def test_foreign_album_continuation_and_host_are_rejected_before_transport(self):
        for url, body in [
            ('https://music.youtube.com/youtubei/v1/browse', {'browseId': 'MPREb_other'}),
            ('https://music.youtube.com/youtubei/v1/browse', {'browseId': 'MPREb_test', 'continuation': 'x'}),
            ('https://example.org/youtubei/v1/browse', {'browseId': 'MPREb_test'}),
        ]:
            seen = set()
            with self.assertRaises(ValueError):
                request_allowed(('POST', url), {'json': body}, 'MPREb_test', seen)
            self.assertEqual(seen, set())


if __name__ == '__main__':
    unittest.main()
