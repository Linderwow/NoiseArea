NoiseArea research archive (copied 2026-10-15). Everything needed to re-run the validation without Claude.

Data:   build the 5-minute CSVs from your Databento files with
          python convert.py   (edit the file list at the bottom; writes mnq_db_5m.csv / mes_db_5m.csv)
        or use NinjaTrader's own export agent_exports\NoiseArea_bars_MNQ_strategy_<account>.csv renamed with a 'time' column.
Needs:  python 3, pip install pandas numpy scipy databento

Scripts (run from this folder):
  published.py       reference implementation of the strategy (S1) and two other published strategies; S1 is ground truth
  research_engine.py fast engine for six strategy families; fam_A reproduces published.S1 to the dollar
  discovery.py       684-variant sweep on 2019-2022 (VARIANTS_PLAN.txt)          -> discovery_results.csv
  validation.py      finalists on unseen MNQ 2023-25, MES 2023-26, NT 2026        -> validation_results.csv
  adaptive.py        adaptive-tuning experiment (ADAPTIVE_PLAN.txt)               -> adaptive_results_*.csv
  s1_trades.py       trade list from the reference rules (for parity checks against NinjaTrader's trade log)
  robustness.py      slippage drag, top-trade dependence, Monte Carlo on a NinjaTrader trade CSV
  onfade.py / sim.py overnight-range fade family (failed its holdout; kept for the record)

Annual re-validation (do this every January, change nothing before looking):
  1. Export the year's bars (Strategy Analyzer run of NoiseAreaStrategy writes NoiseArea_bars_MNQ_strategy_Backtest.csv).
  2. python robustness.py <the Analyzer trade CSV>   -> compare with the 2019-2026 figures in the launch plan.
  3. The strategy stays as it is unless the new year falls outside the historical range AND you decide to stop, never to tweak.

2026-10-15 (evening) specification fix: sigma at a time slot is the average over the sessions in the 14-day window that HAVE that
slot (at least 7 of 14), instead of "undefined if any session lacks it" (which disabled afternoon checks for 14 sessions after
every early close). Applied in NoiseAreaMomentum.cs TrySigma, published.py, s1_trades.py, research_engine.py and daily_review.py.
reimpl_independent.py is the independent re-implementation from the paper that surfaced this; trades_independent_reimpl.csv its
trades (1,666), reproduced exactly by s1_reference_trades_ntbars_v2.csv. discovery/validation/adaptive result files predate the fix.

2026-10-16 final audit changes: partial sessions (CME equity index closes at 13:00 ET on US cash-market holidays, 13:15 ET the
day after Thanksgiving and on Dec 24) are now handled from the Trading Hours template (SessionIterator) with a date list as
backup; the reference (published.py, s1_trades.py, research_engine.py) is flat on the bar before the last on such days, so
s1_reference_trades_ntbars_v3.csv (1,666 trades, $24,232 net after 1 tick/side slippage and $1/RT commission, i.e. raw points x $2
less $2 a round trip) is the current parity reference. Strategy: account/strategy
desync guard, day-state persistence for the loss stop, history warning, richer heartbeat. Review: see daily_review.py header.

2026-10-16 cross-market and long-history test (data bought from Databento for $53.74: MGC, ZN, M6E, MCL, MBT 2019/2021-2026;
NQ and ES 2010-2019; MNQ 2026): MARKETS_PLAN.txt (pre-registered), markets.py (runner: python markets.py <folder with the
.dbn.zst files>), MARKETS_RESULTS.txt (verdicts), markets_run1*.txt (raw output). Result: no other market passes; NQ 2010-2019
fails the net t>2 bar (1.94; gross t 3.28, 2018-dominated); ES never net-tradable. Consequence: MNQ only, 1 micro, prudent cushion
until 60 clean live sessions. Data files stay in Desktop\Desktop\databento_data.

2026-10-16 16-year expectations: LONGHIST_PLAN.txt (pre-registered), longhist.py (python longhist.py; needs nq_2010-2019_5m.csv,
nt_analyzer_bars.csv and raw_open930.csv - the raw 09:30 prices, because the continuous files are back-adjusted), LONGHIST_RESULTS.txt
(verdicts: ~8% of notional a year, worst drawdown 14% of notional in 2010-11, cushion raised to 21%/38% of notional per micro),
longhist_results_raw.txt / longhist_dd.txt / longhist_bulenox.txt (raw output), calendar_validation.txt and ntbars_integrity.txt
(data checks; daily_review.py Juneteenth fix came from them).

2026-10-16 addendum: M2K and MYM bought ($18.03) and run through the same section A (markets_run2_m2k_mym.txt, MARKETS_RESULTS.txt
section A2): both fail; the effect is Nasdaq-100 specific. MNQ-only conclusion closed.

2026-10-16 stop-loss / profit-target test: STOPS_PLAN.txt (pre-registered), stops.py (python stops.py; overlay engine reproduces
fam_A to the cent with the overlay off), STOPS_RESULTS.txt (verdict: not adopted - the best variants sit on a narrow ridge, fail the
plateau test and do not survive realistic fills on NQ 2010-2019; a wide stop alone at 0.75 noise widths is neutral-to-mildly-helpful,
tight stops hurt, targets alone do nothing), stops_grid_output.txt / stops_checks.txt / stops_results.csv (raw).

2026-10-16 remaining-ideas tests: ALPHA2_PLAN.txt (pre-registered), alpha2.py, ALPHA2_RESULTS.txt (FOMC-day handling, overnight
hold, reversal-vs-flat, daily loss stop, parameter ensembles, entry window: all neutral or harmful out of sample; nothing adopted),
alpha2_grid_output.txt (raw).

2026-10-16 dealer gamma exposure test: GEX_PLAN.txt (pre-registered), gex_dix.csv (Squeezemetrics daily SPX DIX/GEX 2011-2026,
free), gex_test.py, GEX_RESULTS.txt (mechanism visible - low-gamma days pay 2-5x more per day - but no filter or sizing rule clears
the bar; nothing adopted), gex_results_raw.txt (raw).

2026-10-19 scaling-schedule test: SCALING_PLAN.txt (pre-registered), scaling.py (python scaling.py; rebuilds its inputs into scaling_inputs\
from Desktop\Desktop\databento_data\NQ_ohlcv-1m_2010-2019.dbn.zst and agent_exports\NoiseArea_bars_MNQ_strategy_Backtest.csv cut at
2026-10-14, then audits against LONGHIST_RESULTS.txt column (b) - reproduced to $0.31), SCALING_RESULTS.txt (verdict: month-end sizing for a
self-funded account after the 60-session gate - 1 micro per 21% of notional from a $25k-50k start, per 38% from $105k; half-Kelly fails
everywhere), scaling_results_raw.txt (raw). scaling_inputs\ holds rebuilt copies of the 2026-10-16 LONGHIST inputs; the originals
(nq_2010-2019_5m.csv, nt_analyzer_bars.csv) are back in this folder since relvol_data.py rebuilt them on 2026-10-19.

2026-10-19 alarms: MONITOR_PLAN.txt (pre-registered), monitor_calibrate.py (python monitor_calibrate.py; CUSUM levels by month-block
bootstrap of scaling_inputs\scaling_daily.csv, cost rule on exec_pilot_sides.csv) -> monitor_calibrate_output.txt, MONITOR_RESULTS.txt
(wired into daily_review.py via review\monitor.py; also documents that the 20-session gate is in 2019-2025 dollars).

2026-10-19 relative-volume filter test: RELVOL_PLAN.txt (pre-registered), relvol_data.py (rebuilds nt_analyzer_bars.csv, mnq_db_5m.csv,
mnq_db_5m_2019-2026.csv and nq_2010-2019_5m.csv into this folder with convert.py, about 124 MB, and sizes the Databento calendar-roll volume
artifact -> relvol_data_output.txt), relvol.py (python relvol.py; the filter-off overlay reproduces s1_reference_trades_ntbars_v3.csv trade
for trade and fam_A to the cent), RELVOL_RESULTS.txt (verdict: not adopted. Requiring RV >= 1.0 cuts validation net by 46% on NT bars and
68% on DB bars. Paying break-outs come at about normal volume), relvol_output.txt (raw), relvol_check.py -> relvol_checks.txt (independent
re-implementation, identical to the cent).

2026-10-19 trend-following sleeve test: TREND_PLAN.txt (pre-registered), trend.py (python trend.py [--work DIR]; builds its own 5-minute bars
with the raw close and instrument id kept - nq_2010-2019_5m_raw.csv, mnq_db_2019-2026_5m_raw.csv and their *_switches.csv, about 100 MB - and a
renamed copy of the NT Backtest bars export into --work, default this folder), nasdaq100_fred.csv (FRED NASDAQ100 cash index, free: look-back
warm-up before June 2010 and a reported roll-weekend sensitivity), TREND_RESULTS.txt (verdict: fails at discovery - no trend rule beat buy-and-hold
risk-adjusted; a trend leg smooths NoiseArea but no better than buy-and-hold at equal risk; nothing adopted), trend_results_raw.txt (raw),
trend_daily.csv (16-year daily NoiseArea / 6-month trend / buy-and-hold, return on notional). Data note: the Databento .c.0 files have no session on
quarterly expiry days (all 30 in 2019-2026, 35 of 36 in NQ 2010-2019) and lack 2025-12-31 (boundary between two purchases).

2026-10-19 fill-cost pilot: EXEC_PILOT_PLAN.txt (pre-registered), exec_pilot.py (python exec_pilot.py; reads the Dec-2025 1-second file and
the MNQ trades tapes in D:\databento_archive in 2M-trade chunks, about 6 minutes), exec_pilot_verify.py (independent loop-based re-computation:
python exec_pilot_verify.py exec_pilot_sides.csv <trades file> <first date> <last date>), exec_pilot_ntbars.py (NinjaTrader bar boundary vs the
tape), EXEC_PILOT_RESULTS.txt (verdict: 1-second bars cannot measure the fill cost, so the $20.86 purchase is NO-GO and there is no Sim101 trial;
market-order cost 0.6-1.4 ticks/side at 0-50 ms order latency and 5.4 at 250 ms, the excess in the 10:00 decisions; a limit-then-market variant
looks promising on 124 sides but is untested), exec_pilot_output.txt / exec_pilot_sides.csv / exec_pilot_ntbars_output.txt (raw). Data note:
D:\databento_archive\mnq_trades_2026-01-01_2026-04-16.dbn.zst is truncated at 2026-04-09 11:40 ET; Apr 10-16 come from mnq_trades_2026-04-10_2026-04-17.

2026-10-20 routine and alarm fixes: MONITOR_PLAN.txt amendment (cost rule for daily re-testing), monitor_cost_seq.py ->
monitor_cost_seq_output.txt (calibration), MONITOR_RESULTS.txt addendum; review\annual_window.py (annual re-validation's
same-calendar-window comparison, % of notional, raw_open930.csv as notional); routines restart-test.md (14:40, proof-of-restart
pass rule), annual-revalidation.md (partial-year handling), morning-preflight.md (history wording, roll timing).

2026-10-20 double-verification follow-up: VERIFY_FOLLOWUP_20261020.txt (the 16 verifier disagreements the cut-off workflow left
unsettled, every still-open finding, the gaps pass, and what was fixed); its scripts are settle_research.py (R1 on 1,000,000 paths,
partial months, exact-CRN CUSUM levels, section D bootstrap -> settle_research_output.txt) and test_followup.py (18 synthetic checks
of the review changes -> test_followup_output.txt), copied into verify_followup_20261020\. Addenda in MONITOR_RESULTS.txt and
SCALING_RESULTS.txt. robustness.py no longer charges commission twice on logs that carry it (NinjaTrader's profit is net).

2026-10-20 regime-switching catalog test ("Patrick's way": state -> structure -> pick a strategy from a catalog -> risk gate):
REGIME_PLAN.txt (pre-registered), regime_test.py (python regime_test.py build / build_db / discover / holdout; 9 families, 732
variants incl. three new ones - VWAP stretch, prior-day high/low, opening gap; NQ 2010-2019 discovery, MNQ 2019-2026 holdout; cache
in regime_work\, about 3 MB, rebuilt in about 3 minutes), regime_check.py (audits: new families re-implemented, holdout rebuilt),
regime_diag.py (why it fails), REGIME_ML_PLAN.txt + regime_ml.py (the same with a learned state classifier), REGIME_RESULTS.txt
(verdict: dead on this data, both versions), regime_output.txt / regime_ml_output.txt (raw).

2026-10-20 market-structure methods from the web (Market Profile open types / initial balance / 80% rule, ICT Silver Bullet,
liquidity sweep / Wyckoff spring, Gao et al. last-half-hour momentum, Crabel ORB with NR4 / NR7 / inside day; structure states:
compression, open vs prior value and range, open type, variance ratio, choppiness): STRUCTURE_PLAN.txt (pre-registered),
structure_test.py (build / discover / holdout; uses regime_test.py's cache), structure_check.py (independent audit), STRUCTURE_RESULTS.txt
(verdict: dead on this data - no standalone edge, no useful addition, no switcher beats NoiseArea), structure_output.txt (raw).

2026-10-20 MNQ-only program (walk-forward on MNQ data alone, OOS 2022-2026): MNQ_PROGRAM_PLAN.txt (pre-registered), mnq_program.py
(python mnq_program.py run; uses regime_work\), mnq_program_check.py (audits), MNQ_PROGRAM_RESULTS.txt (verdict: the FOMC-eve overnight
long passes as an add-on to NoiseArea on both data sources; walk-forward selection, Patrick's way, ensembles, long/short-only, vol
sizing and a learned entry filter fail), mnq_program_output.txt and mnq_fomc_diag.txt (raw). fomc_eve\ holds the NinjaScript add-on (live
source since 2026-10-20, off by default), its reference trades, deployment notes, fomc_parity.py and parity_20261020.txt (both runs passed).

2026-10-20 YouTube strategies (Patrick's suggestion; 15 videos, 8 codable): YOUTUBE_PLAN.txt (pre-registered, with sources and links),
youtube_test.py (python youtube_test.py smoke | run; MNQ 1-minute bars from D:\dev\trading\MNQPropBook\out, cached in regime_work\mnq_1m.npz),
youtube_check.py -> youtube_check_output.txt (independent audit), YOUTUBE_RESULTS.txt (verdict: all eight dead on MNQ; every claimed win
rate far above what the rules produce), youtube_output.txt (raw).

2026-10-20 research-grade sources + automated rule search: RULEMINE_PLAN.txt (pre-registered), rulemine.py (python rulemine.py audit |
run), rulemine_output.txt, rulemine_audit.txt, rulemine_check.py -> rulemine_check_output.txt, vwap_trend_diag.txt, RULEMINE_RESULTS.txt (verdict: the Zarattini-Aziz VWAP trend passes statistically on MNQ
2019-2026 but is cost-dominated (32 sides a day) and negative since its publication; Connors RSI(2)/Double 7s, Turnaround Tuesday,
overnight-after-down-day below the bar; 9,390 machine-generated rules: none better than luck).

2026-10-20 wide source sweep + self-improving test loop: SOURCES2_PLAN.txt (pre-registered, amended only before each stage),
SOURCES2_CATALOG.txt (every source, score, action), SOURCES2_RESULTS.txt; code src2_lib.py (1-min NQ/MNQ/ES/MES caches from the local
Databento files + scorecard), src2_test.py (stages), src2_strats_a.py / src2_strats_b.py / src2_strats_c.py (113 strategies), src2_run.py
(python src2_run.py smoke | stage1 [--new] | stage2 [prefix prior_csv]); YouTube miner yt_mine.py (search + transcripts via the iOS
player API, nothing played) and yt_screen.py; audits src2_check.py, src2_check_c.py; outputs src2_stage1_AB.txt, src2_stage1.txt /
src2_stage1_ABC.csv, src2_stage2_AB.txt, src2_stage2_C_log.txt, src2_check*_output.txt. Order-flow pilot FLOWPILOT_PLAN.txt,
flow_pilot.py -> flow_pilot_output.txt (regime_work\mes_flow5m_2025-09_2026-06.csv built by ..\downsample_trades.py). Tick-data price
tickdata_quote.py -> tickdata_quote.txt (get_cost only). Verdict: overnight-above-SMA200 (C15) and its volatility-managed version
(C05) and Williams %R(5) (W33) pass but do not add to NoiseArea at the bar and hold overnight; FOMC-eve confirmed; YouTube all failed.

2026-10-20 order flow at NoiseArea's NQ entries (purchased data): ORDERFLOW_PLAN.txt (pre-registered before buying), nq_tick_buy.py
(capped, resumable Databento download + ledger), orderflow_test.py (python orderflow_test.py trades | run) -> orderflow_output.txt,
orderflow_trades_nq.csv, ORDERFLOW_RESULTS.txt. Verdict: no feature predicts the trade outcome on 2010-2014 (all |rho| < 0.05); stopped
as pre-registered; order flow as an entry filter is closed.

2026-10-20 Bulenox-legal routes to more profit: BULENOX_ROUTES_PLAN.txt (pre-registered; Bulenox rules from its own help center),
bulenox_routes.py (python bulenox_routes.py a | b) -> bulenox_routes_output.txt, bulenox_t1/t2/t3_daily.csv, bulenox_account_model.txt;
audit bulenox_routes_check.py -> bulenox_routes_check_output.txt; BULENOX_ROUTES_RESULTS.txt. Verdict: no legal rule passes (FOMC night
leg 18:05 -> 09:30 fails 3 gates, FOMC morning loses, overnight-premium night leg fails 4); the legal lever is the number of accounts -
at 1 micro only the 25K plan pays after fees because qualifying inside the 30-day access is rare above 25K.

2026-10-20 projections for the live set-up: projections.py -> projections_output.txt (16-year month-block bootstrap per micro at
today's cost; reproduces LONGHIST by its day-by-day method and the MNQ-era $6,180/yr / $4,590 drawdown). Rows: NoiseArea alone, with
1.5 ticks/side live fill drag, with the edge halved, MNQ era only, and + the FOMC-eve add-on (parked: Bulenox forbids overnight holds).

2026-10-20 event-day batch, Bulenox-legal intraday only: EVENTDAYS_PLAN.txt (pre-registered), event_calendars\ (BLS CPI/PPI/NFP
release dates from bls.gov archives, checksummed; Treasury 10y/30y auctions from fiscaldata), event_days.py -> eventdays_output.txt,
eventdays_e1..e5_daily.csv; audit eventdays_check.py -> eventdays_check_output.txt; EVENTDAYS_RESULTS.txt. Verdict: all five dead
(announcement-day long t 0.03, news-day momentum t -0.59, release continuation t -1.05, post-auction long t -0.84, NoiseArea extra
micro on macro days fails Welch/ADDITION/drawdown). Nasdaq-100 rebalance days untestable on Databento files (expiry-day gap).

2026-10-20 ~19:00 ET Bulenox clean-up (Sashkin: "anything not allowed by Bulenox, remove"): the FOMC-eve add-on is out of the live
NoiseAreaStrategy.cs (back to the 10-19 parity build) and out of daily_review.py and the morning pre-flight (pre-FOMC versions
restored; review output identical); exit-on-session-close moved from 30 s to >= 120 s before the session end (15:58 CT, inside
Bulenox's 3:59 PM CT). fomc_eve\ is now research only. Offline whole-project build 0 errors.

2026-10-20 free batch - earnings days, cross-market lead-lag, VIX term structure (intraday only): EDGES3_PLAN.txt (pre-registered),
event_calendars\earnings_8k_202_20261020.json (SEC EDGAR 8-K Item 2.02 dates, checksummed) and vix3m_history_cboe_20261020.csv,
caches regime_work\{zn,m6e,mgc,mcl}_1m.npz, edges3.py -> edges3_output.txt, edges3_*_daily.csv; audit edges3_check.py ->
edges3_check_output.txt; EDGES3_RESULTS.txt. Verdict: 0 of 7 (closest: extra NoiseArea micro on AAPL/MSFT/AMZN/GOOGL reaction days,
t 2.63 vs 2.70 bar, fails drop-5 / Welch / ADDITION; lead-lag from ZN, M6E, MGC, MCL flat or losing).

2026-10-20 NoiseArea modifications for a drawdown-limited account (after three web scouts): NAVAR_PLAN.txt (pre-registered), nav_lab.py
(variant engine fam_V; reproduces regime_test.joined()'s NoiseArea column exactly; python nav_lab.py check), navar.py ->
navar_output.txt, navar_series.csv; audit navar_check.py -> navar_check_output.txt; NAVAR_RESULTS.txt. 5-minute bars for ES, MES,
MYM, M2K in regime_work\bars5m\. Verdict: 0 of 9 (long-only, shorts at half size, 90-day lookback, fast-alpha execution, exit
persistence gate, lunch pause, EMA confirmation, intraday shock control, no gap adjustment) - no MAR gain beyond luck; lunch pause,
half-size shorts, exit gate and EMA confirmation improve all three Nasdaq data sets without significance.

2026-10-20 second edge on another Bulenox market: XMKT_PLAN.txt (pre-registered), xmkt.py -> xmkt_output.txt, xmkt_stage1.csv; checks
xmkt_check.py -> xmkt_check_output.txt, xmkt_gross.csv; XMKT_RESULTS.txt. 5-minute bars for MGC, MCL, M6E, MBT added to
regime_work\bars5m\. Verdict: none - 0 of 70 rules (9 catalog families + last-half-hour momentum on MES, MYM, M2K, MGC, M6E, MCL,
MBT at Bulenox commissions) reach discovery t 2.0; gross of costs the median rule has t +0.08. ZN is not on Bulenox's rate sheet.

2026-10-20 limit-order test data price (pricing only, nothing bought): limit_quote.py -> limit_quote.txt, limit_quote_windows.csv.
6-minute windows around all 3,329 MNQ order times 2019-2026: trades $108.11, tbbo $180.18, mbp-1 $250.17; 10:00 orders 2023-2026
trades $18.86 (fits the ~$19.70 credit).

2026-10-21 market vs limit entry at 10:00 (purchased data, $28.90 approved by Sashkin): LIMIT_PLAN.txt (pre-registered, with a
follow-up), limit_buy.py (capped download -> D:\databento_archive\mnq_trades_1000_windows_2019-2026\), limit_test.py ->
limit_output.txt, limit_entries.csv; limit_followup.py -> limit_followup_output.txt; LIMIT_RESULTS.txt. Verdict: keep market orders -
a 60-s limit loses 5.7 ticks/side (missed limits chase the breakout), delays lose too; 10:00 market entries cost +2.6 ticks/side
(~$54/yr per micro over the 1-tick model) and +6.2 at 150 ms latency.

2026-10-21 market-mechanics effects (web scout): MECH_PLAN.txt (pre-registered), mech.py -> mech_output.txt; audit mech_check.py ->
mech_check_output.txt; MECH_RESULTS.txt; free data event_calendars\dgs10_fred_20261021.csv, btc_usd_hourly_coinbase.csv
(btc_hourly_fetch.py). Verdict: none adopted. Third-Friday price spike short (Baltussen-Terstegge-Whelan 2025) passes 7 of 8 gates,
fails the drop-5 robustness (t 1.57) - WATCH LIST, re-test yearly. Rebalancing, pinning, round numbers, weekend crypto, periodicity dead.

2026-10-21 today's whipsaw ideas + near-miss combination: DAYCAP_PLAN.txt (pre-registered), daycap.py -> daycap_output.txt,
daycap_series.csv; audit daycap_check.py -> daycap_check_output.txt; DAYCAP_RESULTS.txt. Verdict: none adopted - daily caps and
loss stops lose $714-960/yr; K1 (exit gate + lunch pause + EMA) is the best candidate yet but not beyond luck (P 0.27).
Paper forward test (DAYCAP_PLAN part B): fwd_shadow.py update | report | january -> fwd\shadow_ledger.csv (append-only, from
2026-10-22; run by the daily-review routine), fwd\live_bars_research_format.csv (scratch copy of the strategy's bars),
fwd\bars_archive_MNQ_Sim101.csv (2026-10-22: every finished calendar day of the strategy's 5-minute bars, appended once, first
copy kept - the live bars file itself is rewritten from history on every strategy start).

2026-10-21 the big lever - Bulenox account type and micros in the evaluation vs after funding: BIGLEVER_PLAN.txt (pre-registered;
amendment 1 after the first run: intraday stops), intraday_worst.py -> intraday_worst.csv (each session's worst point; r_close ==
nav_lab.series), biglever.py [close] -> biglever_output.txt (primary) / biglever_output_closeonly.txt (first run reproduced:
biglever_output_run1_closeonly.txt); audit biglever_check.py -> biglever_check_output.txt; BIGLEVER_RESULTS.txt. Verdict: size the
EVALUATION (50K at 6-8 micros + resets ~60-68% per try vs 2.4% at 1 micro), Master at 1 micro: ~x2.4 value per account slot;
Fast Track and Momentum not better.

2026-10-22 early fire at 10:00 (decide on the last trade before 10:00:00 - 250 ms / 1 s and send then; INSTITUTIONAL_OPS item 7):
EARLYFIRE_PLAN.txt (pre-registered, amendment A1), earlyfire.py -> earlyfire_output.txt, earlyfire_days.csv, earlyfire_ntyear.csv;
audit earlyfire_audit.py -> earlyfire_audit_output.txt, earlyfire_audit_quotes.csv; EARLYFIRE_RESULTS.txt. Verdict: NOT PROMISING -
1 s saves +3.3 ticks/side on the tape (+2.3 after recorded quotes) but flips 1.6% of decisions (missed momentum entries) costing
more than it saves ($205 vs $105 in the NT year; misses $73-145/yr over 7 years); 250 ms has no saving with real quotes. Side
finding: MNQ spreads around 10:00 are 2-3 ticks median (p90 14-20) in 2025-26, so the 1-tick touch model understates fill costs.

2026-10-31 weekend program (his ask: Patrick-style chart recognition -> strategy, other edges, drastically lower drawdown):
WEEKEND_PLAN_20261031.txt (pre-registered, 9 trials), weekend.py (python weekend.py audit | run -> weekend_output.txt,
weekend_series.csv; the 10-30 run's complete output incl. chval.compare lines is weekend_run.log), weekend_check.py (independent
re-implementation of every new switch -> weekend_check_output.txt, ALL PASS), weekend_knn.py (A1 analog days -> weekend_knn_output.txt),
weekend_flow.py (A3 order flow at the open from D:\nt_ticks -> weekend_flow_output.txt, weekend_flow_features.csv), weekend_extra.py
(post-hoc controls + the Bulenox prop model -> weekend_extra_output.txt), patrick_calls.py (+ patrick_calls_template.csv: scores
time-stamped day-type calls), WEEKEND_RESULTS_20261031.txt (verdict: 0 of 9 adopted; B2 = K1 + chandelier is the best MAR ever seen
but dead by its kills, recorded for information in fwd\shadow_ledger_weekend.csv via fwd_shadow.update_weekend from 2026-11-02).
Same evening: GAMMA_SWITCH_PLAN.txt (pre-registered), gamma_switch.py -> gamma_switch_output.txt (the dealer-gamma state as the
strategy switch, on regime_test's catalog matrices + gex_dix.csv): DEAD - reversion families lose in every gamma state; verdict in
WEEKEND_RESULTS_20261031.txt section E.
LH_PLAN.txt / lh_test.py -> lh_output.txt (Baltussen last-half-hour rule on NQ/MNQ: dead, section F). STRUCTURE_LIT_20261031.txt =
the evidence review of every 'market structure reading' family (five web scouts), mapped to what was tested here and its status.

2026-11-03 MARKET STRUCTURE (MSB): MSB_PLAN.txt (pre-registered, amended for time frames before the first run), msb.py (python msb.py ->
msb_output.txt; bar-for-bar port of the chart indicator staged\20261103_market_structure\MarketStructure.cs: swing N bars each side
confirmed N bars later, BOS / CHoCH on bar close), msb_test.py (20 hand-made-bar checks of the rules, ALL PASS), msb_parity.py
(<breaks csv> <bars csv>: the indicator's agent_exports\MarketStructure_breaks_*.csv against the port on the chart's own bars),
regime_work\bars1m\ (Databento MNQ / MES 1-minute bars, built by msb.py). Verdict MSB_RESULTS.txt: the break's direction predicts
nothing at 1- to 60-minute bars (primary 5-min N=5: NQ t +0.10, MNQ t -0.00, MES t -1.48; follow-through 47-50%). Map, not signal.
