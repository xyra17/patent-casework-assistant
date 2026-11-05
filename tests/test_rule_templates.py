import unittest
from rules.templates import TEMPLATES
from rules.models import RuleInput
from rules.engine import matches
class RuleTemplateTests(unittest.TestCase):
    def test_examples_are_disabled_valid_and_scoped(self):
        self.assertEqual(len({t['key'] for t in TEMPLATES}),5)
        for template in TEMPLATES:
            rule=RuleInput.model_validate({k:v for k,v in template.items() if k!='key'})
            self.assertFalse(rule.active)
            self.assertEqual(rule.match_mode,'keywords')
            self.assertTrue(rule.scope_note)
            self.assertTrue(any(matches('请查询'+term,term) for term in rule.match_terms))
            self.assertFalse(any(matches('无关的库存销售问题',term) for term in rule.match_terms))

    def test_patent_jargon_covers_project_terms_without_turning_a_plan_into_a_fact(self):
        rule=next(t for t in TEMPLATES if t['key']=='patent-jargon')
        self.assertEqual(rule['category'],'term')
        for query in ['所限快到了','这个案子交局了吗','找客户IPR开三方会','Boss分案','看提交稿']:
            self.assertTrue(any(matches(query,t) for t in rule['match_terms']+rule['aliases']),query)
        self.assertIn('不表示已经授权',rule['content'])
        self.assertIn('先询问具体含义',rule['content'])
        self.assertIn('本项目',rule['scope_note'])
