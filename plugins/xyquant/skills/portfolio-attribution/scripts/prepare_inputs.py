"""Plan public-data selectors from local customer files; weights stay local."""
import argparse
import json
import sys
import uuid
import pandas as pd
from data_store import FREQUENCIES, require, schedule
from input_data import weight_input


def plan(holdings, benchmark, calendar, start, end, frequency):
    frequency = FREQUENCIES.get(frequency)
    require(frequency, '频率仅支持日、周、月')
    calendar = pd.DatetimeIndex(pd.to_datetime(calendar))
    periods = schedule(calendar, pd.Timestamp(start), pd.Timestamp(end), frequency)
    starts = pd.DatetimeIndex([x[0] for x in periods])
    codes = set()
    for path, kind in [(holdings, 'portfolio'), (benchmark, 'benchmark')]:
        rows, snapshots = weight_input(path, kind, kind, calendar, frequency)
        available = set(snapshots.loc[snapshots.valid, 'date'])
        require(set(starts) <= available, '缺少有效期初持仓快照', kind=kind,
                dates=[str(x.date()) for x in starts if x not in available])
        codes.update(rows.loc[rows.date.isin(starts), 'stock_code'])
    common = {'dataset': 'bundle', 'start': str(periods[0][0].date()),
              'end': str(periods[-1][1].date()), 'codes': sorted(codes) if len(codes)<=4000 else []}
    stock_query = {**common, 'request_id': str(uuid.uuid4())}
    barra_query = {**common, 'frequency': frequency, 'request_id': str(uuid.uuid4())}
    if len(starts)<=1000:
        barra_query['exposure_dates'] = [str(d.date()) for d in starts]
    return {'requests': {'query_stock_data': stock_query, 'query_barra_data': barra_query}, 'stock_count': len(codes),
            'periods': [[str(a.date()), str(b.date())] for a, b in periods]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ['holdings', 'benchmark-holdings', 'start', 'end', 'frequency']:
        parser.add_argument('--'+key, required=True)
    args = parser.parse_args()
    try:
        calendar = json.load(sys.stdin)['dates']
        print(json.dumps(plan(args.holdings, args.benchmark_holdings, calendar, args.start, args.end, args.frequency), ensure_ascii=False))
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({'status': 'error', 'message': str(error), 'details': getattr(error, 'details', {})}, ensure_ascii=False))
        raise SystemExit(2)
