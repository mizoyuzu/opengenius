import unittest

from baseline_graph_projection_v01 import validate_projection


class BaselineProjectionTests(unittest.TestCase):
    def test_assignment_only_rows_are_not_recommendation_roots(self):
        self.assertEqual(validate_projection({'a': ['b']}, {'a': ['b'], 'b': []}), 2)

    def test_rejects_missing_or_changed_relations(self):
        for graph, state in [
            ({'a': ['b']}, {'a': ['b'], 'b': ['a']}),
            ({'a': ['c', 'b']}, {'a': ['b', 'c']}),
            ({'a': []}, {}),
        ]:
            with self.subTest(graph=graph, state=state):
                with self.assertRaises(ValueError):
                    validate_projection(graph, state)
