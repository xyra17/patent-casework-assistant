import copy
import unittest

from api.round_budget import select_round_budget


class RoundRecommendationTests(unittest.TestCase):
    def test_dependencies_and_deliverables_raise_bounded_budget(self):
        simple = {'task_type': 'qa', 'required_sources': ['knowledge']}
        analysis = {**simple, 'task_type': 'analysis'}
        multi = {**analysis, 'required_sources': ['knowledge', 'db_query']}
        complex_plan = {**multi, 'complexity': 'complex'}
        budgets = [select_round_budget(p, [], None)['max_rounds'] for p in [simple, analysis, multi, complex_plan]]
        self.assertEqual(budgets, sorted(budgets))
        self.assertLessEqual(max(budgets), 3)
        self.assertGreater(select_round_budget({**simple, 'require_pdf': True}, [], None)['recommended_rounds'], budgets[0])
        self.assertGreater(select_round_budget(simple, ['a.docx', 'b.docx'], None)['max_rounds'], budgets[0])
        self.assertEqual(select_round_budget(simple, [], None, fixed_workflow=True)['max_rounds'], 3)

    def test_recommendation_cannot_mutate_permissions_or_override_manual_budget(self):
        plan = {'task_type': 'report', 'required_sources': ['knowledge'], 'forbidden_sources': ['web_search', 'db_query']}
        before = copy.deepcopy(plan)
        budget = select_round_budget(plan, [], 1)
        self.assertEqual(plan, before)
        self.assertEqual(budget['max_rounds'], 1)
        self.assertEqual(budget['mode'], 'manual')
        self.assertGreater(budget['recommended_rounds'], 1)
        for invalid in [True, 0, 7, '3', 2.5]:
            with self.assertRaises(ValueError):
                select_round_budget(plan, [], invalid)
