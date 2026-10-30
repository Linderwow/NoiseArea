//
// NoiseAreaStrategy
// ---------------------------------------------------------------------------------------------
// Trades NoiseAreaMomentum.DesiredPos with market orders (signal at a bar close -> fill at the next bar).
// All rules live in the indicator, so chart, alerts, Strategy Analyzer and Sim101 trade the same thing.
//
// Writes every completed trade to
//   Documents\NinjaTrader 8\agent_exports\NoiseArea_trades_<instrument>_<account>_<run start>.csv
// (Strategy Analyzer runs use account "Backtest"; live/paper runs use the account name, e.g. Sim101.)
// The indicator instance inside the strategy also writes its checks/days files tagged "strategy_<account>".
//
// Safety:
//   - Start behavior "Immediately submit, synchronize account": a restart re-adopts the position the rules say
//     it should hold and flattens anything else, so a position is never orphaned. Trade manually on another account.
//   - Start checks (2026-10-22 audit; live accounts only): the strategy refuses to start - during its historical replay, before
//     the start-up synchronisation can send any order - when Contracts differs from the size registered for the account in
//     agent_exports\review\accounts.json (a typo would otherwise resize the account at once), or when another NoiseAreaStrategy
//     is already active on the same account on a different contract (both contracts enabled around a roll: both would trade
//     and overwrite each other's files). Heartbeat state "refused" with the reason; an event is logged.
//   - Daily loss stop (0 = off): when the session's realized + open P&L reaches -MaxDailyLoss the position is closed and
//     no new entries are taken until the next session. Set it below your Bulenox daily loss limit. Live only: it is
//     not modelled in Strategy Analyzer (the validated reference has no stop). Checked on every trade print in real time
//     (2026-10-22: on 5-minute closes alone it could never act before Bulenox's limit at 6 micros) and at each bar close;
//     the open P&L uses the account's real fill price, not the replay's virtual entry. The session's realized P&L and the
//     halted flag are persisted to a small file (after every bar, every position change and at shutdown), so a restart within
//     the same session carries them over; a halt saved for the current session is also applied to that session's historical
//     replay, so the start-up synchronisation cannot re-enter the position the halt closed. A saved halt is restored only
//     while the stop is on. Keep Calculate = OnBarClose (the hosted indicator inherits it and must see completed bars).
//   - Exit on session close is forced on as the backup for early-close days (needs the Trading Hours template's holidays).
//     2026-10-20: it fires at least 120 s before the session end (17:00 ET -> 16:58 ET = 15:58 CT), inside Bulenox's "flat by
//     3:59 PM CT"; 30 s fired at 15:59:30 CT. Still inside the last 5-minute bar, so Strategy Analyzer results are unchanged.
//   - Account/strategy desync guard: if the account's position in this instrument (direction AND size) differs from the
//     strategy's, no order is sent, an alert fires and the heartbeat reports it. If it is still different on the next
//     5-minute bar (so not a fill in progress), the strategy flattens the ACCOUNT's real position in this instrument and stops
//     itself (2026-10-22 audit: stopping without flattening left the position with no exit at all, held overnight on an
//     unattended PC): heartbeat state "desync", the note names both positions. A human reconciles and re-enables. A disconnect
//     neither clears nor advances the guard. A restart with fewer than LookbackDays sessions of history alerts too (with too
//     little history the rules see no sigma and a restart would flatten a correct position): load >= 30 days.
//   - Events (live start, start refused, desync, desync stop, halt, halt restored, history alert, termination) are appended to
//     agent_exports\NoiseArea_events_<instrument>_<account>.txt, because NinjaTrader does not save its Output window. The
//     heartbeat, day state and events are written whatever the trade-export switch says.
//   - Bulenox rules (2026-10-30, Master Account Agreement pasted by the user; research\BULENOX_AGREEMENT_20261030.txt):
//     * Same direction across accounts (s.14): every equity-index position across the user's Bulenox accounts must point one way.
//       On a live (non-simulation) account an entry - or the new side of a reversal - is held back while another account on the same
//       connection holds an ES/NQ/YM/RTY/EMD/MES/MNQ/MYM/M2K position the other way (exits always go through); it is retried on every
//       trade print (at most twice a second) and sent once the other side is flat. Event "direction_block"/"direction_clear", alert,
//       heartbeat "direction_block". It cannot stop a MANUAL order against an open NoiseArea position - that stays the trader's.
//     * Inactivity (Exhibit A): a trade at least every 5 trading days. "Activity after N dry sessions" (registered per account in
//       accounts.json "activity_after", start refused on a mismatch) switches on the indicator's activity fallback, and the account's
//       own last fill (agent_exports\NoiseArea_activity_<inst>_<acct>.txt, written on every real-time fill) forces it for the session
//       when the ACCOUNT has gone N sessions without a fill (missed signal, PC down, daily stop, direction block).
//   2026-10-20: size-aware desync, stop on persistent desync, halt persistence fixes and the event log
//   (research\GUARDS_PLAN.txt / GUARDS_RESULTS.txt). 2026-10-22: start checks, flatten on desync stop, intrabar loss stop,
//   atomic day state (independent code audit). 2026-10-30: direction guard + activity fallback (off on Sim101).
// ---------------------------------------------------------------------------------------------
#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Media;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
	public class NoiseAreaStrategy : Strategy
	{
		private NoiseAreaMomentum na;
		private int tradesWritten, tradesAtLive;
		private string tradesPath, statusPath;
		private double dayStartRealized, dayCarry;          // dayCarry = this session's realized P&L before the last (re)start
		private double dayRealizedNow;                       // this session's realized P&L at the last bar close / fill (for the tick stop)
		private bool halted, etWarned, desync;
		private string haltNote = "", dayStatePath, liveSince = "";
		private TimeZoneInfo etZone;
		private int lastDesired; private DateTime lastBarEt = DateTime.MinValue; private string sessionKey = "";
		private int desyncBars;                              // consecutive real-time bars on which the account differed
		private bool desyncStopped;                          // the strategy stopped itself on a persistent desync
		private string restoredHaltSession;                  // session whose saved halt also applies to its historical replay
		private string eventsPath;
		private string blockNote;                            // why this start is refused (start checks), or null
		private bool refused;
		private string activityPath;                         // last session (yyyy-MM-dd) with a real fill on this account
		private DateTime lastActive = DateTime.MinValue;
		private string forcedKey = "";                       // session latched into the activity band by the account's dry count
		private int accountDry = -1;
		private bool dirBlocked;                             // an entry is held back by the same-direction rule
		private string dirNote = "";
		private DateTime dirChecked = DateTime.MinValue;
		private static readonly string[] EquityIndex = { "ES", "NQ", "YM", "RTY", "EMD", "MES", "MNQ", "MYM", "M2K" };
		private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name								= "NoiseAreaStrategy";
				Description							= "Intraday momentum noise-area strategy (Zarattini/Aziz/Barbon 2024, base case) with CSV trade log.";
				Calculate							= Calculate.OnBarClose;
				EntriesPerDirection					= 1;
				EntryHandling						= EntryHandling.AllEntries;
				IsExitOnSessionCloseStrategy		= true;
				ExitOnSessionCloseSeconds			= 120;
				IsFillLimitOnTouch					= false;
				MaximumBarsLookBack					= MaximumBarsLookBack.Infinite;
				OrderFillResolution					= OrderFillResolution.Standard;
				Slippage							= 1;
				StartBehavior						= StartBehavior.ImmediatelySubmitSynchronizeAccount;
				ConnectionLossHandling				= ConnectionLossHandling.Recalculate;
				DisconnectDelaySeconds				= 10;
				TimeInForce							= TimeInForce.Day;
				TraceOrders							= false;
				RealtimeErrorHandling				= RealtimeErrorHandling.StopCancelClose;
				StopTargetHandling					= StopTargetHandling.PerEntryExecution;
				BarsRequiredToTrade					= 20;
				IsInstantiatedOnEachOptimizationIteration = true;

				Contracts		= 1;
				LookbackDays	= 14;
				VolMultiplier	= 1.0;
				MaxDailyLoss	= 0;
				EarlyCloseDates	= "";
				ExportTrades	= true;
				ActivityAfterDays	= 0;
				ActivityMultiplier	= 0.0;
				DirectionGuard		= true;
			}
			else if (State == State.Configure)
			{
				// Bulenox: flat by 3:59 PM CT. A saved instance keeps its old 30 s, so the minimum is enforced here, and the
				// session-close exit itself cannot be switched off (2026-10-22 audit: unticking it removed the 16:58 backup).
				IsExitOnSessionCloseStrategy = true;
				if (ExitOnSessionCloseSeconds < 120) ExitOnSessionCloseSeconds = 120;
			}
			else if (State == State.DataLoaded)
			{
				string acct = Account != null ? Account.Name : "account";
				try   { etZone = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time"); }
				catch { etZone = null; }
				na = NoiseAreaMomentum();
				na.LookbackDays  = LookbackDays;
				na.VolMultiplier = VolMultiplier;
				na.SoundAlerts   = false;
				na.ShowPanel     = false;
				na.FileTag       = "strategy_" + acct;
				na.ActivityAfterDays  = ActivityAfterDays;
				na.ActivityMultiplier = ActivityMultiplier;
				if (!string.IsNullOrWhiteSpace(EarlyCloseDates)) na.EarlyCloseDates = EarlyCloseDates;
				AddChartIndicator(na);

				// Heartbeat, day state and events do not depend on the export switch (2026-10-22 audit: they are safety state).
				try
				{
					string dir = Path.Combine(Core.Globals.UserDataDir, "agent_exports");
					Directory.CreateDirectory(dir);
					string inst = Instrument != null ? Instrument.MasterInstrument.Name : "inst";
					statusPath = Path.Combine(dir, "NoiseArea_status_" + inst + "_" + acct + ".json");
					dayStatePath = Path.Combine(dir, "NoiseArea_daystate_" + inst + "_" + acct + ".txt");
					eventsPath = Path.Combine(dir, "NoiseArea_events_" + inst + "_" + acct + ".txt");
					activityPath = Path.Combine(dir, "NoiseArea_activity_" + inst + "_" + acct + ".txt");
					if (ExportTrades)
					{
						tradesPath = Path.Combine(dir, "NoiseArea_trades_" + inst + "_" + acct + "_" + DateTime.Now.ToString("yyyyMMdd_HHmmss", CI) + ".csv");
						File.WriteAllText(tradesPath, "trade,entry_time_et,exit_time_et,direction,qty,entry_price,exit_price,profit_points,profit_currency,commission,mae_currency,mfe_currency,entry_name,exit_name" + Environment.NewLine);
					}
				}
				catch (Exception ex) { Print("NoiseAreaStrategy: cannot create export files: " + ex.Message); tradesPath = null; }
				// A halt saved for the CURRENT session (stop on) is applied to that session's historical replay too, so the
				// start-up account synchronisation cannot re-enter the position the halt closed. Never true in Strategy Analyzer
				// (the day-state file is written in real time only).
				restoredHaltSession = ReadSavedHaltSession();

				// Start checks (live accounts only; Strategy Analyzer runs on "Backtest"). Acted on at the first historical bar.
				if (Account != null && acct != "Backtest")
				{
					int reg = RegisteredInt(acct, "contracts");
					int regAct = Math.Max(0, RegisteredInt(acct, "activity_after"));
					string other = OtherInstance();
					if (reg > 0 && reg != Contracts)
						blockNote = string.Format(CI, "Contracts is {0} but {1} is registered for {2} (agent_exports\\review\\accounts.json) - fix the Contracts setting (or the registration) and enable again", Contracts, acct, reg);
					else if (reg > 0 && regAct != ActivityAfterDays)
						blockNote = string.Format(CI, "'Activity after N dry sessions' is {0} but {1} is registered with activity_after {2} (agent_exports\\review\\accounts.json; Bulenox accounts 3, others 0) - fix the setting and enable again", ActivityAfterDays, acct, regAct);
					else if (other != null)
						blockNote = string.Format(CI, "another NoiseAreaStrategy is already active on {0} ({1}) - disable it first (one instance per account), then enable this one", acct, other);
				}
			}
			else if (State == State.Transition)
			{
				// Going live: the historical replay's virtual trades are not logged, and the daily loss stop starts from zero.
				if (SystemPerformance != null) tradesWritten = tradesAtLive = SystemPerformance.AllTrades.Count;
				dayStartRealized = SystemPerformance != null ? SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit : 0;
				halted = false; haltNote = ""; dayCarry = 0; dayRealizedNow = 0;
				liveSince = ToEt(DateTime.Now).ToString("yyyy-MM-dd HH:mm:ss", CI);
				if (na != null) na.FlushExports();
				RestoreDayState();
				dayRealizedNow = dayCarry;
				RestoreActivity();
				LogEvent("live", string.Format(CI, "contracts {0}, daily loss stop {1:0}, history sessions {2}, exit on session close {3} ({4} s), start {5}, on error {6}, activity after {7} (x{8:0.##}, account last fill session {9:yyyy-MM-dd}), direction guard {10}",
					Contracts, MaxDailyLoss, na != null ? na.SessionsOfHistory : 0, IsExitOnSessionCloseStrategy ? "on" : "OFF", ExitOnSessionCloseSeconds, StartBehavior, RealtimeErrorHandling,
					ActivityAfterDays, ActivityMultiplier, lastActive, !DirectionGuard ? "OFF" : IsSimAccount(Account) ? "on (idle: simulation account)" : "on"));
				if (na != null && na.SessionsOfHistory < LookbackDays)
				{
					string msg = string.Format("NoiseAreaStrategy: only {0} complete sessions loaded, {1} needed - no decisions until enough history exists. Increase 'Days to load' (>= 30) and restart.", na.SessionsOfHistory, LookbackDays);
					Print(msg);
					LogEvent("history_short", msg);
					try { Alert("NAHist", Priority.High, msg, Path.Combine(Core.Globals.InstallDir, "sounds", "Alert2.wav"), 60, Brushes.Black, Brushes.OrangeRed); } catch { }
				}
				Print("NoiseAreaStrategy: live from here; historical replay trades are not written to the trade log");
			}
			else if (State == State.Terminated)
			{
				WriteNewTrades();
				if (liveSince != "" && sessionKey != "") SaveDayState(DayRealized());
				WriteStatus(refused ? "refused" : desyncStopped ? "desync" : "terminated", refused);
				if (liveSince != "") LogEvent("terminated", string.Format(CI, "strategy {0} x{1}, account {2}{3}", Position.MarketPosition, Position.Quantity, AccountQty(), desyncStopped ? " (stopped on desync)" : ""));
				if (tradesPath != null) Print("NoiseAreaStrategy: trade log at " + tradesPath);
			}
		}

		protected override void OnBarUpdate()
		{
			if (BarsInProgress != 0 || na == null) return;
			if (blockNote != null)
			{
				// Refuse to start while still in the historical replay: no virtual position exists yet, so terminating here sends no
				// order at all (the start-up synchronisation only happens at the switch to real time).
				if (!refused)
				{
					refused = true;
					haltNote = "not started: " + blockNote;
					Print("NoiseAreaStrategy: NOT STARTED - " + blockNote);
					try { Log("NoiseAreaStrategy NOT STARTED: " + blockNote, LogLevel.Error); } catch { }
					LogEvent("start_refused", blockNote, true);
					WriteStatus("refused", true);
					SetState(State.Terminated);
				}
				return;
			}
			double dp = na.DesiredPos[0];
			int want = double.IsNaN(dp) ? 0 : (int)Math.Round(dp);
			lastDesired = want; lastBarEt = ToEt(Time[0]);
			sessionKey = SessionKey(lastBarEt);
			if (CurrentBar < BarsRequiredToTrade) return;

			int cur = Position.MarketPosition == MarketPosition.Long ? 1 : Position.MarketPosition == MarketPosition.Short ? -1 : 0;

			// Daily loss stop: per session (18:00 -> 17:00 ET on the default CME template), realized + open P&L.
			// Armed in real time only (also checked on every trade print, OnMarketData). The session's realized P&L and the halted
			// flag are persisted so a restart within the same session carries them over instead of starting from zero.
			if (Bars.IsFirstBarOfSession)
			{
				dayStartRealized = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
				halted = false; haltNote = ""; dayCarry = 0;
			}
			if (restoredHaltSession != null && sessionKey == restoredHaltSession && !halted)
			{
				halted = true;
				haltNote = "daily loss stop was hit earlier this session (restored after restart)";
			}
			double dayRealized = dayCarry + SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit - dayStartRealized;
			dayRealizedNow = dayRealized;
			if (MaxDailyLoss > 0 && !halted && State == State.Realtime)
			{
				double dayPnl = dayRealized + (cur != 0 ? OpenPnl(Close[0]) : 0.0);
				if (dayPnl <= -MaxDailyLoss)
				{
					halted = true;
					haltNote = string.Format(CI, "daily loss stop hit: {0:0} <= -{1:0} at {2:HH:mm} ET", dayPnl, MaxDailyLoss, lastBarEt);
					Print("NoiseAreaStrategy: " + haltNote);
					LogEvent("halt", haltNote);
					if (State == State.Realtime)
						try { Alert("NAHalt" + CurrentBar, Priority.High, "NoiseAreaStrategy halted for the day: " + haltNote, Path.Combine(Core.Globals.InstallDir, "sounds", "Alert2.wav"), 30, Brushes.Black, Brushes.OrangeRed); } catch { }
				}
			}
			if (halted) want = 0;

			// Desync guard (live only): the account's position in this instrument (direction and size) must match the strategy's
			// bookkeeping. If it does not (manual trade, broker flatten, copied trade, another strategy), send nothing and alert.
			// If it still differs on the next bar, it was not a fill in progress: flatten the ACCOUNT's real position in this
			// instrument and stop the strategy, so neither a position with no exit nor NinjaTrader's session-close exit acting on
			// the stale bookkeeping can remain; a human reconciles and re-enables. While disconnected the guard is held as it was.
			bool connected = Account != null && Account.Connection != null && Account.Connection.Status == ConnectionStatus.Connected;
			if (State == State.Realtime && connected)
			{
				int acctQty = AccountQty();
				int expected = cur * Position.Quantity;
				bool nowDesync = acctQty != expected;
				if (nowDesync && !desync)
				{
					string msg = string.Format("NoiseAreaStrategy: account position {0} differs from the strategy position {1} ({2} x{3}) - no orders; if it still differs on the next bar the account is flattened and the strategy stops", acctQty, expected, Position.MarketPosition, Position.Quantity);
					Print(msg);
					LogEvent("desync", msg);
					try { Alert("NADesync" + CurrentBar, Priority.High, msg, Path.Combine(Core.Globals.InstallDir, "sounds", "Alert2.wav"), 60, Brushes.Black, Brushes.OrangeRed); } catch { }
				}
				else if (!nowDesync && desync) LogEvent("desync_cleared", string.Format(CI, "account {0} = strategy {1}", acctQty, expected));
				desync = nowDesync;
				desyncBars = desync ? desyncBars + 1 : 0;
				if (desyncBars >= 2 && !desyncStopped)
				{
					desyncStopped = true;
					string flat;
					try { Account.Flatten(new[] { Instrument }); flat = "the account's " + Instrument.FullName + " position (" + acctQty.ToString(CI) + ") was flattened"; }
					catch (Exception ex) { flat = "FLATTEN FAILED (" + ex.Message + ") - flatten the account by hand NOW"; }
					haltNote = string.Format(CI, "stopped on a persistent desync: account {0} vs strategy {1} at {2:HH:mm} ET - {3}; reconcile, then re-enable", acctQty, expected, lastBarEt, flat);
					Print("NoiseAreaStrategy: " + haltNote);
					LogEvent("desync_stop", haltNote);
					try { Alert("NADesyncStop" + CurrentBar, Priority.High, "NoiseAreaStrategy stopped: " + haltNote, Path.Combine(Core.Globals.InstallDir, "sounds", "Alert2.wav"), 60, Brushes.Black, Brushes.OrangeRed); } catch { }
					WriteNewTrades();
					WriteStatus("desync");
					SetState(State.Terminated);
					return;
				}
			}
			else if (State != State.Realtime) { desync = false; desyncBars = 0; }

			// Activity fallback, account side (live only): N sessions without a real fill on this account latch the indicator's activity
			// band for this session, whatever the rules' own count says (a missed or blocked signal still leaves the account idle).
			if (State == State.Realtime && ActivityAfterDays > 0)
			{
				accountDry = na.SessionsAfter(lastActive);
				if (accountDry >= ActivityAfterDays && forcedKey != sessionKey)
				{
					forcedKey = sessionKey;
					LogEvent("activity", string.Format(CI, "account {0}: last fill session {1:yyyy-MM-dd}, {2} sessions without a trade - activity band (x{3:0.##}) for session {4}; the rules' own count is {5}",
						Account.Name, lastActive, accountDry, ActivityMultiplier, sessionKey, na.DryDays));
				}
				na.ForceActivity = forcedKey == sessionKey;
			}

			if (want != cur && !desync)
			{
				string conflict = State == State.Realtime && want != 0 ? DirectionConflict(want) : null;
				if (conflict == null)
				{
					if (dirBlocked) ClearDirectionBlock("cleared at the bar close");
					if (want == 1)       EnterLong(Contracts, "NA_Long");
					else if (want == -1) EnterShort(Contracts, "NA_Short");
					else if (cur == 1)   ExitLong("NA_Flat", "NA_Long");
					else if (cur == -1)  ExitShort("NA_Flat", "NA_Short");
				}
				else
				{
					// Exits always go through; the entry (or a reversal's new side) waits and is retried on every trade print.
					if (cur == 1)       ExitLong("NA_Flat", "NA_Long");
					else if (cur == -1) ExitShort("NA_Flat", "NA_Short");
					if (!dirBlocked)
					{
						dirNote = string.Format(CI, "{0} held back at {1:HH:mm} ET: {2} (Bulenox same-direction rule) - sent as soon as that side is flat", want == 1 ? "LONG" : "SHORT", lastBarEt, conflict);
						Print("NoiseAreaStrategy: " + dirNote);
						LogEvent("direction_block", dirNote);
						try { Alert("NADir" + CurrentBar, Priority.High, "NoiseAreaStrategy: " + dirNote, Path.Combine(Core.Globals.InstallDir, "sounds", "Alert2.wav"), 60, Brushes.Black, Brushes.OrangeRed); } catch { }
					}
					dirBlocked = true;
				}
			}
			else if (dirBlocked && want == cur) ClearDirectionBlock(want == 0 ? "no longer wanted" : "position matches");
			if (State != State.Historical) WriteNewTrades();   // live runs log live trades only; Analyzer runs log everything at Terminated
			if (State == State.Realtime) { WriteStatus(desync ? "desync" : halted ? "halted" : "running"); SaveDayState(dayRealized); }
		}

		// Intrabar daily loss stop (live only; 2026-10-22 audit: at 6 micros Bulenox's daily limit was reached inside the same
		// 5-minute bar on every simulated day, so the bar-close check alone could never act first). Each trade print: the session's
		// realized P&L (as of the last bar close or fill) plus the open P&L of the account's real position.
		protected override void OnMarketData(MarketDataEventArgs e)
		{
			if (e.MarketDataType != MarketDataType.Last || BarsInProgress != 0 || State != State.Realtime) return;
			if (dirBlocked) RetryBlockedEntry();
			if (MaxDailyLoss <= 0 || halted || desyncStopped || refused || sessionKey == "" || Position.MarketPosition == MarketPosition.Flat) return;
			double dayPnl = dayRealizedNow + OpenPnl(e.Price);
			if (dayPnl > -MaxDailyLoss) return;
			halted = true;
			haltNote = string.Format(CI, "daily loss stop hit: {0:0} <= -{1:0} at {2:HH:mm:ss} ET (intrabar, price {3})", dayPnl, MaxDailyLoss, ToEt(DateTime.Now), e.Price);
			Print("NoiseAreaStrategy: " + haltNote);
			LogEvent("halt", haltNote);
			try { Alert("NAHaltTick", Priority.High, "NoiseAreaStrategy halted for the day: " + haltNote, Path.Combine(Core.Globals.InstallDir, "sounds", "Alert2.wav"), 30, Brushes.Black, Brushes.OrangeRed); } catch { }
			if (Position.MarketPosition == MarketPosition.Long) ExitLong("NA_Stop", "NA_Long");
			else ExitShort("NA_Stop", "NA_Short");
			WriteStatus("halted");
			SaveDayState(dayRealizedNow);
		}

		// Bulenox same-direction rule: a note naming another account on this account's connection that holds an equity-index position
		// against `dir`, or null. Simulation / playback accounts neither are checked nor check (Sim101 trades exactly as before). A
		// failed lookup lets the order through (logged): the guard must never be the reason NoiseArea stops trading.
		private string DirectionConflict(int dir)
		{
			if (!DirectionGuard || dir == 0 || Account == null || IsSimAccount(Account)) return null;
			try
			{
				lock (NinjaTrader.Cbi.Account.All)
					foreach (NinjaTrader.Cbi.Account a in NinjaTrader.Cbi.Account.All)
					{
						if (ReferenceEquals(a, Account) || IsSimAccount(a) || !ReferenceEquals(a.Connection, Account.Connection)) continue;
						lock (a.Positions)
							foreach (NinjaTrader.Cbi.Position p in a.Positions)
							{
								if (p.Instrument == null || p.MarketPosition == MarketPosition.Flat) continue;
								if (Array.IndexOf(EquityIndex, p.Instrument.MasterInstrument.Name) < 0) continue;
								int d = p.MarketPosition == MarketPosition.Long ? 1 : -1;
								if (d == -dir) return string.Format(CI, "{0} is {1} {2} {3}", a.Name, p.MarketPosition == MarketPosition.Long ? "LONG" : "SHORT", p.Quantity, p.Instrument.FullName);
							}
					}
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: direction check failed (order not held back): " + ex.Message); }
			return null;
		}
		private static bool IsSimAccount(NinjaTrader.Cbi.Account a) { return a == null || a.Provider == NinjaTrader.Cbi.Provider.Simulator || a.Provider == NinjaTrader.Cbi.Provider.Playback; }

		private void ClearDirectionBlock(string why)
		{
			dirBlocked = false;
			LogEvent("direction_clear", why + (dirNote != "" ? " (" + dirNote + ")" : ""));
			dirNote = "";
		}

		// A held-back entry is sent once no other account holds the other side, at most twice a second, while the rules still want it.
		private void RetryBlockedEntry()
		{
			DateTime now = DateTime.Now;
			if ((now - dirChecked).TotalMilliseconds < 500) return;
			dirChecked = now;
			if (halted || desync || desyncStopped || refused) return;
			int cur = Position.MarketPosition == MarketPosition.Long ? 1 : Position.MarketPosition == MarketPosition.Short ? -1 : 0;
			if (lastDesired == 0 || lastDesired == cur) { ClearDirectionBlock(lastDesired == 0 ? "no longer wanted" : "position matches"); return; }
			if (cur != 0 || DirectionConflict(lastDesired) != null) return;      // a blocked reversal's exit is still in flight, or still opposite
			ClearDirectionBlock(string.Format(CI, "sent at {0:HH:mm:ss} ET", ToEt(now)));
			if (lastDesired == 1) EnterLong(Contracts, "NA_Long"); else EnterShort(Contracts, "NA_Short");
		}

		// Every real-time fill of this strategy marks the session as active for Bulenox's inactivity rule.
		protected override void OnExecutionUpdate(Execution execution, string executionId, double price, int quantity, MarketPosition marketPosition, string orderId, DateTime time)
		{
			if (State != State.Realtime || activityPath == null) return;
			DateTime d = DateTime.ParseExact(SessionKey(ToEt(time)), "yyyy-MM-dd", CI);
			if (d <= lastActive) return;
			lastActive = d;
			try
			{
				string tmp = activityPath + ".tmp";
				File.WriteAllText(tmp, lastActive.ToString("yyyy-MM-dd", CI));
				if (File.Exists(activityPath)) File.Replace(tmp, activityPath, null); else File.Move(tmp, activityPath);
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: activity file write failed: " + ex.Message); }
		}
		// The account's last session with a fill. A new account (no file) starts from the day before the current trading day, so the
		// first live session already counts as one without a trade until something fills.
		private void RestoreActivity()
		{
			lastActive = DateTime.ParseExact(SessionKey(ToEt(DateTime.Now)), "yyyy-MM-dd", CI).AddDays(-1);
			if (activityPath == null) return;
			try
			{
				DateTime d;
				if (File.Exists(activityPath) && DateTime.TryParseExact(File.ReadAllText(activityPath).Trim(), "yyyy-MM-dd", CI, DateTimeStyles.None, out d)) lastActive = d;
				else File.WriteAllText(activityPath, lastActive.ToString("yyyy-MM-dd", CI));
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: activity file read failed: " + ex.Message); }
		}

		// Open P&L of the account's real position (its fill price); the strategy's own position after a restart during a trade
		// carries the historical replay's virtual entry price instead (2026-10-22 audit).
		private double OpenPnl(double price)
		{
			try
			{
				if (State == State.Realtime && PositionAccount != null && PositionAccount.MarketPosition != MarketPosition.Flat)
					return PositionAccount.GetUnrealizedProfitLoss(PerformanceUnit.Currency, price);
			}
			catch { }
			return Position.MarketPosition != MarketPosition.Flat ? Position.GetUnrealizedProfitLoss(PerformanceUnit.Currency, price) : 0.0;
		}

		// Persist the session's realized P&L as soon as a real-time fill changes the position, not only at the next bar close,
		// so a shutdown between bars cannot lose a realized loss.
		protected override void OnPositionUpdate(Cbi.Position position, double averagePrice, int quantity, Cbi.MarketPosition marketPosition)
		{
			if (State != State.Realtime || sessionKey == "") return;
			dayRealizedNow = DayRealized();
			SaveDayState(dayRealizedNow);
		}

		private double DayRealized()
		{
			if (SystemPerformance == null) return dayCarry;
			return dayCarry + SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit - dayStartRealized;
		}

		// CME trading-day key in ET: bars after 17:00 belong to the next calendar day's session.
		private static string SessionKey(DateTime et) { return (et.Hour * 60 + et.Minute > 17 * 60 ? et.Date.AddDays(1) : et.Date).ToString("yyyy-MM-dd", CI); }

		// Net position of the account in this instrument (+qty long, -qty short, 0 flat).
		private int AccountQty()
		{
			try
			{
				if (Account == null || Instrument == null) return 0;
				lock (Account.Positions)
				{
					foreach (NinjaTrader.Cbi.Position p in Account.Positions)
						if (p.Instrument != null && p.Instrument.FullName == Instrument.FullName)
							return p.MarketPosition == MarketPosition.Long ? p.Quantity : p.MarketPosition == MarketPosition.Short ? -p.Quantity : 0;
				}
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: account position lookup failed: " + ex.Message); }
			return 0;
		}

		// An integer field ("contracts", "activity_after") registered for this account in agent_exports\review\accounts.json;
		// 0 = not registered, field missing or unreadable (no block).
		private int RegisteredInt(string acct, string key)
		{
			try
			{
				string p = Path.Combine(Core.Globals.UserDataDir, "agent_exports", "review", "accounts.json");
				if (!File.Exists(p)) return 0;
				string txt = File.ReadAllText(p);
				foreach (Match m in Regex.Matches(txt, @"\{[^{}]*\}"))
				{
					if (!Regex.IsMatch(m.Value, "\"account\"\\s*:\\s*\"" + Regex.Escape(acct) + "\"")) continue;
					Match c = Regex.Match(m.Value, "\"" + Regex.Escape(key) + "\"\\s*:\\s*(\\d+)");
					if (c.Success) return int.Parse(c.Groups[1].Value, CI);
				}
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: accounts.json read failed: " + ex.Message); }
			return 0;
		}

		// The contract of another NoiseAreaStrategy already active on this account on a DIFFERENT contract (the roll case), or null.
		// The same contract is deliberately not matched: a restart after a connection loss must never be refused, and two
		// instances on one contract double the account position, which the desync guard already catches.
		private string OtherInstance()
		{
			try
			{
				if (Account == null || Instrument == null) return null;
				lock (Account.Strategies)
				{
					foreach (StrategyBase s in Account.Strategies)
						if (!ReferenceEquals(s, this) && s.GetType().Name == "NoiseAreaStrategy" && s.Instrument != null
							&& s.Instrument.FullName != Instrument.FullName
							&& (s.State == State.Realtime || s.State == State.Historical || s.State == State.Transition))
							return s.Instrument.FullName;
				}
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: strategy list read failed: " + ex.Message); }
			return null;
		}

		// Day-state file: "<session yyyy-MM-dd>|<realized P&L this session>|<halted 0/1>", so a restart keeps the daily loss stop honest.
		// Written via a temp file and an atomic replace (2026-10-22 audit: a power cut mid-write could leave a truncated file).
		private void SaveDayState(double dayRealized)
		{
			if (dayStatePath == null) return;
			try
			{
				string tmp = dayStatePath + ".tmp";
				File.WriteAllText(tmp, sessionKey + "|" + dayRealized.ToString("0.00", CI) + "|" + (halted ? "1" : "0"));
				if (File.Exists(dayStatePath)) File.Replace(tmp, dayStatePath, null); else File.Move(tmp, dayStatePath);
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: day-state write failed: " + ex.Message); }
		}
		private void RestoreDayState()
		{
			if (dayStatePath == null || !File.Exists(dayStatePath)) return;
			try
			{
				string[] f = File.ReadAllText(dayStatePath).Trim().Split('|');
				string nowKey = SessionKey(ToEt(DateTime.Now));
				if (f.Length >= 3 && f[0] == nowKey)
				{
					dayCarry = double.Parse(f[1], CI);
					halted = f[2] == "1" && MaxDailyLoss > 0;           // a halt saved under an earlier setting does not block trading with the stop off
					if (halted) { haltNote = "daily loss stop was hit earlier this session (restored after restart)"; LogEvent("halt_restored", haltNote); }
					Print(string.Format(CI, "NoiseAreaStrategy: restored session state - realized so far {0:0}, halted {1}", dayCarry, halted));
				}
			}
			catch (Exception ex)
			{
				Print("NoiseAreaStrategy: day-state read failed: " + ex.Message);
				LogEvent("daystate_unreadable", "the session's saved realized P&L / halt could not be read (" + ex.Message + ") - the daily loss stop starts from zero");
			}
		}
		// The session key of a halt saved for the current session, or null (stop off, no file, other session, halt not set).
		private string ReadSavedHaltSession()
		{
			if (MaxDailyLoss <= 0 || dayStatePath == null || !File.Exists(dayStatePath)) return null;
			try
			{
				string[] f = File.ReadAllText(dayStatePath).Trim().Split('|');
				string nowKey = SessionKey(ToEt(DateTime.Now));
				if (f.Length >= 3 && f[0] == nowKey && f[2] == "1") return f[0];
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: day-state read failed: " + ex.Message); }
			return null;
		}

		// Append-only event log for the review (NinjaTrader does not save its Output window). Live runs only, unless forced (a
		// refused start happens during the historical replay).
		private void LogEvent(string evt, string detail, bool force = false)
		{
			if (eventsPath == null || (liveSince == "" && !force)) return;
			try
			{
				File.AppendAllText(eventsPath, string.Format(CI, "{0:yyyy-MM-dd HH:mm:ss}|{1}|{2}|{3}{4}", ToEt(DateTime.Now), evt,
					Account != null ? Account.Name : "", (detail ?? "").Replace("\r", " ").Replace("\n", " ").Replace("|", "/"), Environment.NewLine));
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: event log write failed: " + ex.Message); }
		}

		private DateTime ToEt(DateTime t)
		{
			if (etZone == null) return t;
			try { return TimeZoneInfo.ConvertTime(DateTime.SpecifyKind(t, DateTimeKind.Unspecified), Core.Globals.GeneralOptions.TimeZoneInfo, etZone); }
			catch (Exception ex)
			{
				if (!etWarned) { etWarned = true; Print("NoiseAreaStrategy: ET conversion failed, using platform time: " + ex.Message); }
				return t;
			}
		}

		// Heartbeat for the automated daily review and the watchdog: proves the strategy was alive and shows its state. Times are
		// ET. Real time only, unless forced (a refused start).
		private void WriteStatus(string state, bool force = false)
		{
			if (statusPath == null || (State == State.Historical && !force)) return;
			try
			{
				string json = string.Format(CI,
					"{{\"state\":\"{0}\",\"updated_local\":\"{1:yyyy-MM-dd HH:mm:ss}\",\"updated_et\":\"{12:yyyy-MM-dd HH:mm:ss}\",\"last_bar\":\"{2:yyyy-MM-dd HH:mm:ss}\",\"account\":\"{3}\",\"instrument\":\"{13}\",\"position\":\"{4}\",\"quantity\":{5},\"account_position\":{14},\"desired\":{6},\"trades_logged\":{7},\"trade_log\":\"{8}\",\"max_daily_loss\":{9},\"halt_note\":\"{10}\",\"connected\":{11},\"history_sessions\":{15},\"live_since\":\"{16}\",\"session\":\"{17}\",\"activity_after\":{18},\"activity_now\":{19},\"account_dry\":{20},\"rules_dry\":{21},\"direction_block\":\"{22}\"}}",
					state, DateTime.Now, lastBarEt, Account != null ? Account.Name : "",
					Position.MarketPosition, Position.Quantity, lastDesired, tradesWritten - tradesAtLive,
					tradesPath != null ? Path.GetFileName(tradesPath) : "", MaxDailyLoss, haltNote.Replace("\"", "'").Replace("\\", "/"),
					Account != null && Account.Connection != null && Account.Connection.Status == ConnectionStatus.Connected ? "true" : "false",
					ToEt(DateTime.Now), Instrument != null ? Instrument.FullName : "", AccountQty(), na != null ? na.SessionsOfHistory : 0, liveSince, sessionKey,
					ActivityAfterDays, na != null && na.ActivityNow ? "true" : "false", accountDry, na != null ? na.DryDays : 0,
					dirBlocked ? dirNote.Replace("\"", "'").Replace("\\", "/") : "");
				string tmp = statusPath + ".tmp";
				File.WriteAllText(tmp, json);
				if (File.Exists(statusPath)) File.Replace(tmp, statusPath, null); else File.Move(tmp, statusPath);
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: status write failed: " + ex.Message); }
		}

		private void WriteNewTrades()
		{
			if (!ExportTrades || tradesPath == null || SystemPerformance == null) return;
			try
			{
				int n = SystemPerformance.AllTrades.Count;
				if (n <= tradesWritten) return;
				var sb = new StringBuilder();
				for (int i = tradesWritten; i < n; i++)
				{
					Trade t = SystemPerformance.AllTrades[i];
					sb.AppendLine(string.Format(CI, "{0},{1:yyyy-MM-dd HH:mm:ss},{2:yyyy-MM-dd HH:mm:ss},{3},{4},{5},{6},{7:0.00},{8:0.00},{9:0.00},{10:0.00},{11:0.00},{12},{13}",
						i + 1, ToEt(t.Entry.Time), ToEt(t.Exit.Time), t.Entry.MarketPosition == MarketPosition.Long ? "long" : "short", t.Quantity,
						t.Entry.Price, t.Exit.Price, t.ProfitPoints, t.ProfitCurrency, t.Commission, t.MaeCurrency, t.MfeCurrency, t.Entry.Name, t.Exit.Name));
				}
				File.AppendAllText(tradesPath, sb.ToString());
				tradesWritten = n;
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: trade log write failed: " + ex.Message); }
		}

		#region Properties
		[NinjaScriptProperty] [Range(1, 50)]    [Display(Name = "Contracts", GroupName = "1. Trading", Order = 1)] public int Contracts { get; set; }
		[NinjaScriptProperty] [Range(2, 100)]   [Display(Name = "Lookback days (sigma)", GroupName = "2. Rules (published base case)", Order = 1)] public int LookbackDays { get; set; }
		[NinjaScriptProperty] [Range(0.1, 5.0)] [Display(Name = "Volatility multiplier", GroupName = "2. Rules (published base case)", Order = 2)] public double VolMultiplier { get; set; }
		[NinjaScriptProperty] [Display(Name = "Early-close dates (yyyy-MM-dd, comma separated; blank = indicator default)", Description = "Backup list of partial sessions passed to the indicator; the Trading Hours template is used first.", GroupName = "2. Rules (published base case)", Order = 3)] public string EarlyCloseDates { get; set; }
		[NinjaScriptProperty] [Range(0, 100000)] [Display(Name = "Daily loss stop $ (0 = off)", Description = "Close and stop for the session when realized + open P&L reaches this loss (checked on every trade print). Set it below the prop firm's daily limit.", GroupName = "1. Trading", Order = 2)] public double MaxDailyLoss { get; set; }
		[NinjaScriptProperty] [Display(Name = "Export trades CSV", GroupName = "3. Export", Order = 1)] public bool ExportTrades { get; set; }
		[NinjaScriptProperty] [Range(0, 10)]    [Display(Name = "Activity after N dry sessions (0 = off)", Description = "Bulenox inactivity rule (a trade at least every 5 trading days): after N sessions without a trade the next session trades the same rules with the activity multiplier until a trade happens. 3 on Bulenox accounts (registered in accounts.json), 0 on Sim101.", GroupName = "4. Bulenox rules", Order = 1)] public int ActivityAfterDays { get; set; }
		[NinjaScriptProperty] [Range(0.0, 5.0)] [Display(Name = "Activity multiplier", Description = "Volatility multiplier on activity sessions (adopted 0: the bands are the open / prior close).", GroupName = "4. Bulenox rules", Order = 2)] public double ActivityMultiplier { get; set; }
		[NinjaScriptProperty] [Display(Name = "Same-direction guard", Description = "Bulenox: no entry while another account on this connection holds an equity-index position the other way (simulation accounts exempt).", GroupName = "4. Bulenox rules", Order = 3)] public bool DirectionGuard { get; set; }
		#endregion
	}
}
