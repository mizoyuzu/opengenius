import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_macos_real_library import audit_ordinary_playlists


class NativePlaylistAuditTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'name': 'Validation', 'persistent_ids': ['0000000000000001', '0000000000000002']}
        observed = {**self.plan, 'playlist_pid': '0000000000000003', 'genius': False}
        self.manifest = {'recommendation_playlists': [self.plan]}
        self.report = {'after_copy_consistent_exit': True, 'ordinary_recommendation_playlists': {
            'creation': [copy.deepcopy(observed)], 'reopened': [copy.deepcopy(observed)],
            'music_exited_before_reopen': True, 'reopen_membership_order_verified': True}}

    def verified(self):
        return audit_ordinary_playlists(self.report, self.manifest)['ordinary_playlist_persistence_verified']

    def test_exact_membership_and_same_native_playlist_survive_restart(self):
        self.assertTrue(self.verified())

    def test_native_success_flag_does_not_override_reordered_or_replaced_playlist(self):
        later = self.report['ordinary_recommendation_playlists']['reopened'][0]
        later['persistent_ids'].reverse()
        self.assertFalse(self.verified())
        later['persistent_ids'].reverse()
        later['playlist_pid'] = '0000000000000004'
        self.assertFalse(self.verified())

    def test_requires_observed_ordinary_flag_and_both_clean_exits(self):
        later = self.report['ordinary_recommendation_playlists']['reopened'][0]
        later['genius'] = True
        self.assertFalse(self.verified())
        later['genius'] = False
        self.report['ordinary_recommendation_playlists']['music_exited_before_reopen'] = False
        self.assertFalse(self.verified())

    def test_missing_evidence_never_verifies(self):
        self.report['ordinary_recommendation_playlists']['reopened'] = []
        self.assertFalse(self.verified())
        self.assertFalse(audit_ordinary_playlists({}, {})['ordinary_playlist_persistence_verified'])
