//
// MarketStructure
// ---------------------------------------------------------------------------------------------
// Swing-point market structure with break detection (BOS / CHoCH) and a direction read. Nothing repaints.
//
//   Swing high  = a bar whose high is strictly above the N bars before it and at or above the N bars after it
//                 (ties go to the earlier bar). Swing low mirrored. N = "Swing strength".
//   Confirmed   = known only N bars later, so a swing is drawn and used N bars after it formed, never earlier.
//   Pending level = the latest confirmed swing high (and low) that price has not yet closed beyond. A newer
//                 confirmed swing replaces the pending one.
//   Bullish break = a bar closes above the pending swing high ("Break on" = Wicks: the bar's high trades above it).
//                 BOS   (break of structure)  when the structure was already bullish  -> continuation
//                 CHoCH (change of character) when it was bearish or undefined        -> reversal, structure turns bullish
//   Bearish break mirrored. If both levels break in one bar, the bar's own direction (close vs open) decides.
//
//   Direction read ("which way"): the structure state after the last break - bullish = up, bearish = down. It is the
//   method's reading, NOT a forecast. Tested 2026-11-03 (agent_exports\review\research\MSB_PLAN.txt / MSB_RESULTS.txt,
//   16 years of NQ/MNQ plus MES, 1- to 60-minute bars): the hour after a break goes the break's way 47-50% of the time,
//   the mean follow-through is 0-2 ticks before costs, and trading every break nets about $0/yr on MNQ. Use the structure
//   as a map (where the swings are, which levels a break would change), not as an entry signal.
//
//   Chart: break lines from the swing to the break bar, labelled BOS / CHoCH, green up / red down; arrows at the break bar;
//   dots on confirmed swings; thin hashes = the pending levels; a panel with the state, the last break and the two levels
//   whose break would be the next BOS / CHoCH. Calculate is forced to OnBarClose (the non-repainting guarantee).
//   For strategies: Bias (+1 / -1 / 0), Signal (+2 CHoCH up, +1 BOS up, -1 BOS down, -2 CHoCH down, 0 none),
//   StructureHigh / StructureLow (the pending levels, NaN when none).
//   Export: every break is appended to Documents\NinjaTrader 8\agent_exports\MarketStructure_breaks_<instrument>_<period>_<tag>.csv
//   (time_et,kind,direction,level,close,bias_after); research\msb_parity.py re-runs the same rules in Python on the chart's bars and
//   must match break for break (the research port msb.py is the same code, line for line).
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

	public class MarketStructure : Indicator
	{
		private double topPrice = double.NaN, btmPrice = double.NaN;   // pending swing high / low
		private int topBar = -1, btmBar = -1;                          // the bar (CurrentBar index) on which each formed
		private bool topCrossed = true, btmCrossed = true;             // true once broken (or none yet)
		private int bias;                                              // +1 bullish, -1 bearish, 0 undefined
		private int biasSinceBar = -1;
		private int lastSignal;                                        // +2 CHoCH up, +1 BOS up, -1 BOS down, -2 CHoCH down
		private int lastBreakBar = -1;
		private double lastBreakLevel = double.NaN;
		private int legs;                                              // consecutive BOS in the current direction (the CHoCH counts as leg 1)
		private ATR atr;
		private readonly List<string> rows = new List<string>();     // break rows buffered through history, appended live
		private string exportPath;
		private bool filesWritten;
		private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name						= "MarketStructure";
				Description					= "Swing-point market structure: BOS / CHoCH breaks and the direction they imply. Non-repainting (swings confirm N bars later, breaks on bar close).";
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
				ExportCsv		= true;
				FileTag			= "chart";

				AddPlot(new Stroke(Brushes.Transparent, 1),		PlotStyle.Line, "Bias");
				AddPlot(new Stroke(Brushes.Transparent, 1),		PlotStyle.Line, "Signal");
				AddPlot(new Stroke(Brushes.DimGray, 1),			PlotStyle.Hash, "StructureHigh");
				AddPlot(new Stroke(Brushes.DimGray, 1),			PlotStyle.Hash, "StructureLow");
			}
			else if (State == State.Configure)
			{
				Calculate = Calculate.OnBarClose;              // forced: a developing bar must never break or confirm anything
				BarsRequiredToPlot = 2 * Strength + 1;
				topPrice = btmPrice = double.NaN; topBar = btmBar = -1; topCrossed = btmCrossed = true;
				bias = 0; biasSinceBar = -1; lastSignal = 0; lastBreakBar = -1; lastBreakLevel = double.NaN; legs = 0;
				rows.Clear(); exportPath = null; filesWritten = false;
				if (!ShowLevels) { Plots[2].Brush = Brushes.Transparent; Plots[3].Brush = Brushes.Transparent; }
			}
			else if (State == State.DataLoaded)
			{
				atr = ATR(14);
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
			double off = atr != null && atr.IsValidDataPoint(0) ? atr[0] * 0.3 : TickSize * 4;

			// 1. confirm the swing candidate N bars ago: [n] against the N older bars [n+1..2n] and the N newer bars [0..n-1]
			bool isTop = true, isBtm = true;
			double hc = High[n], lc = Low[n];
			for (int k = 0; k <= 2 * n; k++)
			{
				if (k == n) continue;
				if (k < n) { if (High[k] > hc) isTop = false; if (Low[k] < lc) isBtm = false; }     // newer: ties go to the candidate
				else       { if (High[k] >= hc) isTop = false; if (Low[k] <= lc) isBtm = false; }   // older: strictly beyond
				if (!isTop && !isBtm) break;
			}
			if (isTop)
			{
				topPrice = hc; topBar = CurrentBar - n; topCrossed = false;
				if (ShowSwings && draw) Draw.Dot(this, "msT" + topBar, false, n, hc + off * 0.5, BearBrush);
			}
			if (isBtm)
			{
				btmPrice = lc; btmBar = CurrentBar - n; btmCrossed = false;
				if (ShowSwings && draw) Draw.Dot(this, "msB" + btmBar, false, n, lc - off * 0.5, BullBrush);
			}

			// 2. breaks on this bar's close (or wicks)
			double up = BreakOn == MarketStructureBreakMode.Wicks ? High[0] : Close[0];
			double dn = BreakOn == MarketStructureBreakMode.Wicks ? Low[0]  : Close[0];
			bool breakUp = !topCrossed && up > topPrice;
			bool breakDn = !btmCrossed && dn < btmPrice;
			if (breakUp && breakDn)
			{
				if (Close[0] >= Open[0]) breakDn = false; else breakUp = false;
				topCrossed = btmCrossed = true;                // both levels retire; the bar's direction names the break
			}
			int sig = 0;
			if (breakUp)
			{
				sig = bias == 1 ? 1 : 2; topCrossed = true;
				if (bias != 1) { bias = 1; biasSinceBar = CurrentBar; legs = 1; } else legs++;
				Break(sig, topBar, topPrice, draw, off);
			}
			else if (breakDn)
			{
				sig = bias == -1 ? -1 : -2; btmCrossed = true;
				if (bias != -1) { bias = -1; biasSinceBar = CurrentBar; legs = 1; } else legs++;
				Break(sig, btmBar, btmPrice, draw, off);
			}

			Bias[0] = bias; Signal[0] = sig;
			StructureHigh[0] = topCrossed ? double.NaN : topPrice;
			StructureLow[0]  = btmCrossed ? double.NaN : btmPrice;
			if (ShowPanel && (State != State.Historical || CurrentBar >= Count - 2)) Panel();   // last historical bar is Count - 2 (as NoiseAreaMomentum)
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
				sb.AppendFormat(CI, "Structure : {0}  since {1}  ({2} leg{3})\nRead      : {4} by the method; measured follow-through ~50% (MSB_RESULTS)\n",
					bias > 0 ? "BULLISH (higher highs)" : "BEARISH (lower lows)", TimeOf(biasSinceBar), legs, legs == 1 ? "" : "s", bias > 0 ? "UP" : "DOWN");
			if (lastSignal != 0)
				sb.AppendFormat(CI, "Last break: {0} {1}  {2:0.00}  at {3}\n", Math.Abs(lastSignal) == 2 ? "CHoCH" : "BOS", lastSignal > 0 ? "up" : "down", lastBreakLevel, TimeOf(lastBreakBar));
			sb.AppendFormat(CI, "Above     : {0}\n", topCrossed ? "no pending swing high" : string.Format(CI, "{0:0.00} (swing {1}) -> {2}", topPrice, TimeOf(topBar), bias == 1 ? "BOS up" : "CHoCH up"));
			sb.AppendFormat(CI, "Below     : {0}",   btmCrossed ? "no pending swing low"  : string.Format(CI, "{0:0.00} (swing {1}) -> {2}", btmPrice, TimeOf(btmBar), bias == -1 ? "BOS down" : "CHoCH down"));
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

		[Display(Name = "Show swing points", GroupName = "2. Display", Order = 1)] public bool ShowSwings { get; set; }
		[Display(Name = "Show break lines + labels", GroupName = "2. Display", Order = 2)] public bool ShowBreakLines { get; set; }
		[Display(Name = "Show arrows at breaks", GroupName = "2. Display", Order = 3)] public bool ShowArrows { get; set; }
		[Display(Name = "Show pending levels (hashes)", GroupName = "2. Display", Order = 4)] public bool ShowLevels { get; set; }
		[Display(Name = "Show panel", GroupName = "2. Display", Order = 5)] public bool ShowPanel { get; set; }
		[Display(Name = "Panel position", GroupName = "2. Display", Order = 6)] public TextPosition PanelPosition { get; set; }
		[Range(0, 100000)] [Display(Name = "Draw history for last N bars (0 = all)", GroupName = "2. Display", Order = 7)] public int HistoryBars { get; set; }
		[Display(Name = "Sound alert on a break (live bars only)", GroupName = "2. Display", Order = 8)] public bool SoundAlert { get; set; }
		[XmlIgnore] [Display(Name = "Bullish color", GroupName = "2. Display", Order = 9)] public Brush BullBrush { get; set; }
		[Browsable(false)] public string BullBrushSerialize { get { return Serialize.BrushToString(BullBrush); } set { BullBrush = Serialize.StringToBrush(value); } }
		[XmlIgnore] [Display(Name = "Bearish color", GroupName = "2. Display", Order = 10)] public Brush BearBrush { get; set; }
		[Browsable(false)] public string BearBrushSerialize { get { return Serialize.BrushToString(BearBrush); } set { BearBrush = Serialize.StringToBrush(value); } }

		[Display(Name = "Export breaks CSV (agent_exports folder)", Description = "One row per break; research\\msb_parity.py checks it against the research port.", GroupName = "3. Export", Order = 1)] public bool ExportCsv { get; set; }
		[Display(Name = "File tag", GroupName = "3. Export", Order = 2)] public string FileTag { get; set; }
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
