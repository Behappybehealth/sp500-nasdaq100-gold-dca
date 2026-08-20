"""Single-asset backtest: 100% SP500 / NDX100 / Gold, each with the dynamic amount mechanism.

Same budget rules as the live skill (monthly 30000, first-month forfeiture,
skip roll-forward, month-end release); deploy multiplier driven by that asset's own score.

【归档脚本 · 非回归载体】一次性回测，结果已定稿在 backtest/results_single_compare.json；
Tab5「回测结果」读的是那份 json，不会调用本脚本。重跑会覆盖同目录的
results_single_compare.json，想留底先拷走。路径按 __file__ 相对定位，随项目搬家不会断。
"""
import json
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # project root (this script lives in backtest/)
sys.path.insert(0, str(BASE / 'scripts'))
import dca_calculator as m  # noqa: E402  -- needs the sys.path wiring above

CACHE = BASE / 'data' / 'market_history'
OUT = Path(__file__).resolve().parent  # results land next to this script
config = m.read_json(BASE / 'data' / 'config.json')
model = dict(m.DEFAULT_MODEL)
model.update(config.get('model', {}))
SW = model['score_weights']
MONTHLY = 30000.0
FX = 6.7334
SINGLE = {
    'sp500': {'sig': '^GSPC', 'trade': 'SPY'},
    'nasdaq100': {'sig': '^NDX', 'trade': 'QQQ'},
    'gold': {'sig': 'GC=F', 'trade': 'GC=F'},
}
START_MIN = '2021-08-11'
END = '2026-08-10'


def load(sym):
    closes = m.load_cached_closes(m.cache_file_for(CACHE, sym))
    days = sorted(closes)
    return days, [closes[d] for d in days], {d: i for i, d in enumerate(days)}


DATA = {s: load(s) for s in {v['sig'] for v in SINGLE.values()} | {v['trade'] for v in SINGLE.values()}}
CAL = [d for d in DATA['^GSPC'][0] if START_MIN <= d <= END]

METRICS = {sym: {} for sym in {v['sig'] for v in SINGLE.values()}}
for sym in METRICS:
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


def run(start_day, key, mode):
    sig = SINGLE[key]['sig']
    trade = SINGLE[key]['trade']
    tdays, tcloses, tidx = DATA[trade]
    days = [d for d in CAL if d >= start_day]
    shares = 0.0
    units = 0.0
    invested_total = 0.0
    flows = []
    uw_days = consec = max_consec = 0
    navs = []
    cur_month = None
    month_pool = invested_month = 0.0
    for d in days:
        info = DAYINFO[d]
        if mode == 'dynamic':
            if info['month'] != cur_month:
                cur_month = info['month']
                forfeited = MONTHLY * info['td_index'] / info['month_total_td'] if d == start_day else 0.0
                month_pool = MONTHLY - forfeited
                invested_month = 0.0
            pool = max(0.0, month_pool - invested_month)
            base = pool / info['remaining_td']
            s = m.asset_score(METRICS[sig][d], SW).get('score')
            deploy = m.clip(1.0 + GAIN * s, 0.0, DMAX) if s is not None else 1.0
            amt = 0.0 if deploy < SKIP_BELOW else base * deploy
            if amt > 0 and info['remaining_cal_days'] <= RELEASE_WINDOW:
                amt = max(amt, base)
            amt = min(amt, pool)
        else:  # fixed: 1500 every trading day, no timing
            amt = 1500.0
        px = tcloses[tidx[d]]
        value_pre = shares * px * FX
        nav = value_pre / units if units > 0 else 1.0
        if amt > 0:
            units += amt / nav
            shares += amt / FX / px
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
    final_value = shares * tcloses[tidx[days[-1]]] * FX
    peak = -1e18
    maxdd = 0.0
    for v in navs:
        peak = max(peak, v)
        if peak > 0:
            maxdd = min(maxdd, v / peak - 1.0)
    x = m.xirr(flows + [(date.fromisoformat(days[-1]), final_value)]) if invested_total > 0 else None
    n = len(days)
    return {'days': n, 'invested': round(invested_total, 2), 'final_value': round(final_value, 2),
            'simple_return': (final_value / invested_total - 1.0) if invested_total else None,
            'xirr': x, 'max_nav_dd': maxdd, 'uw_days': uw_days,
            'uw_ratio': uw_days / n if n else None, 'max_consec_uw': max_consec}


def bucket(days):
    if days < 63: return '<3mo'
    if days < 126: return '3-6mo'
    if days < 252: return '6-12mo'
    if days < 504: return '1-2y'
    if days < 756: return '2-3y'
    return '3y+'


BUCKETS = ['<3mo', '3-6mo', '6-12mo', '1-2y', '2-3y', '3y+']

final = {}
for key in SINGLE:
    per_mode = {}
    for mode in ['dynamic', 'fixed']:
        rows = [run(s, key, mode) for s in CAL]
        xirrs = sorted(r['xirr'] for r in rows if r['xirr'] is not None)
        rets = sorted(r['simple_return'] for r in rows)
        dds = sorted(r['max_nav_dd'] for r in rows)
        n = len(rows)
        per_mode[mode] = {
            'win_rate': sum(1 for r in rows if r['simple_return'] > 0) / n,
            'ret_med': rets[n // 2], 'ret_worst': rets[0],
            'xirr_med': xirrs[len(xirrs) // 2],
            'maxdd_med': dds[n // 2], 'maxdd_worst': dds[0],
            'uw_ratio_mean': sum(r['uw_ratio'] for r in rows) / n,
            'max_consec_uw_max': max(r['max_consec_uw'] for r in rows),
            'invested_med': sorted(r['invested'] for r in rows)[n // 2],
        }
    # pairwise: dynamic vs fixed on identical start days
    diffs = []
    for s in CAL:
        rd = run(s, key, 'dynamic')
        rf = run(s, key, 'fixed')
        if rd['xirr'] is not None and rf['xirr'] is not None:
            diffs.append(rd['xirr'] - rf['xirr'])
    pairwise = {'beat_pct': sum(1 for x in diffs if x > 0) / len(diffs), 'mean_diff': sum(diffs) / len(diffs)}
    examples = {}
    for es in ['2021-08-11', '2022-01-03', '2024-08-05']:
        s = next((d for d in CAL if d >= es), None)
        if s:
            examples[s] = {mode: run(s, key, mode) for mode in ['dynamic', 'fixed']}
    final[key] = {'dynamic': per_mode['dynamic'], 'fixed': per_mode['fixed'],
                  'pairwise_dyn_vs_fixed': pairwise, 'examples': examples}
    print(f'{key} done', flush=True)

with (OUT / 'results_single_compare.json').open('w', encoding='utf-8') as f:
    json.dump(final, f, ensure_ascii=False, indent=2)
print(json.dumps(final, ensure_ascii=False))
