"""Post-build offline smoke check of the actual EXE, with isolated local state."""
import asyncio
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]
EXPECTED={'get_connection_status','start_login','get_login_status','disconnect','list_factors','get_factor_info',
          'preview_factor','create_export','get_export','list_exports','query_strategy_research','download_export','get_download',
          'search_research','compare_strategies','get_research_updates'}


async def check(binary,root,version):
    parameters=StdioServerParameters(command=str(binary),args=['--stdio','--state-root',str(root)])
    async with stdio_client(parameters) as (reader,writer):
        async with ClientSession(reader,writer) as session:
            initialized=await session.initialize()
            if initialized.server_info.version!=version:raise ValueError('Bundled runtime version mismatch.')
            tools=await session.list_tools()
            if {tool.name for tool in tools.tools}!=EXPECTED:raise ValueError('Bundled runtime tool set mismatch.')
            return dict(version=version,tools=len(tools.tools),protocol=initialized.protocol_version,network_tools_called=0)

def check_local_skills(command, plugin, directory):
    factors = ('Size', 'Beta', 'Momentum', 'ResidualVolatility', 'NonlinearSize',
               'BookToPrice', 'Liquidity', 'EarningsYield', 'Growth', 'Leverage')
    holdings = {'日期':'2025-12-31','股票代码':'000001.SZ','持仓比例':0.5}
    exposure = {'月份':'2025-12','股票代码':'000001.SZ', **dict.fromkeys(factors, 2.0)}
    returns = {'月份':'2026-01', **dict.fromkeys(factors, 0.01)}
    fixture = directory / '策略 输入.json'
    fixture.write_text(json.dumps({'holdings':[holdings], 'stock_exposures':[exposure],
                                   'factor_returns':[returns]}), encoding='utf-8')
    def invoke(flag):
        return json.loads(subprocess.check_output([*command, flag, str(fixture)], cwd=directory, timeout=90))
    barra = invoke('--barra')
    if (barra['月末组合暴露'] != [{'月份':'2025-12', **dict.fromkeys(factors, 1.0)}] or
            barra['月度风格收益贡献'] != [{'月份':'2026-01', **dict.fromkeys(factors, 0.01)}]):
        raise ValueError('Installed Barra entry point returned incorrect attribution.')
    reference = (plugin / 'skills/strategy-attribution/references/input.md').read_text(encoding='utf-8')
    fixture.write_text(re.search(r'```json\s*(.*?)\s*```', reference, flags=re.S).group(1), encoding='utf-8')
    selection = invoke('--selection')['个股']
    if (selection['股票代码'], selection['组内名次'], selection['状态']) != ('000002.SZ', 2, '按规则未入选'):
        raise ValueError('Installed selection entry point returned an incorrect rank.')


def main():
    binary=ROOT/'plugins/xyquant-factors/bin/FactorBridge.exe'
    metadata=json.loads((ROOT/'plugins/xyquant-factors/release.json').read_text(encoding='utf8'))
    actual=subprocess.check_output([str(binary),'--version'],timeout=30).decode().strip()
    if actual!=metadata['version']:raise ValueError('Bundled CLI version mismatch.')
    with tempfile.TemporaryDirectory(prefix='xyquant-offline-smoke-') as td:
        result=asyncio.run(asyncio.wait_for(check(binary,Path(td),metadata['version']),timeout=30))
        check_local_skills([str(binary)], binary.parent.parent, Path(td))
        result['local_attribution_checks'] = ['barra', 'selection']
    print(json.dumps(result))


if __name__=='__main__':main()
