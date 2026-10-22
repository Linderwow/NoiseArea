# NoiseArea — automated MNQ strategy (Sashkin's NinjaTrader 8 folder)

Read this first in every session. The full description, rules, parameters, decision log and gates are in
`agent_exports\review\NoiseArea_Strategy.md` (a copy sits on the Desktop). This file is the working brief.

## What this is
- `NoiseAreaStrategy` + `NoiseAreaMomentum` (bin\Custom\Strategies, bin\Custom\Indicators): the published noise-area
  intraday momentum method (Zarattini / Aziz / Barbon 2024) on MNQ, 5-minute bars, decisions every 30 minutes
  10:00–15:30 ET, flat at 16:00 ET, 1 micro, fixed published parameters. Live on the Sim101 paper account since
  2026-10-15 (first trade 2026-10-16, short +457.5 pts = +$915). Bulenox prop accounts come after the gates pass.
- Research discipline: every test is pre-registered in a `*_PLAN.txt` before it runs, discovery 2019–22 / validation
  2023–26, on both NinjaTrader bars and Databento bars, verdict in a `*_RESULTS.txt`. Nothing is adopted without
  passing the bar written in the plan. Everything tried so far is in `NoiseArea_Strategy.md` §9; nothing beyond the
  published rules has ever passed. Do not re-run those ideas; do not propose parameter changes after a bad stretch.

## Hard rules (Sashkin's standing instructions)
- NinjaTrader is view-only: never operate it, never click, never place/change/close trades. Compiling (F5), enabling
  the strategy and the Strategy Analyzer are his clicks — say exactly what needs clicking and wait.
- Never run `buy_markets.py --yes` (`buy_markets_yes.bat`) — purchases are his. The dry run is fine. The Databento
  key lives in the `DATABENTO_API_KEY` environment variable; never print it, paste it into a file, or echo it back.
- Never hard-delete anything. Inert files go to `_to_delete\` (reversible); he empties that folder himself.
- Run everything you can yourself, in the background, without check-ins or go-ahead requests. Triple-check numbers
  before reporting them. Never leave a test unaudited or a result unverified. Report bottom line first, short.
- No manual trades on the strategy's account (Sim101): the strategy reports `desync` and stops if the account's
  position differs from its own. That includes copied trades: Replikanto (leader BX49277-31, tab in the "MNQ Pro
  Cockpit" workspace) lists Sim101 as a follower with status Off (2026-10-18). It must stay Off, or every trade on -31
  is copied into Sim101.
- 2026-10-22 09:08 a Chart Trader HOT KEY on the strategy's chart bought 3 MNQ on Sim101 (ATM "3 contracts - 1"); his Chart
  Trader "Close position" flattened it at 09:08:47 ($0) and, as NinjaTrader does, DISABLED NoiseAreaStrategy on that chart; he
  re-enabled it at 09:09:34 (Contracts 1), before the first bar. Any manual action on that chart can switch the strategy off:
  after one, check the heartbeat. Hiding Chart Trader on the strategy chart removes the hot-key risk.

## Where things are
- `agent_exports\` — strategy exports: `NoiseArea_status_MNQ_<acct>.json` (heartbeat, rewritten every 5-min bar; state
  running/halted/desync/terminated, updated_et, instrument, position, account_position, desired, connected,
  history_sessions), `NoiseArea_trades_MNQ_<acct>_<start>.csv` (closed live trades, one file per strategy start),
  `NoiseArea_checks_MNQ_strategy_<acct>.csv`, `NoiseArea_bars_MNQ_strategy_<acct>.csv`, `NoiseArea_days_…csv`,
  `NoiseArea_daystate_…txt`. Strategy Analyzer runs write the same files with account `Backtest`.
- `agent_exports\review\` — `daily_review.py` (end-of-day check), `robustness.py` (slippage drag, top-trade
  dependence, Monte Carlo on any trade CSV), `downsample_trades.py`, `NoiseArea_Strategy.md`, `routines\` (prompts
  for the scheduled checks, see below).
- `agent_exports\review\research\` — the whole research trail: `README.txt` (index), `published.py` (reference rules
  = ground truth), `research_engine.py` (fam_A reproduces published.S1 to the dollar), `s1_trades.py`,
  `s1_reference_trades_ntbars_v3.csv` (1,666 trades, current parity reference), `convert.py` (5-minute CSVs from
  Databento files), every `*_PLAN.txt` / `*_RESULTS.txt` / raw output, `gex_dix.csv`, `raw_open930.csv`.
- `C:\Users\linde\OneDrive\Desktop\Desktop\databento_data\` — Databento 1-minute history (MNQ, MES, M2K, MYM, MGC,
  ZN, M6E, MCL, MBT 2019/2021–2026; NQ and ES 2010–2019; VIX daily; MNQ 1-second Dec-2025), `buy_markets.py` (+ dry-run
  and yes .bat, hard cap `BUDGET` = remaining credit), `databento_usage_ledger.csv` (every purchase).
  Tick files (2.4 GB) are archived in `D:\databento_archive\`.
- `_to_delete\` — files moved out on 2026-10-16/17 (two unused indicator .cs files, old Backtest trade logs,
  duplicate MES files, get_gex / price_options scripts, the four pc_shell files, and `.bak_20261017_*` copies of
  `daily_review.py`, `robustness.py` and `nt_build_check.ps1` from before the 2026-10-17 audit fixes). He deletes the
  folder himself. Note: with get_gex moved out, `research\gex_dix.csv` stops at 2026-10-16.

## Package on GitHub (2026-10-22)
- PRIVATE repo https://github.com/Linderwow/NoiseArea, local clone `D:\dev\trading\NoiseArea` (identity Linderwow, repo-local).
  `nt8\` mirrors this folder: CLAUDE.md, every custom NinjaScript file, templates, workspaces, agent_exports\review (code,
  results, routines, registry, core 5-min data). NOT included: Config.xml (stores his NT password), db\, logs, vendor DLLs,
  raw Databento files, research\regime_work caches, D:\nt_ticks, D:\nt_recorder, run outputs. New machine: README.md +
  `tools\install.ps1` (dry run; -Apply refuses while NinjaTrader runs, backs up, rewrites the routine paths).
- AUTOMATIC since 2026-10-22 ("committing, verifying and pushing upon success should be happening automatically"): the
  Windows task "NoiseArea autopush" (hourly at :07, pythonw - no window, catches up after sleep) runs `tools\autopush.py`:
  sync -> git-rm files gone from live (max 20 / 5%, else blocked) -> verify every change (secrets/Config/size; py_compile +
  JSON; .cs changed -> whole-project build + canary; review script changed -> review_all smoke test) -> commit -> push ->
  confirm GitHub HEAD. A failed check commits nothing and is retried every hour. Status
  `agent_exports\NoiseArea_autopush_status.json` (line + warning in review_all.py); log `D:\dev\trading\NoiseArea\.autopush\`.
  After changing package files yourself, run `python D:\dev\trading\NoiseArea\tools\autopush.py` at once instead of waiting.
  All gates were tested blocking on 2026-10-22 (fake key, syntax error, broken .cs, crashing review script).

## Running the checks locally (Windows, Python 3.12 on PATH; pandas/numpy present via the databento package)
    python "agent_exports\review\review_all.py" "agent_exports"      (every account in review\accounts.json + unregistered ones flagged)
    python "agent_exports\review\daily_review.py" "agent_exports" --account Sim101 [--contracts N] [--eval 50K --eval-start YYYY-MM-DD]
                                                                                        (exit 0 good / 1 warnings / 2 attention)
  ACCOUNT REGISTRY (2026-10-21): `agent_exports\review\accounts.json` lists every account NoiseArea runs on with its registered
  Contracts (Sim101 = 1). When Sashkin buys the Bulenox evaluation, add {"account": "<BX…>", "contracts": 6, "eval": "<size he
  bought>", "eval_start": "<purchase date>"} from HIS statement (never guess the size); after a $78 reset, eval_start = reset date.
  The review checks the strategy's Contracts / daily loss stop at its last start (events file), the size of every fill, and for
  the eval: net vs target, end-of-day threshold, room, day N of 30. The eval account must never be a Replikanto follower and gets
  no manual trades (the desync guard stops the strategy).
  Saved strategy settings can be read (never written) from `db\NinjaTrader.sqlite` ONLY while NinjaTrader is closed: open with
  `?mode=ro`, table Strategies, column Userdata (UTF-16-LE, HTML-escaped XML: <Contracts>, <MaxDailyLoss>, ...); the account via
  Strategy2Account -> Accounts. 2026-10-21 21:0x: NoiseAreaStrategy 398114620 on Sim101, Contracts 1, MaxDailyLoss 0, exit 120 s.
    python "agent_exports\review\robustness.py" <trade CSV>
    python "agent_exports\review\research\s1_trades.py"   (reference trade list; see README.txt for inputs)
Keep scripts in `review\research\` runnable from that folder (they read their CSVs relative to it).

## Status and open items (as of 2026-10-17)
- Gates (§8 of the strategy doc): parity PASSED three times, the last on 2026-10-19 against the 2026-10-18 build (Analyzer
  run 20261019_152021: 1,666 of 1,666 trades match `s1_reference_trades_ntbars_v3.csv`, net $24,170.50 vs $24,232, both
  at 1 tick/side slippage + $1/RT commission, -0.25%; verified twice in the 2026-10-20 double verification); live fill cost ≤1.5 ticks, heartbeat 20 sessions,
  20-session P&L inside the backtest's 90% band, now -1.68% .. +4.00% of one micro's notional (-$938 .. +$2,228 at $27,875;
  restated 2026-10-21 from the old -$600 .. +$1,100 in 2019-2025 dollars) — all pending; cushion gate $12,000 per micro; Bulenox approval PASSED. Sizing only
  after 60 clean live sessions.
- OPERATING FACT (2026-10-20 double verification): NOTHING starts NinjaTrader - it is in no Startup folder, Run key or
  scheduled task - and a restored workspace brings NoiseAreaStrategy back DISABLED (it runs on the MNQ chart in the "MNQ
  Pro Cockpit" workspace, data series 500 days). After any NinjaTrader exit or reboot Sashkin must start NinjaTrader, check
  Bullenox is connected and enable the strategy before 09:35 ET. NinjaTrader was exited by hand after the close on 10-16
  and 10-19. The pre-flight's push alert is suppressed while the Claude app counts him as active ("Not sent - this
  terminal is active": 0 of 2 routine alerts delivered so far) and Remote Control is off, so do not rely on the push alone.
- Calendar: early closes Nov 26 (13:00 ET) and Nov 27 (13:15 ET) 2026; MNQ 12-26 expires 2026-12-18 — roll after
  16:15 ET in the week before (disable old contract, enable on MNQ 03-27, never both at once).
- NO BUDGET (Sashkin 2026-10-21: "I don't have any money left, so only free") - propose nothing that costs money.
- The Chart Trader panel on the strategy's MNQ chart (account Sim101, 3-contract ATM preset) is leftover config from manual
  trading; Sashkin does not use it on that chart (2026-10-21) - no need to warn about it again.
- Remaining Databento credit ~ $4.10: $33 per Sashkin (2026-10-20 evening) minus the $28.90 MNQ 10:00 trades purchase (2026-10-21).
  On 2026-10-21 Sashkin's billing page showed $23 - most likely the $28.90 had not fully posted yet (the $33.54 NQ purchase also
  showed only $5.48 at first); plan on ~$4 until the page settles;
  `buy_markets.py` BUDGET set to 4.10 (backups in `_to_delete\`).
  My earlier estimate was ≈ $19.70 (billing page $47.76 at 15:45 on 2026-10-20 with $5.48 of the $33.54 NQ tick purchase billed). That purchase (approved by Sashkin): NQ trades -15..+2 min around all 1,953
  NQ NoiseArea entries 2010-2019, in `D:\databento_archive\nq_trades_entry_windows_2010-2019\`; result: order flow does not predict
  the trades (`research\ORDERFLOW_RESULTS.txt`) - closed. The
  one-year MNQ 1-second purchase ($20.86) is NO-GO (`research\EXEC_PILOT_RESULTS.txt`: 1-second bars cannot measure these
  fills); real fill cost needs tick data (a separate purchase, his call) or a Rithmic account's own fills.
- Audit 2026-10-17 (each fix verified, then re-run on real data): `daily_review.py` never reported a mismatch between
  the indicator's decisions and its independent recomputation (the branch was unreachable) - fixed, and the fix shows 0
  mismatches over all 22,501 Backtest decisions (1,907 days); reviewing a past session (`--date`) no longer reports a false
  "heartbeat is stale"; `robustness.py` no longer crashes below 201 trades (output on the 1,666-trade file unchanged);
  `D:\dev\trading\MNQPropBook\tools\nt_build_check.ps1` skips csproj entries moved out since the last F5 and its canary
  fails again. The pending F5 compiles clean (offline check, 0 errors).
- 2026-10-18: F5 done (NT 8.1.8.3) and NoiseAreaStrategy re-enabled on Sim101 (Days to load 500 -> 354 sessions loaded);
  the new build's replay matches the recomputed rules on all 4,010 decisions / 340 days. Fill slippage in
  `daily_review.py` is now measured only against bars recorded live: every restart rewrites the bars file from
  historical data, which moved Friday's measured slippage from +1.50 to -5.00 ticks/side for the same two fills. Legs
  before the last restart are reported as "not measured". Fill-cost gate evidence must come from same-day reviews (no
  restart in between); the 1-second data study is NO-GO (see above).
- 2026-10-19 scaling-schedule test (`research\SCALING_PLAN.txt` / `SCALING_RESULTS.txt`): for a self-funded account after the
  60-clean-session gate, contracts reset at month ends - 1 micro per ≈ $11,700 (21% of notional) from a $25k-50k start, 1 per
  ≈ $21,200 (38%) from $105k; the 21% rule carries about a 1-in-20 chance of a 50% drawdown in five years, so it is the ceiling,
  not the target. Half-Kelly fails. Table in `NoiseArea_Strategy.md` §7. Trading rules unchanged.
- 2026-10-19, three more pre-registered tests, all logged in §9 (`research\RELVOL_*`, `TREND_*`, `EXEC_PILOT_*`): a relative-volume
  entry filter FAILED (cuts validation net 46-68%; the paying break-outs come at about normal volume); a multi-day trend sleeve
  beside NoiseArea FAILED at discovery (no rule beat buy-and-hold risk-adjusted); the fill-cost pilot says NO-GO on the $20.86
  1-second purchase (1-second bars cannot measure these fills). The pilot's open finding: real market-order cost depends on the
  PC -> NinjaTrader -> Rithmic order latency (about +1.2 ticks/side at 25-50 ms, +5.4 at 250 ms, mostly the 10:00 decisions when
  data releases hit), and the Sim101 fill-cost gate cannot measure it (the simulator fills internally); the first real measure is
  fills on a Rithmic account, with 10:00 read separately. `D:\databento_archive\mnq_trades_2026-01-01_2026-04-16.dbn.zst` is
  truncated at 2026-04-09 11:40 ET.
- 2026-10-19 latency check (`research\LATENCY_NOTE.txt`): the PC clock gains ~72 ms/hour and is synced only about daily (w32time
  Manual/stopped), so log times run up to ~1.3 s fast; NinjaTrader's live bars use exchange time, so decisions are unaffected. The
  strategy sends orders 48-68 ms after the true bar close (16:00 flat: ~600 ms, one observation); Rithmic adds ~20 ms. Done by
  Sashkin 2026-10-19 ~17:10: sleep and hibernate Never (High performance plan), Windows Time service Automatic + running, synced to
  time.windows.com at 17:10:38. At 17:15 the clock was still 0.78 s fast (w32tm Phase Offset agrees with an independent SNTP check):
  offsets under 1 s are slewed, not stepped. Sashkin then set SpecialPollInterval 900 (15-minute polls), restarted w32time and
  forced a resync: at 18:03 the clock was 0.46 s fast and no longer drifting (flat over 90 s, was +72 ms/h), still closing.
  Check `w32tm /query /status` and the SNTP offset later; until it is near 0, correct log timestamps for the drift since the last sync
  (System log, Kernel-General event 1).
- 2026-10-19 alarms wired into `daily_review.py` (section 5, `review\monitor.py`; `research\MONITOR_PLAN.txt` / `MONITOR_RESULTS.txt`):
  edge-decay CUSUM (watch 681 bp = warning, alarm 1,297 bp = NEEDS ATTENTION, vs the 16-year record) and live fill-cost drift vs the
  1.5-tick gate. Flag only. To restart the CUSUM after a reviewed alarm, set RESET_DATE in monitor.py and date it in MONITOR_PLAN.txt.
  2026-10-21 (full-auto instruction): the 20-session gate is restated as a share of notional - daily_review.py BAND20_SHARE /
  band20(), 5th..95th percentile of rolling 20-session P&L 2019-05 .. 2026-10 (-$938 .. +$2,228 at p_ref, -$1,028 .. +$2,442 at
  30,552), priced at the latest bar; review output on today's files identical (backup `_to_delete\daily_review.py.bak_20261021_band`).
- 2026-10-21 safety guards LIVE (F5 08:14, parity 1,662/1,666 on the 08:23 Analyzer run, enabled on Sim101 09:50) (`research\GUARDS_PLAN.txt` / `GUARDS_RESULTS.txt`): the live
  bin\Custom\Strategies\NoiseAreaStrategy.cs is now the guards build plus the 120-s Bulenox exit backup (hash 01F3AC21A0C6BB58;
  whole-project offline build 0 errors, canary fails): size-aware desync guard, stop without flattening on a persistent desync,
  daily-loss-stop restart fixes, event log `agent_exports\NoiseArea_events_<inst>_<acct>.txt`. Rollback:
  `research\guards\NoiseAreaStrategy.cs.live_20261020_120s` (= the compiled 21:29 build). Sashkin: close editor tabs, F5 while
  flat, enable; then an Analyzer parity run (expect 1,666 of 1,666 to 2026-10-14; `fomc_eve\fomc_parity.py off` checks it).
- 2026-10-20 routine and cost-alarm fixes: the cost alarm is re-calibrated for being re-tested after every session
  (MONITOR_PLAN.txt amendment, `research\monitor_cost_seq.py`): NEEDS ATTENTION when mean - 2.286 x sd/sqrt(n) > 1.5 ticks,
  WARNING at 1.513 (false alarm over a year of looks 2.5% / 20%; the old rule gave 6.6%). Restart test moved to 14:40 and passes
  only on proof of a restart (new live_since and trade_log, same position, account = strategy). Annual re-validation first runs
  `review\annual_window.py` (partial live year vs the same calendar window of every reference year). Pre-flight: history 14 =
  fully loaded; roll reminder follows NinjaTrader's Monday rollover. Strategy, parameters and NinjaScript unchanged.
- 2026-10-20 double-verification follow-up (`research\VERIFY_FOLLOWUP_20261020.txt`): the 10-19 workflow's challenge,
  tie-break and critic agents were cut off by the usage limit; finished by hand. daily_review.py: full CME holidays and a flat
  shutdown after the close are reported, not flagged (closing NinjaTrader in the evening is normal here); roll escalates to
  NEEDS ATTENTION the evening before expiry; split fills kept; EXPECTED_QTY = 1 (change only with the sizing schedule); live
  sessions from the calendar. monitor.py: edge per micro position, re-adopted entries not costed. robustness.py: NinjaTrader's
  profit is already net of its commission - $1 is added only where the log has none. Pre-flight prompt + task updated (holidays,
  alert carries the problem line). $50k survival sizing = 5.00% on 1M paths: use prudent there. Strategy unchanged.
- 2026-10-20 regime-switching catalog ("Patrick's way", Golden Gate's design) DEAD ON THIS DATA (`research\REGIME_PLAN.txt`,
  `REGIME_ML_PLAN.txt`, `REGIME_RESULTS.txt`; harness `regime_test.py` can score anyone's state calls). Rule-based and learned
  state classifiers both lose to NoiseArea out of sample (Sharpe 0.4-0.5 vs 1.29, pass odds 45-49% vs 82%); the state is worth a
  lot in hindsight but is not predictable (AUC 0.51-0.56); momentum/breakout is the only family that earns in every state.
- 2026-10-20 market-structure methods from the web DEAD ON THIS DATA (`research\STRUCTURE_PLAN.txt` / `STRUCTURE_RESULTS.txt`):
  Dalton open types, initial balance, 80% rule, ICT Silver Bullet, liquidity sweep / Wyckoff spring, last-half-hour momentum,
  Crabel ORB + NR4/NR7/inside day; also as state detectors. No standalone edge, no useful addition, no switcher beats NoiseArea.
  Do not re-run these on 2019-2026 to "find" one; only a new pre-registered idea (order flow needs tick data) can reopen it.
- 2026-10-20 MNQ-only program (`research\MNQ_PROGRAM_PLAN.txt` / `MNQ_PROGRAM_RESULTS.txt`, walk-forward OOS 2022-2026): the
  FOMC-eve overnight long PASSES as an add-on (NoiseArea + it Sharpe 1.65 vs 1.41, P 0.002, both sources); everything else fails.
  It was staged and parity-checked on 2026-10-20 (1,713/1,717, 58/58 FOMC rows) and then REMOVED the same evening (Sashkin:
  "anything not allowed by Bulenox, remove"): Bulenox forbids overnight holds. The live NoiseAreaStrategy.cs is back to the
  10-19 parity build (SHA 3B96F18B) plus one Bulenox fix: exit-on-session-close fires >= 120 s before the session end (16:58 ET =
  15:58 CT; 30 s was 15:59:30 CT, past Bulenox's 3:59 PM CT), enforced in State.Configure; whole-project offline build 0 errors.
  daily_review.py and the morning pre-flight (file + task, byte-identical) are back to their pre-FOMC versions (review output
  identical). The add-on version stays only as research: `research\fomc_eve\` (+~$1,040/yr per micro, for an account that allows
  overnight holds - never a Bulenox account). Observation: long-only halves NoiseArea's drawdown.
  Post-removal Analyzer run 2026-10-20 21:02: same 1,668 trades as the 17:42 add-on-off run, parity 1,662/1,666; net $23,387 vs
  $24,388 only because the "NinjaTrader Brokerage" commission template now charges $1.90 RT (was $1.30). Bulenox's rate sheet
  (bulenox.com/legal/Bulenox-Rates.pdf): MNQ $0.61/side, $1.22 RT all-in - research uses $0.50/side (~$50/yr per micro optimistic).
- 2026-10-20 projections (`research\projections.py` -> `projections_output.txt`, reproduces LONGHIST): NoiseArea 1 micro at
  today's cost $4,739/yr, median year $4,600, losing year 12-13%, bad year (5%) -$1,750..-2,200, 1-in-20 in-year DD $5,100;
  with 1.5 ticks/side live fill drag $4,400/yr; if the edge halves $2,400/yr and 1 year in 3.5 loses; MNQ era alone $6,180/yr.
- 2026-10-20 YouTube strategies (`research\YOUTUBE_PLAN.txt` / `YOUTUBE_RESULTS.txt`, `youtube_test.py`): 15 videos, 8 codable, all
  dead on MNQ 1-minute bars 2019-2026 with costs; claimed 55-73% win rates measure 23-50%. Transcripts come from YouTube's own
  captions in the built-in browser (mute first; the page must be loaded and ads finished before capturing).
- 2026-10-20 research-grade sources + automated rule search (`research\RULEMINE_PLAN.txt` / `RULEMINE_RESULTS.txt`, `rulemine.py`):
  Zarattini-Aziz VWAP trend passes statistically on MNQ 1-min but is cost-dominated (32 sides/day, t 1.88 at 2 ticks) and negative
  since 2023-10; Connors RSI(2)/Double 7s, Turnaround Tuesday, overnight-after-down-day below the bar; 9,390 machine-generated rules,
  best = luck level, 0 survivors, holdout untouched. Nothing adopted. More searching on bars is exhausted; tick data is the next lever.
- 2026-10-20 wide source sweep + self-improving loop (`research\SOURCES2_*.txt`, `src2_*.py`, `python src2_run.py ...`): 113 published
  strategies (papers, quant sites, open code, best of 1,101 YouTube videos) on 16 years of 1-min NQ+MNQ. All YouTube systems failed.
  Pass but NOT adopted (no NoiseArea addition at the bar, overnight holds): C15 overnight long above SMA200 (t 3.96, DD $13.3k),
  C05 vol-managed overnight, W33 Williams %R(5). FOMC eve confirmed (t 3.78). YouTube transcripts: `yt_mine.py` (iOS player API,
  no playback). 1-min caches for NQ/ES 2010-19 and MNQ/MES 2019-26 in `research\regime_work\*_1m.npz` (src2_lib.py build).
- 2026-10-20 Bulenox-legal routes (`research\BULENOX_ROUTES_PLAN.txt` / `BULENOX_ROUTES_RESULTS.txt`, `bulenox_routes.py a|b`):
  Bulenox (own help center): flat by 3:59 PM CT, no overnight holds, up to 5 Master accounts, copiers allowed, 40% consistency.
  No legal rule passes - FOMC night leg 18:05->09:30 (t 2.85 but discovery t 1.31, drop-5 1.90, DD 1.23x), FOMC morning (t -1.37),
  overnight-premium night leg (alpha 1.66, P 0.065, DD 1.36x). Account model at 1 micro: only 25K pays after fees (+$1,550/account
  over 2 years; qualifying in the 30-day access: 25K 20%, 50K 3.7%). Open question for Bulenox: can the 30-day access be extended.
  SUPERSEDED 2026-10-21 by BIGLEVER (below): "only 25K pays" held only for a 1-micro evaluation.
- 2026-10-21 THE BIG LEVER (`research/BIGLEVER_PLAN.txt` / `BIGLEVER_RESULTS.txt`, `biglever.py`, `intraday_worst.py`, audit
  `biglever_check.py` 16/16): micros DURING the Bulenox evaluation (only the fee at risk). Intraday stops modelled (each session's
  worst point from 5-min bars; a close-only model overstated big sizes). 50K Qualification Option 2 + $78 resets: 1 micro 2.4% per
  30-day try -> 6 micros 60%, 8 micros 68% (fees per funded account $7,277 -> ~$370); 25K + resets 3-4 micros 58-65%. Value per
  slot over 2 years: plan of record $1,003 -> 50K eval 8 / Master 1 micro $2,425 (x2.4); Master 4 micros $3,078 but 18% open after
  1 yr. Fast Track (no evaluation, $1,000 drawdown, 20% consistency) and Momentum ($94 25K) are NOT better. Cost: Bulenox's daily
  loss limit touched 4-6 days per window at 6-8 micros - set NoiseArea's daily loss stop ~$1,000 so it halts itself first. Buying
  is Sashkin's call (no money now); first fee -> one 50K Qualification after the 20 clean Sim101 sessions; coupon code BULENOX.
  Era check (`biglever_eras.py`, real windows): 50K at 6 micros 54% per try in the last 12 months vs 48% at 8 -> use 6 micros;
  ~$250 per try incl. one reset, about one pass in two tries. Sim101 stays at 1 micro; the Master at 1 micro.
- 2026-10-20 event-day batch, intraday only (`research\EVENTDAYS_PLAN.txt` / `EVENTDAYS_RESULTS.txt`, `event_days.py`; BLS and
  Treasury calendars in `research\event_calendars\`): CPI/PPI/NFP announcement-day long, news-day momentum, release continuation,
  post-auction long, NoiseArea extra micro on macro days - all five dead (t 0.03 / -0.59 / -1.05 / -0.84; E5 fails Welch 0.91,
  P 0.35, DD 1.23x). Post-hoc only: NoiseArea earns more on NFP and auction days - seen, not tested.
- 2026-10-20 free batch, intraday only (`research/EDGES3_PLAN.txt` / `EDGES3_RESULTS.txt`, `edges3.py`): extra NoiseArea micro on
  mega-cap earnings reaction days (t 2.63 vs 2.70 bar; fails drop-5, Welch, ADDITION), earnings gap continuation (t 2.20), extra
  micro after VIX backwardation (Sharpe falls), lead-lag from ZN/M6E/MGC/MCL (flat or losing) - 0 of 7. Sizing up on
  high-volatility days adds risk in proportion; nothing adopted.
- 2026-10-20 NoiseArea modifications after three web scouts (`research/NAVAR_PLAN.txt` / `NAVAR_RESULTS.txt`, `nav_lab.py`,
  `navar.py`): long-only, shorts at half size, 90-day lookback, fast-alpha execution, exit persistence gate, lunch pause, EMA
  confirmation, intraday shock control, no gap bands - 0 of 9 (no drawdown-adjusted gain beyond luck). Long-only survives Bulenox
  more often (89% vs 66% after a year) but pays less ($1,846 vs $3,038 in two years) and fails on fresh markets. The SPY version
  has decayed since 2024; Nasdaq has held (two independent studies). Live strategy unchanged.
- 2026-10-20 second edge on another Bulenox market (`research/XMKT_PLAN.txt` / `XMKT_RESULTS.txt`, `xmkt.py`): 9 catalog
  families + last-half-hour momentum on MES/MYM/M2K/MGC/M6E/MCL/MBT at Bulenox commissions - 0 of 70 reach discovery t 2.0, median
  t +0.08 even before costs. NoiseArea (long + short) stays the only edge; no Treasury futures on Bulenox.
- 2026-10-20 limit-order test data, PRICED ONLY (`research/limit_quote.txt`, get_cost): MNQ 6-minute windows around all 3,329
  order times 2019-2026 - trades $108.11 / tbbo $180.18 / mbp-1 $250.17; the 249 10:00 orders of 2023-2026 in trades $18.86
  Credit is $33 per Sashkin: all 491 10:00 orders 2019-2026 in trades $28.90, or the 249 of 2023-2026 in tbbo $31.43. Buying is
  Sashkin's call.
- 2026-10-21 market vs limit entry at 10:00 (`research/LIMIT_PLAN.txt` / `LIMIT_RESULTS.txt`; data bought $28.90 with approval,
  D:\databento_archive\mnq_trades_1000_windows_2019-2026): keep market orders - a 1-tick-better limit held 60 s loses 5.7
  ticks/side (misses chase the breakout), 5/15/30-s delays lose too. 10:00 market entries really cost +2.6 ticks/side (1 tick in
  the model; ~$54/yr per micro) and +6.2 at 150 ms - latency is the lever, not order type.
- 2026-10-21 market mechanics (`research/MECH_PLAN.txt` / `MECH_RESULTS.txt`, `mech.py`): WATCH LIST - the third-Friday price
  spike short (short 09:30 -> 12:00 on monthly options-expiration Fridays; Baltussen-Terstegge-Whelan 2025) +13.2 bp/event, t 2.40,
  adds to NoiseArea (P 0.041, lower DD), 2022-26 out of sample +15 bp - but fails drop-5 (t 1.57; 3 of the 5 best in 2022). Re-run
  mech.py each January with the new year's events; a pass earns a forward test on a separate account. Month-end rebalancing,
  pinning, round numbers, weekend bitcoin, intraday periodicity: dead.
- 2026-10-21 today's whipsaw ideas + near-miss combination (`research/DAYCAP_PLAN.txt` / `DAYCAP_RESULTS.txt`, `daycap.py`, audit
  `daycap_check.py`; switches cap / stop_losses / no_relose in nav_lab.fam_V): 1 or 2 positions a day, stop after 2 losses, no
  re-entry in the losing direction - all LOSE $714-960/yr over 16 years ($1,700-2,400/yr on MNQ 2019-26; the third try is often
  the trend). K1 = 20-min exit gate + lunch pause + EMA: best candidate yet (+$552/yr, Sharpe 1.27 vs 1.08, DD $6,418 vs $7,906,
  better on all 3 Nasdaq sets, ES, MES and Bulenox +$1,050/2 yrs) but P 0.27 vs 0.0036 bar - not adopted.
- PAPER FORWARD TEST (zero clicks, since 2026-10-22): `research/fwd_shadow.py update` runs in the daily review (step 1b) and
  appends base, V2, V5, V6, V7, K1, C1-C4 recomputed from the strategy's own bars to `research/fwd/shadow_ledger.csv`
  (append-only). Decision only each January (`fwd_shadow.py january`, annual routine step 6): extended-series bootstrap at each
  variant's original bar. Power is low (V5 needs ~23 years for t 2); it catches a variant that is clearly worse, cheaply.
  Standing rule (2026-10-21): every possible improvement gets tested without asking; never offer free work as a question.
- 2026-10-22 volatility-scaled evaluation micros (`research/EVALVOL_PLAN.txt` / `EVALVOL_RESULTS.txt`, `evalvol.py`): 2..10 or
  2..6 micros by the strategy's own sigma vs a fixed 6 - neither better (E1 41% per try in the last 12 months vs 54%). Fixed 6 stays.
- TAPE RECORDER (2026-10-22): `bin/Custom/Indicators/TapeRecorder.cs` (copy in `research/recorder/`), offline build 0 errors,
  canary fails; F5 pending (his, when flat). Listens only: every MNQ trade print with the bid/ask at the print, every best bid/ask
  change, and every order event / execution on all accounts with ms receive times -> `D:\nt_recorder\` (CSV per UTC day; parquet
  copies by `fwd_shadow.py update`); status `agent_exports\TapeRecorder_status.json`, checked by `review_all.py`. Own chart only,
  one instance. No Level 2 (not used by NoiseArea; tens of millions of events a day on the live platform).
- NINJATRADER'S OWN TICK STORE (found 2026-10-22): `db\tick\MNQ <contract>\*.Last.ncd` holds every MNQ trade since 2025-10-26
  (~1.9 GB, hourly files), `db\minute\` MNQ/MES minutes since 2019. Never write there; work on copies. Decoder: research\ncd_reader.py.
- DATA MAP AFTER THE 2026-10-22 AUDIT (research/LOGGING_AUDIT_20261022.txt, INSTITUTIONAL_DATA_/OPS_20261022.txt):
  Windows tasks (all pythonw, headless, created at his request): "NoiseArea autopush" hourly :07 (package + offsite records to
  GitHub, clock probe), "NoiseArea nightly archive" 18:20 (`review/archive_nightly.py`: bars, tape parquet, NinjaTrader tick store
  -> D:\nt_ticks, NinjaTrader log/trace -> D:\nt_logs (NT deletes them after 20 days), database backup -> D:\nt_db + ledgers,
  Windows power/crash events, daily manifest of code hashes / NT version / settings, saved evening review, SHA-256-verified mirror
  D:->C:\NoiseAreaData_mirror and Desktop Databento files -> D:\NoiseAreaData_mirror, VIX), "NoiseArea watchdog" every 2 min
  (`review/watchdog.py`: NinjaTrader running, heartbeat fresh, state running, account = strategy, flat after the close; log
  records/watchdog_log.csv; phone push only once D:\nt_logs\watchdog_config.json has an ntfy topic / healthchecks URL).
  Records (offsite via GitHub): agent_exports\records\ nt_executions / nt_orders / nt_order_updates (every NinjaTrader order event
  and fill since the db began, append-only), account_items (daily balances, raw AccountItem codes), windows_events, daily_manifest,
  clock_offset (hourly; PC ~35-45 ms FAST on 10-22), watchdog_log, reviews\<date>.txt. review_all.py prints a line for recorder,
  package, archive and watchdog. OneDrive.exe has NOT run since 2026-08-16 - nothing here was cloud-backed before this.
  Known residual gaps: NT "Record live data as historical" OFF (his click); TapeRecorder awaits F5 + its own chart (its chart must
  move to the new contract at each roll, like the strategy); the strategy itself still logs no per-order events, 16:00 flat or
  build stamp (covered by TapeRecorder + db ledgers + daily manifest; a strategy-side order log / pre-trade limits / stale-data
  check is the next NinjaScript build, needs its own plan + parity). Windows Time is now Automatic (polls every 4,096 s).
  Tested and NOT adopted 2026-10-22 (RISKCTL_PLAN/RESULTS): broker-held disaster stop ($400-1,000/micro costs 9-15% of P&L) and
  room-scaled evaluation micros (worse). EARLYFIRE (`research/EARLYFIRE_PLAN.txt` / `_RESULTS.txt`): sending the 10:00 order
  250 ms / 1 s before 10:00:00 saves ticks on the tape but flips 1.2-1.6% of decisions, and the missed entries are exactly the
  last-second surges into the band (cost $73-145/yr vs $77-112/yr saved) -> NOT PROMISING; keep the bar-close order. Side finding:
  MNQ spreads around 10:00:00 are 2-3 ticks median, 14-20 at the 90th percentile, so the +2.6 ticks/side 10:00 cost is a floor -
  only live fill logging (TapeRecorder) can measure it.
- PC RELIABILITY (found 2026-10-22 when he asked about leaving it unattended): Windows logged 10 "rebooted without cleanly
  shutting down" (Kernel-Power 41, BugcheckCode 0, no long power-button press = power cut / power supply / hard freeze) in 60
  days: 2026-10-21 21:22, 10-03 17:47, 10-02 13:54 (in session), 09-27 02:01 and 02:10, 09-14 12:22-14:24 x5 (in session).
  After any restart NinjaTrader needs its own login and never re-enables a strategy, and the Windows tasks (Interactive logon)
  only run once someone logs in. Settings already right: updates PAUSED until 2026-11-26, active hours 06-24, AC sleep/hibernate
  never, High performance. review_all.py now warns on any unexpected shutdown in the last 26 h. Before unattended trading: find
  the cause / UPS, remote access (Chrome Remote Desktop), ntfy + healthchecks (dead-man), Rithmic mobile kill switch.
- BARS ARCHIVE (2026-10-22): the strategy rewrites `NoiseArea_bars_MNQ_strategy_<acct>.csv` from NinjaTrader's history on every
  start, so live-recorded bars were lost. `fwd_shadow.py update` (daily review step 1b) now appends each finished calendar day,
  first copy kept, to `research/fwd/bars_archive_MNQ_Sim101.csv` (seeded 2026-10-22: 430 days, 2025-06-08 .. 2026-10-21, 5-min
  OHLCV incl. overnight). Kept elsewhere already: trades (one file per start), checks_live (append-only decisions + bands),
  events (append-only), the paper-test ledger, NinjaTrader's own db (orders/executions). Nothing finer than 5-minute bars is kept.
- MNQPropBook (the earlier overnight strategy) is retired on this PC: its .cs is gone from bin\Custom (copy on GitHub
  Linderwow/MNQPropBook) and its Windows tasks EdgeHealthMonitor / EdgeHealthWatchdog are disabled, not deleted.

## Scheduled checks (all local Claude Code routines since 2026-10-17)
Prompts live in `agent_exports\review\routines\*.md`; each task's instructions are a byte-identical copy. To change
one, edit the .md AND the task (Scheduled -> task -> Edit), then compare. Times include the app's fixed per-task delay.

| Task | Runs (ET) | Alerts via PushNotification when |
|---|---|---|
| noisearea-morning-preflight | weekdays 09:17 | anything other than the one-line "Pre-flight OK" |
| noisearea-restart-test | weekdays 14:43 (cron 14:40 since 2026-10-20, between the 14:30 and 15:00 decisions) | a position is open (act now) and with the verdict; PASSED needs a new live_since/trade_log and account = strategy position; delete the task once PASSED |
| noisearea-daily-review | weekdays 16:38 (step 1b appends the paper forward test ledger, information only) | NEEDS ATTENTION or an EARLY CLOSE warning |
| noisearea-annual-revalidation | Jan 5 09:13 | always (the verdict line) |

- Routines fire only while the Claude desktop app is open and the PC awake (missed runs catch up on the next launch).
  PushNotification reaches the desktop, and the phone only while Remote Control is connected.
- Each task must have Permission mode = Auto (set by Sashkin in the app; Claude cannot set it). Without it a run stalls
  on its first command with nobody there.
- Cloud: "NoiseArea restart test prompt" is paused (can be deleted from the Chat side); the other three cloud tasks were
  deleted. The local versions replace all four.
