# NoiseArea scheduled checks - how they are set up (current state, 2026-10-17)

All four checks run as **local Claude Code routines** on this PC. The move from the cloud tasks is complete: the cloud
"NoiseArea restart test prompt" is paused and the other three cloud tasks were deleted.

| Task id | Prompt file | Cron (local = ET) | Fires at (with the app's fixed delay) |
|---|---|---|---|
| noisearea-morning-preflight | morning-preflight.md | `12 9 * * 1-5` | weekdays 09:17 |
| noisearea-restart-test | restart-test.md | `40 14 * * 1-5` | weekdays 14:43 (moved from 14:20 on 2026-10-20 so the test sits between the 14:30 and 15:00 decisions) |
| noisearea-daily-review | daily-review.md | `24 16 * * 1-5` | weekdays 16:38 |
| noisearea-annual-revalidation | annual-revalidation.md | `10 9 5 1 *` | Jan 5, 09:13 |

## Rules for keeping them right

- **The .md file is the source; the task is a byte-identical copy.** Change both, then compare (strip the task's
  SKILL.md frontmatter at `C:\Users\linde\.claude\scheduled-tasks\<id>\SKILL.md`).
- **Permission mode must be Auto** on every task: Scheduled -> task -> Edit -> Permission mode. Only Sashkin can set it.
  Without it a run stops at its first command and waits for a click that never comes.
- Runs start in `D:\`, so every path in the prompts is absolute.
- Alerts use the PushNotification tool: phone only while Remote Control is connected, and the tool skips the push ("Not
  sent - this terminal is active") whenever the app counts Sashkin as active, even in another session (0 of 2 alerts
  delivered as of 2026-10-20), so the routine's report in its session is the reliable record. A local routine
  does not fire while the app is closed or the PC sleeps; it catches up on the next launch, and each prompt says how to
  label a late run.
- Delete `noisearea-restart-test` once it has reported RESTART TEST PASSED.

## Scripts the routines call

- `..\daily_review.py` - exit 0 / 1 / 2. Before 16:00 or on a weekend the routine reviews the previous weekday with
  `--date`; the script then skips the heartbeat checks.
- `..\robustness.py` - runs on any number of trades (the Monte Carlo window shrinks below 201).
- `..\annual_window.py` - the annual re-validation's like-for-like window comparison (a partial live year against the same
  calendar window of every reference year, in % of notional).
- Offline compile check before any F5: `powershell -NoProfile -ExecutionPolicy Bypass -File
  D:\dev\trading\MNQPropBook\tools\nt_build_check.ps1` (add `-Canary` to prove it can fail).
