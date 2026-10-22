# NoiseArea

Automated MNQ (Micro Nasdaq-100 futures) intraday momentum strategy for NinjaTrader 8: the published noise-area method
(Zarattini, Aziz & Barbon 2024) on 5-minute bars, decisions every 30 minutes 10:00–15:30 ET, always flat by 16:00 ET,
fixed published parameters. Plus everything around it: the daily monitoring, the account registry, the research trail
behind every decision, the tape recorder, and the workspace.

- **Strategy description, rules, parameters, gates, decision log, next steps:** [`nt8/agent_exports/review/NoiseArea_Strategy.md`](nt8/agent_exports/review/NoiseArea_Strategy.md)
- **Working brief (status, hard rules, where everything is):** [`nt8/CLAUDE.md`](nt8/CLAUDE.md)
- **Research index:** [`nt8/agent_exports/review/research/README.txt`](nt8/agent_exports/review/research/README.txt)

## Layout

`nt8/` mirrors the NinjaTrader 8 user folder (`Documents\NinjaTrader 8`), so installing is a copy.

| Path | What |
|---|---|
| `nt8/bin/Custom/Strategies/NoiseAreaStrategy.cs` | the strategy (orders, daily loss stop, desync guard, heartbeat, trade log) |
| `nt8/bin/Custom/Indicators/NoiseAreaMomentum.cs` | the rules (noise area, VWAP, decisions, exports) — the strategy hosts it |
| `nt8/bin/Custom/Indicators/TapeRecorder.cs` | listen-only recorder: trade prints, best bid/ask, every order event and fill |
| `nt8/bin/Custom/Indicators/*.cs` | the other custom indicators the workspace uses (AccurateVWAP, OrderPnLLabels, …) |
| `nt8/templates/` | ATM ("3 contracts" …), commission, trading-hours, skin and other templates |
| `nt8/workspaces/MNQ Pro Cockpit.xml` | the workspace (MNQ 12-26 charts, 5-minute strategy chart loading 500 days) |
| `nt8/agent_exports/review/` | `daily_review.py`, `review_all.py`, `monitor.py`, `robustness.py`, `accounts.json`, `routines/` (scheduled-check prompts) |
| `nt8/agent_exports/review/research/` | every pre-registered `*_PLAN.txt` / `*_RESULTS.txt`, the code, and the core 5-minute data (NQ 2010–2019, MNQ 2019–2026) |
| `tools/install.ps1` | copies `nt8/` into a NinjaTrader 8 folder (dry run by default, backs up anything it overwrites) |
| `tools/sync_from_live.py` | refreshes `nt8/` from the live NinjaTrader folder before a commit |
| `tools/nt_build_check.ps1` | compiles the whole NinjaTrader.Custom project offline exactly like F5 (`-Canary` must fail) |

## New machine

1. **NinjaTrader 8** (8.1.8.x): install, log in, start it once, close it.
2. **This repo:** `git clone https://github.com/Linderwow/NoiseArea.git`
3. **Install:** with NinjaTrader closed, from the repo folder:
   `powershell -ExecutionPolicy Bypass -File tools\install.ps1` (dry run), then the same with `-Apply`.
   It backs up anything it overwrites to `Documents\NinjaTrader 8\_to_delete\install_backup_<time>\` and rewrites the
   NinjaTrader path in the scheduled-check prompts and `CLAUDE.md`.
4. **Compile:** start NinjaTrader → New → NinjaScript Editor → **F5**. It must compile without errors.
5. **Connection:** Control Center → Connections → add the Bulenox / Rithmic connection (the workspace calls it `Bullenox`),
   connect. Not carried over on purpose: `Config.xml` holds your NinjaTrader login and password.
6. **Workspace:** Control Center → Workspaces → Open → `MNQ Pro Cockpit`.
7. **Strategy:** on the MNQ 5-minute chart (data series loads 500 days, trading hours = instrument settings), add
   `NoiseAreaStrategy`. Settings: **Account** Sim101 (paper) or the Bulenox account · **Contracts** 1 on Sim101 (6 on a
   Bulenox 50K evaluation, see §9 "big lever") · **Daily loss stop** 0 on Sim101 (1000 on the 50K evaluation, under
   Bulenox's $1,100 limit) · Calculate **On bar close** · Start behavior **Immediately submit, synchronize account** ·
   Exit on session close stays on (the code enforces ≥ 120 s). One instance per account and instrument. Enable before 09:35 ET.
   NinjaTrader keeps the strategy's settings in its own database, so a workspace alone does not bring it back.
8. **Rules that keep it safe:** no manual trades on the strategy's account (the desync guard stops the strategy); in
   Replikanto, Sim101 must not be a follower; hide Chart Trader on the strategy chart (a hotkey there placed a stray order
   on 2026-10-22); register every account in `agent_exports/review/accounts.json`.
9. **Tape recorder:** a NEW chart (MNQ 12-26, 1 Minute, Days to load 1) → add `TapeRecorder` (defaults; Output folder
   `D:\nt_recorder` — change it if the machine has no D:). Never on the strategy's chart; one instance. Then **save the
   workspace** (Ctrl+S) — otherwise the next NinjaTrader start comes back without the recorder and the watchdog alerts.
10. **Replikanto** (trade copier to Bulenox accounts): licensed per machine — reinstall from flowbots.ninja, not from here.
11. **Python 3.12:** `pip install -r requirements.txt`. Check: `python "%USERPROFILE%\…\NinjaTrader 8\agent_exports\review\review_all.py" "%USERPROFILE%\…\NinjaTrader 8\agent_exports"`.
12. **Scheduled checks** (Claude desktop app → Scheduled → new local task; prompt = the file's text, after step 3 rewrote paths):

| Task | Cron (local time, Eastern) | Prompt |
|---|---|---|
| noisearea-morning-preflight | `12 9 * * 1-5` | `agent_exports/review/routines/morning-preflight.md` |
| noisearea-restart-test | `40 14 * * 1-5` | `agent_exports/review/routines/restart-test.md` (delete once PASSED) |
| noisearea-daily-review | `24 16 * * 1-5` | `agent_exports/review/routines/daily-review.md` |
| noisearea-annual-revalidation | `10 9 5 1 *` | `agent_exports/review/routines/annual-revalidation.md` |

The PC must use Eastern time or the cron times shift; the scripts themselves convert NinjaTrader's time zone to ET.

13. **Windows tasks** (the always-on half; the Claude routines above only run while the Claude app is open). Definitions
    exported from the live PC are in `tools/tasks/`:

| Task | Runs | Script |
|---|---|---|
| NoiseArea watchdog | every 2 min, limit 3 min | `agent_exports/review/watchdog.py` — alerts to the phone |
| NoiseArea nightly archive | daily 18:20 local time | `agent_exports/review/archive_nightly.py` — backups, mirror, evening phone summary |
| NoiseArea autopush | hourly at :07 | `tools/autopush.py` — verified commit + push of this package |

    Each runs `pythonw.exe` (no window). On a new PC, edit the `<Command>` / `<Arguments>` paths and the `<UserId>` in each
    XML, then: `Register-ScheduledTask -TaskName 'NoiseArea watchdog' -Xml (Get-Content -Raw tools\tasks\NoiseArea_watchdog.xml)`
    (and the same for the other two). They run only while the user is logged on (Interactive); to have the watchdog alert
    after a reboot that stops at the login screen, set it to "Run whether user is logged on or not" yourself.
14. **Folders and phone alerts:** the scripts create `D:\nt_logs`, `D:\nt_db`, `D:\nt_ticks`, `D:\nt_recorder` and the
    mirrors `C:\NoiseAreaData_mirror`, `D:\NoiseAreaData_mirror` themselves (paths are constants at the top of
    `archive_nightly.py`, `watchdog.py`, `research/ncd_export.py` — change them on a machine without D:). Phone alerts need
    `%LOCALAPPDATA%\NoiseArea\watchdog_config.json` (a copy may also sit in `D:\nt_logs\`):
    `{"ntfy_server": "https://ntfy-me.com", "ntfy_topic": "<long random topic>", "healthchecks_url": "<optional ping URL>"}` —
    never committed (autopush blocks it). Test: `python agent_exports\review\watchdog.py --test-push`.
15. **Autopush login:** run one manual `git push` from the repo so Git Credential Manager stores the GitHub login; the task
    pushes with prompts disabled. Checks before every push: secrets (patterns and this PC's literal secret values), OneDrive
    conflict copies, Python/JSON, append-only ledgers, the NinjaScript build + canary, the review and its regression tests
    (`watchdog_test.py`, `archive_nightly_test.py`, `daily_review_fills_test.py`).

## Not in the package (and why)

- `Config.xml` / `UI.xml`, `db\` (accounts, orders, executions, NinjaTrader's local market data), logs — machine- and
  login-specific; `Config.xml` contains your password. NinjaTrader re-downloads chart history.
- Vendor DLLs (Replikanto, NinjaTrader.Vendor) — licensed, reinstall from the vendor.
- Raw Databento files (`D:\databento_archive`, `Desktop\Desktop\databento_data`), the research caches
  (`research\regime_work`, 436 MB, rebuilt by `src2_lib.py` from the raw files), the decoded NinjaTrader tick history
  (`D:\nt_ticks`, several GB, made by `research\ncd_export.py` from NinjaTrader's local tick store — a new machine's
  NinjaTrader does not have that store, so back `D:\nt_ticks` up separately) and the recorder output (`D:\nt_recorder`).
- Run outputs in `agent_exports\` (trade logs, heartbeats, events, bars) — written by the strategy while it runs.

## Updating the package from the live machine

```
python tools\sync_from_live.py
git add -A
git commit -m "Sync from live"
git push
```

`sync_from_live.py` only adds and updates; files removed from the live folder are listed for a manual `git rm`.
Before pushing NinjaScript changes, `powershell -ExecutionPolicy Bypass -File tools\nt_build_check.ps1` must say
`build OK` (and fail with `-Canary`).
