//
// TapeRecorder (2026-10-22)
// ---------------------------------------------------------------------------------------------
// Listens only: it never submits, changes or cancels an order. Records in real time what NinjaTrader does not keep:
//   1. every trade print of the chart's instrument with the best bid/ask at that moment, and every change of the best bid /
//      best ask (RecordQuotes);
//   2. every order event and every execution on all connected accounts (RecordAccounts), with this PC's receive time and the
//      chart instrument's best bid/ask at that moment, so fill cost and order latency can be measured to the millisecond.
// Files, outside OneDrive (OutputDir, default D:\nt_recorder), one per UTC date, appended across restarts:
//   <OutputDir>\<instrument, e.g. MNQ_12-26>\<date>_tape.csv
//        kind,feed_utc,recv_utc,price,size,bid,ask      kind T = trade (bid/ask = best quote at the print),
//                                                       B / A = best bid / best ask changed (bid,ask = the quote after it)
//   <OutputDir>\accounts\<date>_orders.csv
//        recv_utc,event_utc,account,instrument,order_id,name,action,type,state,quantity,filled,avg_fill,limit,stop,chart_bid,chart_ask,error,server,comment
//   <OutputDir>\accounts\<date>_executions.csv
//        recv_utc,exec_utc,account,instrument,exec_id,order_id,order_name,position,price,quantity,chart_bid,chart_ask
//   <OutputDir>\<instrument>\<date>_bars<period>.csv   time_et (bar END, US Eastern - as the strategy's bars file),open,high,low,
//        close,volume,recv_utc: every completed REAL-TIME bar of the chart's series and of a 5-minute series, as the feed built it
//        (the strategy's own bars file is rewritten from history on every start, so it no longer shows what was traded on)
//   <OutputDir>\accounts\<date>_connections.csv   recv_utc,connection,status,price_status,previous_status,previous_price_status,
//        error,native_error - every connect / disconnect / reconnect
//   agent_exports\TapeRecorder_status.json (rewritten at most every 60 s while data arrives, at start/stop, and at once on a failure).
// 2026-10-22 (logging audit): accounts that connect after the start (e.g. a Bulenox evaluation) are picked up on the next connection
// event; files are flushed every second by a timer even when the market is quiet; a write failure marks the status "failed";
// order events carry NinjaTrader's error code, server name and comment (rejection reasons).
// Times: feed_utc / event_utc / exec_utc = NinjaTrader's event time converted to UTC; recv_utc = this PC's clock (UTC) when the
// event reached the indicator (the PC clock's drift is measured in agent_exports\review\research\LATENCY_NOTE.txt). Files are
// buffered, flushed every second and closed at shutdown; finished days are compressed by Python (research\tape_archive.py).
// Order-book depth (Level 2) is deliberately not recorded: NoiseArea trades market orders at bar closes, nothing in its research
// uses depth, and a depth subscription would add tens of millions of events a day to the live trading platform.
// Put it on its OWN chart (e.g. MNQ 12-26, 1 Minute, Days to load 1), never on the strategy's chart, and run ONE instance with
// RecordAccounts on (two would log every order twice). A chart per extra instrument records that instrument's tape.
// ---------------------------------------------------------------------------------------------
#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Text;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
	public class TapeRecorder : Indicator
	{
		private static readonly CultureInfo CI = CultureInfo.InvariantCulture;
		private const string TapeHeader = "kind,feed_utc,recv_utc,price,size,bid,ask";
		private const string OrderHeader = "recv_utc,event_utc,account,instrument,order_id,name,action,type,state,quantity,filled,avg_fill,limit,stop,chart_bid,chart_ask,error,server,comment";
		private const string ExecHeader = "recv_utc,exec_utc,account,instrument,exec_id,order_id,order_name,position,price,quantity,chart_bid,chart_ask";
		private const string BarHeader = "time_et,open,high,low,close,volume,recv_utc";
		private const string ConnHeader = "recv_utc,connection,status,price_status,previous_status,previous_price_status,error,native_error";

		private class Sink { public StreamWriter W; public string Day; }

		private readonly object sync = new object();
		private readonly Dictionary<string, Sink> sinks = new Dictionary<string, Sink>();
		private readonly List<Account> subscribed = new List<Account>();
		private double bid = double.NaN, ask = double.NaN;
		private string tapeDir, accountsDir, statusPath, instName = "";
		private long nTrades, nQuotes, nOrders, nExecs;
		private DateTime lastFlushUtc = DateTime.MinValue, lastStatusUtc = DateTime.MinValue, lastDataUtc = DateTime.MinValue;
		private bool failed;
		private string failNote = "";
		private System.Threading.Timer flushTimer;
		private TimeZoneInfo etZone;
		private bool connHooked;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Description              = "Records the trade tape, best bid/ask changes and every order event / execution to CSV. Listens only.";
				Name                     = "TapeRecorder";
				Calculate                = Calculate.OnBarClose;
				IsOverlay                = true;
				DisplayInDataBox         = false;
				DrawOnPricePanel         = false;
				PaintPriceMarkers        = false;
				IsSuspendedWhileInactive = false;   // keep recording while the chart window is in the background
				OutputDir                = @"D:\nt_recorder";
				RecordQuotes             = true;
				RecordAccounts           = true;
			}
			else if (State == State.Configure)
			{
				AddDataSeries(BarsPeriodType.Minute, 5);          // BarsInProgress 1: the strategy's bar size, recorded as built live
			}
			else if (State == State.DataLoaded)
			{
				instName    = Instrument != null ? Instrument.FullName : "";
				tapeDir     = Path.Combine(OutputDir, instName.Replace(' ', '_'));
				accountsDir = Path.Combine(OutputDir, "accounts");
				statusPath  = Path.Combine(Core.Globals.UserDataDir, "agent_exports", "TapeRecorder_status.json");
				try { etZone = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time"); } catch { etZone = null; }
				if (RecordAccounts) SubscribeAccounts();
				Connection.ConnectionStatusUpdate += OnConnectionStatusUpdate; connHooked = true;
			}
			else if (State == State.Realtime)
			{
				WriteStatus("running", DateTime.UtcNow);
				flushTimer = new System.Threading.Timer(OnFlushTimer, null, 1000, 1000);
			}
			else if (State == State.Terminated)
			{
				if (flushTimer != null) { try { flushTimer.Dispose(); } catch { } flushTimer = null; }
				if (connHooked) { Connection.ConnectionStatusUpdate -= OnConnectionStatusUpdate; connHooked = false; }
				lock (subscribed)
				{
					foreach (Account a in subscribed)
					{
						a.OrderUpdate     -= OnAccountOrderUpdate;
						a.ExecutionUpdate -= OnAccountExecutionUpdate;
					}
					subscribed.Clear();
				}
				lock (sync)
				{
					foreach (Sink s in sinks.Values)
					{
						try { if (s.W != null) { s.W.Flush(); s.W.Dispose(); s.W = null; } } catch { }
					}
				}
				if (statusPath != null) WriteStatus(failed ? "failed" : "terminated", DateTime.UtcNow);
			}
		}

		// every account in Account.All not yet subscribed (called at load and on every connection change, so late accounts are caught)
		private void SubscribeAccounts()
		{
			lock (subscribed)
			{
				lock (Account.All)
				{
					foreach (Account a in Account.All)
					{
						if (subscribed.Contains(a)) continue;
						a.OrderUpdate     += OnAccountOrderUpdate;
						a.ExecutionUpdate += OnAccountExecutionUpdate;
						subscribed.Add(a);
					}
				}
			}
		}

		private void OnConnectionStatusUpdate(object sender, ConnectionStatusEventArgs e)
		{
			if (failed) return;
			try
			{
				DateTime recv = DateTime.UtcNow;
				string name = e.Connection != null && e.Connection.Options != null ? e.Connection.Options.Name : "";
				Write("connections", accountsDir, ConnHeader, recv, string.Join(",", new string[] {
					Ts(recv), Csv(name), e.Status.ToString(), e.PriceStatus.ToString(), e.PreviousStatus.ToString(), e.PreviousPriceStatus.ToString(),
					e.Error.ToString(), Csv(e.NativeError) }));
				if (RecordAccounts) SubscribeAccounts();
			}
			catch (Exception ex) { Fail("connection event: " + ex.Message); }
		}

		private void OnFlushTimer(object state)
		{
			if (failed) return;
			lock (sync)
			{
				try { foreach (Sink x in sinks.Values) if (x.W != null) x.W.Flush(); }
				catch (Exception ex) { failed = true; failNote = "flush failed: " + ex.Message; }
			}
			if (failed) WriteStatus("failed", DateTime.UtcNow);
		}

		protected override void OnBarUpdate()
		{
			if (State != State.Realtime || failed || CurrentBars[BarsInProgress] < 0) return;
			try
			{
				DateTime recv = DateTime.UtcNow;
				string period = BarsInProgress == 1 ? "5min" : (BarsPeriods[0].Value.ToString(CI) + BarsPeriods[0].BarsPeriodType.ToString().ToLowerInvariant());
				Write("bars" + period, tapeDir, BarHeader, recv, string.Join(",", new string[] {
					ToEt(Times[BarsInProgress][0]).ToString("yyyy-MM-dd HH:mm:ss", CI), Num(Opens[BarsInProgress][0]), Num(Highs[BarsInProgress][0]),
					Num(Lows[BarsInProgress][0]), Num(Closes[BarsInProgress][0]), Volumes[BarsInProgress][0].ToString(CI), Ts(recv) }));
			}
			catch (Exception ex) { Fail("bar write: " + ex.Message); }
		}

		private DateTime ToEt(DateTime t)
		{
			if (etZone == null) return t;
			try { return TimeZoneInfo.ConvertTime(DateTime.SpecifyKind(t, DateTimeKind.Unspecified), Core.Globals.GeneralOptions.TimeZoneInfo, etZone); }
			catch { return t; }
		}

		protected override void OnMarketData(MarketDataEventArgs e)
		{
			if (State != State.Realtime || failed) return;
			DateTime recv = DateTime.UtcNow;
			if (e.MarketDataType == MarketDataType.Last)
			{
				Write("tape", tapeDir, TapeHeader, recv, "T," + Ts(ToUtc(e.Time)) + "," + Ts(recv) + "," + Num(e.Price) + "," + e.Volume.ToString(CI) + "," + Num(e.Bid) + "," + Num(e.Ask));
			}
			else if (e.MarketDataType == MarketDataType.Bid || e.MarketDataType == MarketDataType.Ask)
			{
				bool isBid = e.MarketDataType == MarketDataType.Bid;
				if (isBid) bid = e.Price; else ask = e.Price;
				if (RecordQuotes)
					Write("tape", tapeDir, TapeHeader, recv, (isBid ? "B," : "A,") + Ts(ToUtc(e.Time)) + "," + Ts(recv) + "," + Num(e.Price) + "," + e.Volume.ToString(CI) + "," + Num(bid) + "," + Num(ask));
			}
			else return;
			lastDataUtc = recv;
			if ((recv - lastStatusUtc).TotalSeconds >= 60) WriteStatus("running", recv);
		}

		private void OnAccountOrderUpdate(object sender, OrderEventArgs e)
		{
			if (failed) return;
			try
			{
				DateTime recv = DateTime.UtcNow; Order o = e.Order;
				string line = string.Join(",", new string[] {
					Ts(recv), Ts(ToUtc(e.Time)), Csv(o != null && o.Account != null ? o.Account.Name : ""),
					Csv(o != null && o.Instrument != null ? o.Instrument.FullName : ""), Csv(e.OrderId), Csv(o != null ? o.Name : ""),
					o != null ? o.OrderAction.ToString() : "", o != null ? o.OrderType.ToString() : "", e.OrderState.ToString(),
					e.Quantity.ToString(CI), e.Filled.ToString(CI), Num(e.AverageFillPrice), Num(e.LimitPrice), Num(e.StopPrice), Num(bid), Num(ask),
					e.Error.ToString(), Csv(e.ServerName), Csv(e.Comment) });
				Write("orders", accountsDir, OrderHeader, recv, line);
			}
			catch (Exception ex) { Fail("order event: " + ex.Message); }
		}

		private void OnAccountExecutionUpdate(object sender, ExecutionEventArgs e)
		{
			if (failed) return;
			try
			{
				DateTime recv = DateTime.UtcNow; Execution x = e.Execution;
				string line = string.Join(",", new string[] {
					Ts(recv), Ts(ToUtc(e.Time)), Csv(x != null && x.Account != null ? x.Account.Name : ""),
					Csv(x != null && x.Instrument != null ? x.Instrument.FullName : ""), Csv(e.ExecutionId), Csv(e.OrderId),
					Csv(x != null && x.Order != null ? x.Order.Name : ""), e.MarketPosition.ToString(), Num(e.Price),
					e.Quantity.ToString(CI), Num(bid), Num(ask) });
				Write("executions", accountsDir, ExecHeader, recv, line);
			}
			catch (Exception ex) { Fail("execution event: " + ex.Message); }
		}

		// one writer per stream, rotated on the UTC date; appends, so a restart the same day continues the file
		private void Write(string stream, string dir, string header, DateTime recv, string line)
		{
			if (failed || dir == null) return;
			lock (sync)
			{
				try
				{
					string day = recv.ToString("yyyy-MM-dd", CI);
					Sink s;
					if (!sinks.TryGetValue(stream, out s)) { s = new Sink(); sinks[stream] = s; }
					if (s.W == null || s.Day != day)
					{
						if (s.W != null) { s.W.Flush(); s.W.Dispose(); s.W = null; }
						Directory.CreateDirectory(dir);
						string path = Path.Combine(dir, day + "_" + stream + ".csv");
						bool isNew = !File.Exists(path) || new FileInfo(path).Length == 0;
						s.W = new StreamWriter(new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read, 1 << 16), new UTF8Encoding(false), 1 << 16);
						s.Day = day;
						if (isNew) s.W.WriteLine(header);
					}
					s.W.WriteLine(line);
					if (stream == "tape") { if (line[0] == 'T') nTrades++; else nQuotes++; }
					else if (stream == "orders") nOrders++;
					else if (stream == "executions") nExecs++;
					if ((recv - lastFlushUtc).TotalSeconds >= 1)
					{
						foreach (Sink x in sinks.Values) if (x.W != null) x.W.Flush();
						lastFlushUtc = recv;
					}
				}
				catch (Exception ex) { failed = true; failNote = "write failed: " + ex.Message; Print("TapeRecorder stopped recording: " + failNote); }
			}
			if (failed) WriteStatus("failed", DateTime.UtcNow);      // 2026-10-22: the status no longer says "running" after a failure
		}

		private void Fail(string note)
		{
			lock (sync) { if (failed) return; failed = true; failNote = note; }
			Print("TapeRecorder stopped recording: " + note);
			WriteStatus("failed", DateTime.UtcNow);
		}

		private void WriteStatus(string state, DateTime now)
		{
			lastStatusUtc = now;
			try
			{
				string json = string.Format(CI, "{{\"state\":\"{0}\",\"updated_utc\":\"{1}\",\"instrument\":\"{2}\",\"output_dir\":\"{3}\",\"trades\":{4},\"quotes\":{5},\"orders\":{6},\"executions\":{7},\"last_data_utc\":\"{8}\",\"accounts\":{9},\"note\":\"{10}\"}}",
					state, Ts(now), instName, (OutputDir ?? "").Replace("\\", "\\\\"), nTrades, nQuotes, nOrders, nExecs,
					lastDataUtc == DateTime.MinValue ? "" : Ts(lastDataUtc), subscribed.Count, failNote.Replace("\"", "'").Replace("\\", "/"));
				File.WriteAllText(statusPath, json);
			}
			catch { }
		}

		private static DateTime ToUtc(DateTime t)
		{
			try { return TimeZoneInfo.ConvertTimeToUtc(DateTime.SpecifyKind(t, DateTimeKind.Unspecified), Core.Globals.GeneralOptions.TimeZoneInfo); }
			catch { return DateTime.SpecifyKind(t, DateTimeKind.Local).ToUniversalTime(); }
		}

		private static string Ts(DateTime t)    { return t.ToString("yyyy-MM-dd HH:mm:ss.ffffff", CI); }
		private static string Num(double v)     { return double.IsNaN(v) || double.IsInfinity(v) ? "" : v.ToString("0.########", CI); }
		private static string Csv(string s)     { return string.IsNullOrEmpty(s) ? "" : (s.IndexOfAny(new char[] { ',', '"', '\n', '\r' }) >= 0 ? "\"" + s.Replace("\"", "\"\"") + "\"" : s); }

		#region Properties
		[Display(Name = "Output folder", Description = "Where the CSV files go (keep it outside OneDrive)", GroupName = "Recorder", Order = 1)]
		public string OutputDir { get; set; }

		[Display(Name = "Record best bid/ask changes", Description = "Every top-of-book change (trades are always recorded, with the quote at the print)", GroupName = "Recorder", Order = 2)]
		public bool RecordQuotes { get; set; }

		[Display(Name = "Record orders and executions", Description = "All accounts; run only one instance with this on", GroupName = "Recorder", Order = 3)]
		public bool RecordAccounts { get; set; }
		#endregion
	}
}

#region NinjaScript generated code. Neither change nor remove.

namespace NinjaTrader.NinjaScript.Indicators
{
	public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
	{
		private TapeRecorder[] cacheTapeRecorder;
		public TapeRecorder TapeRecorder()
		{
			return TapeRecorder(Input);
		}

		public TapeRecorder TapeRecorder(ISeries<double> input)
		{
			if (cacheTapeRecorder != null)
				for (int idx = 0; idx < cacheTapeRecorder.Length; idx++)
					if (cacheTapeRecorder[idx] != null &&  cacheTapeRecorder[idx].EqualsInput(input))
						return cacheTapeRecorder[idx];
			return CacheIndicator<TapeRecorder>(new TapeRecorder(), input, ref cacheTapeRecorder);
		}
	}
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
	public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
	{
		public Indicators.TapeRecorder TapeRecorder()
		{
			return indicator.TapeRecorder(Input);
		}

		public Indicators.TapeRecorder TapeRecorder(ISeries<double> input )
		{
			return indicator.TapeRecorder(input);
		}
	}
}

namespace NinjaTrader.NinjaScript.Strategies
{
	public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
	{
		public Indicators.TapeRecorder TapeRecorder()
		{
			return indicator.TapeRecorder(Input);
		}

		public Indicators.TapeRecorder TapeRecorder(ISeries<double> input )
		{
			return indicator.TapeRecorder(input);
		}
	}
}

#endregion
