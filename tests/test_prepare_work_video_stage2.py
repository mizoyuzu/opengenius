from __future__ import annotations

import json
from pathlib import Path
import unittest

from scripts.prepare_work_video_stage2 import request_key


class PrepareWorkVideoStage2Tests(unittest.TestCase):
    def test_key_matches_real_stage1_radio_receipt_filename(self):
        root = Path(__file__).resolve().parents[1]
        stage = root / 'data/ytmusic/post870-work-video-radio-20261015-v01/stage-0001'
        graph = json.loads((stage / 'candidate-graphs.json').read_text())
        result = graph['results'][0]
        key = request_key(result['seed_video_id'], True, 100)
        self.assertEqual(key, result['watch_radio']['request_key'])
        request_path = stage / 'requests' / f'{key}.json'
        self.assertTrue(request_path.is_file())
        request = json.loads(request_path.read_text())
        self.assertEqual(request['arguments'], {
            'videoId': result['seed_video_id'], 'radio': True, 'limit': 100})
        self.assertEqual(len(request['response']['tracks']), 148)


if __name__ == '__main__':
    unittest.main()
