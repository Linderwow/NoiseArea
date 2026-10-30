//
// NoiseAreaMomentum
// ---------------------------------------------------------------------------------------------
// Intraday momentum "noise area" (Zarattini, Aziz & Barbon, 2024, "Beat the Market"), base case.
// Passed a pre-registered test on MNQ 2019-2025 (Databento data, 1 contract, costs included).
//
//   sigma(HH:MM) = average over the previous 14 RTH days of |Close(HH:MM) / Open(9:30) - 1|
//   Upper = max(today's 9:30 open, prior RTH close) * (1 + VM * sigma)
//   Lower = min(today's 9:30 open, prior RTH close) * (1 - VM * sigma)
//   Checks only at 10:00, 10:30 ... 15:30 ET (bar closes):
//     flat : above Upper -> LONG,  below Lower -> SHORT
//     long : below Lower -> reverse SHORT; else below max(Upper, VWAP) -> FLAT
//     short: above Upper -> reverse LONG;  else above min(Lower, VWAP) -> FLAT
//   Flat at the 16:00 bar close. VWAP is RTH-only.
//
// Activity fallback (2026-10-30, off unless "Activity after N dry sessions" > 0; research\INACTIVITY_PLAN.txt / _RESULTS.txt):
// Bulenox's Master Account Agreement needs a trade at least every 5 trading days (else suspension, balance forfeited). After N
// sessions in a row in which these rules took no trade, the next session runs the same rules with VM = "Activity multiplier"
// (adopted N = 3, VM = 0: the bands are the open / prior close themselves) until a trade happens. A hosting strategy can also
// switch it on for the session (ForceActivity) when its ACCOUNT has not traded for N sessions. Check reasons say "[activity band]".
//
// DesiredPos (hidden plot) is what NoiseAreaStrategy trades. Every decision is also written to CSV:
//   Documents\NinjaTrader 8\agent_exports\NoiseArea_checks_<instrument>_<tag>.csv  (every half-hour check)
//   Documents\NinjaTrader 8\agent_exports\NoiseArea_days_<instrument>_<tag>.csv    (one row per session)
// ---------------------------------------------------------------------------------------------
#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
	public class NoiseAreaMomentum : Indicator
	{
		private TimeZoneInfo etZone;
		private readonly List<Dictionary<int, double>> history = new List<Dictionary<int, double>>();
		private Dictionary<int, double> today;
		private bool inRthPrev;
		private double open930, prevClose, lastRthClose;
		private double cumPV, cumV;
		private int pos;
		private DateTime posSince;
		private bool pendingFill;
		private int pendingPos;
		private int simPos;
		private double simEntry;
		private double dayPts, totalPts;
		private int dayTrades, totalTrades, daysTraded;
		private DateTime sessionDate = DateTime.MinValue;
		private int lastBarSeen = -1;
		private double lastUpper = double.NaN, lastLower = double.NaN, lastVwap = double.NaN;
		private readonly List<string> checkRows = new List<string>();
		private readonly List<string> dayRows = new List<string>();
		private readonly List<string> barRows = new List<string>();
		private string checksPath, daysPath, barsPath;
		private bool filesWritten, pathsReady, dayValid, etWarned, datesReady, sessWarned;
		private readonly Dictionary<DateTime, int> earlyClose = new Dictionary<DateTime, int>();   // date -> session end, minutes of day ET
		private SessionIterator sessIter;
		private string checksLivePath;
		private int dryDays;                       // completed sessions in a row in which these rules took no trade (activity fallback)
		private bool activityToday;                // this session runs with ActivityMultiplier (dryDays >= ActivityAfterDays at its start)
		private readonly List<DateTime> sessionDates = new List<DateTime>();   // ET date of every RTH session seen, in order
		private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name						= "NoiseAreaMomentum";
				Description					= "Intraday momentum noise-area bands (Zarattini/Aziz/Barbon 2024) with CSV export.";
				Calculate					= Calculate.OnBarClose;
				IsOverlay					= true;
				IsAutoScale					= false;
				DisplayInDataBox			= true;
				DrawOnPricePanel			= true;
				PaintPriceMarkers			= false;
				IsSuspendedWhileInactive	= false;
				MaximumBarsLookBack			= MaximumBarsLookBack.Infinite;

				LookbackDays		= 14;
				VolMultiplier		= 1.0;
				FirstCheckHHmm		= 1000;
				LastCheckHHmm		= 1530;
				CheckEveryMinutes	= 30;
				FlatHHmm			= 1600;
				ActivityAfterDays	= 0;
				ActivityMultiplier	= 0.0;
				ShowPanel			= true;
				SoundAlerts			= true;
				ExportCsv			= true;
				ExportBars			= true;
				FileTag				= "chart";
				EarlyCloseDates		= "2026-11-26,2026-11-27=1315,2026-12-24=1315,2027-01-18,2027-02-15,2027-05-31,2027-06-18,2027-07-05,2027-09-06,2027-11-25,2027-11-26=1315";
				MarkerDays			= 20;
				PanelPosition		= TextPosition.TopLeft;

				AddPlot(new Stroke(Brushes.DarkGray,	DashStyleHelper.Dash, 2), PlotStyle.Line, "Upper");
				AddPlot(new Stroke(Brushes.DarkGray,	DashStyleHelper.Dash, 2), PlotStyle.Line, "Lower");
				AddPlot(new Stroke(Brushes.SlateGray,	1),                       PlotStyle.Line, "VWAP");
				AddPlot(new Stroke(Brushes.Transparent,	1),                       PlotStyle.Line, "DesiredPos");
				AddPlot(new Stroke(Brushes.Transparent,	1),                       PlotStyle.Line, "Sigma");
			}
			else if (State == State.Configure)
			{
				history.Clear(); today = null; inRthPrev = false;
				open930 = prevClose = lastRthClose = 0; cumPV = cumV = 0;
				pos = 0; pendingFill = false; simPos = 0; simEntry = 0;
				dayPts = totalPts = 0; dayTrades = totalTrades = daysTraded = 0;
				sessionDate = DateTime.MinValue; lastBarSeen = -1;
				checkRows.Clear(); dayRows.Clear(); barRows.Clear(); filesWritten = false; pathsReady = false; dayValid = false; datesReady = false;
				earlyClose.Clear();
				dryDays = 0; activityToday = false; sessionDates.Clear();
			}
			else if (State == State.DataLoaded)
			{
				try   { etZone = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time"); }
				catch { etZone = null; }
				try   { sessIter = new SessionIterator(Bars); }
				catch { sessIter = null; }
			}
			else if (State == State.Transition || State == State.Terminated)
			{
				WriteBufferedFiles();
			}
		}

		#region Helpers
		// Export paths are built on the first bar, not in DataLoaded: a hosting strategy assigns FileTag after construction.
		private void EnsurePaths()
		{
			if (pathsReady) return;
			pathsReady = true;
			if (!ExportCsv) return;
			try
			{
				string dir = Path.Combine(Core.Globals.UserDataDir, "agent_exports");
				Directory.CreateDirectory(dir);
				string inst = Instrument != null ? Instrument.MasterInstrument.Name : "inst";
				string tag  = string.IsNullOrWhiteSpace(FileTag) ? "chart" : FileTag;
				checksPath = Path.Combine(dir, "NoiseArea_checks_" + inst + "_" + tag + ".csv");
				checksLivePath = Path.Combine(dir, "NoiseArea_checks_live_" + inst + "_" + tag + ".csv");
				daysPath   = Path.Combine(dir, "NoiseArea_days_" + inst + "_" + tag + ".csv");
				barsPath   = ExportBars ? Path.Combine(dir, "NoiseArea_bars_" + inst + "_" + tag + ".csv") : null;
			}
			catch (Exception ex) { Print("NoiseAreaMomentum: cannot prepare export folder: " + ex.Message); ExportCsv = false; }
		}
		/// <summary>Writes the buffered historical rows now (a hosting strategy calls this at State.Transition).</summary>
		public void FlushExports() { EnsurePaths(); WriteBufferedFiles(); }
		/// <summary>Number of complete sessions in the sigma history (the strategy warns when fewer than LookbackDays are loaded).</summary>
		public int SessionsOfHistory { get { return history.Count; } }
		/// <summary>Set by a hosting strategy (real time): the account has not traded for ActivityAfterDays sessions, so this session uses
		/// the activity band even if these rules' own count says otherwise. Ignored while ActivityAfterDays is 0.</summary>
		[Browsable(false)] [XmlIgnore] public bool ForceActivity { get; set; }
		/// <summary>True when the current session decides with ActivityMultiplier.</summary>
		[Browsable(false)] [XmlIgnore] public bool ActivityNow { get { return ActivityAfterDays > 0 && (activityToday || ForceActivity); } }
		/// <summary>Completed sessions in a row in which these rules took no trade.</summary>
		[Browsable(false)] [XmlIgnore] public int DryDays { get { return dryDays; } }
		/// <summary>RTH sessions seen after `date` and before the current one (the hosting strategy's account-side dry count).</summary>
		public int SessionsAfter(DateTime date)
		{
			int n = 0; DateTime cur = sessionDates.Count > 0 ? sessionDates[sessionDates.Count - 1] : DateTime.MaxValue;
			foreach (DateTime d in sessionDates) if (d > date.Date && d < cur) n++;
			return n;
		}
		// Parsed on the first bar, not in Configure: a hosting strategy assigns EarlyCloseDates after construction.
		private void EnsureDates()
		{
			if (datesReady) return;
			datesReady = true; earlyClose.Clear();
			foreach (string part in (EarlyCloseDates ?? "").Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries))
			{
				string[] kv = part.Trim().Split('='); DateTime d; int hhmm = 1300;
				if (kv.Length > 1 && !int.TryParse(kv[1], NumberStyles.Integer, CI, out hhmm)) { hhmm = 1300; Print("NoiseAreaMomentum: bad early-close time in '" + part + "', using 13:00"); }
				if (DateTime.TryParseExact(kv[0], "yyyy-MM-dd", CI, DateTimeStyles.None, out d)) earlyClose[d.Date] = HHmmToMin(hhmm);
			}
		}
		// Minute-of-day (ET) at which the Trading Hours template ends the session containing this bar, or -1 if unknown.
		private int SessionEndMinute(DateTime barTime, DateTime et)
		{
			if (sessIter == null) return -1;
			try
			{
				sessIter.GetNextSession(barTime, true);
				DateTime endEt = ToEt(sessIter.ActualSessionEnd);
				if (endEt.Date != et.Date) return -1;
				return endEt.Hour * 60 + endEt.Minute;
			}
			catch (Exception ex)
			{
				if (!sessWarned) { sessWarned = true; Print("NoiseAreaMomentum: session lookup failed, using the early-close date list only: " + ex.Message); }
				return -1;
			}
		}
		private DateTime ToEt(DateTime t)
		{
			if (etZone == null) return t;
			try { return TimeZoneInfo.ConvertTime(DateTime.SpecifyKind(t, DateTimeKind.Unspecified), Core.Globals.GeneralOptions.TimeZoneInfo, etZone); }
			catch (Exception ex)
			{
				if (!etWarned) { etWarned = true; Print("NoiseAreaMomentum: ET conversion failed, using chart time: " + ex.Message); }
				return t;
			}
		}
		private int EarlyCloseMinute(DateTime et) { int v; return earlyClose.TryGetValue(et.Date, out v) ? v : -1; }
		private static int HHmmToMin(int hhmm) { return (hhmm / 100) * 60 + (hhmm % 100); }
		private bool IsCheck(int m)
		{
			int a = HHmmToMin(FirstCheckHHmm), b = HHmmToMin(LastCheckHHmm), step = Math.Max(5, CheckEveryMinutes);
			return m >= a && m <= b && (m - a) % step == 0;
		}
		// sigma(HH:MM) = average |Close(HH:MM)/Open(9:30) - 1| over the previous LookbackDays sessions that HAVE that time slot
		// (an early-close session has no afternoon slots); at least half of the sessions must have it, otherwise no decision.
		private bool TrySigma(int m, out double sigma)
		{
			sigma = double.NaN;
			if (history.Count < LookbackDays) return false;
			double sum = 0; int n = 0;
			for (int k = history.Count - LookbackDays; k < history.Count; k++)
			{
				double v;
				if (history[k].TryGetValue(m, out v)) { sum += v; n++; }
			}
			if (n < Math.Max(1, (LookbackDays + 1) / 2)) return false;
			sigma = sum / n;
			return true;
		}
		private string Px(double p) { return Instrument.MasterInstrument.FormatPrice(p); }
		private static string PosName(int p) { return p > 0 ? "LONG" : p < 0 ? "SHORT" : "FLAT"; }
		#endregion

		protected override void OnBarUpdate()
		{
			if (CurrentBar == lastBarSeen) return;
			lastBarSeen = CurrentBar;
			EnsurePaths(); EnsureDates();
			if (State == State.Realtime && !filesWritten) WriteBufferedFiles();

			DateTime et = ToEt(Time[0]);
			int m = et.Hour * 60 + et.Minute;
			bool inRth = m > 9 * 60 + 30 && m <= 16 * 60;
			// Partial sessions (CME equity index: 13:00 ET on US cash-market holidays, 13:15 ET the day after Thanksgiving and on
			// Dec 24): be flat on the bar that closes 5 minutes before the session end and make no decision at or after it.
			// Source 1: the Trading Hours template (SessionIterator). Source 2: the EarlyCloseDates list (backup, 12:55 flat).
			int flatMin = HHmmToMin(FlatHHmm);
			int listEnd = EarlyCloseMinute(et);
			bool early = listEnd > 0;
			if (early) flatMin = Math.Min(flatMin, listEnd - 5);
			if (inRth)
			{
				int endMin = SessionEndMinute(Time[0], et);
				if (endMin > 9 * 60 + 30 && endMin < 16 * 60) { flatMin = Math.Min(flatMin, endMin - 5); early = true; }
			}
			bool lastBar = false;
			try { lastBar = Bars.IsLastBarOfSession; } catch { }
			if (barsPath != null)
				AddRow(barRows, barsPath, string.Format(CI, "{0:yyyy-MM-dd HH:mm:ss},{1},{2},{3},{4},{5}", et, Open[0], High[0], Low[0], Close[0], Volume[0]));

			// simulated fill (gross-point stats only) at this bar's open for the decision made on the previous bar
			if (pendingFill)
			{
				double px = Open[0];
				if (simPos != 0) { double pts = (px - simEntry) * simPos; dayPts += pts; totalPts += pts; }
				simPos = pendingPos;
				if (simPos != 0) { simEntry = px; dayTrades++; totalTrades++; }
				pendingFill = false;
			}

			if (inRth && (!inRthPrev || et.Date != sessionDate)) StartSession(et, m);

			if (inRth && today != null)
			{
				lastRthClose = Close[0];
				today[m] = Math.Abs(Close[0] / open930 - 1.0);

				double tp = (High[0] + Low[0] + Close[0]) / 3.0, v = Math.Max(0.0, (double)Volume[0]);
				cumPV += tp * v; cumV += v;
				double vwap = cumV > 0 ? cumPV / cumV : Close[0];
				Values[2][0] = vwap; lastVwap = vwap;

				double sig;
				if (prevClose > 0 && TrySigma(m, out sig))
				{
					double vm = ActivityNow ? ActivityMultiplier : VolMultiplier;
					double ub = Math.Max(open930, prevClose) * (1 + vm * sig);
					double lb = Math.Min(open930, prevClose) * (1 - vm * sig);
					Values[0][0] = ub; Values[1][0] = lb; Values[4][0] = sig;
					lastUpper = ub; lastLower = lb;
					if (dayValid && !lastBar && m < flatMin && IsCheck(m)) Check(et, m, ub, lb, sig, vwap);
				}
				if ((m >= flatMin || lastBar) && pos != 0) Decide(0, early ? "flat: early close" : "flat at close", et);
			}
			else if (!inRth && pos != 0)
			{
				// Safety net for early-close days (13:00 ET): never carry a desired position outside RTH.
				Decide(0, "flat outside RTH (early close)", et);
			}

			Values[3][0] = pos;
			inRthPrev = inRth;
			if (ShowPanel && (State == State.Realtime || CurrentBar >= Bars.Count - 2)) DrawPanel(et, m, inRth);
		}

		private void StartSession(DateTime et, int firstMin)
		{
			if (today != null && today.Count > 0)
			{
				if (sessionDate != DateTime.MinValue)
				{
					string row = string.Format(CI, "{0:yyyy-MM-dd},{1},{2},{3},{4:0.00}", sessionDate, open930, prevClose, dayTrades, dayPts);
					AddRow(dayRows, daysPath, row);
					if (dayTrades > 0) daysTraded++;
					// Dry count for the activity fallback: a session with a trade resets it, a decidable one without a trade adds one,
					// a warm-up session (not enough history for a decision yet) leaves it at 0 - the first decidable session starts clean.
					dryDays = dayTrades > 0 ? 0 : history.Count >= LookbackDays && prevClose > 0 ? dryDays + 1 : 0;
				}
				// Only complete sessions (first bar 9:35, at least 30 bars) count as history, as in the validated reference.
				if (dayValid && today.Count >= 30)
				{
					history.Add(today);
					while (history.Count > LookbackDays) history.RemoveAt(0);
					prevClose = lastRthClose;
				}
			}
			today = new Dictionary<int, double>();
			sessionDate = et.Date;
			sessionDates.Add(sessionDate);
			activityToday = ActivityAfterDays > 0 && dryDays >= ActivityAfterDays;
			dayValid = firstMin == 9 * 60 + 35;
			if (!dayValid) Print(string.Format("NoiseAreaMomentum: {0:yyyy-MM-dd} first RTH bar at {1:00}:{2:00} ET, not 09:35 - no trades today, day not used as history", et, firstMin / 60, firstMin % 60));
			open930 = Open[0];
			cumPV = cumV = 0;
			dayPts = 0; dayTrades = 0;
			pos = 0;
		}

		private void Check(DateTime et, int m, double ub, double lb, double sig, double vwap)
		{
			double p = Close[0];
			int before = pos, after = pos;
			string reason = "hold";
			if (pos == 0)
			{
				if (p > ub) { after = 1;  reason = "above upper band"; }
				else if (p < lb) { after = -1; reason = "below lower band"; }
			}
			else if (pos == 1)
			{
				if (p < lb) { after = -1; reason = "reverse: below lower band"; }
				else if (p < Math.Max(ub, vwap)) { after = 0; reason = "exit: below max(upper|VWAP)"; }
			}
			else
			{
				if (p > ub) { after = 1; reason = "reverse: above upper band"; }
				else if (p > Math.Min(lb, vwap)) { after = 0; reason = "exit: above min(lower|VWAP)"; }
			}
			if (ActivityNow) reason += activityToday ? " [activity band]" : " [activity band: account]";
			string row = string.Format(CI, "{0:yyyy-MM-dd},{0:HH:mm},{1},{2:0.00},{3:0.00},{4:0.000000},{5:0.00},{6},{7},{8}",
				et, p, ub, lb, sig, vwap, before, after, reason);
			AddRow(checkRows, checksPath, row);
			AppendLive(row);
			if (after != before) Decide(after, reason, et);
		}

		private void Decide(int newPos, string reason, DateTime et)
		{
			int old = pos;
			pos = newPos; posSince = et;
			pendingFill = true; pendingPos = newPos;

			bool draw = MarkerDays <= 0 || Time[0] >= Bars.GetTime(Bars.Count - 1).AddDays(-MarkerDays);
			if (draw)
			{
				try
				{
					double off = Math.Max(TickSize * 8, (High[0] - Low[0]) * 0.8);
					string tag = "NA" + CurrentBar;
					if (newPos > 0)      Draw.ArrowUp(this, tag, false, 0, Low[0] - off, Brushes.LimeGreen);
					else if (newPos < 0) Draw.ArrowDown(this, tag, false, 0, High[0] + off, Brushes.OrangeRed);
					else                 Draw.Diamond(this, tag, false, 0, Close[0], Brushes.Silver);
					Draw.Text(this, tag + "t", false, newPos == 0 ? "flat" : PosName(newPos), 0,
						newPos > 0 ? Low[0] - off * 2 : High[0] + off * 2, 0, newPos > 0 ? Brushes.LimeGreen : newPos < 0 ? Brushes.OrangeRed : Brushes.Silver,
						new SimpleFont("Consolas", 9), System.Windows.TextAlignment.Center, Brushes.Transparent, Brushes.Transparent, 0);
				}
				catch { }
			}
			if (State == State.Realtime && SoundAlerts)
			{
				string msg = string.Format("{0} noise-area: {1} -> {2} ({3}) at {4}", Instrument.MasterInstrument.Name, PosName(old), PosName(newPos), reason, Px(Close[0]));
				try { Alert("NA" + CurrentBar, Priority.High, msg, Path.Combine(Core.Globals.InstallDir, "sounds", "Alert2.wav"), 10, Brushes.Black,
						newPos > 0 ? Brushes.LimeGreen : newPos < 0 ? Brushes.OrangeRed : Brushes.Silver); } catch { }
			}
		}

		private void DrawPanel(DateTime et, int m, bool inRth)
		{
			var sb = new StringBuilder();
			sb.AppendLine("Noise-area momentum (Zarattini/Aziz/Barbon)");
			sb.AppendLine(string.Format("Position: {0}{1}", PosName(pos), pos != 0 ? " since " + posSince.ToString("HH:mm") : ""));
			if (!double.IsNaN(lastUpper))
				sb.AppendLine(string.Format("Upper {0}   Lower {1}   VWAP {2}", Px(lastUpper), Px(lastLower), Px(lastVwap)));
			if (history.Count < LookbackDays) sb.AppendLine(string.Format("Warming up: {0}/{1} sessions of history", history.Count, LookbackDays));
			if (ActivityNow) sb.AppendLine(string.Format(CI, "Activity band today (VM {0:0.##}): {1}", ActivityMultiplier, activityToday ? dryDays + " sessions without a trade" : "the account has not traded for " + ActivityAfterDays + "+ sessions"));
			int a = HHmmToMin(FirstCheckHHmm), b = HHmmToMin(LastCheckHHmm), step = Math.Max(5, CheckEveryMinutes);
			int next = -1;
			for (int c = a; c <= b; c += step) if (c > m) { next = c; break; }
			sb.AppendLine(inRth && next > 0 ? string.Format("Next check {0:00}:{1:00} ET", next / 60, next % 60) : "No more checks today");
			sb.Append(string.Format(CI, "Gross since load (no costs, 1 ct): {0:+0.0;-0.0} pts over {1} trades, {2} trading days", totalPts, totalTrades, daysTraded));
			Draw.TextFixed(this, "NAPanel", sb.ToString(), PanelPosition, Brushes.White, new SimpleFont("Consolas", 10), Brushes.Transparent, Brushes.Black, 60);
		}

		#region CSV
		// Append-only record of decisions made in real time (the main checks file is rewritten from the historical replay on every start).
		private void AppendLive(string row)
		{
			if (!ExportCsv || checksLivePath == null || State != State.Realtime) return;
			try
			{
				if (!File.Exists(checksLivePath)) File.WriteAllText(checksLivePath, "date,time_et,price,upper,lower,sigma,vwap,pos_before,pos_after,reason" + Environment.NewLine);
				File.AppendAllText(checksLivePath, row + Environment.NewLine);
			}
			catch (Exception ex) { Print("NoiseAreaMomentum live CSV append failed: " + ex.Message); }
		}
		private void AddRow(List<string> buffer, string path, string row)
		{
			if (!ExportCsv || path == null) return;
			if (State == State.Realtime && filesWritten)
			{
				try { File.AppendAllText(path, row + Environment.NewLine); } catch (Exception ex) { Print("NoiseAreaMomentum CSV append failed: " + ex.Message); }
			}
			else buffer.Add(row);
		}

		private void WriteBufferedFiles()
		{
			if (!ExportCsv || filesWritten || checksPath == null) return;
			try
			{
				var c = new List<string> { "date,time_et,price,upper,lower,sigma,vwap,pos_before,pos_after,reason" };
				c.AddRange(checkRows);
				File.WriteAllLines(checksPath, c);
				var d = new List<string> { "date,open_930,prior_close,trades,gross_points" };
				d.AddRange(dayRows);
				File.WriteAllLines(daysPath, d);
				if (barsPath != null)
				{
					var bl = new List<string> { "time_et,open,high,low,close,volume" };
					bl.AddRange(barRows);
					File.WriteAllLines(barsPath, bl);
				}
				filesWritten = true;
				Print(string.Format("NoiseAreaMomentum: wrote {0} checks and {1} days to {2}", checkRows.Count, dayRows.Count, Path.GetDirectoryName(checksPath)));
				checkRows.Clear(); dayRows.Clear(); barRows.Clear();
			}
			catch (Exception ex) { Print("NoiseAreaMomentum CSV write failed: " + ex.Message); }
		}
		#endregion

		#region Properties
		[Browsable(false)] [XmlIgnore] public Series<double> Upper      { get { return Values[0]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> Lower      { get { return Values[1]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> Vwap       { get { return Values[2]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> DesiredPos { get { return Values[3]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> Sigma      { get { return Values[4]; } }

		[Range(2, 100)]   [Display(Name = "Lookback days (sigma)", GroupName = "1. Rules (published base case)", Order = 1)] public int LookbackDays { get; set; }
		[Range(0.1, 5.0)] [Display(Name = "Volatility multiplier", GroupName = "1. Rules (published base case)", Order = 2)] public double VolMultiplier { get; set; }
		[Range(0, 2359)]  [Display(Name = "First check (HHmm ET)", GroupName = "1. Rules (published base case)", Order = 3)] public int FirstCheckHHmm { get; set; }
		[Range(0, 2359)]  [Display(Name = "Last check (HHmm ET)", GroupName = "1. Rules (published base case)", Order = 4)] public int LastCheckHHmm { get; set; }
		[Range(5, 120)]   [Display(Name = "Check every (minutes)", GroupName = "1. Rules (published base case)", Order = 5)] public int CheckEveryMinutes { get; set; }
		[Range(0, 2359)]  [Display(Name = "Flat at (HHmm ET)", GroupName = "1. Rules (published base case)", Order = 6)] public int FlatHHmm { get; set; }
		[Range(0, 10)]    [Display(Name = "Activity after N dry sessions (0 = off)", Description = "Bulenox inactivity rule (a trade every 5 trading days): after N sessions without a trade the next session uses the activity multiplier until a trade happens. Adopted: 3 on Bulenox accounts, 0 elsewhere.", GroupName = "1b. Activity fallback (Bulenox)", Order = 1)] public int ActivityAfterDays { get; set; }
		[Range(0.0, 5.0)] [Display(Name = "Activity multiplier", Description = "Volatility multiplier on activity sessions (adopted 0: the bands are the open / prior close).", GroupName = "1b. Activity fallback (Bulenox)", Order = 2)] public double ActivityMultiplier { get; set; }

		[Display(Name = "Show panel", GroupName = "2. Display", Order = 1)] public bool ShowPanel { get; set; }
		[Display(Name = "Panel position", GroupName = "2. Display", Order = 2)] public TextPosition PanelPosition { get; set; }
		[Range(0, 3000)] [Display(Name = "Draw markers for last N days (0 = all)", GroupName = "2. Display", Order = 3)] public int MarkerDays { get; set; }
		[Display(Name = "Sound + popup on position change (live)", GroupName = "2. Display", Order = 4)] public bool SoundAlerts { get; set; }

		[Display(Name = "Export CSV (agent_exports folder)", GroupName = "3. Export", Order = 1)] public bool ExportCsv { get; set; }
		[Display(Name = "File tag", GroupName = "3. Export", Order = 2)] public string FileTag { get; set; }
		[Display(Name = "Export bars CSV (for independent checks)", GroupName = "3. Export", Order = 3)] public bool ExportBars { get; set; }
		[Display(Name = "Early-close dates (yyyy-MM-dd or yyyy-MM-dd=HHmm, comma separated)", Description = "Backup list of partial sessions: flat on the bar that closes 5 minutes before the session end (13:00 ET unless =HHmm given), no decisions after. The Trading Hours template is used first when it knows the holiday.", GroupName = "1. Rules (published base case)", Order = 7)] public string EarlyCloseDates { get; set; }
		#endregion
	}
}

#region NinjaScript generated code. Neither change nor remove.

namespace NinjaTrader.NinjaScript.Indicators
{
	public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
	{
		private NoiseAreaMomentum[] cacheNoiseAreaMomentum;
		public NoiseAreaMomentum NoiseAreaMomentum()
		{
			return NoiseAreaMomentum(Input);
		}

		public NoiseAreaMomentum NoiseAreaMomentum(ISeries<double> input)
		{
			if (cacheNoiseAreaMomentum != null)
				for (int idx = 0; idx < cacheNoiseAreaMomentum.Length; idx++)
					if (cacheNoiseAreaMomentum[idx] != null &&  cacheNoiseAreaMomentum[idx].EqualsInput(input))
						return cacheNoiseAreaMomentum[idx];
			return CacheIndicator<NoiseAreaMomentum>(new NoiseAreaMomentum(), input, ref cacheNoiseAreaMomentum);
		}
	}
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
	public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
	{
		public Indicators.NoiseAreaMomentum NoiseAreaMomentum()
		{
			return indicator.NoiseAreaMomentum(Input);
		}

		public Indicators.NoiseAreaMomentum NoiseAreaMomentum(ISeries<double> input )
		{
			return indicator.NoiseAreaMomentum(input);
		}
	}
}

namespace NinjaTrader.NinjaScript.Strategies
{
	public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
	{
		public Indicators.NoiseAreaMomentum NoiseAreaMomentum()
		{
			return indicator.NoiseAreaMomentum(Input);
		}

		public Indicators.NoiseAreaMomentum NoiseAreaMomentum(ISeries<double> input )
		{
			return indicator.NoiseAreaMomentum(input);
		}
	}
}

#endregion
