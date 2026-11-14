#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows;
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
	public enum MarketStructureBreakMode { Close, Wicks }
	public enum MarketStructurePlanSession { Bulenox24h, RTH }

	public class MarketStructure : Indicator
	{
		private double topPrice = double.NaN, btmPrice = double.NaN;
		private int topBar = -1, btmBar = -1;
		private bool topCrossed = true, btmCrossed = true;
		private int originBar = -1;
		private double originPrice = double.NaN;
		private readonly List<int> tlBars = new List<int>();
		private readonly List<double> tlPrices = new List<double>();
		private int bias;
		private int biasSinceBar = -1;
		private int lastSignal;
		private int lastBreakBar = -1;
		private double lastBreakLevel = double.NaN;
		private int legs;
		private ATR atr;
		private readonly List<string> rows = new List<string>();
		private string exportPath;
		private bool filesWritten;
		private TimeZoneInfo etZone;
		private bool etWarned;
		private int planSide, planBar = -1;
		private double planStop = double.NaN;
		private int tradeSide, tradeBreakBar = -1, tradeEntryBar = -1;
		private double tradeEntry, tradeStop, tradeTarget;
		private int planTrades, planTargets;
		private double planSumR;
		private int nowMinute;
		private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name						= "MarketStructure";
				Description					= "Swing-point market structure: BOS / CHoCH breaks, the levels that would change it, and a trade plan per break (entry next open, stop at the opposite pending swing, target in R). Non-repainting. The plan was tested on 16 years (research\\MSBTRADE_RESULTS.txt) and does not make money; the panel prints the measured result.";
				Calculate					= Calculate.OnBarClose;
				IsOverlay					= true;
				IsAutoScale					= false;
				DisplayInDataBox			= true;
				DrawOnPricePanel			= true;
				PaintPriceMarkers			= false;
				IsSuspendedWhileInactive	= true;
				BarsRequiredToPlot			= 11;

				Strength		= 5;
				BreakOn			= MarketStructureBreakMode.Close;
				ShowDrawings	= false;
				ShowTrendLine	= true;
				ShowSwings		= true;
				ShowBreakLines	= true;
				ShowArrows		= true;
				ShowLevels		= true;
				ShowPanel		= true;
				PanelPosition	= TextPosition.TopLeft;
				HistoryBars		= 3000;
				SoundAlert		= false;
				BullBrush		= Brushes.LimeGreen;
				BearBrush		= Brushes.OrangeRed;
				ShowTradePlan	= true;
				TargetR			= 2.0;
				PlanSession		= MarketStructurePlanSession.Bulenox24h;
				ExportCsv		= true;
				FileTag			= "chart";

				AddPlot(new Stroke(Brushes.Transparent, 1),		PlotStyle.Line, "Bias");
				AddPlot(new Stroke(Brushes.Transparent, 1),		PlotStyle.Line, "Signal");
				AddPlot(new Stroke(Brushes.DimGray, 1),			PlotStyle.Hash, "StructureHigh");
				AddPlot(new Stroke(Brushes.DimGray, 1),			PlotStyle.Hash, "StructureLow");
			}
			else if (State == State.Configure)
			{
				Calculate = Calculate.OnBarClose;
				BarsRequiredToPlot = 2 * Strength + 1;
				topPrice = btmPrice = double.NaN; topBar = btmBar = -1; topCrossed = btmCrossed = true;
				originBar = -1; originPrice = double.NaN; tlBars.Clear(); tlPrices.Clear();
				bias = 0; biasSinceBar = -1; lastSignal = 0; lastBreakBar = -1; lastBreakLevel = double.NaN; legs = 0;
				rows.Clear(); exportPath = null; filesWritten = false;
				planSide = 0; planBar = -1; planStop = double.NaN; tradeSide = 0; tradeBreakBar = tradeEntryBar = -1;
				planTrades = planTargets = 0; planSumR = 0; nowMinute = 0;
				if (!ShowLevels || !ShowDrawings) { Plots[2].Brush = Brushes.Transparent; Plots[3].Brush = Brushes.Transparent; }
			}
			else if (State == State.DataLoaded)
			{
				atr = ATR(14);
				try   { etZone = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time"); }
				catch { etZone = null; }
				if (ExportCsv)
				{
					try
					{
						string dir = Path.Combine(Core.Globals.UserDataDir, "agent_exports");
						Directory.CreateDirectory(dir);
						string inst = Instrument.MasterInstrument.Name.Replace(" ", "");
						string per = BarsPeriod.Value.ToString(CI) + BarsPeriod.BarsPeriodType.ToString();
						string tag = string.IsNullOrEmpty(FileTag) ? "chart" : FileTag;
						exportPath = Path.Combine(dir, "MarketStructure_breaks_" + inst + "_" + per + "_" + tag + ".csv");
					}
					catch (Exception ex) { Print("MarketStructure export path failed: " + ex.Message); exportPath = null; }
				}
			}
			else if (State == State.Transition || State == State.Terminated)
			{
				WriteBufferedFile();
			}
		}

		protected override void OnBarUpdate()
		{
			int n = Strength;
			Bias[0] = bias; Signal[0] = 0;
			StructureHigh[0] = topCrossed ? double.NaN : topPrice;
			StructureLow[0]  = btmCrossed ? double.NaN : btmPrice;
			if (State == State.Realtime && !filesWritten) WriteBufferedFile();
			if (CurrentBar < 2 * n) return;

			bool draw = State != State.Historical || HistoryBars <= 0 || CurrentBar >= Count - 1 - HistoryBars;
			bool dr = draw && ShowDrawings;
			double off = atr != null && atr.IsValidDataPoint(0) ? atr[0] * 0.3 : TickSize * 4;

			bool isTop = true, isBtm = true;
			double hc = High[n], lc = Low[n];
			for (int k = 0; k <= 2 * n; k++)
			{
				if (k == n) continue;
				if (k < n) { if (High[k] > hc) isTop = false; if (Low[k] < lc) isBtm = false; }
				else       { if (High[k] >= hc) isTop = false; if (Low[k] <= lc) isBtm = false; }
				if (!isTop && !isBtm) break;
			}
			if (isTop)
			{
				topPrice = hc; topBar = CurrentBar - n; topCrossed = false;
				if (ShowSwings && dr) Draw.Dot(this, "msT" + topBar, false, n, hc + off * 0.5, BearBrush);
				if (bias < 0) AddStructureSwing(topBar, hc, hc > originPrice);
			}
			if (isBtm)
			{
				btmPrice = lc; btmBar = CurrentBar - n; btmCrossed = false;
				if (ShowSwings && dr) Draw.Dot(this, "msB" + btmBar, false, n, lc - off * 0.5, BullBrush);
				if (bias > 0) AddStructureSwing(btmBar, lc, lc < originPrice);
			}

			double up = BreakOn == MarketStructureBreakMode.Wicks ? High[0] : Close[0];
			double dn = BreakOn == MarketStructureBreakMode.Wicks ? Low[0]  : Close[0];
			bool breakUp = !topCrossed && up > topPrice;
			bool breakDn = !btmCrossed && dn < btmPrice;
			if (breakUp && breakDn)
			{
				if (Close[0] >= Open[0]) breakDn = false; else breakUp = false;
				topCrossed = btmCrossed = true;
			}
			int sig = 0;
			if (breakUp)
			{
				sig = bias == 1 ? 1 : 2; topCrossed = true;
				if (bias != 1) { bias = 1; biasSinceBar = CurrentBar; legs = 1; SetOrigin(btmBar, btmPrice); } else legs++;
				Break(sig, topBar, topPrice, dr, off);
			}
			else if (breakDn)
			{
				sig = bias == -1 ? -1 : -2; btmCrossed = true;
				if (bias != -1) { bias = -1; biasSinceBar = CurrentBar; legs = 1; SetOrigin(topBar, topPrice); } else legs++;
				Break(sig, btmBar, btmPrice, dr, off);
			}

			Bias[0] = bias; Signal[0] = sig;
			StructureHigh[0] = topCrossed ? double.NaN : topPrice;
			StructureLow[0]  = btmCrossed ? double.NaN : btmPrice;
			Plan(sig, draw && ShowTradePlan, off);
			bool last = State != State.Historical || CurrentBar >= Count - 2;
			if (ShowTrendLine && draw && (isTop || isBtm || sig != 0 || last)) TrendLine();
			if (ShowPanel && last) Panel();
		}

		private DateTime ToEt(DateTime t)
		{
			if (etZone == null) return t;
			try { return TimeZoneInfo.ConvertTime(DateTime.SpecifyKind(t, DateTimeKind.Unspecified), Core.Globals.GeneralOptions.TimeZoneInfo, etZone); }
			catch (Exception ex)
			{
				if (!etWarned) { etWarned = true; Print("MarketStructure: ET conversion failed, using chart time: " + ex.Message); }
				return t;
			}
		}

		private static DateTime TradingDay(DateTime et) { return et.AddMinutes(419).Date; }

		private bool IsPlanTime(int m)
		{
			return PlanSession == MarketStructurePlanSession.RTH ? m >= 575 && m <= 930 : m >= 1085 || m <= 990;
		}

		private bool IsFlatTime(int m)
		{
			return PlanSession == MarketStructurePlanSession.RTH ? m >= 960 && m < 1080 : m >= 1015 && m < 1080;
		}

		private double TargetFrom(double entry, double stop, int side)
		{
			double r = side * (entry - stop);
			return entry + side * Math.Max(1, Math.Round(TargetR * r / TickSize)) * TickSize;
		}

		private void Plan(int sig, bool dp, double off)
		{
			DateTime et = ToEt(Time[0]);
			bool newDay = TradingDay(et) != TradingDay(ToEt(Time[1]));
			int m = et.Hour * 60 + et.Minute;
			nowMinute = m;

			if (planSide != 0)
			{
				int side = planSide; planSide = 0;
				double r = side * (Open[0] - planStop);
				if (newDay || r <= 0) ClearRays(planBar);
				else
				{
					tradeSide = side; tradeEntry = Open[0]; tradeStop = planStop; tradeBreakBar = planBar; tradeEntryBar = CurrentBar;
					tradeTarget = TargetFrom(tradeEntry, tradeStop, side);
					if (dp) DrawRays(tradeBreakBar, tradeEntry, tradeStop, tradeTarget, false);
				}
			}

			if (tradeSide != 0 && newDay && CurrentBar > tradeEntryBar) CloseTrade(Close[1], "out", 1, dp, off);

			if (tradeSide != 0)
			{
				double px = double.NaN; string kind = null;
				if (tradeSide > 0)
				{
					if (Open[0] <= tradeStop) { px = Open[0]; kind = "SL"; }
					else if (Low[0] <= tradeStop) { px = tradeStop; kind = "SL"; }
					else if (High[0] >= tradeTarget + TickSize) { px = tradeTarget; kind = "TP"; }
				}
				else
				{
					if (Open[0] >= tradeStop) { px = Open[0]; kind = "SL"; }
					else if (High[0] >= tradeStop) { px = tradeStop; kind = "SL"; }
					else if (Low[0] <= tradeTarget - TickSize) { px = tradeTarget; kind = "TP"; }
				}
				if (kind == null && IsFlatTime(m)) { px = Close[0]; kind = "out"; }
				if (kind != null) CloseTrade(px, kind, 0, dp, off);
			}

			if (tradeSide == 0 && planSide == 0 && sig != 0 && IsPlanTime(m))
			{
				int side = sig > 0 ? 1 : -1;
				double lv = side > 0 ? (btmCrossed ? double.NaN : btmPrice) : (topCrossed ? double.NaN : topPrice);
				if (!double.IsNaN(lv))
				{
					planSide = side; planStop = lv; planBar = CurrentBar;
					if (dp && side * (Close[0] - lv) > 0) DrawRays(CurrentBar, Close[0], lv, TargetFrom(Close[0], lv, side), true);
				}
			}
		}

		private void DrawRays(int breakBar, double entry, double stop, double target, bool provisional)
		{
			string id = breakBar.ToString(CI);
			int ago = CurrentBar - breakBar;
			Brush eb = Brushes.Silver;
			Draw.Ray(this, "msPRe" + id, false, ago + 1, entry, ago, entry, eb, DashStyleHelper.Solid, 1);
			Draw.Ray(this, "msPRs" + id, false, ago + 1, stop, ago, stop, BearBrush, DashStyleHelper.Dash, 2);
			Draw.Ray(this, "msPRt" + id, false, ago + 1, target, ago, target, BullBrush, DashStyleHelper.Dash, 2);
			SimpleFont f = new SimpleFont("Arial", 10) { Bold = true };
			Draw.Text(this, "msPLe" + id, false, string.Format(CI, provisional ? "Entry ~{0:0.00} (next open)" : "Entry {0:0.00}", entry), ago, entry, 9, eb, f, TextAlignment.Left, Brushes.Transparent, Brushes.Transparent, 0);
			Draw.Text(this, "msPLs" + id, false, string.Format(CI, "SL {0:0.00}", stop), ago, stop, 9, BearBrush, f, TextAlignment.Left, Brushes.Transparent, Brushes.Transparent, 0);
			Draw.Text(this, "msPLt" + id, false, string.Format(CI, "TP {0:0.00} ({1:0.#}R)", target, TargetR), ago, target, 9, BullBrush, f, TextAlignment.Left, Brushes.Transparent, Brushes.Transparent, 0);
		}

		private void ClearRays(int breakBar)
		{
			string id = breakBar.ToString(CI);
			foreach (string p in new[] { "msPRe", "msPRs", "msPRt", "msPLe", "msPLs", "msPLt" }) RemoveDrawObject(p + id);
		}

		private void CloseTrade(double px, string kind, int barsAgo, bool dp, double off)
		{
			double risk = Math.Abs(tradeEntry - tradeStop);
			double r = risk > 0 ? tradeSide * (px - tradeEntry) / risk : 0;
			planTrades++; if (kind == "TP") planTargets++; planSumR += r;
			if (dp)
			{
				ClearRays(tradeBreakBar);
				string id = tradeBreakBar.ToString(CI);
				int a0 = CurrentBar - tradeEntryBar, a1 = barsAgo;
				if (a0 < a1) a0 = a1;
				Draw.Line(this, "msPE" + id, false, a0, tradeEntry, a1, tradeEntry, Brushes.Silver, DashStyleHelper.Solid, 1);
				Draw.Line(this, "msPS" + id, false, a0, tradeStop, a1, tradeStop, BearBrush, DashStyleHelper.Dash, 1);
				Draw.Line(this, "msPT" + id, false, a0, tradeTarget, a1, tradeTarget, BullBrush, DashStyleHelper.Dash, 1);
				Brush b = kind == "TP" ? BullBrush : kind == "SL" ? BearBrush : Brushes.Silver;
				Draw.Diamond(this, "msPD" + id, false, a1, px, b);
				Draw.Text(this, "msPX" + id, false, string.Format(CI, "{0} {1:+0.0;-0.0}R", kind, r), a1, px + (tradeSide > 0 ? off : -off), 0, b,
					new SimpleFont("Arial", 10) { Bold = true }, TextAlignment.Center, Brushes.Transparent, Brushes.Transparent, 0);
			}
			tradeSide = 0;
		}

		private string TestedText()
		{
			int minutes = BarsPeriod.BarsPeriodType == BarsPeriodType.Minute ? BarsPeriod.Value : 0;
			int k = (int)Math.Round(TargetR);
			if (Strength != 5 || BreakOn != MarketStructureBreakMode.Close || Math.Abs(TargetR - k) > 1e-9 || k < 1 || k > 3 || (minutes != 1 && minutes != 5))
				return "these settings were not tested (tested: swing 5, close, 1/5-min, 1-3R)";
			bool rth = PlanSession == MarketStructurePlanSession.RTH;
			double[] usd = minutes == 1
				? (rth ? new[] { -5727.0, -639, -516 } : new[] { -19665.0, -10710, -7395 })
				: (rth ? new[] { 1009.0, 967, 1028 } : new[] { -6299.0, -7626, -4117 });
			double[] t = minutes == 1
				? (rth ? new[] { -2.88, -0.30, -0.23 } : new[] { -7.53, -3.85, -2.55 })
				: (rth ? new[] { 0.55, 0.49, 0.51 } : new[] { -2.56, -2.83, -1.47 });
			double u = usd[k - 1], tt = t[k - 1];
			return string.Format(CI, "NQ 2010-19, these settings: {0}{1:#,0}/yr per micro after costs, t {2:0.00} -> {3}",
				u >= 0 ? "+$" : "-$", Math.Abs(u), tt, u < 0 ? "LOSES" : "no edge (luck level)");
		}

		private void Break(int sig, int fromBar, double level, bool draw, double off)
		{
			lastSignal = sig; lastBreakBar = CurrentBar; lastBreakLevel = level;
			bool upBreak = sig > 0;
			string label = Math.Abs(sig) == 2 ? "CHoCH" : "BOS";
			Brush brush = upBreak ? BullBrush : BearBrush;
			AddRow(string.Format(CI, "{0:yyyy-MM-dd HH:mm},{1},{2},{3:0.00},{4:0.00},{5}", Time[0], label, upBreak ? "up" : "down", level, Close[0], bias));
			if (draw)
			{
				string tag = "msX" + CurrentBar;
				int ago = CurrentBar - fromBar;
				if (ShowBreakLines)
				{
					Draw.Line(this, tag, false, ago, level, 0, level, brush, Math.Abs(sig) == 2 ? DashStyleHelper.Solid : DashStyleHelper.Dash, 2);
					Draw.Text(this, tag + "t", false, label, ago / 2, level, upBreak ? 14 : -14, brush,
						new SimpleFont("Arial", 11) { Bold = true }, TextAlignment.Center, Brushes.Transparent, Brushes.Transparent, 0);
				}
				if (ShowArrows)
				{
					if (upBreak) Draw.ArrowUp(this, tag + "a", false, 0, Low[0] - off, brush);
					else         Draw.ArrowDown(this, tag + "a", false, 0, High[0] + off, brush);
				}
			}
			if (SoundAlert && State == State.Realtime)
			{
				try
				{
					Alert("ms" + CurrentBar, Priority.High, string.Format(CI, "{0} {1} {2} {3:0.00}", Instrument.MasterInstrument.Name, label, upBreak ? "UP" : "DOWN", level),
						Path.Combine(Core.Globals.InstallDir, "sounds", "Alert2.wav"), 10, Brushes.Black, brush);
				}
				catch (Exception ex) { Print("MarketStructure alert failed: " + ex.Message); }
			}
		}

		private void SetOrigin(int bar, double price)
		{
			originBar = bar; originPrice = price; tlBars.Clear(); tlPrices.Clear();
			RemoveDrawObject("msTL");
		}

		private void AddStructureSwing(int bar, double price, bool beyondOrigin)
		{
			if (originBar < 0 || beyondOrigin) { SetOrigin(bar, price); return; }
			tlBars.Add(bar); tlPrices.Add(price);
		}

		private void TrendLine()
		{
			if (bias == 0 || originBar < 0 || tlBars.Count == 0) { RemoveDrawObject("msTL"); return; }
			int best = -1; double bestSlope = 0;
			for (int i = 0; i < tlBars.Count; i++)
			{
				int db = tlBars[i] - originBar; if (db <= 0) continue;
				double s = (tlPrices[i] - originPrice) / db;
				if (best < 0 || (bias < 0 ? s > bestSlope : s < bestSlope)) { best = i; bestSlope = s; }
			}
			if (best < 0) { RemoveDrawObject("msTL"); return; }
			Draw.Ray(this, "msTL", false, CurrentBar - originBar, originPrice, CurrentBar - tlBars[best], tlPrices[best],
				bias > 0 ? BullBrush : BearBrush, DashStyleHelper.Solid, 2);
		}

		private void AddRow(string row)
		{
			if (!ExportCsv || exportPath == null) return;
			if (filesWritten)
			{
				try { File.AppendAllText(exportPath, row + Environment.NewLine); } catch (Exception ex) { Print("MarketStructure CSV append failed: " + ex.Message); }
			}
			else rows.Add(row);
		}

		private void WriteBufferedFile()
		{
			if (!ExportCsv || filesWritten || exportPath == null) return;
			try
			{
				var all = new List<string> { "time_et,kind,direction,level,close,bias_after" };
				all.AddRange(rows);
				File.WriteAllLines(exportPath, all);
				filesWritten = true;
				Print(string.Format(CI, "MarketStructure: wrote {0} breaks to {1}", rows.Count, exportPath));
				rows.Clear();
			}
			catch (Exception ex) { Print("MarketStructure CSV write failed: " + ex.Message); }
		}

		private string TimeOf(int bar)
		{
			if (bar < 0 || CurrentBar - bar >= Count) return "-";
			DateTime t = Time[CurrentBar - bar];
			return t.Date == Time[0].Date ? t.ToString("HH:mm", CI) : t.ToString("MM-dd HH:mm", CI);
		}

		private void Panel()
		{
			var sb = new StringBuilder();
			sb.AppendFormat(CI, "MARKET STRUCTURE  {0}  swing {1}  break on {2}\n", Instrument.MasterInstrument.Name, Strength, BreakOn == MarketStructureBreakMode.Wicks ? "wicks" : "close");
			if (bias == 0)
				sb.Append("Structure : undefined (no break yet)\n");
			else
				sb.AppendFormat(CI, "Structure : {0}  since {1}  ({2} leg{3})\n", bias > 0 ? "BULLISH" : "BEARISH", TimeOf(biasSinceBar), legs, legs == 1 ? "" : "s");
			if (lastSignal != 0)
				sb.AppendFormat(CI, "Last break: {0} {1}  {2:0.00}  at {3}\n", Math.Abs(lastSignal) == 2 ? "CHoCH" : "BOS", lastSignal > 0 ? "up" : "down", lastBreakLevel, TimeOf(lastBreakBar));
			sb.AppendFormat(CI, "Above     : {0}\n", topCrossed ? "no pending swing high" : string.Format(CI, "{0:0.00} (swing {1}) -> {2}", topPrice, TimeOf(topBar), bias == 1 ? "BOS up" : "CHoCH up"));
			sb.AppendFormat(CI, "Below     : {0}",   btmCrossed ? "no pending swing low"  : string.Format(CI, "{0:0.00} (swing {1}) -> {2}", btmPrice, TimeOf(btmBar), bias == -1 ? "BOS down" : "CHoCH down"));
			if (ShowTradePlan)
			{
				if (tradeSide != 0)
					sb.AppendFormat(CI, "\nPlan      : IN {0} from {1:0.00}  stop {2:0.00}  target {3:0.00} ({4:0.#}R)", tradeSide > 0 ? "LONG" : "SHORT", tradeEntry, tradeStop, tradeTarget, TargetR);
				else if (planSide != 0)
					sb.AppendFormat(CI, "\nPlan      : {0} at the next open  stop {1:0.00}  target ~{2:0.00} ({3:0.#}R)", planSide > 0 ? "LONG" : "SHORT", planStop, TargetFrom(Close[0], planStop, planSide), TargetR);
				else
					sb.Append(IsPlanTime(nowMinute) ? "\nPlan      : flat, waiting for the next break" : "\nPlan      : flat, outside the plan's hours");
				if (planTrades > 0)
					sb.AppendFormat(CI, "\nThis chart: {0} plans, {1:0}% reached the target, average {2:+0.00;-0.00}R before costs", planTrades, 100.0 * planTargets / planTrades, planSumR / planTrades);
				sb.Append("\nTested    : " + TestedText());
			}
			Brush fg = bias > 0 ? BullBrush : bias < 0 ? BearBrush : Brushes.White;
			Draw.TextFixed(this, "MSPanel", sb.ToString(), PanelPosition, fg, new SimpleFont("Consolas", 11), Brushes.Transparent, Brushes.Black, 60);
		}

		#region Properties
		[Browsable(false)] [XmlIgnore] public Series<double> Bias          { get { return Values[0]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> Signal        { get { return Values[1]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> StructureHigh { get { return Values[2]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> StructureLow  { get { return Values[3]; } }
		[Browsable(false)] [XmlIgnore] public int CurrentBias { get { return bias; } }

		[NinjaScriptProperty] [Range(1, 100)]
		[Display(Name = "Swing strength (bars each side)", Description = "A swing high is above the N bars before and after it; it is confirmed N bars after it forms. 5 on a 5-minute chart = 25 minutes each side.", GroupName = "1. Rules", Order = 1)]
		public int Strength { get; set; }
		[Display(Name = "Break on", Description = "Close: a bar must close beyond the swing (fewer false breaks). Wicks: the high/low trading beyond it counts.", GroupName = "1. Rules", Order = 2)]
		public MarketStructureBreakMode BreakOn { get; set; }

		[Display(Name = "Show panel", GroupName = "2. Display", Order = 1)] public bool ShowPanel { get; set; }
		[Display(Name = "Panel position", GroupName = "2. Display", Order = 2)] public TextPosition PanelPosition { get; set; }
		[Display(Name = "Structure trend line", Description = "From the extreme where the structure began through the lower high (bearish) or higher low (bullish) that keeps every other one inside, extended right; moves only when a swing confirms or the structure flips.", GroupName = "2. Display", Order = 3)] public bool ShowTrendLine { get; set; }
		[Display(Name = "Drawings on chart (swings, breaks, arrows, levels)", Description = "Master switch for the chart objects below; off = panel and trend line only.", GroupName = "2. Display", Order = 4)] public bool ShowDrawings { get; set; }
		[Display(Name = "   Show swing points", GroupName = "2. Display", Order = 5)] public bool ShowSwings { get; set; }
		[Display(Name = "   Show break lines + labels", GroupName = "2. Display", Order = 6)] public bool ShowBreakLines { get; set; }
		[Display(Name = "   Show arrows at breaks", GroupName = "2. Display", Order = 7)] public bool ShowArrows { get; set; }
		[Display(Name = "   Show pending levels (hashes)", GroupName = "2. Display", Order = 8)] public bool ShowLevels { get; set; }
		[Range(0, 100000)] [Display(Name = "Draw history for last N bars (0 = all)", GroupName = "2. Display", Order = 9)] public int HistoryBars { get; set; }
		[Display(Name = "Sound alert on a break (live bars only)", GroupName = "2. Display", Order = 10)] public bool SoundAlert { get; set; }
		[XmlIgnore] [Display(Name = "Bullish color", GroupName = "2. Display", Order = 11)] public Brush BullBrush { get; set; }
		[Browsable(false)] public string BullBrushSerialize { get { return Serialize.BrushToString(BullBrush); } set { BullBrush = Serialize.StringToBrush(value); } }
		[XmlIgnore] [Display(Name = "Bearish color", GroupName = "2. Display", Order = 12)] public Brush BearBrush { get; set; }
		[Browsable(false)] public string BearBrushSerialize { get { return Serialize.BrushToString(BearBrush); } set { BearBrush = Serialize.StringToBrush(value); } }

		[Display(Name = "Show trade plan (entry, stop, target, exit)", Description = "At each break: entry at the next open, stop at the opposite pending swing (the level that would flip the structure), target at R times the risk; the exit is marked when the stop, the target or the session end comes first.", GroupName = "3. Trade plan", Order = 1)] public bool ShowTradePlan { get; set; }
		[Range(0.5, 10)] [Display(Name = "Target (R = multiples of the risk)", GroupName = "3. Trade plan", Order = 2)] public double TargetR { get; set; }
		[Display(Name = "Plan hours", Description = "Bulenox24h: breaks from 18:05 to 16:30 ET, out by 16:55 ET. RTH: breaks 09:35-15:30 ET, out at 16:00 ET.", GroupName = "3. Trade plan", Order = 3)] public MarketStructurePlanSession PlanSession { get; set; }

		[Display(Name = "Export breaks CSV (agent_exports folder)", Description = "One row per break; research\\msb_parity.py checks it against the research port.", GroupName = "4. Export", Order = 1)] public bool ExportCsv { get; set; }
		[Display(Name = "File tag", GroupName = "4. Export", Order = 2)] public string FileTag { get; set; }
		#endregion
	}
}

#region NinjaScript generated code. Neither change nor remove.

namespace NinjaTrader.NinjaScript.Indicators
{
	public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
	{
		private MarketStructure[] cacheMarketStructure;
		public MarketStructure MarketStructure(int strength)
		{
			return MarketStructure(Input, strength);
		}

		public MarketStructure MarketStructure(ISeries<double> input, int strength)
		{
			if (cacheMarketStructure != null)
				for (int idx = 0; idx < cacheMarketStructure.Length; idx++)
					if (cacheMarketStructure[idx] != null && cacheMarketStructure[idx].Strength == strength && cacheMarketStructure[idx].EqualsInput(input))
						return cacheMarketStructure[idx];
			return CacheIndicator<MarketStructure>(new MarketStructure(){ Strength = strength }, input, ref cacheMarketStructure);
		}
	}
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
	public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
	{
		public Indicators.MarketStructure MarketStructure(int strength)
		{
			return indicator.MarketStructure(Input, strength);
		}

		public Indicators.MarketStructure MarketStructure(ISeries<double> input , int strength)
		{
			return indicator.MarketStructure(input, strength);
		}
	}
}

namespace NinjaTrader.NinjaScript.Strategies
{
	public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
	{
		public Indicators.MarketStructure MarketStructure(int strength)
		{
			return indicator.MarketStructure(Input, strength);
		}

		public Indicators.MarketStructure MarketStructure(ISeries<double> input , int strength)
		{
			return indicator.MarketStructure(input, strength);
		}
	}
}

#endregion
