import unittest

from scripts.fetch_kirby_playlist_direct_v04 import pinned_inputs
from scripts.probe_kirby_playlist_discovery_v03 import exact_playlist_rows


class DirectPlaylistV04Tests(unittest.TestCase):
    def test_pinned_search_row_and_zero_credit_parent(self):
        summary_path, intent_path, code_path, search_path, search = pinned_inputs()
        self.assertTrue(summary_path.is_file())
        self.assertTrue(intent_path.is_file())
        self.assertTrue(code_path.is_file())
        self.assertTrue(search_path.is_file())
        self.assertEqual(search['endpoint'], 'search')
        self.assertEqual(exact_playlist_rows(search['response'])[0]['playlist_id'],
                         'VLPLlvbNxiPAuN9SigrXnphMm_xov4fJ5Xya')


if __name__ == '__main__':
    unittest.main()
