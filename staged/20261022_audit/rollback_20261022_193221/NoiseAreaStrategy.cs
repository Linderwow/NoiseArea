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
//   - Daily loss stop (0 = off): when the session's realized + open P&L reaches -MaxDailyLoss the position is closed and
//     no new entries are taken until the next session. Set it below your Bulenox daily loss limit. Live only: it is
//     not modelled in Strategy Analyzer (the validated reference has no stop). The session's realized P&L and the halted
//     flag are persisted to a small file (after every bar, every position change and at shutdown), so a restart within the
//     same session carries them over; a halt saved for the current session is also applied to that session's historical
//     replay, so the start-up synchronisation cannot re-enter the position the halt closed. A saved halt is restored only
//     while the stop is on. Evaluated on 5-minute bar closes only; keep Calculate = OnBarClose (the hosted indicator
//     inherits it and must see completed bars).
//   - Exit on session close stays on as the backup for early-close days (needs the Trading Hours template's holidays).
//     2026-10-20: it fires at least 120 s before the session end (17:00 ET -> 16:58 ET = 15:58 CT), inside Bulenox's "flat by
//     3:59 PM CT"; 30 s fired at 15:59:30 CT. Still inside the last 5-minute bar, so Strategy Analyzer results are unchanged.
//   - Account/strategy desync guard: if the account's position in this instrument (direction AND size) differs from the
//     strategy's, no order is sent, an alert fires and the heartbeat reports it. If it is still different on the next
//     5-minute bar (so not a fill in progress), the strategy stops itself without flattening anything: heartbeat state
//     "desync", the note names both positions, and NinjaTrader's own session-close exit can no longer act on the stale
//     bookkeeping. A human reconciles the account and re-enables. A restart with fewer than LookbackDays sessions of
//     history alerts too (with too little history the rules see no sigma and a restart would flatten a correct position):
//     load >= 30 days.
//   - Events (live start, desync, desync stop, halt, halt restored, history alert, termination) are appended to
//     agent_exports\NoiseArea_events_<instrument>_<account>.txt, because NinjaTrader does not save its Output window.
//   2026-10-20: size-aware desync, stop on persistent desync, halt persistence fixes and the event log
//   (research\GUARDS_PLAN.txt / GUARDS_RESULTS.txt). Trading rules unchanged.
// ---------------------------------------------------------------------------------------------
#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Text;
using System.Windows.Media;
using NinjaTrader.Cbi;
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
		private bool halted, etWarned, desync;
		private string haltNote = "", dayStatePath, liveSince = "";
		private TimeZoneInfo etZone;
		private int lastDesired; private DateTime lastBarEt = DateTime.MinValue; private string sessionKey = "";
		private int desyncBars;                              // consecutive real-time bars on which the account differed
		private bool desyncStopped;                          // the strategy stopped itself on a persistent desync
		private string restoredHaltSession;                  // session whose saved halt also applies to its historical replay
		private string eventsPath;
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
			}
			else if (State == State.Configure)
			{
				// Bulenox: flat by 3:59 PM CT. A saved instance keeps its old 30 s, so the minimum is enforced here.
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
				if (!string.IsNullOrWhiteSpace(EarlyCloseDates)) na.EarlyCloseDates = EarlyCloseDates;
				AddChartIndicator(na);

				if (ExportTrades)
				{
					try
					{
						string dir = Path.Combine(Core.Globals.UserDataDir, "agent_exports");
						Directory.CreateDirectory(dir);
						string inst = Instrument != null ? Instrument.MasterInstrument.Name : "inst";
						tradesPath = Path.Combine(dir, "NoiseArea_trades_" + inst + "_" + acct + "_" + DateTime.Now.ToString("yyyyMMdd_HHmmss", CI) + ".csv");
						statusPath = Path.Combine(dir, "NoiseArea_status_" + inst + "_" + acct + ".json");
						dayStatePath = Path.Combine(dir, "NoiseArea_daystate_" + inst + "_" + acct + ".txt");
						eventsPath = Path.Combine(dir, "NoiseArea_events_" + inst + "_" + acct + ".txt");
						File.WriteAllText(tradesPath, "trade,entry_time_et,exit_time_et,direction,qty,entry_price,exit_price,profit_points,profit_currency,commission,mae_currency,mfe_currency,entry_name,exit_name" + Environment.NewLine);
					}
					catch (Exception ex) { Print("NoiseAreaStrategy: cannot create trade log: " + ex.Message); tradesPath = null; }
				}
				// A halt saved for the CURRENT session (stop on) is applied to that session's historical replay too, so the
				// start-up account synchronisation cannot re-enter the position the halt closed. Never true in Strategy Analyzer
				// (the day-state file is written in real time only).
				restoredHaltSession = ReadSavedHaltSession();
			}
			else if (State == State.Transition)
			{
				// Going live: the historical replay's virtual trades are not logged, and the daily loss stop starts from zero.
				if (SystemPerformance != null) tradesWritten = tradesAtLive = SystemPerformance.AllTrades.Count;
				dayStartRealized = SystemPerformance != null ? SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit : 0;
				halted = false; haltNote = ""; dayCarry = 0;
				liveSince = ToEt(DateTime.Now).ToString("yyyy-MM-dd HH:mm:ss", CI);
				if (na != null) na.FlushExports();
				RestoreDayState();
				LogEvent("live", string.Format(CI, "contracts {0}, daily loss stop {1:0}, history sessions {2}", Contracts, MaxDailyLoss, na != null ? na.SessionsOfHistory : 0));
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
				WriteStatus(desyncStopped ? "desync" : "terminated");
				if (liveSince != "") LogEvent("terminated", string.Format(CI, "strategy {0} x{1}, account {2}{3}", Position.MarketPosition, Position.Quantity, AccountQty(), desyncStopped ? " (stopped on desync)" : ""));
				if (tradesPath != null) Print("NoiseAreaStrategy: trade log at " + tradesPath);
			}
		}

		protected override void OnBarUpdate()
		{
			if (BarsInProgress != 0 || na == null) return;
			double dp = na.DesiredPos[0];
			int want = double.IsNaN(dp) ? 0 : (int)Math.Round(dp);
			lastDesired = want; lastBarEt = ToEt(Time[0]);
			sessionKey = SessionKey(lastBarEt);
			if (CurrentBar < BarsRequiredToTrade) return;

			int cur = Position.MarketPosition == MarketPosition.Long ? 1 : Position.MarketPosition == MarketPosition.Short ? -1 : 0;

			// Daily loss stop: per session (18:00 -> 17:00 ET on the default CME template), realized + open P&L.
			// Armed in real time only. The session's realized P&L and the halted flag are persisted so a restart within the
			// same session carries them over instead of starting from zero.
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
			if (MaxDailyLoss > 0 && !halted && State == State.Realtime)
			{
				double dayPnl = dayRealized + (cur != 0 ? Position.GetUnrealizedProfitLoss(PerformanceUnit.Currency, Close[0]) : 0.0);
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
			// If it still differs on the next bar, it was not a fill in progress: stop the strategy WITHOUT flattening, so
			// NinjaTrader's own session-close exit cannot act on the stale position; a human reconciles and re-enables.
			bool connected = Account != null && Account.Connection != null && Account.Connection.Status == ConnectionStatus.Connected;
			if (State == State.Realtime && connected)
			{
				int acctQty = AccountQty();
				int expected = cur * Position.Quantity;
				bool nowDesync = acctQty != expected;
				if (nowDesync && !desync)
				{
					string msg = string.Format("NoiseAreaStrategy: account position {0} differs from the strategy position {1} ({2} x{3}) - no orders; the strategy stops itself if it still differs on the next bar", acctQty, expected, Position.MarketPosition, Position.Quantity);
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
					haltNote = string.Format(CI, "stopped on a persistent desync: account {0} vs strategy {1} at {2:HH:mm} ET - nothing was flattened; reconcile the account by hand, then re-enable", acctQty, expected, lastBarEt);
					Print("NoiseAreaStrategy: " + haltNote);
					LogEvent("desync_stop", haltNote);
					try { Alert("NADesyncStop" + CurrentBar, Priority.High, "NoiseAreaStrategy stopped: " + haltNote, Path.Combine(Core.Globals.InstallDir, "sounds", "Alert2.wav"), 60, Brushes.Black, Brushes.OrangeRed); } catch { }
					WriteNewTrades();
					WriteStatus("desync");
					SetState(State.Terminated);
					return;
				}
			}
			else { desync = false; desyncBars = 0; }

			if (want != cur && !desync)
			{
				if (want == 1)       EnterLong(Contracts, "NA_Long");
				else if (want == -1) EnterShort(Contracts, "NA_Short");
				else if (cur == 1)   ExitLong("NA_Flat", "NA_Long");
				else if (cur == -1)  ExitShort("NA_Flat", "NA_Short");
			}
			if (State != State.Historical) WriteNewTrades();   // live runs log live trades only; Analyzer runs log everything at Terminated
			if (State == State.Realtime) { WriteStatus(desync ? "desync" : halted ? "halted" : "running"); SaveDayState(dayRealized); }
		}

		// Persist the session's realized P&L as soon as a real-time fill changes the position, not only at the next bar close,
		// so a shutdown between bars cannot lose a realized loss.
		protected override void OnPositionUpdate(Cbi.Position position, double averagePrice, int quantity, Cbi.MarketPosition marketPosition)
		{
			if (State != State.Realtime || sessionKey == "") return;
			SaveDayState(DayRealized());
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

		// Day-state file: "<session yyyy-MM-dd>|<realized P&L this session>|<halted 0/1>", so a restart keeps the daily loss stop honest.
		private void SaveDayState(double dayRealized)
		{
			if (dayStatePath == null) return;
			try { File.WriteAllText(dayStatePath, sessionKey + "|" + dayRealized.ToString("0.00", CI) + "|" + (halted ? "1" : "0")); }
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
			catch (Exception ex) { Print("NoiseAreaStrategy: day-state read failed: " + ex.Message); }
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

		// Append-only event log for the review (NinjaTrader does not save its Output window). Live runs only.
		private void LogEvent(string evt, string detail)
		{
			if (eventsPath == null || liveSince == "") return;
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

		// Heartbeat for the automated daily review: proves the strategy was alive and shows its state. Times are ET.
		private void WriteStatus(string state)
		{
			if (!ExportTrades || statusPath == null || State == State.Historical) return;
			try
			{
				string json = string.Format(CI,
					"{{\"state\":\"{0}\",\"updated_local\":\"{1:yyyy-MM-dd HH:mm:ss}\",\"updated_et\":\"{12:yyyy-MM-dd HH:mm:ss}\",\"last_bar\":\"{2:yyyy-MM-dd HH:mm:ss}\",\"account\":\"{3}\",\"instrument\":\"{13}\",\"position\":\"{4}\",\"quantity\":{5},\"account_position\":{14},\"desired\":{6},\"trades_logged\":{7},\"trade_log\":\"{8}\",\"max_daily_loss\":{9},\"halt_note\":\"{10}\",\"connected\":{11},\"history_sessions\":{15},\"live_since\":\"{16}\",\"session\":\"{17}\"}}",
					state, DateTime.Now, lastBarEt, Account != null ? Account.Name : "",
					Position.MarketPosition, Position.Quantity, lastDesired, tradesWritten - tradesAtLive,
					tradesPath != null ? Path.GetFileName(tradesPath) : "", MaxDailyLoss, haltNote.Replace("\"", "'"),
					Account != null && Account.Connection != null && Account.Connection.Status == ConnectionStatus.Connected ? "true" : "false",
					ToEt(DateTime.Now), Instrument != null ? Instrument.FullName : "", AccountQty(), na != null ? na.SessionsOfHistory : 0, liveSince, sessionKey);
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
		[NinjaScriptProperty] [Range(0, 100000)] [Display(Name = "Daily loss stop $ (0 = off)", Description = "Close and stop for the session when realized + open P&L reaches this loss. Set it below the prop firm's daily limit.", GroupName = "1. Trading", Order = 2)] public double MaxDailyLoss { get; set; }
		[NinjaScriptProperty] [Display(Name = "Export trades CSV", GroupName = "3. Export", Order = 1)] public bool ExportTrades { get; set; }
		#endregion
	}
}
