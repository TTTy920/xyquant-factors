"""Explain selection from local score tables and explicit ranking rules. No network I/O."""
import argparse
from collections import defaultdict, namedtuple
from datetime import date
import json
import math
from pathlib import Path
import re
import sys

Row = namedtuple('Row', 'code score values masks')


def _number(value):
    if type(value) not in (int, float):
        raise ValueError('评分和权重必须是有限数值')
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError('数值超出范围') from exc
    if not math.isfinite(value):
        raise ValueError('评分和权重必须是有限数值')
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + '必须是非空文本')
    return value


def _load(data):
    if not isinstance(data, dict):
        raise ValueError('输入必须是 JSON 对象')
    strategy = _text(data.get('strategy'), '策略名称')
    month = data.get('month')
    if not isinstance(month, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}', month):
        raise ValueError('查询月份必须是 YYYY-MM')
    date.fromisoformat(month + '-01')
    symbol = data.get('symbol')
    if symbol is not None and (not isinstance(symbol, str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', symbol)):
        raise ValueError('查询股票代码需要交易所后缀')
    columns, groups, tables = data.get('columns'), data.get('groups'), data.get('tables')
    if not isinstance(columns, dict) or not {'date', 'symbol', 'score'} <= columns.keys():
        raise ValueError('columns 需要 date、symbol、score 三个字段映射')
    for value in columns.values():
        _text(value, '列名')
    if len({columns[k] for k in ('date', 'symbol', 'score')}) != 3:
        raise ValueError('日期、股票、综合分列不能相同')
    if not isinstance(groups, list) or not groups or not isinstance(tables, dict):
        raise ValueError('需要非空分组规则和本地 tables 数据')
    snapshots, dates, seen, labels = {}, {}, set(), set()
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError('分组规则必须是对象')
        label = _text(group.get('label'), '分组名')
        sheet = _text(group.get('sheet'), '数据表名称')
        if label in labels:
            raise ValueError('分组名重复')
        labels.add(label)
        if type(group.get('quota')) is not int or group['quota'] < 1 or group.get('order') not in ('ascending', 'descending'):
            raise ValueError('名额必须是正整数，order 必须明确升序或降序')
        dims = group.get('dimensions', [])
        if not isinstance(dims, list):
            raise ValueError('dimensions 必须是数组')
        weights, required = [], set(columns[k] for k in ('date', 'symbol', 'score'))
        for dim in dims:
            if not isinstance(dim, dict):
                raise ValueError('评分项规则必须是对象')
            _text(dim.get('label'), '评分项名称')
            required.add(_text(dim.get('column'), '评分项列名'))
            weight = _number(dim.get('weight'))
            if weight <= 0:
                raise ValueError('评分项权重必须为正')
            weights.append(weight)
            components = dim.get('components', {})
            if not isinstance(components, dict):
                raise ValueError('components 必须是列名到显示名称的映射')
            for name, title in components.items():
                required.add(_text(name, '子项列名'))
                _text(title, '子项名称')
        rows = tables.get(sheet)
        if not isinstance(rows, list):
            raise ValueError('缺少本地评分表：' + sheet)
        months = defaultdict(list)
        for cells in rows:
            if not isinstance(cells, dict) or not required <= cells.keys():
                raise ValueError('评分记录缺少已配置列；缺失值请显式使用 null')
            day, code = cells[columns['date']], cells[columns['symbol']]
            if not isinstance(day, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day):
                raise ValueError('明细日期必须是 YYYY-MM-DD')
            day = date.fromisoformat(day).isoformat()
            period = day[:7]
            if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', code):
                raise ValueError('明细股票代码需要交易所后缀')
            if dates.setdefault(period, day) != day or (period, code) in seen:
                raise ValueError('同月明细日期不一致或股票记录重复')
            seen.add((period, code))
            total = _number(cells[columns['score']])
            values, masks = [], []
            for dim in dims:
                value = cells[dim['column']]
                score = None if value is None else _number(value)
                components = list(dim.get('components', {}))
                mask = sum(1 << i for i, key in enumerate(components) if cells[key] is not None)
                if components:
                    parts = [_number(cells[key]) for key in components if cells[key] is not None]
                    if (score is None) != (not parts) or (parts and abs(sum(parts)/len(parts)-score) > 1e-8):
                        raise ValueError('子项得分与已提供的评分项不一致')
                values.append(score)
                masks.append(mask)
            terms = [(w, v) for w, v in zip(weights, values) if v is not None]
            if terms and abs(sum(w*v for w, v in terms)/sum(w for w, v in terms)-total) > 1e-8:
                raise ValueError('评分权重与已提供的综合分不一致')
            months[period].append(Row(code, total, tuple(values), tuple(masks)))
        direction = 1 if group['order'] == 'descending' else -1
        for items in months.values():
            items.sort(key=lambda row: (-direction*row.score, row.code))
        snapshots[label] = months
    return strategy, month, symbol, {'groups': groups}, snapshots, dates


def _label(row, dim, i):
    components = list(dim.get('components', {}).values())
    if len(components)>1 and row.masks[i] and row.masks[i] & (row.masks[i]-1)==0:
        return components[row.masks[i].bit_length()-1]
    return dim['label']


def _explain(row, peer, group):
    dims = group.get('dimensions', [])
    used = [i for i,v in enumerate(row.values) if v is not None]
    result = {'有效评分项数':len(used),'配置评分项数':len(dims)}
    direction = 1 if group['order']=='descending' else -1
    if peer:
        result['边界参照'] = {'股票代码':peer.code,'综合分':peer.score,
                            '综合分差':round(row.score-peer.score,10)}
    if not used:
        result['解释口径'] = '该记录仅提供综合分和排名。'
        return result
    total_weight = sum(dims[i]['weight'] for i in used)
    if peer and all((v is None)==(p is None) for v,p in zip(row.values,peer.values)) and row.masks==peer.masks:
        changes = {i:(row.values[i]-peer.values[i])*dims[i]['weight']/total_weight for i in used}
        ordered = sorted((i for i in used if abs(changes[i])>1e-12),key=lambda i:-direction*changes[i])
        if ordered and direction*changes[ordered[0]]>0>direction*changes[ordered[-1]]:
            shown = [ordered[0],ordered[-1]]
        else:
            shown = sorted(ordered,key=lambda i:-abs(changes[i]))[:2]
        result['解释口径'] = '与名额线附近参照股比较，列出主要评分差异。'
        result['主要驱动'] = [{'因素':_label(row,dims[i],i),'本股得分':row.values[i],'参照得分':peer.values[i],
                           '综合分差贡献':round(changes[i],10),
                           '排名影响':'有利' if direction*changes[i]>0 else '不利'} for i in shown]
    else:
        result['解释口径'] = ('两股有效评分项不同，仅展示本股评分依据。' if peer else '展示本股有效评分依据。')
        best = max(used,key=lambda i:direction*row.values[i]*dims[i]['weight'])
        result['主要评分依据'] = {'因素':_label(row,dims[best],best),'得分':row.values[best],
                               '有效权重':dims[best]['weight']/total_weight}
    return result



def calculate(data):
    strategy, month, symbol, config, snapshots, dates = _load(data)
    missing = [g['label'] for g in config['groups'] if month not in snapshots[g['label']]]
    result = {'策略':strategy,'月份':month,'明细日期':dates.get(month),
              '选股口径':'按当期策略明细的综合分、组内排序和名额推算。',
              '数据覆盖':'当月无数据' if month not in dates else ('部分分组' if missing else '全部分组'),
              '缺失分组':missing}
    if month not in dates:
        return result
    found, groups = None, []
    for group in config['groups']:
        label, quota = group['label'], group['quota']
        rows = snapshots[label].get(month, [])
        if not rows:
            continue
        cutoff = rows[min(quota,len(rows))-1].score
        boundary_tie = len(rows)>quota and cutoff==rows[quota].score
        direction = 1 if group['order']=='descending' else -1
        ranks = {}
        for index,item in enumerate(rows,1):
            ranks.setdefault(item.score,index)
        summary = {'分组':label,'名额':quota,'明细股票数':len(rows),'名额线综合分':cutoff,
                   '排序':'高分优先' if direction==1 else '低分优先'}
        if boundary_tie:
            summary['并列待定名额'] = quota-ranks[cutoff]+1
            summary['名额线同分股票数'] = sum(x.score==cutoff for x in rows)
        selections = []
        for item in rows:
            if symbol and item.code!=symbol:
                continue
            rank = ranks[item.score]
            if not symbol and rank>quota:
                break
            if boundary_tie and item.score==cutoff:
                status, peer = '名额线并列待定', None
            elif rank<=quota:
                status, peer = '按规则入选', rows[quota] if len(rows)>quota else None
            else:
                status, peer = '按规则未入选', rows[quota-1]
            record = {'股票代码':item.code,'组内名次':rank,'综合分':item.score,'状态':status,
                      **_explain(item,peer,group)}
            if symbol:
                found = {**summary,**record}
                break
            selections.append(record)
        if not symbol:
            groups.append({**summary,'名单':selections})
    if symbol:
        result['个股'] = found or {'股票代码':symbol,'状态':'当期已有明细中无记录'}
    else:
        result['分组名单'] = groups
    return result


def main():
    parser = argparse.ArgumentParser(description='在本地按评分和组内名额解释策略选股')
    parser.add_argument('input', type=Path, help='包含查询条件、评分规则和原始本地记录的 UTF-8 JSON')
    args = parser.parse_args()
    try:
        with args.input.open(encoding='utf-8-sig') as stream:
            data = json.load(stream)
        result = calculate(data)
        output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    except (OSError, ValueError, OverflowError) as exc:
        parser.exit(1, str(exc) + '\n')
    sys.stdout.buffer.write((output + '\n').encode('utf-8'))


if __name__ == '__main__':
    main()
