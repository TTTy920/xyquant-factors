"""Unified research queries replace four legacy views and exclude team tools."""
import unittest
from unittest.mock import AsyncMock, Mock, patch

import factor_bridge
import remote
from mcp.server.mcpserver.exceptions import ToolError

TOOL = 'query_strategy_research'
DEFAULTS = dict(strategy_id=None, sections=None, search='', category='', page=1,
                page_size=20, version=None, start=None, end=None, offset=0, limit=50)
LEGACY = {'list_strategies', 'get_strategy_info', 'get_strategy_performance',
          'get_strategy_nav', 'get_research_team'}


class ResearchBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_uses_one_read_only_query_and_removes_legacy_team_tools(self):
        tools = {tool.name: tool for tool in await factor_bridge.mcp.list_tools()}
        self.assertEqual(len(tools), 17)
        self.assertIn(TOOL, tools)
        self.assertFalse(LEGACY & tools.keys())
        tool = tools[TOOL]
        self.assertTrue(tool.annotations.read_only_hint)
        self.assertFalse(tool.annotations.destructive_hint)
        self.assertTrue(tool.annotations.idempotent_hint)
        self.assertIs(tool.input_schema['additionalProperties'], False)

    async def test_catalog_combined_detail_and_nav_keep_versions_nulls_and_source(self):
        value = {'version': 'a' * 32, 'source': {'sha256': 'b' * 64},
                 'performance': {'metrics': {'columns': ['收益风险比'], 'values': [1.37]}},
                 'nav': {'rows': [['2026-01-02', None]], 'next_offset': 50, 'has_more': True},
                 'warnings': [{'code': 'period_difference'}]}
        queries = [dict(search='合成策略', category='权益'),
                   dict(strategy_id='s_' + 'b' * 12),
                   dict(strategy_id='s_' + 'b' * 12, sections=['info', 'performance', 'nav']),
                   dict(strategy_id='s_' + 'b' * 12, sections=['nav'],
                        version='a' * 32, start='2026-01-02', offset=50)]
        with patch('factor_bridge.call', new_callable=AsyncMock, return_value=value) as call:
            for args in queries:
                result = await factor_bridge.mcp.call_tool(TOOL, args)
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content, value)
                call.assert_awaited_with(TOOL, {**DEFAULTS, **args})

    async def test_team_and_unpinned_later_pages_are_rejected_before_remote_access(self):
        cases = [dict(sections=['team']), dict(page=True), dict(include_team=True),
                 dict(strategy_id='s_'+'b'*12, sections=['nav'], offset=50)]
        with patch('factor_bridge.call', new_callable=AsyncMock) as call:
            for args in cases:
                with self.subTest(args=args), self.assertRaises(ToolError):
                    await factor_bridge.mcp.call_tool(TOOL, args)
            call.assert_not_called()

    async def test_research_denial_is_sanitized_without_erasing_factor_login(self):
        auth = Mock()
        for code in ['research_authorization_required', 'version_changed', 'content_unavailable', 'research_storage_unavailable']:
            safe = remote._code({'code': code, 'error': 'secret-token-must-not-escape'})
            failure = remote._failure(safe, auth, 'secret-token-must-not-escape')
            self.assertNotIn('secret-token', str(failure))
            auth.invalidate.assert_not_called()
            with patch('factor_bridge.remote_call', new_callable=AsyncMock, side_effect=failure):
                with self.assertRaises(ToolError) as error:
                    await factor_bridge.mcp.call_tool(TOOL, {})
                self.assertIn(safe, str(error.exception))
                self.assertNotIn('secret-token', str(error.exception))


if __name__ == '__main__':
    unittest.main()
