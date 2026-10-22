r"""Capped purchase of MNQ trades data around the 491 10:00 entries 2019-2026 (LIMIT_PLAN.txt; approved by Sashkin in chat
2026-10-20 "DO A then" at the quoted $28.90). Resumable: finished windows are recorded and never requested twice. Appends the ledger.
    python limit_buy.py
"""
import os, sys, ctypes, time, threading
sys.dont_write_bytecode = True
try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__))
import numpy as np, pandas as pd
import databento as db

CAP = 30.00                                                                       # hard cap in USD (quote $28.90, credit $33)
OUT = r"D:\databento_archive\mnq_trades_1000_windows_2019-2026"
LEDGER = r"C:\Users\linde\OneDrive\Desktop\Desktop\databento_data\databento_usage_ledger.csv"
client = db.Historical()                                                          # key from DATABENTO_API_KEY (never printed)


def utc(ts): return pd.Timestamp(ts).tz_localize('America/New_York').tz_convert('UTC')


def windows():
    w = pd.read_csv(os.path.join(HERE, 'limit_quote_windows.csv'), parse_dates=['start', 'end'])
    w = w[(w.start + pd.Timedelta(minutes=1)).dt.strftime('%H:%M') == '10:00'].reset_index(drop=True)
    return w


def main():
    os.makedirs(OUT, exist_ok=True)
    w = windows(); total = float(w.trades.sum())
    print(f"{len(w)} windows, quoted ${total:.2f} (cap ${CAP:.2f})", flush=True)
    if total > CAP: print("REFUSED: quote above the cap - nothing bought"); return
    done_f = os.path.join(OUT, 'done.txt'); done = set(open(done_f).read().split()) if os.path.exists(done_f) else set()
    todo = [(i, r.start, r.end) for i, r in enumerate(w.itertuples()) if str(i) not in done]
    spent = float(w.trades.iloc[[int(x) for x in done]].sum()) if done else 0.0
    print(f"already done {len(done)} (${spent:.2f}); to download {len(todo)}", flush=True)
    lock = threading.Lock(); buf = []; nchunk = len([f for f in os.listdir(OUT) if f.startswith('chunk_')])

    def flush():
        nonlocal buf, nchunk
        if not buf: return
        pd.concat([b for b, _ in buf]).to_parquet(os.path.join(OUT, f'chunk_{nchunk:03d}.parquet'), index=False)
        with open(done_f, 'a') as f: f.write(''.join(f"{i}\n" for _, i in buf))
        nchunk += 1; buf = []

    def one(item):
        i, s, e = item
        for k in range(3):
            try:
                st = client.timeseries.get_range(dataset='GLBX.MDP3', symbols=['MNQ.v.0'], stype_in='continuous', schema='trades',
                                                 start=utc(s).isoformat(), end=utc(e).isoformat())
                df = st.to_df()
                x = pd.DataFrame({'win': np.int32(i), 'start_et': s, 'ts': df.index.values.astype('datetime64[ns]'), 'price': df.price.values.astype(float),
                                  'size': df['size'].values.astype(np.int32), 'side': df.side.values.astype(str), 'iid': df.instrument_id.values.astype(np.int64)})
                return i, x
            except Exception as ex:
                if k == 2: print(f"  window {i} {s} failed: {type(ex).__name__}", flush=True); return i, None
                time.sleep(3 + 5 * k)

    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time(); n = 0; failed = []
    with ThreadPoolExecutor(16) as ex:
        for i, x in ex.map(one, todo):
            n += 1
            if x is None: failed.append(i); continue
            with lock:
                buf.append((x, i)); spent += float(w.trades.iloc[i])
                if len(buf) >= 20: flush()
            if n % 50 == 0: print(f"  {n}/{len(todo)} windows, ~${spent:.2f}, {time.time() - t0:.0f}s", flush=True)
            if spent > CAP: print("STOP: running total above the cap"); break
    flush()
    done = set(open(done_f).read().split())
    print(f"finished: {len(done)}/{len(w)} windows stored, failed {len(failed)}, ~${spent:.2f}", flush=True)
    if len(done) == len(w) and not os.path.exists(os.path.join(OUT, 'ledger_written.txt')):
        led = pd.read_csv(LEDGER); cum = float(led.cumulative_usd.iloc[-1]) + total
        row = (f"{pd.Timestamp.utcnow().isoformat()},MNQ trades 09:59-10:05 around 491 NoiseArea 10:00 entries,trades,MNQ.v.0,"
               f"{w.start.min():%Y-%m-%d},{w.start.max():%Y-%m-%d},{total:.4f},{cum:.4f}\n")
        with open(LEDGER, 'a') as f: f.write(row)
        open(os.path.join(OUT, 'ledger_written.txt'), 'w').write(row); print("ledger appended:", row.strip())


if __name__ == '__main__':
    main()
