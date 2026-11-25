"""Natural-calendar, close-to-close cross-sectional RankIC. No network access."""
from dataclasses import dataclass
from datetime import date
import re

import numpy as np
import pandas as pd

FREQUENCIES = {'daily': None, 'weekly': 'W-SUN', 'monthly': 'M', 'quarterly': 'Q-DEC'}


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('date must be YYYY-MM-DD')
    return date.fromisoformat(value).isoformat()


def normalize_code(value):
    if not isinstance(value, str):
        raise ValueError('stock codes must be strings, preserving leading zeros')
    value = value.strip().upper()
    if re.fullmatch(r'(SH|SZ|BJ)\d{6}', value):
        value = value[2:] + '.' + value[:2]
    if re.fullmatch(r'\d{6}', value):
        exchange = 'SH' if value.startswith(('6', '9')) else 'BJ' if value.startswith(('4', '8')) else 'SZ'
        if value.startswith('92'):
            exchange = 'BJ'
        value += '.' + exchange
    if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', value):
        raise ValueError('invalid stock code')
    return value


def is_a_share(code):
    return ((code.endswith('.SH') and code.startswith('6')) or
            (code.endswith('.SZ') and code.startswith(('00', '30'))) or
            (code.endswith('.BJ') and code.startswith(('43', '83', '87', '88', '92'))))


@dataclass(frozen=True)
class Parameters:
    factor_name: str
    start: str
    end: str
    frequency: str
    pool: str
    codes: tuple = ()
    min_stocks: int = 20

    def __post_init__(self):
        iso_date(self.start)
        iso_date(self.end)
        if not isinstance(self.factor_name, str) or not self.factor_name.strip() or len(self.factor_name) > 200:
            raise ValueError('factor_name is required (maximum 200 characters)')
        if self.start > self.end or self.frequency not in FREQUENCIES or self.pool not in ('all_a', 'custom'):
            raise ValueError('invalid date range, frequency or pool')
        if type(self.min_stocks) is not int or self.min_stocks < 3:
            raise ValueError('min_stocks must be an integer >= 3')
        if not isinstance(self.codes, (list, tuple)):
            raise ValueError('codes must be a list of strings')
        codes = tuple(sorted(set(normalize_code(c) for c in self.codes)))
        if any(not is_a_share(c) for c in codes):
            raise ValueError('custom pool accepts A-share stock codes only')
        if (self.pool == 'custom' and not codes) or (self.pool == 'all_a' and codes):
            raise ValueError('codes are required only for a custom pool')
        object.__setattr__(self, 'codes', codes)


def period_pairs(calendar, start, end, frequency, calendar_through):
    """Only closed natural periods; never use query-end as a period boundary."""
    start, end, through = map(iso_date, (start, end, calendar_through))
    if start > end or through < end or frequency not in FREQUENCIES:
        raise ValueError('calendar coverage or frequency is invalid')
    dates = [iso_date(d) for d in calendar]
    if len(set(dates)) != len(dates):
        raise ValueError('duplicate calendar dates')
    dates = sorted(d for d in dates if d <= through)
    if not dates or dates[0] > end:
        return []
    if frequency == 'daily':
        return [(day, dates[i + 1] if i + 1 < len(dates) else None)
                for i, day in enumerate(dates) if start <= day <= end]
    groups = {}
    for day in dates:
        period = pd.Period(day, freq=FREQUENCIES[frequency])
        if period.end_time.date().isoformat() <= through:
            groups[period] = day
    return [(day, groups.get(period + 1)) for period, day in sorted(groups.items())
            if start <= day <= end]


def _checked(series):
    if not isinstance(series, pd.Series):
        raise ValueError('cross section must be a Series')
    series = series.copy()
    series.index = [normalize_code(c) for c in series.index]
    if series.index.has_duplicates:
        raise ValueError('duplicate date/security keys')
    return pd.to_numeric(series, errors='raise').astype(float)


def summarize(rows):
    valid = [r for r in rows if r['status'] == 'valid']
    values = np.array([r['rank_ic'] for r in valid], dtype=float)
    mean = float(values.mean()) if len(values) else None
    std = (0.0 if np.all(values == values[0]) else float(values.std(ddof=1))) if len(values) > 1 else None
    icir = float(mean / std) if std is not None and std > 0 else None
    if icir is not None and not np.isfinite(icir):
        icir = None
    counts = [r['n_valid'] for r in valid]
    return dict(valid_periods=len(valid), invalid_periods=sum(r['status'] == 'invalid' for r in rows),
                incomplete_periods=sum(r['status'] == 'incomplete' for r in rows),
                mean_rank_ic=mean, std_rank_ic=std, icir=icir,
                positive_fraction=float((values > 0).mean()) if len(values) else None,
                min_valid_stocks=min(counts) if counts else None,
                mean_valid_stocks=float(np.mean(counts)) if counts else None,
                max_valid_stocks=max(counts) if counts else None)


def calculate(parameters, calendar, source, *, calendar_through, price_through):
    p = parameters
    price_through = iso_date(price_through)
    pairs = period_pairs(calendar, p.start, p.end, p.frequency, calendar_through)
    authorized = {normalize_code(c) for c in source.authorized_codes}
    rows = []
    for observation, exit_date in pairs:
        row = dict(observation_date=observation, exit_date=exit_date, status='invalid', reason='',
                   n_pool=0, n_valid=0, rank_ic=None, unknown_universe=0, excluded_authorization=0,
                   excluded_factor=0, excluded_price=0)
        if exit_date is None or exit_date > price_through:
            row.update(status='incomplete', reason='exit_not_available')
            rows.append(row)
            continue
        if p.pool == 'all_a':
            listed = _checked(source.listed_at(observation))
            membership = listed.reindex(sorted(c for c in authorized if is_a_share(c)))
            row['unknown_universe'] = int((~np.isfinite(membership)).sum())
            pool = sorted(c for c, value in listed.items() if value == 1 and is_a_share(c))
        else:
            pool = list(p.codes)
        row['n_pool'] = len(pool)
        permitted = [c for c in pool if c in authorized]
        row['excluded_authorization'] = len(pool) - len(permitted)
        factor = _checked(source.factor_at(observation)).reindex(permitted)
        buy = _checked(source.price_at(observation)).reindex(permitted)
        sell = _checked(source.price_at(exit_date)).reindex(permitted)
        good_factor = np.isfinite(factor)
        row['excluded_factor'] = int((~good_factor).sum())
        with np.errstate(over='ignore', divide='ignore', invalid='ignore'):
            returns = sell / buy - 1.0
        good_price = np.isfinite(buy) & (buy > 0) & np.isfinite(sell) & (sell > 0) & np.isfinite(returns)
        row['excluded_price'] = int((good_factor & ~good_price).sum())
        selected = good_factor & good_price
        frame = pd.DataFrame({'value': factor[selected], 'forward_return': returns[selected]})
        row['n_valid'] = len(frame)
        if len(frame) < p.min_stocks:
            row['reason'] = 'insufficient_stocks'
        elif frame['value'].nunique() < 2 or frame['forward_return'].nunique() < 2:
            row['reason'] = 'constant_vector'
        else:
            ranks = frame.rank(method='average')
            factor_ranks, return_ranks = ranks['value'], ranks['forward_return']
            # Exact rank identities must not acquire sample-size-dependent roundoff.
            if np.array_equal(factor_ranks, return_ranks):
                ic = 1.0
            elif np.array_equal(factor_ranks + return_ranks, np.full(len(ranks), len(ranks) + 1.0)):
                ic = -1.0
            else:
                ic = float(factor_ranks.corr(return_ranks))
            if np.isfinite(ic):
                row.update(status='valid', reason='', rank_ic=max(-1.0, min(1.0, ic)))
            else:
                row['reason'] = 'undefined_correlation'
        rows.append(row)
    summary = summarize(rows)
    coverage_partial = any(r['unknown_universe'] for r in rows)
    status = ('no_result' if not summary['valid_periods'] else
              'partial_success' if summary['invalid_periods'] or summary['incomplete_periods'] or coverage_partial else 'success')
    warnings = ['Theoretical close-to-close RankIC; no trading costs or tradeability filters.',
                'ICIR is not annualized. No neutralization, winsorization or direction reversal.']
    if coverage_partial:
        warnings.append('Historical universe has unknown membership in the authorized scope; all-A completeness is unverified. See unknown_universe per date.')
    if len(p.codes) < 20 and p.pool == 'custom':
        warnings.append('Small custom pool: the configured minimum is not lowered automatically.')
    if summary['valid_periods'] < 10:
        warnings.append('Fewer than 10 valid periods; estimates are unstable, not significance evidence.')
    if summary['std_rank_ic'] is None:
        warnings.append('Sample standard deviation and ICIR are undefined with fewer than two valid periods.')
    elif summary['std_rank_ic'] == 0:
        warnings.append('ICIR is undefined because sample standard deviation is zero.')
    if not pairs:
        warnings.append('No completed observation period falls in the requested range.')
    priced = [r for r in rows if r['exit_date'] is not None and r['exit_date'] <= price_through]
    return dict(status=status, summary=summary, periods=rows, warnings=warnings,
                return_range=[min(r['observation_date'] for r in priced), max(r['exit_date'] for r in priced)]
                if priced else None)
