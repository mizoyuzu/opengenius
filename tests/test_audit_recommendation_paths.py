import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_recommendation_paths import audit_paths
from music_identity_map import IdentityMap


class ObservedPathAuditTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [dict(persistent_id=str(i), title=str(i), artist=artist)
                       for i, artist in enumerate(('Seed', 'Alias', 'Artist', 'Other'), 1)]
        self.matcher = IdentityMap(dict(schema_version=1, library_sha256='a' * 64,
                                        artist_alias_groups=[dict(enabled=True, aliases=['Alias', 'Artist'])]),
                                   self.tracks, 'a' * 64)
        self.evaluation = dict(relation_rows=[dict(root_pid='1', ordered_target_pids=['2', '4']),
                                              dict(root_pid='2', ordered_target_pids=['3'])],
                               root_results=[dict(root_pid='1', playlist_pids=['1', '2', '3', '4'],
                                                  playlist=[dict(persistent_id=pid, observed_relation_path=path,
                                                                 observed_relation_hops=len(path)-1)
                                                            for pid, path in [('1', ['1']), ('2', ['1', '2']),
                                                                              ('3', ['1', '2', '3']), ('4', ['1', '4'])]])])

    def test_bridge_concentration_uses_nonroot_paths_and_canonical_credits(self):
        original = copy.deepcopy(self.evaluation)
        row = audit_paths(self.evaluation, self.matcher)['root_results'][0]
        self.assertEqual(row['first_hop_gateway_count'], 2)
        self.assertEqual(row['largest_gateway_verified_nonroot_share'], 2/3)
        self.assertEqual(row['nonroot_artist_credit_count'], 2)
        self.assertEqual(row['largest_known_nonroot_artist_credit_share'], 2/3)
        self.assertEqual(row['hop_distribution'], {0: 1, 1: 2, 2: 1})
        self.assertEqual(self.evaluation, original)

    def test_missing_paths_are_not_invented(self):
        self.evaluation['root_results'][0]['playlist'][2].pop('observed_relation_path')
        row = audit_paths(self.evaluation, self.matcher)['root_results'][0]
        self.assertEqual(row['missing_path_count'], 1)
        self.assertEqual(row['largest_gateway_verified_nonroot_share'], 1/2)

    def test_fabricated_reverse_edge_wrong_hop_and_duplicate_playlist_rejected(self):
        for field, value in [('observed_relation_path', ['1', '4', '3']), ('observed_relation_hops', 1),
                             ('observed_relation_hops', True)]:
            with self.subTest(field=field):
                invalid = copy.deepcopy(self.evaluation)
                invalid['root_results'][0]['playlist'][2][field] = value
                with self.assertRaises(ValueError):
                    audit_paths(invalid, self.matcher)
        self.evaluation['root_results'][0]['playlist'].append(self.evaluation['root_results'][0]['playlist'][0])
        with self.assertRaises(ValueError):
            audit_paths(self.evaluation, self.matcher)


if __name__ == '__main__':
    unittest.main()
