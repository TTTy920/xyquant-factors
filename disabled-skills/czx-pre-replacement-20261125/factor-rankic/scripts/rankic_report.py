"""Auditable Markdown/JSON and formula-backed Excel, with numeric snapshot caches."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pandas as pd
import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.workbook.properties import CalcProperties

VERSION = '0.4.0-rc.10'
DETAIL_COLUMNS = ['observation_date', 'exit_date', 'status', 'n_pool', 'n_valid', 'rank_ic', 'valid_stock_count', 'reason', 'valid_rank_ic']
DIAGNOSTIC_COLUMNS = ['observation_date', 'status', 'n_pool', 'unknown_universe', 'excluded_authorization', 'excluded_factor', 'excluded_price', 'n_valid', 'reason']
XMLNS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def _text(value):
    return str(value).replace('|', '\\|').replace('\r', ' ').replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;')


def _number(value):
    return '—' if value is None else f'{value:.6f}' if isinstance(value, float) else str(value)


def _cell(sheet, row, column, value, *, formula=False):
    cell = sheet.cell(row, column, value)
    # Labels/factor names are data, not user-supplied executable Excel formulas.
    if isinstance(value, str) and not formula:
        cell.data_type = 's'
    return cell


def _cache_formula_values(path, caches):
    """Store Python-verified snapshot results; Excel recalculates on open/edit.

    This is not an independent spreadsheet-engine evaluation. Formula and cached
    results are tested separately; no LibreOffice runtime dependency is imposed.
    """
    temporary = path.with_suffix('.cached.xlsx')
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename in caches:
                root = ET.fromstring(content)
                for cell in root.iter(f'{{{XMLNS}}}c'):
                    coordinate = cell.attrib['r']
                    if coordinate not in caches[item.filename]:
                        continue
                    value = caches[item.filename][coordinate]
                    node = cell.find(f'{{{XMLNS}}}v')
                    if node is None:
                        node = ET.SubElement(cell, f'{{{XMLNS}}}v')
                    node.text = '' if value is None else repr(value)
                    if value is None:
                        cell.set('t', 'str')
                    else:
                        cell.attrib.pop('t', None)
                content = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            target.writestr(item, content)
    temporary.replace(path)


def _workbook(path, metadata):
    workbook = openpyxl.Workbook()
    summary = workbook.active
    summary.title = 'Summary'
    periods = workbook.create_sheet('Periods')
    diagnostics = workbook.create_sheet('Diagnostics')
    workbook.calculation = CalcProperties(calcId=191029, fullCalcOnLoad=True, forceFullCalc=True, calcMode='auto')
    caches = {'xl/worksheets/sheet1.xml': {}, 'xl/worksheets/sheet2.xml': {}}
    for index, name in enumerate(DETAIL_COLUMNS, 1):
        _cell(periods, 1, index, name)
    for index, name in enumerate(DIAGNOSTIC_COLUMNS, 1):
        _cell(diagnostics, 1, index, name)
    for row_index, row in enumerate(metadata['periods'], 2):
        for col, name in enumerate(DETAIL_COLUMNS, 1):
            if name == 'valid_stock_count':
                _cell(periods, row_index, col, f'=IF(C{row_index}="valid",E{row_index},"")', formula=True)
                caches['xl/worksheets/sheet2.xml'][f'G{row_index}'] = row['n_valid'] if row['status'] == 'valid' else None
            elif name == 'valid_rank_ic':
                _cell(periods, row_index, col, f'=IF(AND(C{row_index}="valid",ISNUMBER(F{row_index})),F{row_index},"")', formula=True)
                caches['xl/worksheets/sheet2.xml'][f'I{row_index}'] = row['rank_ic'] if row['status'] == 'valid' else None
            else:
                _cell(periods, row_index, col, row.get(name))
        for col, name in enumerate(DIAGNOSTIC_COLUMNS, 1):
            _cell(diagnostics, row_index, col, row.get(name))
    last = max(2, len(metadata['periods']) + 1)
    ranks, counts, statuses = f"'Periods'!I2:I{last}", f"'Periods'!G2:G{last}", f"'Periods'!C2:C{last}"
    formulas = {
        'valid_periods': f'=COUNT({ranks})',
        'invalid_periods': f'=COUNTIF({statuses},"invalid")',
        'incomplete_periods': f'=COUNTIF({statuses},"incomplete")',
        'mean_rank_ic': f'=IF(COUNT({ranks})=0,"",AVERAGE({ranks}))',
        'std_rank_ic': f'=IF(COUNT({ranks})<2,"",IF(MIN({ranks})=MAX({ranks}),0,STDEV({ranks})))',
        'icir': '=IF(AND(ISNUMBER(B6),B6>0),B5/B6,"")',
        'positive_fraction': f'=IF(COUNT({ranks})=0,"",COUNTIF({ranks},">0")/COUNT({ranks}))',
        'min_valid_stocks': f'=IF(COUNT({counts})=0,"",MIN({counts}))',
        'mean_valid_stocks': f'=IF(COUNT({counts})=0,"",AVERAGE({counts}))',
        'max_valid_stocks': f'=IF(COUNT({counts})=0,"",MAX({counts}))',
    }
    summary.append(['metric', 'value'])
    for i, (metric, value) in enumerate(metadata['summary'].items(), 2):
        _cell(summary, i, 1, metric)
        cell = _cell(summary, i, 2, formulas[metric], formula=True)
        cell.number_format = '0.000000' if metric in ('mean_rank_ic', 'std_rank_ic', 'icir', 'mean_valid_stocks') else '0.00%' if metric == 'positive_fraction' else '0'
        cell.comment = Comment('Source: Periods sheet. ddof=1; ICIR is mean/sample standard deviation, not annualized. Cached value is the Python audit snapshot; Excel recalculates on open.', 'XYQuant')
        caches['xl/worksheets/sheet1.xml'][f'B{i}'] = value
    entries = dict(status=metadata['status'], factor_name=metadata['parameters']['factor_name'],
                   observation_start=metadata['parameters']['start'], observation_end=metadata['parameters']['end'],
                   frequency=metadata['parameters']['frequency'], pool=metadata['parameters']['pool'],
                   min_stocks=metadata['parameters']['min_stocks'], origin=metadata['data']['origin'],
                   adjustment_id=metadata['data']['adjustment_id'], snapshot_id=metadata['data']['snapshot_id'],
                   return_range=' to '.join(metadata['return_range']) if metadata['return_range'] else 'unavailable',
                   workbook_note='Formula caches are Python-verified snapshots; not independently evaluated by Excel/LibreOffice.')
    for name, value in entries.items():
        row = summary.max_row + 1
        _cell(summary, row, 1, name)
        _cell(summary, row, 2, value)
    for warning in metadata['warnings']:
        row = summary.max_row + 1
        _cell(summary, row, 1, 'warning')
        _cell(summary, row, 2, warning)
    for sheet in workbook:
        sheet.freeze_panes = 'B2' if sheet == summary else 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        sheet.row_dimensions[1].height = 26
        for row in sheet:
            for cell in row:
                cell.font = Font(name='Arial', size=11, color='172B4D')
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                if cell.row == 1:
                    cell.font = Font(name='Arial', bold=True, size=11, color='FFFFFF')
                    cell.fill = PatternFill('solid', fgColor='183A54')
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = 29 if sheet != summary else (32 if letter == 'A' else 90)
    for column in (6, 9):
        for row in periods.iter_rows(min_row=2, min_col=column, max_col=column):
            row[0].number_format = '0.000000'
    workbook.save(path)
    workbook.close()
    _cache_formula_values(path, caches)


def write_reports(project, parameters, result, data_metadata):
    """Only accept whitelisted, noncredential metadata from DataBundle."""
    folder = Path(project).resolve() / 'results' / 'rankic' / str(uuid.uuid4())
    metadata = dict(schema_version=1, task_id=folder.name, created_at=datetime.now(timezone.utc).isoformat(),
                    parameters=asdict(parameters), software=dict(plugin=VERSION, python=platform.python_version(),
                    numpy=np.__version__, pandas=pd.__version__, openpyxl=openpyxl.__version__),
                    data=data_metadata, **result)
    metadata['method'] = dict(return_formula='adj_close(exit) / adj_close(T) - 1', correlation='Spearman; average-tie ranks',
                              std_ddof=1, icir_annualized=False, observation_dates_inclusive=True,
                              missing_values='excluded; no forward fill', tradeability_filters=False)
    # Validate JSON before creating an output directory. No NaN/Infinity is allowed.
    json_text = json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False)
    lines = ['# 因子 RankIC 报告', '', f"**状态：{result['status']}**", '',
             f'- 因子：{_text(parameters.factor_name)}',
             f'- 因子观测范围（含首尾）：{parameters.start} 至 {parameters.end}',
             f'- 频率 / 股票池：{parameters.frequency} / {parameters.pool}',
             f'- 每期最低有效股票数：{parameters.min_stocks}',
             f"- 实际收益价格范围：{' 至 '.join(result['return_range']) if result['return_range'] else '不可用'}",
             f"- 数据来源：{_text(data_metadata['origin'])}；范围类型：{_text(data_metadata['scope_kind'])}；参考/授权范围代码数：{data_metadata['scope_stock_count']}",
             f"- 范围代码交易所分布（本地参考不代表服务器授权或完整全 A）：{_text(data_metadata['scope_exchange_counts'])}",
             f"- 快照：{_text(data_metadata['snapshot_id'])}；复权口径标识：{_text(data_metadata['adjustment_id'])}", '',
             '收益 = 卖出日复权收盘价 / T 日同口径复权收盘价 − 1。逐 T 独立计算平均并列秩的 Spearman。', '',
             '## 汇总', '', '| 指标 | 数值 |', '|---|---:|']
    labels = dict(valid_periods='有效期数', invalid_periods='无效期数', incomplete_periods='未完成期数',
                  mean_rank_ic='平均 RankIC', std_rank_ic='样本标准差（ddof=1）', icir='ICIR（未年化）',
                  positive_fraction='正 RankIC 比例', min_valid_stocks='最少有效股票数',
                  mean_valid_stocks='平均有效股票数', max_valid_stocks='最多有效股票数')
    lines.extend(f'| {labels[key]} | {_number(value)} |' for key, value in result['summary'].items())
    lines += ['', '## 逐期结果', '', '| T | 卖出日 | 状态 | 有效股票 | RankIC | 剔除数 | 在市状态未知 | 原因 |', '|---|---|---|---:|---:|---:|---:|---|']
    for row in result['periods']:
        excluded = sum(row[k] for k in ('excluded_authorization', 'excluded_factor', 'excluded_price'))
        lines.append(f"| {row['observation_date']} | {row['exit_date'] or '待定'} | {row['status']} | {row['n_valid']} | {_number(row['rank_ic'])} | {excluded} | {row['unknown_universe']} | {row['reason']} |")
    lines += ['', '## 限制与诊断', '', '— 表示统计量未定义，不是 0。无效/未完成期不参与汇总。完整剔除分类见 Excel Diagnostics。',
              '只做理论收盘到收盘 RankIC，不代表可执行交易，不生成收益率或显著性保证。',
              'Excel 汇总使用公式，保留 Python 审核快照数值；本次未经过独立 Excel/LibreOffice 重算。', '']
    lines.extend('- ' + _text(warning) for warning in result['warnings'])
    folder.parent.mkdir(parents=True, exist_ok=True)
    # Publish only a complete set. Same-filesystem rename makes the UUID directory atomic.
    with tempfile.TemporaryDirectory(prefix='.rankic-', dir=folder.parent) as temporary:
        staging = Path(temporary) / 'report'
        staging.mkdir()
        (staging / 'metadata.json').write_text(json_text + '\n', encoding='utf-8')
        (staging / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        _workbook(staging / 'rankic.xlsx', metadata)
        staging.rename(folder)
    return folder
