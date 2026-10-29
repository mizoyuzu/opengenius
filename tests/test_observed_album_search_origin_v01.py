import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from observed_album_search_origin_v01 import KIND, SUMMARY, SUMMARY_SHA256, sha, validate


class ObservedAlbumOriginTests(unittest.TestCase):
    def source(self, ordinal):
        page = SUMMARY.parent / 'first-pages' / f'{ordinal:02d}-first-page.json'
        return {'source_kind': KIND, 'summary_path': str(SUMMARY),
                'summary_sha256': SUMMARY_SHA256, 'response_path': str(page),
                'response_sha256': sha(page), 'query_ordinal': ordinal, 'result_position': 0}

    def test_real_authenticated_first_page_supports_metadata_id_only(self):
        result = validate(self.source(0), 'MPREb_U5HNLV2Dxkg',
                          'THE IDOLM@STER SHINY COLORS ORIGINAL SOUNDTRACK')
        self.assertEqual(result['browseId'], 'MPREb_U5HNLV2Dxkg')

    def test_same_page_another_result_cannot_be_claimed_as_original_ost(self):
        source = self.source(0)
        source['result_position'] = 1
        with self.assertRaisesRegex(ValueError, 'unique exact'):
            validate(source, 'MPREb_U5HNLV2Dxkg',
                     'THE IDOLM@STER SHINY COLORS ORIGINAL SOUNDTRACK')

    def test_changed_summary_receipt_is_not_accepted(self):
        source = self.source(0)
        source['summary_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'sealed receipt'):
            validate(source, 'MPREb_U5HNLV2Dxkg',
                     'THE IDOLM@STER SHINY COLORS ORIGINAL SOUNDTRACK')


if __name__ == '__main__':
    unittest.main()
