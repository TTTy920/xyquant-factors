"""Local selection outcomes, boundaries, missing data and score consistency."""
import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'plugins/xyquant/skills/strategy-attribution/scripts/attribute.py'
spec = importlib.util.spec_from_file_location('local_selection', PATH)
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)


def fixture():
    return dict(strategy='合成策略', month='2025-12',
        columns=dict(date='日期', symbol='证券', score='总分'),
        groups=[dict(sheet='评分', label='全市场', quota=2, order='descending', dimensions=[
            dict(column='甲', label='盈利', weight=1), dict(column='乙', label='质量', weight=1)])],
        tables={'评分': [dict(zip(['日期','证券','总分','甲','乙'], ['2025-12-31',code,score,a,b]))
            for code,score,a,b in [('000001.SZ',.75,1,.5),('000002.SZ',.6,.4,.8),
                                  ('000003.SZ',.6,.7,.5),('000004.SZ',.4,None,.4)]]})


class LocalSelectionTests(unittest.TestCase):
    def test_boundary_ties_are_pending_and_not_resolved_by_code(self):
        data=fixture();group=selection.calculate(data)['分组名单'][0]
        self.assertEqual((group['并列待定名额'],group['名额线同分股票数']),(1,2))
        self.assertEqual([x['状态'] for x in group['名单']],['按规则入选','名额线并列待定','名额线并列待定'])
        for symbol in ('000002.SZ','000003.SZ'):
            row=selection.calculate(dict(data,symbol=symbol))['个股']
            self.assertEqual(row['组内名次'],2)
            self.assertNotIn('边界参照',row)

    def test_selected_and_excluded_use_the_correct_peer_and_signed_drivers(self):
        data=fixture();data['groups'][0]['quota']=1
        row=selection.calculate(dict(data,symbol='000001.SZ'))['个股']
        self.assertEqual(row['边界参照']['股票代码'],'000002.SZ')
        self.assertEqual([x['综合分差贡献'] for x in row['主要驱动']],[.3,-.15])
        loser=selection.calculate(dict(data,symbol='000003.SZ'))['个股']
        self.assertEqual(loser['状态'],'按规则未入选')
        self.assertEqual(loser['边界参照']['股票代码'],'000001.SZ')

    def test_ascending_and_score_only_rules_are_respected(self):
        data=fixture();data['groups'][0].update(order='ascending',dimensions=[],quota=1)
        row=selection.calculate(dict(data,symbol='000004.SZ'))['个股']
        self.assertEqual((row['组内名次'],row['状态']),(1,'按规则入选'))
        self.assertNotIn('主要驱动',row)
        self.assertEqual(row['有效评分项数'],0)

    def test_partial_coverage_absent_stock_and_absent_month_are_distinct(self):
        data=fixture();data['groups'].append(dict(sheet='空表',label='另一组',quota=1,order='descending'))
        data['tables']['空表']=[]
        self.assertEqual(selection.calculate(data)['缺失分组'],['另一组'])
        self.assertEqual(selection.calculate(dict(data,symbol='999999.SZ'))['个股']['状态'],'当期已有明细中无记录')
        absent=selection.calculate(dict(data,month='2026-01'))
        self.assertEqual(absent['数据覆盖'],'当月无数据')
        self.assertIsNone(absent['明细日期'])

    def test_missing_dimensions_are_not_invented_or_compared(self):
        row=selection.calculate(dict(fixture(),symbol='000004.SZ'))['个股']
        self.assertEqual(row['有效评分项数'],1)
        self.assertEqual(row['主要评分依据']['因素'],'质量')
        self.assertNotIn('主要驱动',row)

    def test_component_masks_change_the_explanation_basis(self):
        data=fixture();group=data['groups'][0];group['quota']=1
        group['dimensions'][0]['components']={'甲一':'指标一','甲二':'指标二'}
        for i,row in enumerate(data['tables']['评分']):
            row.update(甲一=row['甲'] if i!=1 else None,甲二=row['甲'] if i==1 else None)
        row=selection.calculate(dict(data,symbol='000001.SZ'))['个股']
        self.assertNotIn('主要驱动',row)
        self.assertEqual(row['主要评分依据']['因素'],'指标一')

    def test_ambiguous_rows_and_inconsistent_scores_are_rejected(self):
        changes=[lambda d:d['tables']['评分'].append(copy.deepcopy(d['tables']['评分'][0])),
                 lambda d:d['tables']['评分'][1].update(日期='2025-12-30'),
                 lambda d:d['tables']['评分'][0].update(总分=.9),
                 lambda d:d['tables']['评分'][0].update(甲=float('nan')),
                 lambda d:d['tables']['评分'][0].update(总分=float('inf')),
                 lambda d:d['groups'][0].update(quota=True),
                 lambda d:d['groups'][0]['dimensions'][0].update(weight=0),
                 lambda d:d.update(month='2025-13'),
                 lambda d:d.update(symbol='000001'),
                 lambda d:d['tables'].clear()]
        for i,change in enumerate(changes):
            with self.subTest(i=i):
                data=fixture();change(data)
                with self.assertRaises(ValueError):selection.calculate(data)

    def test_input_is_not_mutated_and_small_groups_do_not_get_fictional_peers(self):
        data=fixture();data['groups'][0]['quota']=8;before=copy.deepcopy(data)
        rows=selection.calculate(data)['分组名单'][0]['名单']
        self.assertTrue(all('边界参照' not in row for row in rows))
        self.assertEqual(data,before)


if __name__=='__main__':unittest.main()
