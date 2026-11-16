import os, sys, json, glob
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import livestack as LS
import naplus3 as N3

AE = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(HERE, 'ANALYZER_PARITY.txt')
LINES = []


def say(s=''):
    print(s, flush=True); LINES.append(s)


def research_trades(mk, kw):
    pos = {}; pnl, _ = N3.engine3(mk, pos_out=pos, **kw); rows = []
    for k, x in enumerate(mk.days):
        key = pd.Timestamp(x.date)
        if key not in pos: continue
        pp = pos[key][x.slot]; O, C, M = x.O, x.C, x.M
        flat_i = x.nb - 1 if M[-1] >= 960 else x.nb - 2
        prev = 0; e = None; tot = 0.0
        for j in range(1, flat_i + 1):
            d = int(pp[j - 1])
            if d != prev:
                if prev != 0: rows.append((e[0], key + pd.Timedelta(minutes=int(M[j])), prev, e[1], O[j])); tot += prev * (O[j] - e[1])
                if d != 0: e = (key + pd.Timedelta(minutes=int(M[j])), O[j])
                prev = d
        if prev != 0:
            rows.append((e[0], key + pd.Timedelta(minutes=int(M[flat_i]) + 5), prev, e[1], C[flat_i])); tot += prev * (C[flat_i] - e[1])
        p = 0.0 if np.isnan(pnl[k]) else pnl[k]
        if abs(tot - p) > 1e-6: raise SystemExit(f"trade rebuild != engine P&L on {key:%Y-%m-%d}: {tot} vs {p}")
    return pd.DataFrame(rows, columns=['entry', 'exit', 'dir', 'px_in', 'px_out'])


def stats(usd, d):
    eq = usd.cumsum().values; dd = float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max())
    w, l = usd[usd > 0], usd[usd <= 0]
    return (f"trades {len(usd)}, net ${usd.sum():,.2f}, max DD ${dd:,.2f}, profit factor {w.sum() / -l.sum():.2f}, winners "
            f"{len(w) / len(usd) * 100:.2f}%, avg ${usd.mean():.2f}, longs {int((d > 0).sum())} ${usd[d > 0].sum():,.0f}, "
            f"shorts {int((d < 0).sum())} ${usd[d < 0].sum():,.0f}")


def main():
    st = json.load(open(os.path.join(AE, 'NoiseArea_status_MNQ_Backtest.json'), encoding='utf-8'))
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(AE, st['trade_log'])
    if not os.path.exists(path): path = max(glob.glob(os.path.join(AE, 'NoiseArea_trades_MNQ_Backtest_*.csv')), key=os.path.getmtime)
    nt = pd.read_csv(path, parse_dates=['entry_time_et', 'exit_time_et'])
    nt['dir'] = np.where(nt.direction == 'long', 1, -1)
    say(f"analyzer_parity.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  {os.path.basename(path)} ({len(nt)} trades, "
        f"{nt.entry_time_et.min():%Y-%m-%d} .. {nt.entry_time_et.max():%Y-%m-%d})")
    ch = float(st.get('chandelier', 0) or 0); gate = int(st.get('exit_gate', 0) or 0)
    tf, lunch, ema = bool(st.get('trend_filter')), bool(st.get('lunch_pause')), bool(st.get('ema20_confirm'))
    act = int(st.get('activity_after', 0) or 0)
    say(f"run settings (status file): chandelier {ch:g}, trend filter {'on' if tf else 'off'}, exit gate {gate}, lunch pause "
        f"{'on' if lunch else 'off'}, EMA(20) {'on' if ema else 'off'}, activity after {act}")
    if act: say("NOTE: the activity fallback is not in the research engine - sessions after 3 dry sessions may differ")
    mk = N.market('MNQ'); end = pd.Timestamp(mk.dates[-1]) + pd.Timedelta(days=1)
    q = None
    if tf:
        full = LS.full_bars(mk); q = LS.per_day(mk, full, LS.ema_state(full.close.values))
    rs = research_trades(mk, dict(q=q, gate=gate, lunch=lunch, ema=ema, ch=ch if ch > 0 else None))
    start = nt.entry_time_et.min().normalize(); rs = rs[rs.entry >= start].reset_index(drop=True)
    nw = nt[nt.entry_time_et < end].copy()
    m = rs.merge(nw, left_on=['entry', 'dir'], right_on=['entry_time_et', 'dir'], how='outer', indicator=True)
    both = m[m._merge == 'both']
    slip = float(np.median(both.dir * (both.entry_price - both.px_in))) if len(both) else 0.25
    comm = float(nw.commission.median()) if len(nw) else 0.0
    rs['usd'] = (rs.dir * (rs.px_out - rs.px_in) - 2 * slip) * 2 - comm
    both = both.assign(usd=(both.dir * (both.px_out - both.px_in) - 2 * slip) * 2 - comm)
    say(f"common window {start:%Y-%m-%d} (the Analyzer's first trade, after its 14-session warm-up) .. {mk.dates[-1]:%Y-%m-%d} (research data end); inferred slippage {slip / 0.25:g} tick(s) a side, "
        f"commission ${comm:.2f} a trade")
    say(f"trades: research {len(rs)}, Analyzer {len(nw)}; same entry bar and side {len(both)} ({len(both) / max(len(nw), 1) * 100:.1f}% of the "
        f"Analyzer's); same exit bar {int((both.exit == both.exit_time_et).sum())}; research only {int((m._merge == 'left_only').sum())}, "
        f"Analyzer only {int((m._merge == 'right_only').sum())}")
    d = both.usd - both.profit_currency
    say(f"matched trades $ research - Analyzer: mean {d.mean():+.3f}, median {d.median():+.3f}, max |diff| {d.abs().max():.2f}")
    say(f"research : {stats(rs.usd, rs.dir)}")
    say(f"Analyzer : {stats(nw.profit_currency, nw.dir)}")
    tot_r, tot_a = rs.usd.sum(), nw.profit_currency.sum(); gap = (tot_a - tot_r) / abs(tot_r) * 100 if tot_r else float('nan')
    yr = rs.groupby(rs.entry.dt.year).usd.sum(); yn = nw.groupby(nw.entry_time_et.dt.year).profit_currency.sum()
    say("per year research / Analyzer: " + "; ".join(f"{y} ${yr.get(y, 0):,.0f} / ${yn.get(y, 0):,.0f}" for y in sorted(set(yr.index) | set(yn.index))))
    rest = nt[nt.entry_time_et >= end]
    say(f"Analyzer after the research window: {len(rest)} trades, ${rest.profit_currency.sum():,.2f}")
    ok = len(both) >= 0.97 * len(nw) and abs(gap) < 3
    say(f"VERDICT: {'MATCH' if ok else 'MISMATCH'} - net differs {gap:+.2f}%, {len(both) / max(len(nw), 1) * 100:.1f}% of trades identical "
        f"(bar: >= 97% identical and net within 3%)")
    if not ok:
        bad = m[m._merge != 'both'].sort_values(['entry', 'entry_time_et']).head(12)
        say(bad[['entry', 'exit', 'dir', 'entry_time_et', 'exit_time_et', 'direction', '_merge']].to_string())


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
