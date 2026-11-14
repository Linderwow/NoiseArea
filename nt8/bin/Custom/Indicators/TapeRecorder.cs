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

		private const string BuildTag      = "2026-10-22b";
		private const int    QueueCapacity = 250000;
		private const int    QueueReserve  = 10000;
		private const int    RetrySeconds  = 60;
		private const int    JoinMillis    = 3000;
		private const int    BatchMax      = 20000;

		private struct Rec { public string Stream, Dir, Header, Line; public DateTime Recv; public char Kind; }

		private sealed class Sink
		{
			public string Stream, Day = "", FailNote = "";
			public StreamWriter W;
			public DateTime DayDate, LastWriteUtc, FailedUtc, RetryUtc;
			public bool Dirty, Failed;
			public int Unflushed;
		}

		private System.Collections.Concurrent.ConcurrentQueue<Rec> queue;
		private System.Threading.ManualResetEventSlim wake;
		private System.Threading.Thread writer;
		private volatile bool accepting, stopRequested;
		private volatile string lastEventError = "";
		private int queued, queuePeak, statusRequest, accountsCount;
		private long dropped, eventErrors, mdSkipped, lastDataTicks, lastEventErrorTicks;
		private double bid = double.NaN, ask = double.NaN;

		private readonly List<Account> subscribed = new List<Account>();
		private string tapeDir, accountsDir, statusPath, instName = "", primaryStream = "";
		private bool primaryIs5Min;
		private TimeZoneInfo etZone;
		private bool connHooked;

		private readonly Dictionary<string, Sink> sinks = new Dictionary<string, Sink>();
		private long nTrades, nQuotes, nOrders, nExecs, lost, streamFailures, repairedTails, droppedAtStatus, lostAtStatus;
		private DateTime lastFlushUtc = DateTime.MinValue, lastStatusUtc = DateTime.MinValue, lastStatusTryUtc = DateTime.MinValue;
		private bool statusPending;
		private int statusFailures;
		private string recoveredNote = "", writerNote = "";

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
				IsSuspendedWhileInactive = false;
				OutputDir                = @"D:\nt_recorder";
				RecordQuotes             = true;
				RecordAccounts           = true;
			}
			else if (State == State.Configure)
			{
				AddDataSeries(BarsPeriodType.Minute, 5);
			}
			else if (State == State.DataLoaded)
			{
				instName    = Instrument != null ? Instrument.FullName : "";
				tapeDir     = Path.Combine(OutputDir, instName.Replace(' ', '_'));
				accountsDir = Path.Combine(OutputDir, "accounts");
				statusPath  = Path.Combine(Core.Globals.UserDataDir, "agent_exports", "TapeRecorder_status.json");
				try { etZone = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time"); } catch { etZone = null; }
				primaryIs5Min = BarsPeriods[0].BarsPeriodType == BarsPeriodType.Minute && BarsPeriods[0].Value == 5;
				primaryStream = "bars" + BarsPeriods[0].Value.ToString(CI) + BarsPeriods[0].BarsPeriodType.ToString().ToLowerInvariant();
				StartWriter();
				if (RecordAccounts) SubscribeAccounts();
				Connection.ConnectionStatusUpdate += OnConnectionStatusUpdate; connHooked = true;
			}
			else if (State == State.Realtime)
			{
				RequestStatus();
			}
			else if (State == State.Terminated)
			{
				if (connHooked) { Connection.ConnectionStatusUpdate -= OnConnectionStatusUpdate; connHooked = false; }
				lock (subscribed)
				{
					foreach (Account a in subscribed)
					{
						a.OrderUpdate     -= OnAccountOrderUpdate;
						a.ExecutionUpdate -= OnAccountExecutionUpdate;
					}
					subscribed.Clear();
					System.Threading.Volatile.Write(ref accountsCount, 0);
				}
				StopWriter();
			}
		}

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
				System.Threading.Volatile.Write(ref accountsCount, subscribed.Count);
			}
		}

		private void OnConnectionStatusUpdate(object sender, ConnectionStatusEventArgs e)
		{
			try
			{
				DateTime recv = DateTime.UtcNow;
				string name = e.Connection != null && e.Connection.Options != null ? e.Connection.Options.Name : "";
				Enqueue("connections", accountsDir, ConnHeader, recv, string.Join(",", new string[] {
					Ts(recv), Csv(name), e.Status.ToString(), e.PriceStatus.ToString(), e.PreviousStatus.ToString(), e.PreviousPriceStatus.ToString(),
					e.Error.ToString(), Csv(e.NativeError) }), 'C');
				if (RecordAccounts) SubscribeAccounts();
			}
			catch (Exception ex) { EventError("connection event: " + ex.Message); }
		}

		protected override void OnBarUpdate()
		{
			if (State != State.Realtime || CurrentBars[BarsInProgress] < 0) return;
			if (BarsInProgress == 1 && primaryIs5Min) return;
			try
			{
				DateTime recv = DateTime.UtcNow;
				string stream = BarsInProgress == 1 ? "bars5min" : primaryStream;
				Enqueue(stream, tapeDir, BarHeader, recv, string.Join(",", new string[] {
					ToEt(Times[BarsInProgress][0]).ToString("yyyy-MM-dd HH:mm:ss", CI), Num(Opens[BarsInProgress][0]), Num(Highs[BarsInProgress][0]),
					Num(Lows[BarsInProgress][0]), Num(Closes[BarsInProgress][0]), Volumes[BarsInProgress][0].ToString(CI), Ts(recv) }), 'K');
			}
			catch (Exception ex) { EventError("bar: " + ex.Message); }
		}

		private DateTime ToEt(DateTime t)
		{
			if (etZone == null) return t;
			try { return TimeZoneInfo.ConvertTime(DateTime.SpecifyKind(t, DateTimeKind.Unspecified), Core.Globals.GeneralOptions.TimeZoneInfo, etZone); }
			catch { return t; }
		}

		protected override void OnMarketData(MarketDataEventArgs e)
		{
			if (State != State.Realtime) return;
			bool isLast = e.MarketDataType == MarketDataType.Last;
			bool isBid  = e.MarketDataType == MarketDataType.Bid;
			if (!isLast && !isBid && e.MarketDataType != MarketDataType.Ask) return;
			if (BarsInProgress != 0)
			{
				System.Threading.Interlocked.Increment(ref mdSkipped);
				return;
			}
			try
			{
				DateTime recv = DateTime.UtcNow;
				if (isLast)
				{
					Enqueue("tape", tapeDir, TapeHeader, recv, "T," + Ts(ToUtc(e.Time)) + "," + Ts(recv) + "," + Num(e.Price) + "," + e.Volume.ToString(CI) + "," + Num(e.Bid) + "," + Num(e.Ask), 'T');
				}
				else
				{
					if (isBid) System.Threading.Volatile.Write(ref bid, e.Price); else System.Threading.Volatile.Write(ref ask, e.Price);
					if (RecordQuotes)
						Enqueue("tape", tapeDir, TapeHeader, recv, (isBid ? "B," : "A,") + Ts(ToUtc(e.Time)) + "," + Ts(recv) + "," + Num(e.Price) + "," + e.Volume.ToString(CI) + "," + Num(bid) + "," + Num(ask), 'Q');
				}
				System.Threading.Volatile.Write(ref lastDataTicks, recv.Ticks);
			}
			catch (Exception ex) { EventError("market data: " + ex.Message); }
		}

		private void OnAccountOrderUpdate(object sender, OrderEventArgs e)
		{
			try
			{
				DateTime recv = DateTime.UtcNow; Order o = e.Order;
				double b = System.Threading.Volatile.Read(ref bid), a = System.Threading.Volatile.Read(ref ask);
				string line = string.Join(",", new string[] {
					Ts(recv), Ts(ToUtc(e.Time)), Csv(o != null && o.Account != null ? o.Account.Name : ""),
					Csv(o != null && o.Instrument != null ? o.Instrument.FullName : ""), Csv(e.OrderId), Csv(o != null ? o.Name : ""),
					o != null ? o.OrderAction.ToString() : "", o != null ? o.OrderType.ToString() : "", e.OrderState.ToString(),
					e.Quantity.ToString(CI), e.Filled.ToString(CI), Num(e.AverageFillPrice), Num(e.LimitPrice), Num(e.StopPrice), Num(b), Num(a),
					e.Error.ToString(), Csv(e.ServerName), Csv(e.Comment) });
				Enqueue("orders", accountsDir, OrderHeader, recv, line, 'O');
			}
			catch (Exception ex) { EventError("order event: " + ex.Message); }
		}

		private void OnAccountExecutionUpdate(object sender, ExecutionEventArgs e)
		{
			try
			{
				DateTime recv = DateTime.UtcNow; Execution x = e.Execution;
				double b = System.Threading.Volatile.Read(ref bid), a = System.Threading.Volatile.Read(ref ask);
				string line = string.Join(",", new string[] {
					Ts(recv), Ts(ToUtc(e.Time)), Csv(x != null && x.Account != null ? x.Account.Name : ""),
					Csv(x != null && x.Instrument != null ? x.Instrument.FullName : ""), Csv(e.ExecutionId), Csv(e.OrderId),
					Csv(x != null && x.Order != null ? x.Order.Name : ""), e.MarketPosition.ToString(), Num(e.Price),
					e.Quantity.ToString(CI), Num(b), Num(a) });
				Enqueue("executions", accountsDir, ExecHeader, recv, line, 'E');
			}
			catch (Exception ex) { EventError("execution event: " + ex.Message); }
		}


		private void Enqueue(string stream, string dir, string header, DateTime recv, string line, char kind)
		{
			if (!accepting || dir == null) return;
			int limit = (kind == 'T' || kind == 'Q') ? QueueCapacity : QueueCapacity + QueueReserve;
			int n = System.Threading.Interlocked.Increment(ref queued);
			if (n > limit)
			{
				System.Threading.Interlocked.Decrement(ref queued);
				System.Threading.Interlocked.Increment(ref dropped);
				return;
			}
			if (n > queuePeak) queuePeak = n;
			Rec r;
			r.Stream = stream; r.Dir = dir; r.Header = header; r.Line = line; r.Recv = recv; r.Kind = kind;
			queue.Enqueue(r);
		}

		private void EventError(string note)
		{
			long n = System.Threading.Interlocked.Increment(ref eventErrors);
			lastEventError = note;
			System.Threading.Volatile.Write(ref lastEventErrorTicks, DateTime.UtcNow.Ticks);
			RequestStatus();
			if (n <= 3) SafePrint("TapeRecorder: an event was not recorded - " + note);
		}

		private void RequestStatus() { System.Threading.Interlocked.Exchange(ref statusRequest, 1); }

		private void SafePrint(string s) { try { Print(s); } catch { } }

		private void StartWriter()
		{
			if (writer != null) return;
			queue = new System.Collections.Concurrent.ConcurrentQueue<Rec>();
			wake  = new System.Threading.ManualResetEventSlim(false);
			stopRequested = false;
			System.Threading.Thread t = new System.Threading.Thread(WriterLoop);
			t.IsBackground = true;
			t.Name = "TapeRecorder writer";
			writer = t;
			accepting = true;
			t.Start();
		}

		private void StopWriter()
		{
			System.Threading.Thread t = writer;
			if (t == null) return;
			writer = null;
			accepting = false;
			stopRequested = true;
			try { wake.Set(); } catch { }
			bool done = false;
			try { done = t.Join(JoinMillis); } catch { }
			if (!done) SafePrint("TapeRecorder: the file writer did not finish within " + JoinMillis.ToString(CI) + " ms (disk busy?) - it finishes in the background");
		}


		private void WriterLoop()
		{
			try
			{
				while (true)
				{
					bool stop = stopRequested;
					int n = 0;
					try
					{
						DateTime now = DateTime.UtcNow;
						Rec r;
						while (n < BatchMax && queue.TryDequeue(out r))
						{
							System.Threading.Interlocked.Decrement(ref queued);
							WriteRec(ref r, now);
							if ((++n & 255) == 0) now = DateTime.UtcNow;
						}
						now = DateTime.UtcNow;
						if ((now - lastFlushUtc).TotalMilliseconds >= 1000)
						{
							FlushAll(now);
							CloseOldDays(now);
							lastFlushUtc = now;
						}
						if (!stop) MaybeStatus(now, false);
					}
					catch (Exception ex)
					{
						writerNote = "writer: " + ex.Message;
						SafePrint("TapeRecorder: " + writerNote);
						if (stop) break;
						wake.Wait(1000);
						continue;
					}
					if (stop && queue.IsEmpty) break;
					if (!stop && n < BatchMax) wake.Wait(100);
				}
			}
			catch (Exception ex) { writerNote = "writer stopped: " + ex.Message; SafePrint("TapeRecorder: " + writerNote); }
			DateTime end = DateTime.UtcNow;
			try { FlushAll(end); } catch { }
			try { foreach (Sink s in sinks.Values) CloseSink(s, end); } catch { }
			for (int i = 0; i < 5; i++)
			{
				try { MaybeStatus(end, true); } catch { }
				if (!statusPending) break;
				System.Threading.Thread.Sleep(200);
			}
		}

		private void WriteRec(ref Rec r, DateTime now)
		{
			Sink s;
			if (!sinks.TryGetValue(r.Stream, out s)) { s = new Sink(); s.Stream = r.Stream; sinks[r.Stream] = s; }
			if (s.Failed && now < s.RetryUtc) { lost++; return; }
			string what = "open failed: ";
			try
			{
				if (s.W == null || r.Recv.Date != s.DayDate) OpenSink(s, ref r, now);
				what = "write failed: ";
				s.W.WriteLine(r.Line);
				s.Dirty = true; s.Unflushed++; s.LastWriteUtc = now;
				switch (r.Kind)
				{
					case 'T': nTrades++; break;
					case 'Q': nQuotes++; break;
					case 'O': nOrders++; break;
					case 'E': nExecs++; break;
				}
			}
			catch (Exception ex)
			{
				lost += s.Unflushed + 1;
				MarkFailed(s, what + ex.Message, now);
			}
		}

		private void OpenSink(Sink s, ref Rec r, DateTime now)
		{
			StreamWriter old = s.W;
			s.W = null; s.Dirty = false;
			if (old != null)
			{
				try { old.Flush(); }
				finally { try { old.Dispose(); } catch { } }
			}
			s.Unflushed = 0;
			Directory.CreateDirectory(r.Dir);
			string day = r.Recv.ToString("yyyy-MM-dd", CI);
			string path = Path.Combine(r.Dir, day + "_" + r.Stream + ".csv");
			bool isNew = !File.Exists(path) || new FileInfo(path).Length == 0;
			bool brokenTail = !isNew && !EndsWithLineBreak(path);
			FileStream fs = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read, 1 << 16);
			StreamWriter w;
			try { w = new StreamWriter(fs, new UTF8Encoding(false), 1 << 16); }
			catch { fs.Dispose(); throw; }
			s.W = w; s.Day = day; s.DayDate = r.Recv.Date;
			if (isNew) w.WriteLine(r.Header);
			else if (brokenTail) { w.WriteLine(); repairedTails++; }
			if (s.Failed)
			{
				s.Failed = false;
				recoveredNote = s.Stream + " recording again since " + Ts(now) + " (had failed " + Ts(s.FailedUtc) + ": " + s.FailNote + ")";
				RequestStatus();
				SafePrint("TapeRecorder: " + recoveredNote);
			}
		}

		private static bool EndsWithLineBreak(string path)
		{
			using (FileStream f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1))
			{
				if (f.Length == 0) return true;
				f.Seek(-1, SeekOrigin.End);
				return f.ReadByte() == '\n';
			}
		}

		private void MarkFailed(Sink s, string note, DateTime now)
		{
			StreamWriter w = s.W;
			s.W = null; s.Dirty = false; s.Unflushed = 0;
			if (w != null) { try { w.Dispose(); } catch { } }
			bool first = !s.Failed;
			s.Failed = true; s.FailNote = note; s.FailedUtc = now; s.RetryUtc = now.AddSeconds(RetrySeconds);
			streamFailures++;
			RequestStatus();
			if (first) SafePrint("TapeRecorder: " + s.Stream + " not recording (" + note + "); retried every " + RetrySeconds.ToString(CI) + " s, the other files continue");
		}

		private void FlushAll(DateTime now)
		{
			foreach (Sink s in sinks.Values)
			{
				if (s.W == null || !s.Dirty) continue;
				try { s.W.Flush(); s.Dirty = false; s.Unflushed = 0; }
				catch (Exception ex) { lost += s.Unflushed; MarkFailed(s, "flush failed: " + ex.Message, now); }
			}
		}

		private void CloseOldDays(DateTime now)
		{
			DateTime today = now.Date;
			foreach (Sink s in sinks.Values)
				if (s.W != null && s.DayDate != today && (now - s.LastWriteUtc).TotalSeconds >= 10) CloseSink(s, now);
		}

		private void CloseSink(Sink s, DateTime now)
		{
			if (s.W == null) return;
			try { s.W.Flush(); s.Dirty = false; s.Unflushed = 0; }
			catch (Exception ex) { lost += s.Unflushed; MarkFailed(s, "flush failed: " + ex.Message, now); return; }
			StreamWriter w = s.W;
			s.W = null;
			try { w.Dispose(); } catch { }
		}

		private void MaybeStatus(DateTime now, bool final)
		{
			if (statusPath == null) return;
			bool want = System.Threading.Interlocked.Exchange(ref statusRequest, 0) != 0 || statusPending || final;
			long lastData = System.Threading.Volatile.Read(ref lastDataTicks);
			long d = System.Threading.Interlocked.Read(ref dropped);
			double sinceStatus = (now - lastStatusUtc).TotalSeconds;
			if (!want && lastData > lastStatusUtc.Ticks && sinceStatus >= 60) want = true;
			if (!want && (d != droppedAtStatus || lost != lostAtStatus) && sinceStatus >= 5) want = true;
			if (!want) return;
			if (!final && statusPending && (now - lastStatusTryUtc).TotalMilliseconds < 1000) return;
			lastStatusTryUtc = now;
			statusPending = !WriteStatus(now, final);
			if (!statusPending) { lastStatusUtc = now; droppedAtStatus = d; lostAtStatus = lost; }
		}

		private bool WriteStatus(DateTime now, bool final)
		{
			string tmp = statusPath + ".tmp";
			try
			{
				bool anyFailed = false;
				List<string> failedNames = new List<string>(), notes = new List<string>();
				foreach (Sink s in sinks.Values)
				{
					if (!s.Failed) continue;
					anyFailed = true;
					failedNames.Add(s.Stream);
					notes.Add(s.Stream + " " + s.FailNote + " (since " + Ts(s.FailedUtc) + ", retried after " + Ts(s.RetryUtc) + ")");
				}
				if (!anyFailed && recoveredNote.Length > 0) notes.Add(recoveredNote);
				long errs = System.Threading.Interlocked.Read(ref eventErrors);
				long errTicks = System.Threading.Volatile.Read(ref lastEventErrorTicks);
				bool recentError = errTicks > 0 && now.Ticks - errTicks < TimeSpan.TicksPerMinute;
				if (errs > 0) notes.Add(errs.ToString(CI) + " event(s) not recorded, last: " + lastEventError + " (" + Ts(new DateTime(errTicks, DateTimeKind.Utc)) + ")");
				long d = System.Threading.Interlocked.Read(ref dropped);
				if (d > 0) notes.Add(d.ToString(CI) + " line(s) dropped (write queue full)");
				if (lost > 0) notes.Add(lost.ToString(CI) + " line(s) lost while a file was failing");
				if (writerNote.Length > 0) notes.Add(writerNote);
				string state = anyFailed || recentError ? "failed" : (final ? "terminated" : "running");
				long lastData = System.Threading.Volatile.Read(ref lastDataTicks);

				StringBuilder j = new StringBuilder(640);
				j.Append("{\"state\":\"").Append(state)
				 .Append("\",\"updated_utc\":\"").Append(Ts(now))
				 .Append("\",\"instrument\":\"").Append(instName)
				 .Append("\",\"output_dir\":\"").Append((OutputDir ?? "").Replace("\\", "\\\\"))
				 .Append("\",\"trades\":").Append(nTrades.ToString(CI))
				 .Append(",\"quotes\":").Append(nQuotes.ToString(CI))
				 .Append(",\"orders\":").Append(nOrders.ToString(CI))
				 .Append(",\"executions\":").Append(nExecs.ToString(CI))
				 .Append(",\"last_data_utc\":\"").Append(lastData == 0 ? "" : Ts(new DateTime(lastData, DateTimeKind.Utc)))
				 .Append("\",\"accounts\":").Append(System.Threading.Volatile.Read(ref accountsCount).ToString(CI))
				 .Append(",\"note\":\"").Append(Clean(string.Join("; ", notes.ToArray())))
				 .Append("\",\"dropped\":").Append(d.ToString(CI))
				 .Append(",\"lost\":").Append(lost.ToString(CI))
				 .Append(",\"queue\":").Append(System.Threading.Volatile.Read(ref queued).ToString(CI))
				 .Append(",\"queue_peak\":").Append(System.Threading.Volatile.Read(ref queuePeak).ToString(CI))
				 .Append(",\"failed_streams\":\"").Append(Clean(string.Join(",", failedNames.ToArray())))
				 .Append("\",\"stream_failures\":").Append(streamFailures.ToString(CI))
				 .Append(",\"event_errors\":").Append(errs.ToString(CI))
				 .Append(",\"md_other_series_skipped\":").Append(System.Threading.Interlocked.Read(ref mdSkipped).ToString(CI))
				 .Append(",\"repaired_tails\":").Append(repairedTails.ToString(CI))
				 .Append(",\"build\":\"").Append(BuildTag).Append("\"}");

				File.WriteAllText(tmp, j.ToString());
				if (File.Exists(statusPath)) File.Replace(tmp, statusPath, null);
				else File.Move(tmp, statusPath);
				statusFailures = 0;
				return true;
			}
			catch
			{
				if (++statusFailures >= 5)
				{
					try { File.Copy(tmp, statusPath, true); statusFailures = 0; return true; } catch { }
				}
				return false;
			}
		}

		private static string Clean(string s)
		{
			StringBuilder b = new StringBuilder(s.Length);
			foreach (char c in s) b.Append(c == '"' ? '\'' : c == '\\' ? '/' : c < ' ' ? ' ' : c);
			return b.ToString();
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
