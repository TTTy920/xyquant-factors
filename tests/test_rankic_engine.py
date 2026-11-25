"""Contract tests: calendar alignment, cross-sectional math and diagnostics."""
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/xyquant/skills/factor-rankic/scripts'
sys.path.insert(0, str(SCRIPTS))
import rankic_engine as engine


CODES = ['000001.SZ', '000002.SZ', '600000.SH', '600001.SH']


class MemorySource:
    def __init__(self, factors, prices, listed=None, authorized=None):
        self.factors = factors
        self.prices = prices
        self.listed = listed
        self.authorized_codes = set(authorized or CODES)

    def factor_at(self, date):
        return self.factors.get(date, pd.Series(dtype=float))

    def price_at(self, date):
        return self.prices.get(date, pd.Series(dtype=float))

    def listed_at(self, date):
        if self.listed is None:
            raise ValueError('historical universe required')
        return self.listed.get(date, pd.Series(dtype=float))


def series(values, codes=CODES):
    return pd.Series(values, index=codes, dtype=float)


def params(**updates):
    values = dict(factor_name='test', start='2025-01-03', end='2025-01-03',
                  frequency='weekly', pool='custom', codes=CODES, min_stocks=3)
    values.update(updates)
    return engine.Parameters(**values)


def calculate(source, settings=None, calendar=None, through='2025-01-12', price_through='2025-01-10'):
    return engine.calculate(settings or params(), calendar or ['2025-01-02', '2025-01-03', '2025-01-10'],
                            source, calendar_through=through, price_through=price_through)


class CalendarTest(unittest.TestCase):
    def pairs(self, dates, start, end, frequency, through):
        return engine.period_pairs(dates, start, end, frequency, through)

    def test_weekly_uses_next_natural_week_not_query_end(self):
        dates = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-10']
        self.assertEqual(self.pairs(dates, '2025-01-02', '2025-01-02', 'weekly', '2025-01-12'), [])
        self.assertEqual(self.pairs(dates, '2025-01-03', '2025-01-03', 'weekly', '2025-01-12'),
                         [('2025-01-03', '2025-01-10')])

    def test_month_quarter_leap_year_and_holiday(self):
        dates = ['2024-02-28', '2024-02-29', '2024-03-28', '2024-04-30', '2024-06-28']
        self.assertEqual(self.pairs(dates, '2024-02-01', '2024-02-29', 'monthly', '2024-04-30'),
                         [('2024-02-29', '2024-03-28')])
        self.assertEqual(self.pairs(dates, '2024-03-01', '2024-03-31', 'quarterly', '2024-06-30'),
                         [('2024-03-28', '2024-06-28')])

    def test_week_crosses_year(self):
        self.assertEqual(self.pairs(['2024-12-27', '2024-12-31', '2025-01-03'],
                                   '2024-12-27', '2024-12-27', 'weekly', '2025-01-05'),
                         [('2024-12-27', '2025-01-03')])

    def test_daily_next_trading_day_and_unknown_exit(self):
        self.assertEqual(self.pairs(['2025-01-03', '2025-01-06'], '2025-01-03', '2025-01-06',
                                   'daily', '2025-01-06'),
                         [('2025-01-03', '2025-01-06'), ('2025-01-06', None)])

    def test_incomplete_current_calendar_period_is_not_period_end(self):
        self.assertEqual(self.pairs(['2025-01-02', '2025-01-03', '2025-01-08'],
                                   '2025-01-02', '2025-01-08', 'weekly', '2025-01-08'),
                         [('2025-01-03', None)])

    def test_no_session_in_next_cycle_does_not_jump_to_later_cycle(self):
        # A complete holiday week can legitimately have no trading sessions.
        self.assertEqual(self.pairs(['2025-01-03', '2025-01-17'], '2025-01-03',
                                   '2025-01-03', 'weekly', '2025-01-19'),
                         [('2025-01-03', None)])

    def test_duplicate_dates_and_insufficient_calendar_are_errors(self):
        for dates, through in [(['2025-01-03'] * 2, '2025-01-12'), (['2025-01-03'], '2025-01-02')]:
            with self.subTest(dates=dates, through=through), self.assertRaises(ValueError):
                self.pairs(dates, '2025-01-03', '2025-01-03', 'weekly', through)


class CalculationTest(unittest.TestCase):
    def source(self, factor=(1, 2, 3, 4), exit_prices=(11, 12, 13, 14), **kwargs):
        return MemorySource({'2025-01-03': series(factor)},
                            {'2025-01-03': series([10] * 4), '2025-01-10': series(exit_prices)}, **kwargs)

    def test_perfect_positive_and_negative(self):
        for values, expected in [((1, 2, 3, 4), 1), ((4, 3, 2, 1), -1)]:
            result = calculate(self.source(factor=values))
            self.assertEqual(result['status'], 'success')
            self.assertAlmostEqual(result['periods'][0]['rank_ic'], expected)
            self.assertIsNone(result['summary']['std_rank_ic'])
            self.assertIsNone(result['summary']['icir'])

    def test_average_ties_are_not_ordinal_ranks(self):
        result = calculate(self.source(factor=(1, 1, 2, 3)))
        self.assertAlmostEqual(result['periods'][0]['rank_ic'], 0.9486832980505138)

    def test_constant_vectors_are_invalid_not_zero(self):
        for source in [self.source(factor=(1, 1, 1, 1)), self.source(exit_prices=(11, 11, 11, 11))]:
            result = calculate(source)
            self.assertEqual(result['status'], 'no_result')
            self.assertIsNone(result['periods'][0]['rank_ic'])
            self.assertIn('constant', result['periods'][0]['reason'])

    def test_exclusions_are_disjoint_and_no_forward_fill(self):
        source = self.source(factor=(np.nan, 2, 3, 4), exit_prices=(11, 0, np.inf, 14))
        row = calculate(source)['periods'][0]
        self.assertEqual(row['n_valid'], 1)
        self.assertEqual(row['excluded_factor'], 1)
        self.assertEqual(row['excluded_price'], 2)
        self.assertIsNone(row['rank_ic'])
        self.assertEqual(row['reason'], 'insufficient_stocks')

    def test_default_threshold_is_not_silently_lowered(self):
        result = calculate(self.source(), params(min_stocks=20))
        self.assertEqual(result['status'], 'no_result')
        with self.assertRaises(ValueError):
            params(min_stocks=2)

    def test_custom_codes_normalized_deduplicated_and_authorized(self):
        settings = params(codes=['000001', 'sz000001', '000002.SZ', '600000.sh', '600001.SH'])
        self.assertEqual(len(settings.codes), 4)
        row = calculate(self.source(authorized=CODES[:3]), settings)['periods'][0]
        self.assertEqual(row['n_pool'], 4)
        self.assertEqual(row['excluded_authorization'], 1)
        self.assertEqual(row['n_valid'], 3)
        self.assertAlmostEqual(row['rank_ic'], 1)

    def test_full_a_uses_historical_membership_at_t(self):
        source = self.source(listed={'2025-01-03': series([1, 1, 1, 0])})
        row = calculate(source, params(pool='all_a', codes=[]))['periods'][0]
        self.assertEqual(row['n_pool'], 3)
        self.assertEqual(row['n_valid'], 3)
        with self.assertRaisesRegex(ValueError, 'historical'):
            calculate(self.source(), params(pool='all_a', codes=[]))

    def test_unknown_historical_membership_is_disclosed_as_partial_coverage(self):
        source = self.source(listed={'2025-01-03': series([1, 1, 1], codes=CODES[:3])})
        result = calculate(source, params(pool='all_a', codes=[]))
        self.assertEqual(result['status'], 'partial_success')
        self.assertEqual(result['periods'][0]['unknown_universe'], 1)
        self.assertTrue(any('Historical universe' in warning for warning in result['warnings']))

    def test_duplicate_security_index_is_an_error(self):
        source = self.source()
        source.factors['2025-01-03'] = series([1, 2, 3, 4], codes=['000001.SZ'] * 4)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            calculate(source)

    def test_unavailable_exit_is_incomplete_not_zero(self):
        result = calculate(self.source(), price_through='2025-01-09')
        self.assertEqual(result['status'], 'no_result')
        self.assertEqual(result['summary']['incomplete_periods'], 1)
        self.assertIsNone(result['periods'][0]['rank_ic'])

    def test_summary_uses_valid_dates_and_sample_std(self):
        source = self.source()
        source.factors.update({'2025-01-10': series([4, 3, 2, 1]), '2025-01-17': series([1] * 4)})
        source.prices['2025-01-17'] = series([12.1, 14.4, 16.9, 19.6])
        source.prices['2025-01-24'] = series([13, 16, 20, 25])
        result = calculate(source, params(end='2025-01-17'),
                           ['2025-01-03', '2025-01-10', '2025-01-17', '2025-01-24'],
                           through='2025-01-26', price_through='2025-01-24')
        self.assertEqual(result['status'], 'partial_success')
        summary = result['summary']
        self.assertEqual((summary['valid_periods'], summary['invalid_periods']), (2, 1))
        self.assertAlmostEqual(summary['mean_rank_ic'], 0)
        self.assertAlmostEqual(summary['std_rank_ic'], 2 ** 0.5)
        self.assertAlmostEqual(summary['icir'], 0)
        self.assertEqual(summary['positive_fraction'], 0.5)
        self.assertEqual(result['return_range'], ['2025-01-03', '2025-01-24'])

    def test_stock_pool_rejects_shenzhen_indices(self):
        with self.assertRaisesRegex(ValueError, 'A-share'):
            params(codes=['000001.SZ', '000002.SZ', '399001.SZ'])

    def test_beijing_stock_segments_exclude_indices_and_other_securities(self):
        for code in ['899050.BJ', '899001.BJ', '400001.BJ', '800001.BJ']:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, 'A-share'):
                params(codes=[code, *CODES[:2]])
        for code in ['430047.BJ', '830799.BJ', '870199.BJ', '880001.BJ', '920001.BJ']:
            with self.subTest(code=code):
                self.assertIn(code, params(codes=[code, *CODES[:2]]).codes)

    def test_identical_perfect_ranks_have_zero_std_across_sample_sizes(self):
        codes = [*CODES, '600002.SH']
        dates = ['2025-01-03', '2025-01-10', '2025-01-17']
        for direction in [1, -1]:
            factors = {dates[0]: series([1, 2, 3], codes[:3]),
                       dates[1]: series([1, 2, 3, 4, 5], codes)}
            prices = [np.full(5, 10.0)]
            for _ in range(2):
                prices.append(prices[-1] * (1 + direction * np.arange(1, 6) / 10))
            source = MemorySource(factors, {day: series(value, codes) for day, value in zip(dates, prices)},
                                  authorized=codes)
            result = calculate(source, params(codes=codes, end=dates[1]), dates,
                               through='2025-01-19', price_through=dates[2])
            with self.subTest(direction=direction):
                self.assertEqual([r['n_valid'] for r in result['periods']], [3, 5])
                self.assertEqual([r['rank_ic'] for r in result['periods']], [float(direction)] * 2)
                self.assertEqual(result['summary']['std_rank_ic'], 0.0)
                self.assertIsNone(result['summary']['icir'])

    def test_identical_nonperfect_values_also_have_exact_zero_std(self):
        summary = engine.summarize([dict(status='valid', rank_ic=0.1, n_valid=20)] * 3)
        self.assertEqual(summary['std_rank_ic'], 0.0)
        self.assertIsNone(summary['icir'])
        # A genuine, however small, difference must not be rounded away.
        summary = engine.summarize([dict(status='valid', rank_ic=value, n_valid=20)
                                    for value in [0.1, np.nextafter(0.1, 1)]])
        self.assertGreater(summary['std_rank_ic'], 0)
        self.assertIsNotNone(summary['icir'])

    def test_invalid_parameters_fail_early(self):
        for updates in [dict(start='2025-02-30'), dict(end='2024-01-01'), dict(frequency='yearly'),
                        dict(pool='index'), dict(codes=[]), dict(min_stocks=True)]:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                params(**updates)


if __name__ == '__main__':
    unittest.main()
