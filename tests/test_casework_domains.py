import unittest
from unittest.mock import patch
from casework import domains,entities,parser,analytics


class DomainTests(unittest.TestCase):
    def test_aliases_share_one_canonical_value_and_unknown_is_preserved(self):
        for value,expected in [('AI','人工智能'),('ａｉ','人工智能'),('无线','无线通信'),('光','光通信'),('结构','机械结构'),('车','汽车技术'),('终端应用软件','终端软件')]:
            self.assertEqual(domains.canonical(value),expected)
            self.assertEqual(parser.normalize_value('technical_domain',value),expected)
            self.assertEqual(entities.bind(value,{expected},'domain'),expected)
        self.assertEqual(domains.options(['AI','材料科学','结构']),list(domains.DOMAINS)+['材料科学'])
        self.assertEqual(domains.canonical('其他原始领域'),'其他原始领域')

    def test_short_aliases_do_not_turn_technical_terms_into_domain_filters(self):
        self.assertFalse(domains.mentioned('查看数据结构的历史案件','机械结构'))
        self.assertFalse(domains.mentioned('推断光照影响','光通信'))
        self.assertFalse(domains.mentioned('检索训练chain的历史案','人工智能'))
        for text,name in [('车领域的案件','汽车技术'),('结构类案件','机械结构'),('找AI案件','人工智能'),('无线通信案件','无线通信')]:
            self.assertTrue(domains.mentioned(text,name))

    def test_monthly_grouping_merges_legacy_aliases_but_keeps_unknown_values(self):
        rows=[{'case_key':str(i),'case_id':str(i),'technical_domain':value,'received_date':'2026-09-05','tags':[]} for i,value in enumerate(['AI','人工智能','无线','无线通信','材料科学'])]
        with patch.object(analytics.store,'list_cases',return_value=rows),patch.object(analytics.store,'coverage',return_value={}):
            result=analytics.report('2026-09','2026-09')
        self.assertEqual({r['group']:r['received_count'] for r in result['rows']},{'人工智能':2,'无线通信':2,'材料科学':1})
        self.assertEqual(result['totals'][0]['received_count'],5)
