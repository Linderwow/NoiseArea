r"""msb_parity.py - prove the chart indicator and the research port agree on real bars: MarketStructure.cs exports every break to
agent_exports\MarketStructure_breaks_<instrument>_<period>_<tag>.csv (time_et,kind,direction,level,close,bias_after); this re-runs the
Python port (msb.structure) on the chart's own bars and compares break by break.
    python msb_parity.py <breaks csv> <bars csv> [--strength 5] [--wicks]
bars csv: NoiseArea_bars_*.csv (time_et,...) or a recorder *_bars5min.csv (time_et,...) or any time,open,high,low,close file.
Exit 0 = PASS (every break in the common window matches in time, direction and level, labels agree once both sides have a state)."""
import sys, os
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
from msb import structure


def load_bars(path):
    d = pd.read_csv(path)
    tcol = 'time_et' if 'time_et' in d.columns else 'time'
    d['time'] = pd.to_datetime(d[tcol]); return d.sort_values('time').drop_duplicates('time').reset_index(drop=True)


def port_breaks(bars, n, wicks):
    sig, bias, lvl = structure(bars.open.values, bars.high.values, bars.low.values, bars.close.values, n, wicks)
    idx = np.flatnonzero(sig)
    return pd.DataFrame({'time_et': bars.time.values[idx], 'kind': np.where(np.abs(sig[idx]) == 2, 'CHoCH', 'BOS'),
                         'direction': np.where(sig[idx] > 0, 'up', 'down'), 'level': lvl[idx], 'close': bars.close.values[idx], 'bias_after': bias[idx]})


def compare(exp, port):
    exp = exp.copy(); exp['time_et'] = pd.to_datetime(exp.time_et)
    lo = max(exp.time_et.min(), port.time_et.min()); hi = min(exp.time_et.max(), port.time_et.max())
    e = exp[(exp.time_et >= lo) & (exp.time_et <= hi)].reset_index(drop=True); p = port[(port.time_et >= lo) & (port.time_et <= hi)].reset_index(drop=True)
    key = lambda d: set(zip(d.time_et, d.direction, d.level.round(2)))
    ke, kp = key(e), key(p)
    missing = sorted(kp - ke); extra = sorted(ke - kp)
    m = e.merge(p, on=['time_et', 'direction'], suffixes=('_chart', '_port'))
    m = m[np.isclose(m.level_chart, m.level_port)]
    # labels (BOS / CHoCH) depend on the state, which the two sides reach at different history starts: compare after the first CHoCH both sides agree on
    first = m.index[(m.kind_chart == 'CHoCH') & (m.kind_port == 'CHoCH')]
    label_mismatch = m.loc[first[0]:] if len(first) else m
    label_mismatch = label_mismatch[label_mismatch.kind_chart != label_mismatch.kind_port]
    return dict(window=(lo, hi), chart=len(e), port=len(p), matched=len(m), missing=missing, extra=extra, labels=label_mismatch)


def main():
    a = sys.argv[1:]
    n = int(a[a.index('--strength') + 1]) if '--strength' in a else 5
    wicks = '--wicks' in a
    files = [x for x in a if not x.startswith('--') and x != str(n)]
    exp = pd.read_csv(files[0]); bars = load_bars(files[1])
    r = compare(exp, port_breaks(bars, n, wicks))
    print(f"window {r['window'][0]} .. {r['window'][1]}: chart {r['chart']} breaks, port {r['port']}, matched {r['matched']}")
    for x in r['missing'][:20]: print(f"   on the chart but not in the port: {x}")
    for x in r['extra'][:20]: print(f"   in the port but not on the chart: {x}")
    if len(r['labels']): print(r['labels'][['time_et', 'direction', 'level_chart', 'kind_chart', 'kind_port']].to_string(index=False))
    ok = not r['missing'] and not r['extra'] and not len(r['labels']) and r['matched'] > 0
    print("PARITY PASS" if ok else "PARITY FAIL"); sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
