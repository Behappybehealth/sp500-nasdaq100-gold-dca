"""3-way strategy comparison backtest over 5 years, all possible start days.

Modes:
  dynamic : current skill - amount = pool/remaining_td x deploy(score), weights tilted by score
  tilt    : fixed 1500/day, weights tilted by score model
  equal   : fixed 1500/day, equal thirds (500 each)
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, r'C:\Users\xiezhibo\.claude\skills\sp500-nasdaq100-gold-dca\scripts')
import dca_calculator as m

BASE = Path(r'C:\Users\xiezhibo\.claude\skills\sp500-nasdaq100-gold-dca')
CACHE = BASE / 'data' / 'market_history'
OUT = Path(r'C:\Users\xiezhibo\backtest-dca-5y')
config = m.read_json(BASE / 'data' / 'config.json')
assets = config['assets']
model = dict(m.DEFAULT_MODEL)
model.update(config.get('model', {}))
SW = model['score_weights']
NEUTRAL = {k: float(i['neutral_weight']) for k, i in assets.items()}
EQUAL = {k: 1.0 / 3.0 for k in assets}
MONTHLY = 30000.0
DAILY_FIXED = 1500.0
FX = 6.7334
SIG = {'sp500': '^GSPC', 'nasdaq100': '^NDX', 'gold': 'GC=F'}
TRADE = {'sp500': 'SPY', 'nasdaq100': 'QQQ', 'gold': 'GC=F'}
START_MIN = '2021-08-11'
END = '2026-08-10'


def load(sym):
    closes = m.load_cached_closes(m.cache_file_for(CACHE, sym))
    days = sorted(closes)
    return days, [closes[d] for d in days], {d: i for i, d in enumerate(days)}


DATA = {s: load(s) for s in set(list(SIG.values()) + list(TRADE.values()))}
CAL = [d for d in DATA['^GSPC'][0] if START_MIN <= d <= END]

METRICS = {sym: {} for sym in SIG.values()}
for sym in SIG.values():
    days, closes, idx = DATA[sym]
    for d in CAL:
        i = idx[d]
        win = closes[max(0, i - 259):i + 1]
        METRICS[sym][d] = m.metrics_from_closes(win, closes[i], days[max(0, i - 259)], d)

MONTH_DAYS = {}
for d in CAL:
    MONTH_DAYS.setdefault(d[:7], []).append(d)
DAYINFO = {}
for d in CAL:
    mdays = MONTH_DAYS[d[:7]]
    i = mdays.index(d)
    y, mo = int(d[:4]), int(d[5:7])
    first_next = date(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
    DAYINFO[d] = {'month': d[:7], 'month_total_td': len(mdays), 'td_index': i,
                  'remaining_td': len(mdays) - i,
                  'remaining_cal_days': (first_next - date.fromisoformat(d)).days}

RELEASE_WINDOW = 7
SKIP_BELOW = float(model['skip_below'])
GAIN = float(model['deploy_gain'])
DMAX = float(model['deploy_max'])


def run(start_day, mode):
    days = [d for d in CAL if d >= start_day]
    shares = {k: 0.0 for k in assets}
    units = 0.0
    invested_total = 0.0
    flows = []
    uw_days = 0
    consec = 0
    max_consec = 0
    navs = []
    cur_month = None
    month_pool = 0.0
    invested_month = 0.0
    pool_sum = 0.0
    for d in days:
        info = DAYINFO[d]
        if mode == 'dynamic':
            if info['month'] != cur_month:
                cur_month = info['month']
                forfeited = MONTHLY * info['td_index'] / info['month_total_td'] if d == start_day else 0.0
                month_pool = MONTHLY - forfeited
                invested_month = 0.0
                pool_sum += month_pool
            pool = max(0.0, month_pool - invested_month)
            base = pool / info['remaining_td']
        scores = {k: m.asset_score(METRICS[SIG[k]][d], SW) for k in assets}
        if mode == 'dynamic':
            eq = [scores[k]['score'] for k in assets if k != 'gold' and scores[k].get('score') is not None]
            opp = sum(eq) / len(eq) if eq else 0.0
            deploy = m.clip(1.0 + GAIN * opp, 0.0, DMAX)
            wts = m.score_based_weights(scores, assets, model)
            amt = 0.0 if deploy < SKIP_BELOW else base * deploy
            if amt > 0 and info['remaining_cal_days'] <= RELEASE_WINDOW:
                amt = max(amt, base)
            amt = min(amt, pool)
        elif mode == 'tilt':
            amt = DAILY_FIXED
            wts = m.score_based_weights(scores, assets, model)
            pool_sum += DAILY_FIXED
        else:  # equal
            amt = DAILY_FIXED
            wts = EQUAL
            pool_sum += DAILY_FIXED
        px = {k: DATA[TRADE[k]][1][DATA[TRADE[k]][2][d]] for k in assets}
        value_pre = sum(shares[k] * px[k] for k in assets) * FX
        nav = value_pre / units if units > 0 else 1.0
        if amt > 0:
            units += amt / nav
            for k in assets:
                shares[k] += amt * wts[k] / FX / px[k]
            invested_month += amt
            invested_total += amt
            flows.append((date.fromisoformat(d), -amt))
        value = value_pre + amt
        navs.append(value / units if units > 0 else 1.0)
        if value < invested_total - 1e-9:
            uw_days += 1
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0
    px = {k: DATA[TRADE[k]][1][DATA[TRADE[k]][2][days[-1]]] for k in assets}
    final_value = sum(shares[k] * px[k] for k in assets) * FX
    peak = -1e18
    maxdd = 0.0
    for v in navs:
        peak = max(peak, v)
        if peak > 0:
            maxdd = min(maxdd, v / peak - 1.0)
    x = m.xirr(flows + [(date.fromisoformat(days[-1]), final_value)]) if invested_total > 0 else None
    n = len(days)
    return {
        'start': start_day, 'days': n,
        'invested': round(invested_total, 2), 'final_value': round(final_value, 2),
        'simple_return': (final_value / invested_total - 1.0) if invested_total else None,
        'xirr': x, 'max_nav_dd': maxdd,
        'uw_days': uw_days, 'uw_ratio': uw_days / n if n else None,
        'max_consec_uw': max_consec,
    }


def bucket(days):
    if days < 63: return '<3mo'
    if days < 126: return '3-6mo'
    if days < 252: return '6-12mo'
    if days < 504: return '1-2y'
    if days < 756: return '2-3y'
    return '3y+'


BUCKETS = ['<3mo', '3-6mo', '6-12mo', '1-2y', '2-3y', '3y+']


def agg(rows):
    out = {}
    for b in BUCKETS:
        g = [r for r in rows if bucket(r['days']) == b]
        if not g:
            continue
        n = len(g)
        med = lambda a: a[n // 2] if n % 2 else (a[n // 2 - 1] + a[n // 2]) / 2
        rets = sorted(r['simple_return'] for r in g)
        xirrs = sorted(r['xirr'] for r in g if r['xirr'] is not None)
        dds = sorted(r['max_nav_dd'] for r in g)
        uw = sorted(r['uw_ratio'] for r in g)
        out[b] = {
            'n': n,
            'win_rate': sum(1 for r in g if r['simple_return'] > 0) / n,
            'ret_med': med(rets), 'ret_worst': rets[0],
            'xirr_med': med(xirrs) if xirrs else None,
            'maxdd_med': med(dds), 'maxdd_worst': dds[0],
            'uw_ratio_med': med(uw),
        }
    return out


results = {}
for mode in ['dynamic', 'tilt', 'equal']:
    rows = []
    for i, s in enumerate(CAL):
        rows.append(run(s, mode))
        if (i + 1) % 400 == 0:
            print(f'{mode} {i + 1}/{len(CAL)}', flush=True)
    results[mode] = rows

summary = {mode: agg(results[mode]) for mode in results}


def overall(rows):
    xirrs = [r['xirr'] for r in rows if r['xirr'] is not None]
    return {
        'win_rate': sum(1 for r in rows if r['simple_return'] > 0) / len(rows),
        'ret_med': sorted(r['simple_return'] for r in rows)[len(rows) // 2],
        'xirr_med': sorted(xirrs)[len(xirrs) // 2],
        'maxdd_worst': min(r['max_nav_dd'] for r in rows),
        'maxdd_med': sorted(r['max_nav_dd'] for r in rows)[len(rows) // 2],
        'uw_ratio_mean': sum(r['uw_ratio'] for r in rows) / len(rows),
    }


ov = {mode: overall(results[mode]) for mode in results}


def beat_pct(a, b):
    pairs = [(x['xirr'], y['xirr']) for x, y in zip(results[a], results[b]) if x['xirr'] is not None and y['xirr'] is not None]
    diffs = [x - y for x, y in pairs]
    return {'beat_pct': sum(1 for d in diffs if d > 0) / len(diffs), 'mean_diff': sum(diffs) / len(diffs)}


pairwise = {
    'dynamic_vs_equal': beat_pct('dynamic', 'equal'),
    'tilt_vs_equal': beat_pct('tilt', 'equal'),
    'dynamic_vs_tilt': beat_pct('dynamic', 'tilt'),
}

EXAMPLE_STARTS = ['2021-08-11', '2022-01-03', '2022-10-03', '2024-08-05', '2026-02-02']
examples = []
for es in EXAMPLE_STARTS:
    s = next((d for d in CAL if d >= es), None)
    if s:
        examples.append({'start': s, **{mode: run(s, mode) for mode in results}})

final = {'overall': ov, 'pairwise': pairwise, 'buckets': summary, 'examples': examples,
         'meta': {'window': [START_MIN, END], 'paths': len(CAL), 'daily_fixed': DAILY_FIXED, 'monthly_budget': MONTHLY}}
with (OUT / 'results_compare3.json').open('w', encoding='utf-8') as f:
    json.dump(final, f, ensure_ascii=False, indent=2)
print(json.dumps(final, ensure_ascii=False))
