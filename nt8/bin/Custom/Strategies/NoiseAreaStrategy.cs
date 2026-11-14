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
		private double dayStartRealized, dayCarry;
		private double dayRealizedNow;
		private bool halted, etWarned, desync;
		private string haltNote = "", dayStatePath, liveSince = "";
		private TimeZoneInfo etZone;
		private int lastDesired; private DateTime lastBarEt = DateTime.MinValue; private string sessionKey = "";
		private int desyncBars;
		private bool desyncStopped;
		private string restoredHaltSession;
		private string eventsPath;
		private string blockNote;
		private int chLoggedBar = -1;
		private bool refused;
		private bool startWait;
		private string activityPath;
		private DateTime lastActive = DateTime.MinValue;
		private string forcedKey = "";
		private int accountDry = -1;
		private bool dirBlocked;
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
				StartBehavior						= StartBehavior.WaitUntilFlatSynchronizeAccount;
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
				ChandelierAtr		= 3.0;
				ChandelierPeriod	= 14;
				TrendFilter			= true;
				TrendFast			= 50;
				TrendSlow			= 200;
				ExitGateBars		= 4;
				LunchPause			= true;
				Ema20Confirm		= true;
			}
			else if (State == State.Configure)
			{
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
				na.ChandelierAtr      = ChandelierAtr;
				na.ChandelierPeriod   = ChandelierPeriod;
				na.TrendFilter        = TrendFilter;
				na.TrendFast          = TrendFast;
				na.TrendSlow          = TrendSlow;
				na.ExitGateBars       = ExitGateBars;
				na.LunchPause         = LunchPause;
				na.Ema20Confirm       = Ema20Confirm;
				if (!string.IsNullOrWhiteSpace(EarlyCloseDates)) na.EarlyCloseDates = EarlyCloseDates;
				AddChartIndicator(na);

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
				restoredHaltSession = ReadSavedHaltSession();

				if (Account != null && acct != "Backtest")
				{
					int reg = RegisteredInt(acct, "contracts");
					int regAct = Math.Max(0, RegisteredInt(acct, "activity_after"));
					double regCh = Math.Max(0.0, RegisteredDouble(acct, "chandelier"));
					int regTrend = Math.Max(0, RegisteredInt(acct, "trend_filter"));
					int regGate = Math.Max(0, RegisteredInt(acct, "exit_gate"));
					int regLunch = Math.Max(0, RegisteredInt(acct, "lunch_pause"));
					int regEma20 = Math.Max(0, RegisteredInt(acct, "ema20_confirm"));
					string other = OtherInstance();
					if (BarsPeriod == null || BarsPeriod.BarsPeriodType != BarsPeriodType.Minute || BarsPeriod.Value != 5)
						blockNote = string.Format(CI, "the data series is {0} but NoiseArea trades 5 Minute bars only - set Data Series to Minute, 5 (Days to load 30) and enable again",
							BarsPeriod == null ? "unknown" : BarsPeriod.Value.ToString(CI) + " " + BarsPeriod.BarsPeriodType);
					else if (reg > 0 && reg != Contracts)
						blockNote = string.Format(CI, "Contracts is {0} but {1} is registered for {2} (agent_exports\\review\\accounts.json) - fix the Contracts setting (or the registration) and enable again", Contracts, acct, reg);
					else if (reg > 0 && regAct != ActivityAfterDays)
						blockNote = string.Format(CI, "'Activity after N dry sessions' is {0} but {1} is registered with activity_after {2} (agent_exports\\review\\accounts.json; Bulenox accounts 3, others 0) - fix the setting and enable again", ActivityAfterDays, acct, regAct);
					else if (reg > 0 && Math.Abs(regCh - ChandelierAtr) > 1e-9)
						blockNote = string.Format(CI, "'Chandelier exit (x ATR)' is {0:0.##} but {1} is registered with chandelier {2:0.##} (agent_exports\\review\\accounts.json) - fix the setting (or the registration) and enable again", ChandelierAtr, acct, regCh);
					else if (reg > 0 && (regTrend > 0) != TrendFilter)
						blockNote = string.Format(CI, "'Trend filter (EMA fast/slow)' is {0} but {1} is registered with trend_filter {2} (agent_exports\\review\\accounts.json; 1 = on, missing or 0 = off) - fix the setting (or the registration) and enable again", TrendFilter ? "on" : "off", acct, regTrend);
					else if (reg > 0 && regGate != ExitGateBars)
						blockNote = string.Format(CI, "'Exit gate (bars, 0 = off)' is {0} but {1} is registered with exit_gate {2} (agent_exports\\review\\accounts.json; missing = 0 = off) - fix the setting (or the registration) and enable again", ExitGateBars, acct, regGate);
					else if (reg > 0 && (regLunch > 0) != LunchPause)
						blockNote = string.Format(CI, "'Lunch pause' is {0} but {1} is registered with lunch_pause {2} (agent_exports\\review\\accounts.json; 1 = on, missing or 0 = off) - fix the setting (or the registration) and enable again", LunchPause ? "on" : "off", acct, regLunch);
					else if (reg > 0 && (regEma20 > 0) != Ema20Confirm)
						blockNote = string.Format(CI, "'EMA(20) confirmation' is {0} but {1} is registered with ema20_confirm {2} (agent_exports\\review\\accounts.json; 1 = on, missing or 0 = off) - fix the setting (or the registration) and enable again", Ema20Confirm ? "on" : "off", acct, regEma20);
					else if (other != null)
						blockNote = string.Format(CI, "another NoiseAreaStrategy is already active on {0} ({1}) - disable it first (one instance per account), then enable this one", acct, other);
				}
			}
			else if (State == State.Transition)
			{
				if (SystemPerformance != null) tradesWritten = tradesAtLive = SystemPerformance.AllTrades.Count;
				dayStartRealized = SystemPerformance != null ? SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit : 0;
				halted = false; haltNote = ""; dayCarry = 0; dayRealizedNow = 0;
				liveSince = ToEt(DateTime.Now).ToString("yyyy-MM-dd HH:mm:ss", CI);
				if (na != null) na.FlushExports();
				RestoreDayState();
				dayRealizedNow = dayCarry;
				RestoreActivity();
				LogEvent("live", string.Format(CI, "contracts {0}, daily loss stop {1:0}, history sessions {2}, exit on session close {3} ({4} s), start {5}, on error {6}, activity after {7} (x{8:0.##}, account last fill session {9:yyyy-MM-dd}), direction guard {10}, chandelier {11:0.##} x ATR({12}), trend filter EMA {13}/{14} {15}, exit gate {16}, lunch pause {17}, EMA(20) confirmation {18}",
					Contracts, MaxDailyLoss, na != null ? na.SessionsOfHistory : 0, IsExitOnSessionCloseStrategy ? "on" : "OFF", ExitOnSessionCloseSeconds, StartBehavior, RealtimeErrorHandling,
					ActivityAfterDays, ActivityMultiplier, lastActive, !DirectionGuard ? "OFF" : IsSimAccount(Account) ? "on (idle: simulation account)" : "on", ChandelierAtr, ChandelierPeriod,
					TrendFast, TrendSlow, TrendFilter ? "on" : "off", ExitGateBars > 0 ? ExitGateBars.ToString(CI) + " bars" : "off", LunchPause ? "on" : "off", Ema20Confirm ? "on" : "off"));
				if (na != null && na.SessionsOfHistory < LookbackDays)
				{
					string msg = string.Format("NoiseAreaStrategy: only {0} complete sessions loaded, {1} needed - no decisions until enough history exists. Increase 'Days to load' (>= 30) and restart.", na.SessionsOfHistory, LookbackDays);
					Print(msg);
					LogEvent("history_short", msg);
					try { Alert("NAHist", Priority.High, msg, Path.Combine(Core.Globals.InstallDir, "sounds", "Alert2.wav"), 60, Brushes.Black, Brushes.OrangeRed); } catch { }
				}
				Print("NoiseAreaStrategy: live from here; historical replay trades are not written to the trade log");
			}
			else if (State == State.Realtime)
			{
				startWait = blockNote == null && Account != null && Account.Name != "Backtest"
					&& (StartBehavior == StartBehavior.WaitUntilFlat || StartBehavior == StartBehavior.WaitUntilFlatSynchronizeAccount)
					&& Position.MarketPosition != MarketPosition.Flat;
				if (startWait)
				{
					string msg = string.Format(CI, "the rules already hold {0} x{1} from {2:0.00}: waiting until they are flat - the account joins at the next entry (no buying the move in progress)",
						Position.MarketPosition, Position.Quantity, Position.AveragePrice);
					Print("NoiseAreaStrategy: " + msg);
					LogEvent("start_wait", msg);
				}
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

			if (State == State.Realtime && want == 0 && cur != 0 && na.LastReason != null && na.LastReason.StartsWith("exit: chandelier") && chLoggedBar != CurrentBar)
			{
				chLoggedBar = CurrentBar;
				LogEvent("chandelier", string.Format(CI, "{0} x{1} at {2:HH:mm} ET: {3} (close {4})", cur > 0 ? "LONG" : "SHORT", Position.Quantity, lastBarEt, na.LastReason, Close[0]));
			}
			else if (State == State.Realtime && want == 0 && cur != 0 && na.LastReason != null && na.LastReason.StartsWith("exit: gate") && chLoggedBar != CurrentBar)
			{
				chLoggedBar = CurrentBar;
				LogEvent("exit_gate", string.Format(CI, "{0} x{1} at {2:HH:mm} ET: {3} (close {4})", cur > 0 ? "LONG" : "SHORT", Position.Quantity, lastBarEt, na.LastReason, Close[0]));
			}

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

			bool connected = Account != null && Account.Connection != null && Account.Connection.Status == ConnectionStatus.Connected;
			if (startWait && State == State.Realtime && cur == 0)
			{
				startWait = false;
				LogEvent("start_wait_end", string.Format(CI, "the rules are flat at {0:HH:mm} ET: the account trades from the next entry", lastBarEt));
			}
			if (State == State.Realtime && connected && !startWait)
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
			else if (State != State.Realtime || startWait) { desync = false; desyncBars = 0; }

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
			if (State != State.Historical) WriteNewTrades();
			if (State == State.Realtime) { WriteStatus(desync ? "desync" : halted ? "halted" : "running"); SaveDayState(dayRealized); }
		}

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

		private void RetryBlockedEntry()
		{
			DateTime now = DateTime.Now;
			if ((now - dirChecked).TotalMilliseconds < 500) return;
			dirChecked = now;
			if (halted || desync || desyncStopped || refused) return;
			int cur = Position.MarketPosition == MarketPosition.Long ? 1 : Position.MarketPosition == MarketPosition.Short ? -1 : 0;
			if (lastDesired == 0 || lastDesired == cur) { ClearDirectionBlock(lastDesired == 0 ? "no longer wanted" : "position matches"); return; }
			if (cur != 0 || DirectionConflict(lastDesired) != null) return;
			ClearDirectionBlock(string.Format(CI, "sent at {0:HH:mm:ss} ET", ToEt(now)));
			if (lastDesired == 1) EnterLong(Contracts, "NA_Long"); else EnterShort(Contracts, "NA_Short");
		}

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

		private static string SessionKey(DateTime et) { return (et.Hour * 60 + et.Minute > 17 * 60 ? et.Date.AddDays(1) : et.Date).ToString("yyyy-MM-dd", CI); }

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

		private double RegisteredDouble(string acct, string key)
		{
			try
			{
				string p = Path.Combine(Core.Globals.UserDataDir, "agent_exports", "review", "accounts.json");
				if (!File.Exists(p)) return 0;
				string txt = File.ReadAllText(p);
				foreach (Match m in Regex.Matches(txt, @"\{[^{}]*\}"))
				{
					if (!Regex.IsMatch(m.Value, "\"account\"\\s*:\\s*\"" + Regex.Escape(acct) + "\"")) continue;
					Match c = Regex.Match(m.Value, "\"" + Regex.Escape(key) + "\"\\s*:\\s*(\\d+(?:\\.\\d+)?)");
					if (c.Success) return double.Parse(c.Groups[1].Value, CI);
				}
			}
			catch (Exception ex) { Print("NoiseAreaStrategy: accounts.json read failed: " + ex.Message); }
			return 0;
		}

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
					halted = f[2] == "1" && MaxDailyLoss > 0;
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

		private void WriteStatus(string state, bool force = false)
		{
			if (statusPath == null || (State == State.Historical && !force)) return;
			try
			{
				string json = string.Format(CI,
					"{{\"state\":\"{0}\",\"updated_local\":\"{1:yyyy-MM-dd HH:mm:ss}\",\"updated_et\":\"{12:yyyy-MM-dd HH:mm:ss}\",\"last_bar\":\"{2:yyyy-MM-dd HH:mm:ss}\",\"account\":\"{3}\",\"instrument\":\"{13}\",\"position\":\"{4}\",\"quantity\":{5},\"account_position\":{14},\"desired\":{6},\"trades_logged\":{7},\"trade_log\":\"{8}\",\"max_daily_loss\":{9},\"halt_note\":\"{10}\",\"connected\":{11},\"history_sessions\":{15},\"live_since\":\"{16}\",\"session\":\"{17}\",\"activity_after\":{18},\"activity_now\":{19},\"account_dry\":{20},\"rules_dry\":{21},\"direction_block\":\"{22}\",\"chandelier\":{23},\"ch_level\":{24},\"trend_filter\":{25},\"exit_gate\":{26},\"gate_count\":{27},\"lunch_pause\":{28},\"ema20_confirm\":{29},\"start_wait\":{30}}}",
					state, DateTime.Now, lastBarEt, Account != null ? Account.Name : "",
					Position.MarketPosition, Position.Quantity, lastDesired, tradesWritten - tradesAtLive,
					tradesPath != null ? Path.GetFileName(tradesPath) : "", MaxDailyLoss, haltNote.Replace("\"", "'").Replace("\\", "/"),
					Account != null && Account.Connection != null && Account.Connection.Status == ConnectionStatus.Connected ? "true" : "false",
					ToEt(DateTime.Now), Instrument != null ? Instrument.FullName : "", AccountQty(), na != null ? na.SessionsOfHistory : 0, liveSince, sessionKey,
					ActivityAfterDays, na != null && na.ActivityNow ? "true" : "false", accountDry, na != null ? na.DryDays : 0,
					dirBlocked ? dirNote.Replace("\"", "'").Replace("\\", "/") : "",
					ChandelierAtr.ToString("0.##", CI), na != null && !double.IsNaN(na.ChandelierLevel) ? na.ChandelierLevel.ToString("0.00", CI) : "0",
					TrendFilter ? "1" : "0", ExitGateBars, na != null ? na.GateCount : 0, LunchPause ? "1" : "0", Ema20Confirm ? "1" : "0", startWait ? "true" : "false");
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
		[NinjaScriptProperty] [Range(0.0, 20.0)] [Display(Name = "Chandelier exit (x ATR, 0 = off)", Description = "Exit at the next open when a 5-minute close retraces this many ATRs from the best close since entry (3 x ATR(14) adopted 2026-10-30 at the trader's decision; must equal accounts.json 'chandelier' for the account; 0 = the validated rules).", GroupName = "5. Chandelier exit", Order = 1)] public double ChandelierAtr { get; set; }
		[NinjaScriptProperty] [Range(2, 100)]    [Display(Name = "Chandelier ATR period", GroupName = "5. Chandelier exit", Order = 2)] public int ChandelierPeriod { get; set; }
		[NinjaScriptProperty] [Display(Name = "Trend filter (EMA fast/slow)", Description = "New legs only in the direction of EMA fast vs EMA slow of the chart's 5-minute closes (all hours); a blocked entry stays flat, a blocked reversal goes flat. NOT validated (research\\LIVESTACK_RESULTS.txt). Must equal accounts.json 'trend_filter' (1 = on, missing or 0 = off) for the account; off = the rules exactly as before.", GroupName = "6. Trend filter", Order = 1)] public bool TrendFilter { get; set; }
		[NinjaScriptProperty] [Range(1, 2000)]   [Display(Name = "Trend EMA fast", GroupName = "6. Trend filter", Order = 2)] public int TrendFast { get; set; }
		[NinjaScriptProperty] [Range(1, 5000)]   [Display(Name = "Trend EMA slow", GroupName = "6. Trend filter", Order = 3)] public int TrendSlow { get; set; }
		[NinjaScriptProperty] [Range(0, 20)]     [Display(Name = "Exit gate (bars, 0 = off)", Description = "In a position, exit at the next open after this many 5-minute closes in a row beyond max(upper band, VWAP) / min(lower band, VWAP); the half-hour checks then hold. NOT validated (research\\NAPLUS2_RESULTS.txt X3: 4). Must equal accounts.json 'exit_gate' (missing = 0) for the account; 0 = the rules exactly as before.", GroupName = "7. Exit gate, lunch pause, EMA(20)", Order = 1)] public int ExitGateBars { get; set; }
		[NinjaScriptProperty] [Display(Name = "Lunch pause", Description = "No entry from flat at the 12:00, 12:30 and 13:00 ET checks. NOT validated (research\\NAPLUS2_RESULTS.txt X3). Must equal accounts.json 'lunch_pause' (1 = on, missing or 0 = off) for the account; off = the rules exactly as before.", GroupName = "7. Exit gate, lunch pause, EMA(20)", Order = 2)] public bool LunchPause { get; set; }
		[NinjaScriptProperty] [Display(Name = "EMA(20) confirmation", Description = "A new leg needs the close above EMA(20) of the RTH 5-minute closes for a long, below it for a short; a blocked entry stays flat, a blocked reversal goes flat. NOT validated (research\\NAPLUS2_RESULTS.txt X3). Must equal accounts.json 'ema20_confirm' (1 = on, missing or 0 = off) for the account; off = the rules exactly as before.", GroupName = "7. Exit gate, lunch pause, EMA(20)", Order = 3)] public bool Ema20Confirm { get; set; }
		#endregion
	}
}
