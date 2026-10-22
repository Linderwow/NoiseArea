"""
review_all.py - runs daily_review.py for every account in accounts.json and flags NoiseArea heartbeats from accounts that are not
registered there (2026-10-21, for the Bulenox evaluation running beside Sim101).

    python review_all.py <folder with the agent_exports files> [--date YYYY-MM-DD]

Prints each account's review under its own header, then any unregistered account whose heartbeat was written in the last 3 days.
Exit code: the worst of the reviews (0 all good / 1 warnings / 2 needs attention); an unregistered running account is 2.
Never writes to the folder.
"""
import sys, os, json, glob, argparse, subprocess, datetime as dt
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('folder'); ap.add_argument('--date', default=None)
    ap.add_argument('--registry', default=os.path.join(HERE, 'accounts.json'))      # tests point it elsewhere
    a = ap.parse_args()
    reg = json.load(open(a.registry, encoding='utf-8'))['accounts']
    worst = 0
    for i, r in enumerate(reg):
        cmd = [sys.executable, os.path.join(HERE, 'daily_review.py'), a.folder, '--account', r['account'], '--contracts', str(r.get('contracts', 1))]
        if a.date: cmd += ['--date', a.date]
        if r.get('eval'): cmd += ['--eval', r['eval'], '--eval-start', r['eval_start']]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if i: print()
        print(f"===== {r['account']} =====")
        print(p.stdout.rstrip())
        if p.stderr.strip(): print(p.stderr.rstrip())
        worst = max(worst, p.returncode if p.returncode in (0, 1, 2) else 2)
    known = {r['account'] for r in reg} | {'Backtest'}
    cutoff = dt.datetime.now() - dt.timedelta(days=3); extra = []
    for f in glob.glob(os.path.join(a.folder, 'NoiseArea_status_MNQ_*.json')):
        acct = os.path.basename(f)[len('NoiseArea_status_MNQ_'):-len('.json')]
        if acct in known or dt.datetime.fromtimestamp(os.path.getmtime(f)) < cutoff: continue
        try: st = json.load(open(f))
        except Exception: st = {}
        extra.append(f"UNREGISTERED ACCOUNT {acct}: NoiseArea heartbeat {st.get('updated_et', '?')} ET, state {st.get('state', '?')}, "
                     f"position {st.get('position', '?')} x{st.get('quantity', '?')} - not reviewed. Tell Claude what it is (e.g. the Bulenox "
                     f"evaluation: size and start date) so it is registered in review\\accounts.json")
    if extra:
        print("\nNEEDS ATTENTION:\n- " + "\n- ".join(extra)); worst = 2
    worst = max(worst, recorder_check(a.folder, a.date))
    sys.exit(worst)


def recorder_check(folder, date=None):
    """2026-10-22: TapeRecorder (tick tape, quotes, orders, executions) health from agent_exports\\TapeRecorder_status.json.
    Information only until it is first installed; afterwards a session day without data is a warning."""
    p = os.path.join(folder, 'TapeRecorder_status.json')
    print()
    if not os.path.exists(p):
        print("tape recorder: not running yet (TapeRecorder indicator not installed - agent_exports\\review\\research\\recorder\\TapeRecorder.cs)")
        return 0
    try: st = json.load(open(p))
    except Exception as e: print(f"tape recorder: status file unreadable ({e})"); return 1
    day = dt.date.fromisoformat(date) if date else dt.date.today()
    last = (st.get('last_data_utc') or '')[:10]
    print(f"tape recorder: state {st.get('state')}, updated {st.get('updated_utc')} UTC, {st.get('instrument')}, trades {st.get('trades')}, "
          f"quotes {st.get('quotes')}, orders {st.get('orders')}, executions {st.get('executions')}, accounts {st.get('accounts')}"
          + (f", note: {st.get('note')}" if st.get('note') else ''))
    if day.weekday() < 5 and last < str(day):
        print(f"\nWARNINGS:\n- tape recorder recorded no market data on {day} (last data {st.get('last_data_utc') or 'never'} UTC): put TapeRecorder "
              f"back on its own chart (state {st.get('state')})")
        return 1
    if st.get('state') == 'failed':
        print(f"\nWARNINGS:\n- tape recorder stopped itself: {st.get('note')}"); return 1
    return 0


if __name__ == '__main__':
    main()
