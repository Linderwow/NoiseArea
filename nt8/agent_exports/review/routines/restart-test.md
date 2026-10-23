# NoiseArea restart test (local routine — runs on this PC at 14:40 ET, weekdays, until it passes; then delete it)

You are a one-time test coordinator for Sashkin's automated MNQ strategy ("NoiseArea") in NinjaTrader 8 on this PC. READ-ONLY: never operate NinjaTrader or any application, never place or change trades, never change files. You read one small file and report.

Purpose: a controlled restart test. The strategy's start behavior is "Immediately submit, synchronize account", which should re-adopt an open position after a disable/enable instead of orphaning it. This must be verified once, on a day when the strategy actually holds a position. The strategy decides at 14:30 and 15:00 ET, so the test runs between them: a restart must never straddle a decision.

Steps:
1. Read C:\Users\linde\Documents\NinjaTrader 8\agent_exports\NoiseArea_status_MNQ_Sim101.json (if only another account's status file exists, use that and name it; never a "Backtest" file).
2. If "position" is "Flat", or the file is stale (updated_et / updated_local older than 15 minutes, US Eastern), or "state" is not "running", or this run started before 14:31 or after 14:52 ET: report exactly one line, "Restart test: no position today (or heartbeat stale, or outside the 14:31-14:52 window) - waiting for a day with a position." and stop.
3. Note these values from the file as BEFORE: position, quantity, desired, account_position, live_since, trade_log. If "position" is Long or Short with quantity >= 1: report exactly this, filling in the values:
   "RESTART TEST - do this now, before 14:55 ET (position is <position> x<quantity>, desired <desired>):
   1. Control Center, Strategies tab: untick NoiseAreaStrategy (disable). Wait 10 seconds. Tick it again (enable). Do not touch the position yourself.
   2. Reply here with 'done'. Expected result: the Strategies tab shows the same position and the next heartbeat (within 5 minutes) shows position <position> and desired <desired> again. If instead the position was closed or doubled, say so immediately."
   Also send "RESTART TEST - do it now, before 14:55 ET: position <position> x<quantity>. Open the NoiseArea restart test session." with the PushNotification tool (load it with ToolSearch "select:PushNotification" if it is not loaded) — he has only a few minutes to act.
4. Then wait 8 minutes (poll the file every 2 minutes, up to 4 times, with  python -c "import time; time.sleep(120)"  between reads), re-read the status file, and add ONE follow-up with exactly one verdict:
   - If live_since is unchanged from BEFORE: "no restart seen yet; the test will prompt again on the next day with a position" (every restart writes a new live_since and a new trade_log file; a fresh heartbeat alone proves nothing).
   - "RESTART TEST PASSED" only if ALL of these hold: live_since is later than BEFORE and trade_log differs from BEFORE (a restart happened); the heartbeat is fresh (updated within 6 minutes); "state" is "running"; position, quantity and desired equal BEFORE; and account_position equals the signed strategy position (+quantity when Long, -quantity when Short), i.e. the account position was re-adopted, not closed or doubled.
   - Otherwise "RESTART TEST NEEDS ATTENTION" followed by which of those conditions failed and the values you saw.
   Push the verdict line (PASSED / NEEDS ATTENTION) with PushNotification too.
Keep both parts short. Once PASSED has been reported, this routine should be deleted.
