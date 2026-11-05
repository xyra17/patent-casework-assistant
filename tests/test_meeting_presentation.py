import unittest
from unittest.mock import patch

from api.casework import meeting_records


class MeetingPresentationTests(unittest.TestCase):
    def records(self):
        return [
            {'meeting_id':'m1','case_key':'r:c1','client':'甲客户','iprs':['同名IPR'],'agents':['张三','张三'],'date':'2026-10-01'},
            {'meeting_id':'m2','case_key':'r:c1','client':'甲客户','iprs':['同名IPR'],'agents':['张三','李四'],'date':'2026-10-02'},
            {'meeting_id':'m3','case_key':'r:c2','client':'乙客户','iprs':['同名IPR'],'agents':['王五'],'date':None},
            {'meeting_id':'m4','case_key':'r:c3','client':'甲客户','iprs':['其他IPR'],'agents':['赵六'],'date':'2026-10-03'},
        ]

    def test_summary_uses_all_filtered_records_before_pagination_and_counts_cases_once(self):
        with patch('api.casework.catalog.meetings',return_value=self.records()),patch('api.casework.store.coverage',return_value={}):
            result=meeting_records(client='甲客户',ipr='同名IPR',offset=0,limit=1)
        self.assertEqual(result['total'],2)
        self.assertEqual(len(result['rows']),1)
        self.assertTrue(result['has_more'])
        self.assertFalse(result['same_name_across_clients'])
        self.assertEqual(result['agent_summary'],[
            {'name':'张三','meeting_count':2,'case_count':1,'latest_date':'2026-10-02'},
            {'name':'李四','meeting_count':1,'case_count':1,'latest_date':'2026-10-02'},
        ])
        self.assertEqual(result['options']['clients'],['乙客户','甲客户'])
        self.assertEqual(len(result['options']['iprs']),2)

    def test_same_name_warning_and_unknown_date_remain_explicit(self):
        with patch('api.casework.catalog.meetings',return_value=self.records()),patch('api.casework.store.coverage',return_value={}):
            result=meeting_records(client=None,ipr='同名IPR',offset=0,limit=20)
            empty=meeting_records(client='不存在的客户',ipr=None,offset=0,limit=20)
        self.assertTrue(result['same_name_across_clients'])
        self.assertIsNone(next(p for p in result['agent_summary'] if p['name']=='王五')['latest_date'])
        self.assertEqual(empty['agent_summary'],[])
        self.assertEqual(empty['total'],0)
