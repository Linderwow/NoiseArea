FOMC-EVE ADD-ON - STAGED FOR SIM101 (2026-10-20; evidence in ..\MNQ_PROGRAM_RESULTS.txt, re-confirmed on 16 years in ..\SOURCES2_RESULTS.txt)

*** UPDATE 2026-10-20 ~19:00 ET: REMOVED FROM THE LIVE FILE (Sashkin: "anything not allowed by Bulenox, remove"). bin\Custom\
*** Strategies\NoiseAreaStrategy.cs = NoiseAreaStrategy.cs.before_20261020 + exit-on-session-close >= 120 s (Bulenox 3:59 PM CT);
*** daily_review.py and the pre-flight (file + task) restored to their pre-FOMC versions; the add-on build is kept here and in
*** _to_delete\NoiseAreaStrategy.cs.with_fomc_addon_20261020. Everything below is history. To use it on an account that allows
*** overnight holds: copy this folder's NoiseAreaStrategy.cs into bin\Custom\Strategies (keep the 120 s change), F5, re-teach the
*** review and pre-flight from _to_delete\*.with_fomc_20261020.
*** (earlier) STATUS 2026-10-20 EVENING: PARKED. Bulenox forbids overnight holds and Bulenox is where the strategy is headed, so Sim101 keeps
*** trading exactly the Bulenox version: the box stays UNTICKED (step C below is NOT done) and daily_review.py has FOMC_EVE_LONG = False
*** (backup _to_delete\daily_review.py.bak_20261020_fomc_off; today's review output byte-identical). The code stays in the live file,
*** off by default and parity-proven, at no cost. Use it only on an account that allows overnight holds: tick the box there, set
*** FOMC_EVE_LONG = True, then steps D-E. Worth about +$1,040 a year per micro there (research\projections_output.txt, row E).

What it does: on the evening before each scheduled FOMC announcement (bin\Custom\Strategies\fomc_dates.txt) NoiseAreaStrategy
holds 1 x Contracts long from the 16:00 bar close to the 09:30 bar close of the announcement day (fill at the 09:30 open). A NoiseArea
long already open at 16:00 is kept into the hold. While the switch is on, exit-on-session-close is off (NinjaTrader would flatten the
hold at 16:59:30); early closes stay handled by the Trading Hours template and EarlyCloseDates. Next FOMC eves: 2026-10-27
(announcement 10-28), 2026-12-08 (12-09).

STATE ON 2026-10-20 (done by Claude, NinjaTrader closed and flat)
 - bin\Custom\Strategies\NoiseAreaStrategy.cs replaced by this folder's NoiseAreaStrategy.cs (SHA-256 1BFAB437...; switch OFF by
   default, so it trades exactly like the old file until the box is ticked). Whole-project offline compile: 0 errors (canary fails).
   Rollback: NoiseAreaStrategy.cs.before_20261020 here (= the old live file, SHA-256 3B96F18B...).
 - daily_review.py taught the hold (FOMC_EVE_LONG = True at the top): on the eve a long 1 with "fomc_hold": true after 16:00 is
   reported, not flagged; NinjaTrader closed during the hold -> NEEDS ATTENTION "enable before 09:30"; flat after 16:00 on an eve ->
   warning; the add-on's P&L is kept out of NoiseArea's 20-session band, edge and fill-cost alarms and reported on its own line.
   Six scenario tests passed; output on the real files of 10-16 / 10-19 / 10-20 byte-identical to the old script.
 - Morning pre-flight routine (file + scheduled task, byte-identical): on an announcement day "Pre-flight OK (FOMC hold)" for the
   expected long; "CHECK NINJATRADER BEFORE 9:30" if the hold is open on the account but the strategy is not running.
 - fomc_parity.py compares an Analyzer run with the references (dry run on the 2026-10-19 switch-off run: PARITY PASSED, 1,662 of
   1,666 within tolerance; the 4 others are high-volatility 16:00 flats, exit stamped 16:05 in the Analyzer).
 - 2026-10-20 17:42 ET: Sashkin's F5 done and both Analyzer runs done (steps A1 and B). PARITY PASSED on both, see parity_20261020.txt:
   off 1,666 trades, 1,662 match (99.8%); on 1,717 trades, 1,713 match (99.8%) and all 58 FOMC-eve rows match (entry, exit, points).
   The same 4 high-volatility 16:00 flats differ in both runs. Analyzer 01/01/2019 -> 10/19/2026 with commission: net $24,388 off
   vs $29,815 on, max drawdown $4,070 vs $3,466, profit factor 1.28 vs 1.34. Next: step C (tick the box when enabling the strategy).
 - Bulenox (official help center + FAQ, read 2026-10-20): "All positions must be closed by 3:59 PM CT. Holding positions overnight
   is not permitted" (Qualification and Master accounts); news trading incl. FOMC is allowed intraday. So the add-on is SIM101 ONLY.

SASHKIN'S CLICKS (everything else is done)
 A. Next NinjaTrader start, BEFORE enabling the strategy (e.g. Wed 10-21 before 09:35):
    1. New > NinjaScript Editor > press F5. Wait for the compile to finish with no errors at the bottom of the editor. Close the editor.
    2. Enable NoiseAreaStrategy on the 5-minute MNQ chart as usual. Leave the new box "FOMC-eve overnight long (add-on)" UNTICKED -
       nothing changes yet.
 B. Any evening 10-21 .. 10-26 after 16:15 ET (strategy flat) - parity, about 10 minutes:
    3. New > Strategy Analyzer: NoiseAreaStrategy, MNQ, 5 Minute, 01/01/2019 -> 10/14/2026, slippage 1, commission off, add-on box
       UNTICKED > Run. Tell Claude "off done" (the strategy writes the trades file itself; Claude runs fomc_parity.py off - expect 1,666).
    4. The same with the add-on box TICKED > Run. Tell Claude "on done" (fomc_parity.py on - expect 1,717 = 1,666 - 7 + 58).
 C. When both pass, the same evening (flat):
    5. Strategies tab: untick (disable) NoiseAreaStrategy, open its properties, tick "FOMC-eve overnight long (add-on)", leave the dates
       file blank, OK, tick (enable) it again. Leave "Exit on session close" as it is - the add-on turns it off itself.
 D. Tue 10-27, the FOMC eve:
    6. After the close leave NinjaTrader RUNNING overnight (do not exit it as on other evenings) and keep the PC awake. At 16:00 the
       strategy buys 1 MNQ on Sim101 and holds; the evening review should say "FOMC eve: holding 1 micro long overnight as designed".
    7. If NinjaTrader gets closed anyway: start it and enable the strategy before 09:30 ET Wed 10-28 (enabling later sells at once).
 E. Wed 10-28: nothing to click. 09:17 pre-flight should read "Pre-flight OK (FOMC hold)"; the strategy sells at the 09:30 bar; the
    evening review reports the hold on its own line.
 Keep the Replikanto Sim101 follower OFF (as now) so the hold is not copied to Bulenox; never tick the add-on on a Bulenox account -
 Bulenox prohibits overnight holds (flat by 3:59 PM CT = 4:59 PM ET). To switch the add-on off: untick the box (and set FOMC_EVE_LONG = False in
 daily_review.py - Claude does that part). Rollback of the code: copy NoiseAreaStrategy.cs.before_20261020 over the live file, F5.
