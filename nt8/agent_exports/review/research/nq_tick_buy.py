r"""Capped purchase of NQ trades data around NoiseArea's NQ entry decisions 2010-2019 (ORDERFLOW_PLAN.txt; approved by Sashkin in chat
2026-10-20 at the quoted $33.54). Resumable: finished windows are recorded and never requested twice. Appends the ledger when done.
    python nq_tick_buy.py
"""
import os, sys, ctypes, time, threading
sys.dont_write_bytecode = True
try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__))
import numpy as np, pandas as pd
import databento as db

CAP = 35.00                                                                       # hard cap in USD (quote $33.54)
OUT = r"D:\databento_archive\nq_trades_entry_windows_2010-2019"
LEDGER = r"C:\Users\linde\OneDrive\Desktop\Desktop\databento_data\databento_usage_ledger.csv"
client = db.Historical()                                                          # key from DATABENTO_API_KEY (never printed)


def utc(ts): return pd.Timestamp(ts).tz_localize('America/New_York').tz_convert('UTC')


def main():
    os.makedirs(OUT, exist_ok=True)
    q = pd.read_csv(os.path.join(HERE, 'tickdata_quote_exact_NQ_W15_trades.csv'), index_col=0, parse_dates=True).usd
    q = q[q.index <= pd.Timestamp('2019-05-05')]
    total = float(q.sum()); print(f"{len(q)} windows, quoted ${total:.2f} (cap ${CAP:.2f})", flush=True)
    if total > CAP: print("REFUSED: quote above the cap - nothing bought"); return
    done_f = os.path.join(OUT, 'done.txt'); done = set(open(done_f).read().split()) if os.path.exists(done_f) else set()
    todo = [(i, d) for i, d in enumerate(q.index) if str(i) not in done]
    spent = float(q.iloc[[int(x) for x in done]].sum()) if done else 0.0
    print(f"already done {len(done)} (${spent:.2f}); to download {len(todo)}", flush=True)
    lock = threading.Lock(); buf = []; nchunk = len([f for f in os.listdir(OUT) if f.startswith('chunk_')])

    def flush():
        nonlocal buf, nchunk
        if not buf: return
        pd.concat([b for b, _ in buf]).to_parquet(os.path.join(OUT, f'chunk_{nchunk:03d}.parquet'), index=False)
        with open(done_f, 'a') as f: f.write(''.join(f"{i}\n" for _, i in buf))
        nchunk += 1; buf = []

    def one(item):
        i, d = item
        for k in range(3):
            try:
                st = client.timeseries.get_range(dataset='GLBX.MDP3', symbols=['NQ.v.0'], stype_in='continuous', schema='trades',
                                                 start=utc(d - pd.Timedelta(minutes=15)).isoformat(), end=utc(d + pd.Timedelta(minutes=2)).isoformat())
                df = st.to_df()
                x = pd.DataFrame({'win': np.int32(i), 'decision_et': d, 'ts': df.index.values.astype('datetime64[ns]'), 'price': df.price.values.astype(float),
                                  'size': df['size'].values.astype(np.int32), 'side': df.side.values.astype(str), 'iid': df.instrument_id.values.astype(np.int64)})
                return i, x
            except Exception as e:
                if k == 2: print(f"  window {i} {d} failed: {type(e).__name__}", flush=True); return i, None
                time.sleep(3 + 5 * k)

    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time(); n = 0; failed = []
    with ThreadPoolExecutor(16) as ex:
        for i, x in ex.map(one, todo):
            n += 1
            if x is None: failed.append(i); continue
            with lock:
                buf.append((x, i)); spent += float(q.iloc[i])
                if len(buf) >= 20: flush()
            if n % 100 == 0: print(f"  {n}/{len(todo)} windows, ~${spent:.2f}, {time.time() - t0:.0f}s", flush=True)
            if spent > CAP: print("STOP: running total above the cap"); break
    flush()
    done = set(open(done_f).read().split())
    print(f"finished: {len(done)}/{len(q)} windows stored, failed {len(failed)}, ~${spent:.2f}", flush=True)
    if len(done) == len(q) and not os.path.exists(os.path.join(OUT, 'ledger_written.txt')):
        led = pd.read_csv(LEDGER); cum = float(led.cumulative_usd.iloc[-1]) + total
        row = f"{pd.Timestamp.utcnow().isoformat()},NQ trades -15..+2 min around 1953 NoiseArea entry decisions,trades,NQ.v.0,{q.index[0]:%Y-%m-%d},{q.index[-1]:%Y-%m-%d},{total:.4f},{cum:.4f}\n"
        with open(LEDGER, 'a') as f: f.write(row)
        open(os.path.join(OUT, 'ledger_written.txt'), 'w').write(row); print("ledger appended:", row.strip())


if __name__ == '__main__':
    main()
