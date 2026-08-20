"""5-year full-history backtest of the DCA strategy: start on ANY trading day, invest daily until end.

Compares the dynamic model (score-driven deploy multiplier + tilted weights + skip + month-end release)
against a fixed-amount, neutral-weight baseline, over identical budget rules.

【归档脚本 · 非回归载体】一次性回测，结果已定稿在 backtest/results.json；Tab5「回测结果」
读的是那份 json，不会调用本脚本。重跑会覆盖同目录的 results.json，想留底先拷走。
路径按 __file__ 相对定位，随项目搬家不会断。
"""
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # project root (this script lives in backtest/)
sys.path.insert(0, str(BASE / 'scripts'))
import dca_calculator as m  # noqa: E402  -- needs the sys.path wiring above

CACHE = BASE / 'data' / 'market_history'
OUT = Path(__file__).resolve().parent  # results land next to this script
config = m.read_json(BASE / 'data' / 'config.json')
assets = config['assets']
model = dict(m.DEFAULT_MODEL)
model.update(config.get('model', {}))
SW = model['score_weights']
NEUTRAL = {k: float(i['neutral_weight']) for k, i in assets.items()}
MONTHLY = 30000.0
FX = 6.7334  # constant fx for backtest (RMB terms); gold priced via GC=F (U≈USD)
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

# precompute per-day metric dicts for the three signal series (only backtest-window days)
METRICS = {sym: {} for sym in SIG.values()}
for sym in SIG.values():
    days, closes, idx = DATA[sym]
    for d in CAL:
        i = idx[d]
        win = closes[max(0, i - 259):i + 1]
        METRICS[sym][d] = m.metrics_from_closes(win, closes[i], days[max(0, i - 259)], d)

# precompute per-day budget helpers from the real trading calendar
MONTH_DAYS = {}
for d in CAL:
    MONTH_DAYS.setdefault(d[:7], []).append(d)
DAYINFO = {}
for d in CAL:
    mdays = MONTH_DAYS[d[:7]]
    i = mdays.index(d)
    y, mo = int(d[:4]), int(d[5:7])
    first_next = date(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
    DAYINFO[d] = {
        'month': d[:7], 'month_total_td': len(mdays), 'td_index': i,
        'remaining_td': len(mdays) - i,
        'remaining_cal_days': (first_next - date.fromisoformat(d)).days,
    }

RELEASE_WINDOW = 7
SKIP_BELOW = float(model['skip_below'])
GAIN = float(model['deploy_gain'])
DMAX = float(model['deploy_max'])


def run(start_day, dynamic):
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
        if info['month'] != cur_month:
            cur_month = info['month']
            forfeited = MONTHLY * info['td_index'] / info['month_total_td'] if d == start_day else 0.0
            month_pool = MONTHLY - forfeited
            invested_month = 0.0
            pool_sum += month_pool
        pool = max(0.0, month_pool - invested_month)
        base = pool / info['remaining_td']
        if dynamic:
            scores = {k: m.asset_score(METRICS[SIG[k]][d], SW) for k in assets}
            eq = [scores[k]['score'] for k in assets if k != 'gold' and scores[k].get('score') is not None]
            opp = sum(eq) / len(eq) if eq else 0.0
            deploy = m.clip(1.0 + GAIN * opp, 0.0, DMAX)
            wts = m.score_based_weights(scores, assets, model)
        else:
            deploy = 1.0
            wts = NEUTRAL
        amt = 0.0 if (dynamic and deploy < SKIP_BELOW) else base * deploy
        if amt > 0 and info['remaining_cal_days'] <= RELEASE_WINDOW:
            amt = max(amt, base)  # month-end release floor
        amt = min(amt, pool)
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
        nav_now = value / units if units > 0 else 1.0
        navs.append(nav_now)
        if value < invested_total - 1e-9:
            uw_days += 1
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0
    final_value = navs[-1] * units if units > 0 else 0.0
    # recompute final value precisely at END prices
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
        'start': start_day, 'days': n, 'months': round(n / 21.0, 1),
        'invested': round(invested_total, 2), 'final_value': round(final_value, 2),
        'simple_return': (final_value / invested_total - 1.0) if invested_total else None,
        'xirr': x, 'max_nav_dd': maxdd,
        'uw_days': uw_days, 'uw_ratio': uw_days / n if n else None,
        'max_consec_uw': max_consec,
        'deploy_ratio': (invested_total / pool_sum) if pool_sum else None,
    }


def bucket(days):
    if days < 63: return '<3mo'
    if days < 126: return '3-6mo'
    if days < 252: return '6-12mo'
    if days < 504: return '1-2y'
    if days < 756: return '2-3y'
    return '3y+'


BUCKETS = ['<3mo', '3-6mo', '6-12mo', '1-2y', '2-3y', '3y+']

starts = CAL[:]
dyn = []
base = []
for i, s in enumerate(starts):
    dyn.append(run(s, True))
    base.append(run(s, False))
    if (i + 1) % 200 == 0:
        print(f'progress {i + 1}/{len(starts)}', flush=True)


def agg(rows):
    out = {}
    for b in BUCKETS:
        g = [r for r in rows if bucket(r['days']) == b]
        if not g:
            continue
        rets = sorted(r['simple_return'] for r in g)
        xirrs = sorted(r['xirr'] for r in g if r['xirr'] is not None)
        dds = sorted(r['max_nav_dd'] for r in g)
        uw = sorted(r['uw_ratio'] for r in g)
        n = len(g)
        med = lambda a: a[n // 2] if n % 2 else (a[n // 2 - 1] + a[n // 2]) / 2
        out[b] = {
            'n': n,
            'win_rate': sum(1 for r in g if r['simple_return'] > 0) / n,
            'ret_med': med(rets), 'ret_p10': rets[max(0, int(n * 0.1))], 'ret_p90': rets[min(n - 1, int(n * 0.9))],
            'ret_worst': rets[0], 'ret_best': rets[-1],
            'xirr_med': med(xirrs) if xirrs else None,
            'maxdd_med': med(dds), 'maxdd_worst': dds[0],
            'uw_ratio_med': med(uw), 'uw_days_max': max(r['uw_days'] for r in g),
            'max_consec_uw_max': max(r['max_consec_uw'] for r in g),
            'deploy_med': med(sorted(r['deploy_ratio'] for r in g)),
        }
    return out


summary = {'dynamic': agg(dyn), 'baseline': agg(base)}
diffs = [d['xirr'] - b['xirr'] for d, b in zip(dyn, base) if d['xirr'] is not None and b['xirr'] is not None]
overall = {
    'paths': len(dyn),
    'xirr_diff_mean': sum(diffs) / len(diffs),
    'model_beats_base_pct': sum(1 for x in diffs if x > 0) / len(diffs),
    'dyn_win_rate': sum(1 for r in dyn if r['simple_return'] > 0) / len(dyn),
    'base_win_rate': sum(1 for r in base if r['simple_return'] > 0) / len(base),
    'dyn_uw_ratio_mean': sum(r['uw_ratio'] for r in dyn) / len(dyn),
    'base_uw_ratio_mean': sum(r['uw_ratio'] for r in base) / len(base),
    'dyn_maxdd_worst': min(r['max_nav_dd'] for r in dyn),
    'base_maxdd_worst': min(r['max_nav_dd'] for r in base),
    'dyn_deploy_mean': sum(r['deploy_ratio'] for r in dyn) / len(dyn),
}

EXAMPLE_STARTS = ['2021-08-11', '2022-01-03', '2022-10-03', '2023-07-03', '2024-08-05', '2025-04-07', '2026-02-02']
examples = []
for es in EXAMPLE_STARTS:
    s = next((d for d in CAL if d >= es), None)
    if not s:
        continue
    rd = run(s, True)
    rb = run(s, False)
    examples.append({'start': s, 'dynamic': rd, 'baseline': rb})

result = {'overall': overall, 'buckets': summary, 'examples': examples,
          'meta': {'window': [START_MIN, END], 'paths': len(dyn), 'monthly_budget': MONTHLY, 'fx': FX,
                   'trade_prices': TRADE, 'signal_series': SIG}}
OUT.mkdir(parents=True, exist_ok=True)
with (OUT / 'results.json').open('w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(json.dumps(result, ensure_ascii=False))
