"""Selection explanations: protocol validation and faithful read-only forwarding."""
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import auth_client
import factor_bridge
import remote
from mcp.types import CallToolRequestParams


TOOL = 'explain_strategy_selection'


class SelectionBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_exposes_required_query_and_read_only_contract(self):
        tools = {tool.name: tool for tool in await factor_bridge.mcp.list_tools()}
        tool = tools[TOOL]
        self.assertEqual(set(tool.input_schema['required']), {'strategy', 'month'})
        self.assertIs(tool.input_schema['additionalProperties'], False)
        self.assertTrue(tool.annotations.read_only_hint)
        self.assertTrue(tool.annotations.idempotent_hint)
        self.assertFalse(tool.annotations.destructive_hint)

    async def test_single_stock_and_group_queries_preserve_ties_missing_data_and_scores(self):
        cases = [
            ({'strategy': '合成策略', 'month': '2026-09', 'symbol': '000001.SZ'},
             {'策略': '合成策略', '月份': '2026-09', '数据覆盖': '全部分组',
              '个股': {'股票代码': '000001.SZ', '状态': '按规则未入选',
                       '主要驱动': [{'因素': '合成指标', '综合分差贡献': -0.125, '排名影响': '不利'}]}}),
            ({'strategy': '合成简称', 'month': '2026-10'},
             {'数据覆盖': '部分分组', '缺失分组': ['合成缺失组'],
              '分组名单': [{'并列待定名额': 1, '名额线同分股票数': 2,
                            '名单': [{'状态': '名额线并列待定', '综合分': 0.5}]}]}),
            ({'strategy': '合成策略', 'month': '2026-11', 'symbol': None},
             {'数据覆盖': '当月无数据', '明细日期': None, '缺失分组': ['合成组']}),
        ]
        for arguments, value in cases:
            with self.subTest(arguments=arguments), patch('factor_bridge.call', new_callable=AsyncMock, return_value=value) as call:
                result = await factor_bridge.mcp._handle_call_tool(
                    None, CallToolRequestParams(name=TOOL, arguments=arguments))
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content, value)
                call.assert_awaited_once_with(TOOL, {**arguments, 'symbol': arguments.get('symbol')})

    async def test_invalid_queries_are_rejected_before_sdk_coercion_or_remote_access(self):
        valid = {'strategy': '合成策略', 'month': '2026-09'}
        changes = [
            {'strategy': None}, {'strategy': True}, {'strategy': 123},
            {'strategy': ' '}, {'strategy': 'a' * 121},
            {'month': '202609'}, {'month': '2026-13'}, {'month': '0000-01'},
            {'month': '2026-09-30'}, {'month': True},
            {'symbol': 1}, {'symbol': '000001'}, {'symbol': '000001.sz'},
            {'holdings': []}, {'customer_id': 'synthetic-private-customer'},
        ]
        with patch('factor_bridge.call', new_callable=AsyncMock) as call:
            for change in changes:
                with self.subTest(change=change):
                    result = await factor_bridge.mcp._handle_call_tool(
                        None, CallToolRequestParams(name=TOOL, arguments={**valid, **change}))
                    self.assertTrue(result.is_error)
                    text = ''.join(block.text for block in result.content)
                    self.assertEqual(json.loads(text)['code'], 'invalid_request')
                    self.assertNotIn('synthetic-private', text)
            call.assert_not_called()

    async def test_read_retry_reuses_query_and_authorization_denial_stops(self):
        auth = Mock()
        arguments = {'strategy': '合成策略', 'month': '2026-09', 'symbol': None}
        with patch('remote.auth_access', new_callable=AsyncMock, return_value='synthetic-token'), \
             patch('remote._attempt', new_callable=AsyncMock, side_effect=[remote._AttemptFailure('busy'), {'数据覆盖': '当月无数据'}]) as attempt, \
             patch('remote.RETRY_DELAYS', (0, 0)), patch('remote.record'):
            result = await remote.remote_call(TOOL, arguments, auth)
            self.assertEqual(result['数据覆盖'], '当月无数据')
            self.assertEqual(attempt.await_count, 2)
            self.assertTrue(all(call.args[1] is arguments for call in attempt.await_args_list))
        with patch('remote.auth_access', new_callable=AsyncMock, return_value='synthetic-token'), \
             patch('remote._attempt', new_callable=AsyncMock, side_effect=remote._AttemptFailure('permission_denied')) as attempt, \
             patch('remote.record'), self.assertRaises(auth_client.ClientError) as error:
            await remote.remote_call(TOOL, arguments, auth)
        self.assertEqual(error.exception.code, 'permission_denied')
        attempt.assert_awaited_once()
        auth.invalidate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
