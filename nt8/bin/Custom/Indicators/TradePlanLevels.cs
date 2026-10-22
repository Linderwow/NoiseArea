//
// TradePlanLevels
// ---------------------------------------------------------------------------------------------
// Draws complete trade plans on the chart - direction, entry, stop, T1, T2, R multiple and
// contract size for a fixed dollar risk - when one of the setups below triggers. Every element
// is derived from a level or from volatility; nothing is a directional opinion.
//
// Setups (chosen from what measured as an edge on MNQ 5-min, 2025-06 .. 2026-10, day-level):
//   1. RANGE FADE   price has closed outside the opening range or the overnight range for
//                   N bars. Fade back toward the range: T1 = range edge, T2 = range midpoint,
//                   stop = extreme of the excursion minus k*ATR. (While below the ORB the NY
//                   close was higher 70% of days; while above, lower 60% of days.)
//   2. GAP FILL     RTH gap vs prior RTH close that is <= GapMaxAtr * ATR and still open after
//                   the first bar. Target = prior close. (64-83% fill for gaps < 2 ATR.)
//   3. RECLAIM      (off by default) price closes back inside a range after a failed break.
//                   Continuation toward the opposite side of the range.
//
// Plans are tracked bar by bar to resolution (T1 / T2 / stop / time exit) and the running
// statistics are shown on the chart so you can see the hit rates on your own data.
//
// Hidden plots let a strategy trade the same plans:
//   Signal (+1/-1 on the trigger bar), EntryPrice, StopPrice, Target1, Target2, ActiveDir
// ---------------------------------------------------------------------------------------------
#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript
{
	// declared at the NinjaScript level so the indicator, the strategy and the generated
	// wrapper code (Indicators / Strategies / MarketAnalyzerColumns namespaces) can all see it
	public enum TplExitMode { Target2, Target1, HoldToClose }
}

namespace NinjaTrader.NinjaScript.Indicators
{
	public class TradePlanLevels : Indicator
	{
		#region Types
		private enum SetupKind { RangeFadeORB, RangeFadeON, GapFill, Reclaim }
		private enum Outcome { Open, Target2, Target1ThenStop, Target1ThenTime, Stop, TimeExit }

		private class Plan
		{
			public SetupKind	Kind;
			public int			Dir;			// +1 long, -1 short
			public double		Entry, Stop, T1, T2;
			public int			StartBar, EndBar;
			public DateTime		StartTime, EndTime;
			public bool			T1Hit;
			public Outcome		Result = Outcome.Open;
			public double		RMultiple;		// realised R at resolution (T2 target, T1 partial not modelled)
			public int			Contracts;
			public string		Tag;
		}
		#endregion

		#region Fields
		private TimeZoneInfo etZone;
		private Data.SessionIterator sessionIterator;
		private ATR atr14;

		private DateTime currentDay = Core.Globals.MinDate;
		private bool	inRthPrev, rthStarted, orbDone;
		private double	rthOpen, orbHigh, orbLow, onHigh, onLow;
		private bool	onDone;
		private double	priorRthClose, currentRthClose;
		private bool	gapChecked;
		private DateTime rthStartTime;
		private int		formingAlertedSide;		// +1/-1 side already alerted today (forming)
		private string	statusLine = "";
		private int		skippedToday;
		private string	lastSkipNote = "";
		private int		rthFirstBar = -1;
		private double	orbLastExtreme, onLastExtreme;

		private int		orbOutsideBars, onOutsideBars;	// consecutive closes outside (sign = side)
		private double	orbExtreme, onExtreme;
		private bool	orbArmedLong = true, orbArmedShort = true, onArmedLong = true, onArmedShort = true;
		private int		orbSideLast, onSideLast;		// last side for reclaim detection

		private Plan	active;
		private readonly List<Plan> history = new List<Plan>();
		private int		planSeq;
		private int		lastBarSeen = -1;
		private int		tradesToday;

		private Brush	longFill, shortFill, riskFill;
		#endregion

		#region State
		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name						= "TradePlanLevels";
				Description					= "Draws level-based trade plans (entry / stop / T1 / T2 / size) for measured setups.";
				Calculate					= Calculate.OnBarClose;
				IsOverlay					= true;
				IsAutoScale					= false;
				DisplayInDataBox			= true;
				DrawOnPricePanel			= true;
				PaintPriceMarkers			= false;
				IsSuspendedWhileInactive	= false;
				MaximumBarsLookBack			= MaximumBarsLookBack.Infinite;

				// Defaults = forward-test candidate from the 2025-06..2026-10 study:
				// overnight-range fade, morning entries only, 1.5 ATR stop, hold to close.
				// (ORB fade and gap fill tested negative net of costs and are off.)
				EnableRangeFadeORB	= false;
				EnableRangeFadeON	= true;
				EnableGapFill		= false;
				EnableReclaim		= false;
				OrbMinutes			= 30;
				MinBarsOutside		= 2;
				StopAtrMult			= 1.5;
				MinStopTicks		= 20;
				MinRR				= 0.5;
				GapMaxAtr			= 2.0;
				MinGapTicks			= 20;
				FirstEntryHHmm		= 935;
				LastEntryHHmm		= 1130;
				FlatHHmm			= 1555;
				MaxPlansPerDay		= 3;
				ExitMode			= TplExitMode.HoldToClose;
				RiskPerTrade		= 150;
				MaxContracts		= 10;
				PlanExtendBars		= 12;
				HistoryDays			= 5;
				ShowStats			= true;
				SoundAlerts			= true;
				PanelPosition		= TextPosition.BottomLeft;
				ShowRanges			= true;
				ShowForming			= true;
				AlertOnForming		= true;
				OnlyFadeAgainstOvernightMove = false;

				AddPlot(new Stroke(Brushes.Transparent, 1), PlotStyle.Line, "Signal");
				AddPlot(new Stroke(Brushes.Transparent, 1), PlotStyle.Line, "EntryPrice");
				AddPlot(new Stroke(Brushes.Transparent, 1), PlotStyle.Line, "StopPrice");
				AddPlot(new Stroke(Brushes.Transparent, 1), PlotStyle.Line, "Target1");
				AddPlot(new Stroke(Brushes.Transparent, 1), PlotStyle.Line, "Target2");
				AddPlot(new Stroke(Brushes.Transparent, 1), PlotStyle.Line, "ActiveDir");
			}
			else if (State == State.Configure)
			{
				history.Clear(); active = null; planSeq = 0; lastBarSeen = -1;
				currentDay = Core.Globals.MinDate;
				longFill  = new SolidColorBrush(Color.FromArgb(40, 0, 200, 90));  longFill.Freeze();
				shortFill = new SolidColorBrush(Color.FromArgb(40, 230, 80, 40)); shortFill.Freeze();
				riskFill  = new SolidColorBrush(Color.FromArgb(40, 200, 30, 30)); riskFill.Freeze();
			}
			else if (State == State.DataLoaded)
			{
				sessionIterator = new Data.SessionIterator(Bars);
				try   { etZone = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time"); }
				catch { etZone = null; }
				atr14 = ATR(14);
			}
			else if (State == State.Historical && !Bars.BarsType.IsIntraday)
			{
				Draw.TextFixed(this, "TPLErr", "TradePlanLevels needs an intraday chart (e.g. 5 min).", TextPosition.TopRight);
			}
		}
		#endregion

		#region Helpers
		private DateTime ToEt(DateTime t)
		{
			if (etZone == null) return t;
			try { return TimeZoneInfo.ConvertTime(t, Core.Globals.GeneralOptions.TimeZoneInfo, etZone); }
			catch { return t; }
		}
		private static int HHmmToMin(int hhmm) { return (hhmm / 100) * 60 + (hhmm % 100); }
		private static bool InWindow(int t, int start, int end) { return t > start && t <= end; }
		private double RoundTick(double p) { return Instrument.MasterInstrument.RoundToTickSize(p); }
		private string Px(double p) { return Instrument.MasterInstrument.FormatPrice(p); }
		#endregion

		#region Bar update
		protected override void OnBarUpdate()
		{
			if (!Bars.BarsType.IsIntraday) return;
			if (CurrentBar == lastBarSeen) return;
			lastBarSeen = CurrentBar;

			Values[0][0] = 0; Values[5][0] = active != null ? active.Dir : 0;
			Values[1][0] = active != null ? active.Entry : 0; Values[2][0] = active != null ? active.Stop : 0;
			Values[3][0] = active != null ? active.T1 : 0;    Values[4][0] = active != null ? active.T2 : 0;

			DateTime et = ToEt(Time[0]);
			int etMin   = et.Hour * 60 + et.Minute;
			bool inRth  = InWindow(etMin, 9 * 60 + 30, 16 * 60);
			bool inOrb  = InWindow(etMin, 9 * 60 + 30, 9 * 60 + 30 + OrbMinutes);
			double atr  = atr14[0];

			// ---- trading day roll ----
			DateTime td = sessionIterator.GetTradingDay(Time[0]);
			if (td != currentDay)
			{
				currentDay = td;
				onHigh = High[0]; onLow = Low[0]; onDone = false;
				rthStarted = false; orbDone = false; gapChecked = false;
				orbOutsideBars = 0; onOutsideBars = 0;
				orbArmedLong = orbArmedShort = onArmedLong = onArmedShort = true;
				orbSideLast = 0; onSideLast = 0;
				tradesToday = 0; skippedToday = 0; lastSkipNote = "";
				inRthPrev = false;
			}
			if (!onDone) { onHigh = Math.Max(onHigh, High[0]); onLow = Math.Min(onLow, Low[0]); }

			// ---- RTH boundaries ----
			if (inRth && !inRthPrev)
			{
				rthStarted = true; onDone = true; rthFirstBar = CurrentBar; rthStartTime = Time[0]; formingAlertedSide = 0;
				rthOpen = Open[0]; orbHigh = High[0]; orbLow = Low[0];
				if (currentRthClose > 0) priorRthClose = currentRthClose;
			}
			inRthPrev = inRth;

			if (inRth)
			{
				currentRthClose = Close[0];
				if (inOrb) { orbHigh = Math.Max(orbHigh, High[0]); orbLow = Math.Min(orbLow, Low[0]); }
				else if (!orbDone) orbDone = true;
			}

			// ---- manage the active plan first (resolution uses this bar's H/L) ----
			if (active != null) ManageActive(etMin);

			// ---- flat time: no new plans ----
			bool canEnter = inRth && etMin >= HHmmToMin(FirstEntryHHmm) && etMin <= HHmmToMin(LastEntryHHmm)
							&& atr > 0 && active == null && tradesToday < MaxPlansPerDay;

			// ---- update range-excursion state (needed even when we cannot enter) ----
			UpdateExcursion(orbDone, orbHigh, orbLow, ref orbOutsideBars, ref orbExtreme, ref orbLastExtreme, ref orbArmedLong, ref orbArmedShort, ref orbSideLast);
			UpdateExcursion(onDone && rthStarted, onHigh, onLow, ref onOutsideBars, ref onExtreme, ref onLastExtreme, ref onArmedLong, ref onArmedShort, ref onSideLast);

			if (canEnter)
			{
				// 2. GAP FILL - evaluated once, on the first RTH bar close
				if (EnableGapFill && !gapChecked && rthStarted && CurrentBar == rthFirstBar && priorRthClose > 0)
				{
					gapChecked = true;
					double gap = rthOpen - priorRthClose;
					bool filledFirstBar = Low[0] <= priorRthClose && High[0] >= priorRthClose;
					if (!filledFirstBar && Math.Abs(gap) >= MinGapTicks * TickSize && Math.Abs(gap) <= GapMaxAtr * atr)
					{
						int dir = gap > 0 ? -1 : 1;
						double stop = dir > 0 ? Low[0] - StopAtrMult * atr : High[0] + StopAtrMult * atr;
						TryCreate(SetupKind.GapFill, dir, Close[0], stop, priorRthClose, priorRthClose, etMin);
					}
				}
				// 1. RANGE FADE - ORB
				if (active == null && EnableRangeFadeORB && orbDone && !inOrb)
					TryRangeFade(SetupKind.RangeFadeORB, orbHigh, orbLow, orbOutsideBars, orbExtreme, ref orbArmedLong, ref orbArmedShort, etMin);
				// 1. RANGE FADE - overnight
				if (active == null && EnableRangeFadeON && onDone)
					TryRangeFade(SetupKind.RangeFadeON, onHigh, onLow, onOutsideBars, onExtreme, ref onArmedLong, ref onArmedShort, etMin);
				// 3. RECLAIM (optional)
				if (active == null && EnableReclaim && orbDone && !inOrb && orbOutsideBars == 0 && orbSideLast != 0)
				{
					int dir = -orbSideLast;	// was below -> reclaimed -> long
					double stop = dir > 0 ? orbLastExtreme - StopAtrMult * atr : orbLastExtreme + StopAtrMult * atr;
					double mid = (orbHigh + orbLow) / 2.0;
					TryCreate(SetupKind.Reclaim, dir, Close[0], stop, mid, dir > 0 ? orbHigh : orbLow, etMin);
					orbSideLast = 0;
				}
			}

			if (inRth && ShowRanges) DrawRanges(inOrb);
			UpdateForming(inRth, inOrb, etMin, atr);
			if (ShowStats) DrawStats();
		}

		// consecutive closes outside a range; sign = side (+ above, - below); extreme = furthest H/L of the excursion
		private void UpdateExcursion(bool valid, double hi, double lo, ref int outsideBars, ref double extreme, ref double lastExtreme, ref bool armedLong, ref bool armedShort, ref int sideLast)
		{
			if (!valid || hi <= 0) { outsideBars = 0; return; }
			if (Close[0] > hi)
			{
				if (outsideBars <= 0) { outsideBars = 1; extreme = High[0]; } else { outsideBars++; extreme = Math.Max(extreme, High[0]); }
				sideLast = 1; lastExtreme = extreme;
			}
			else if (Close[0] < lo)
			{
				if (outsideBars >= 0) { outsideBars = -1; extreme = Low[0]; } else { outsideBars--; extreme = Math.Min(extreme, Low[0]); }
				sideLast = -1; lastExtreme = extreme;
			}
			else
			{
				// back inside: re-arm both sides for the next excursion
				if (outsideBars != 0) { armedLong = true; armedShort = true; }
				outsideBars = 0;
			}
		}

		// Optional filter (off by default): only fade AGAINST the overnight move.
		// Overnight move = RTH open vs prior RTH close. Down overnight -> only long fades; up overnight -> only short fades.
		// A filtered setup uses up that side for the day, exactly like the study it came from.
		private bool OnMoveBlocks(int dir)
		{
			if (!OnlyFadeAgainstOvernightMove || priorRthClose <= 0) return false;
			int mv = Math.Sign(rthOpen - priorRthClose);
			return mv != -dir;
		}

		private void TryRangeFade(SetupKind kind, double hi, double lo, int outsideBars, double extreme, ref bool armedLong, ref bool armedShort, int etMin)
		{
			double atr = atr14[0];
			double mid = (hi + lo) / 2.0;
			if (kind == SetupKind.RangeFadeON && ((outsideBars <= -MinBarsOutside && armedLong && OnMoveBlocks(1)) || (outsideBars >= MinBarsOutside && armedShort && OnMoveBlocks(-1))))
			{
				int fdir = outsideBars < 0 ? 1 : -1;
				if (fdir > 0) armedLong = false; else armedShort = false;
				tradesToday++; skippedToday++;
				lastSkipNote = string.Format("filtered {0} ON-range fade: it would trade WITH the overnight move", fdir > 0 ? "LONG" : "SHORT");
				return;
			}
			if (outsideBars <= -MinBarsOutside && armedLong)
			{
				// below the range: fade long back to the range
				double stop = extreme - StopAtrMult * atr;
				if (TryCreate(kind, 1, Close[0], stop, lo, mid, etMin)) armedLong = false;
			}
			else if (outsideBars >= MinBarsOutside && armedShort)
			{
				double stop = extreme + StopAtrMult * atr;
				if (TryCreate(kind, -1, Close[0], stop, hi, mid, etMin)) armedShort = false;
			}
		}

		private bool TryCreate(SetupKind kind, int dir, double entry, double stop, double t1, double t2, int etMin)
		{
			entry = RoundTick(entry); stop = RoundTick(stop); t1 = RoundTick(t1); t2 = RoundTick(t2);
			double risk = dir > 0 ? entry - stop : stop - entry;
			if (risk < MinStopTicks * TickSize) { stop = dir > 0 ? entry - MinStopTicks * TickSize : entry + MinStopTicks * TickSize; risk = MinStopTicks * TickSize; }
			double rewardT2 = dir > 0 ? t2 - entry : entry - t2;
			double rewardT1 = dir > 0 ? t1 - entry : entry - t1;
			if (rewardT1 <= 0 || rewardT2 <= 0) return false;
			if (ExitMode == TplExitMode.Target2 && rewardT2 / risk < MinRR) return false;
			if (ExitMode == TplExitMode.Target1 && rewardT1 / risk < MinRR) return false;

			double perContract = risk * Instrument.MasterInstrument.PointValue;
			int qty = perContract > 0 ? (int)Math.Floor(RiskPerTrade / perContract) : 0;
			qty = Math.Max(0, Math.Min(qty, MaxContracts));

			if (qty < 1)
			{
				// The strategy cannot size this (stop wider than the risk budget) and skips it;
				// mirror that here so the chart shows only what would actually be traded.
				skippedToday++; tradesToday++;	// counts toward the daily limit, as in the backtest
				lastSkipNote = string.Format("skipped {0} {1}: stop {2:0} pts = ${3:0}/ct > ${4} risk budget", dir > 0 ? "LONG" : "SHORT", KindName(kind), risk, perContract, RiskPerTrade);
				bool drawNote = HistoryDays <= 0 || Time[0] >= Bars.GetTime(Bars.Count - 1).AddDays(-HistoryDays);
				if (drawNote) try
				{
					Draw.Text(this, "TPL_skip" + CurrentBar, false, "skipped: stop " + risk.ToString("0") + " pts > $" + RiskPerTrade + " risk",
						0, dir > 0 ? Low[0] - risk * 0.15 : High[0] + risk * 0.15, 0, Brushes.Gray, new SimpleFont("Consolas", 9),
						System.Windows.TextAlignment.Center, Brushes.Transparent, Brushes.Transparent, 0);
				}
				catch { }
				return true;	// side is disarmed for today, exactly like the backtest
			}

			planSeq++;
			active = new Plan
			{
				Kind = kind, Dir = dir, Entry = entry, Stop = stop, T1 = t1, T2 = t2,
				StartBar = CurrentBar, EndBar = CurrentBar, StartTime = Time[0], EndTime = Time[0], Contracts = qty,
				Tag = "TPL" + planSeq
			};
			tradesToday++;
			Values[0][0] = dir; Values[1][0] = entry; Values[2][0] = stop; Values[3][0] = t1; Values[4][0] = t2; Values[5][0] = dir;
			DrawPlan(active, true);

			if (State == State.Realtime && SoundAlerts)
			{
				string msg = string.Format("{0} {1} {2}: entry {3}  stop {4}  T1 {5}  T2 {6}  ({7} ct for ${8} risk)",
					Instrument.MasterInstrument.Name, dir > 0 ? "LONG" : "SHORT", KindName(kind), Px(entry), Px(stop), Px(t1), Px(t2), qty, RiskPerTrade);
				try { Alert(active.Tag, Priority.High, msg, Path.Combine(Core.Globals.InstallDir, "sounds", "Alert2.wav"), 10, Brushes.Black, dir > 0 ? Brushes.LimeGreen : Brushes.OrangeRed); } catch { }
			}
			return true;
		}

		private void ManageActive(int etMin)
		{
			Plan p = active;
			p.EndBar = CurrentBar; p.EndTime = Time[0];
			bool stopHit = p.Dir > 0 ? Low[0] <= p.Stop : High[0] >= p.Stop;
			bool t1Hit   = p.Dir > 0 ? High[0] >= p.T1 : Low[0] <= p.T1;
			bool t2Hit   = p.Dir > 0 ? High[0] >= p.T2 : Low[0] <= p.T2;
			double risk  = Math.Abs(p.Entry - p.Stop);

			// conservative: if stop and target both touched in one bar, count the stop
			if (stopHit)
			{
				p.Result = p.T1Hit ? Outcome.Target1ThenStop : Outcome.Stop;
				p.RMultiple = -1;
				Finish(p); return;
			}
			if (t1Hit) p.T1Hit = true;
			if (ExitMode == TplExitMode.Target2 && t2Hit)
			{
				p.Result = Outcome.Target2;
				p.RMultiple = risk > 0 ? Math.Abs(p.T2 - p.Entry) / risk : 0;
				Finish(p); return;
			}
			if (ExitMode == TplExitMode.Target1 && t1Hit)
			{
				p.Result = Outcome.Target2;	// counted as "target hit"
				p.RMultiple = risk > 0 ? Math.Abs(p.T1 - p.Entry) / risk : 0;
				Finish(p); return;
			}
			if (etMin >= HHmmToMin(FlatHHmm) || etMin > 16 * 60)
			{
				p.Result = p.T1Hit ? Outcome.Target1ThenTime : Outcome.TimeExit;
				p.RMultiple = risk > 0 ? (p.Dir > 0 ? Close[0] - p.Entry : p.Entry - Close[0]) / risk : 0;
				Finish(p); return;
			}
			DrawPlan(p, false);
		}

		private void Finish(Plan p)
		{
			history.Add(p);
			DrawPlan(p, false);
			active = null;
			Values[5][0] = 0;
		}
		#endregion

		#region Drawing
		private static string KindName(SetupKind k)
		{
			switch (k)
			{
				case SetupKind.RangeFadeORB: return "ORB fade";
				case SetupKind.RangeFadeON:  return "ON-range fade";
				case SetupKind.GapFill:      return "Gap fill";
				default:                     return "Reclaim";
			}
		}
		private static string ResultName(Outcome o)
		{
			switch (o)
			{
				case Outcome.Target2:         return "T2 hit";
				case Outcome.Target1ThenStop: return "T1 then stop";
				case Outcome.Target1ThenTime: return "T1 then time exit";
				case Outcome.Stop:            return "STOPPED";
				case Outcome.TimeExit:        return "time exit";
				default:                      return "open";
			}
		}

		private TimeSpan BarSpan()
		{
			switch (BarsPeriod.BarsPeriodType)
			{
				case Data.BarsPeriodType.Minute: return TimeSpan.FromMinutes(BarsPeriod.Value);
				case Data.BarsPeriodType.Second: return TimeSpan.FromSeconds(BarsPeriod.Value);
				default: return TimeSpan.FromMinutes(5);
			}
		}

		private void DrawPlan(Plan p, bool isNew)
		{
			if (HistoryDays > 0)
			{
				DateTime lastLoaded = Bars.GetTime(Bars.Count - 1);
				if (p.StartTime < lastLoaded.AddDays(-HistoryDays)) return;
			}

			DateTime t0 = p.StartTime;
			DateTime t1 = p.Result == Outcome.Open ? p.EndTime.Add(TimeSpan.FromTicks(BarSpan().Ticks * PlanExtendBars)) : p.EndTime;
			if (t1 <= t0) t1 = t0.Add(BarSpan());

			double risk = Math.Abs(p.Entry - p.Stop);
			double rr   = risk > 0 ? Math.Abs(p.T2 - p.Entry) / risk : 0;
			Brush dirBrush = p.Dir > 0 ? Brushes.LimeGreen : Brushes.OrangeRed;

			try
			{
				Draw.Rectangle(this, p.Tag + "rw", false, t0, p.Entry, t1, p.T2,   Brushes.Transparent, p.Dir > 0 ? longFill : shortFill, 100);
				Draw.Rectangle(this, p.Tag + "rk", false, t0, p.Entry, t1, p.Stop, Brushes.Transparent, riskFill, 100);
				Draw.Line(this, p.Tag + "en", false, t0, p.Entry, t1, p.Entry, Brushes.White, DashStyleHelper.Solid, 2);
				Draw.Line(this, p.Tag + "st", false, t0, p.Stop,  t1, p.Stop,  Brushes.Red,   DashStyleHelper.Dash,  2);
				Draw.Line(this, p.Tag + "t1", false, t0, p.T1,    t1, p.T1,    Brushes.Gold,  DashStyleHelper.Dash,  1);
				Draw.Line(this, p.Tag + "t2", false, t0, p.T2,    t1, p.T2,    dirBrush,      DashStyleHelper.Solid, 2);
				if (p.Dir > 0) Draw.ArrowUp(this,   p.Tag + "ar", false, t0, p.Entry - risk * 0.15, dirBrush);
				else           Draw.ArrowDown(this, p.Tag + "ar", false, t0, p.Entry + risk * 0.15, dirBrush);

				string head = string.Format("{0} {1}  {2}", p.Dir > 0 ? "LONG" : "SHORT", KindName(p.Kind), p.Result == Outcome.Open ? "" : "-> " + ResultName(p.Result));
				string exitTxt = ExitMode == TplExitMode.HoldToClose ? "exit  hold to " + (FlatHHmm / 100) + ":" + (FlatHHmm % 100).ToString("00") + " ET"
								: ExitMode == TplExitMode.Target1 ? "exit  at T1" : "exit  at T2";
				string body = string.Format("entry {0}\nstop  {1}  ({2:0.00} pts)\nT1    {3}\nT2    {4}  (R 1:{5:0.0})\n{6}\n{7} ct @ ${8} risk",
					Px(p.Entry), Px(p.Stop), risk, Px(p.T1), Px(p.T2), rr, exitTxt, p.Contracts, RiskPerTrade);
				double labelY = p.Dir > 0 ? p.Stop - risk * 0.3 : p.Stop + risk * 0.3;
				Draw.Text(this, p.Tag + "tx", false, head + "\n" + body, t0, labelY, 0, dirBrush,
					new SimpleFont("Consolas", 10), System.Windows.TextAlignment.Left, Brushes.Transparent, Brushes.Black, 70);
			}
			catch (Exception ex) { if (isNew) Print("TradePlanLevels draw error: " + ex.Message); }
		}

		private DateTime ChartTimeForEt(DateTime etLocalDate, int hhmm)
		{
			DateTime et = etLocalDate.Date.AddMinutes(HHmmToMin(hhmm));
			if (etZone == null) return et;
			try { return TimeZoneInfo.ConvertTime(DateTime.SpecifyKind(et, DateTimeKind.Unspecified), etZone, Core.Globals.GeneralOptions.TimeZoneInfo); }
			catch { return et; }
		}

		private void DrawRanges(bool inOrb)
		{
			try
			{
				DateTime t0 = rthStartTime, t1 = Time[0].Add(BarSpan());
				if (EnableRangeFadeON && onDone && onHigh > 0)
				{
					double mid = (onHigh + onLow) / 2.0;
					Draw.Line(this, "TPL_ONhi", false, t0, onHigh, t1, onHigh, Brushes.Silver,   DashStyleHelper.Solid, 1);
					Draw.Line(this, "TPL_ONlo", false, t0, onLow,  t1, onLow,  Brushes.Silver,   DashStyleHelper.Solid, 1);
					Draw.Line(this, "TPL_ONmd", false, t0, mid,    t1, mid,    Brushes.DimGray,  DashStyleHelper.Dot,   1);
					Draw.Text(this, "TPL_ONhiT", false, "ON high  " + Px(onHigh) + "   fade SHORT zone above", t1, onHigh, 8, Brushes.Silver, new SimpleFont("Consolas", 9), System.Windows.TextAlignment.Left, Brushes.Transparent, Brushes.Transparent, 0);
					Draw.Text(this, "TPL_ONloT", false, "ON low   " + Px(onLow)  + "   fade LONG zone below",  t1, onLow,  -8, Brushes.Silver, new SimpleFont("Consolas", 9), System.Windows.TextAlignment.Left, Brushes.Transparent, Brushes.Transparent, 0);
				}
				if (EnableRangeFadeORB && orbDone && !inOrb && orbHigh > 0)
				{
					Draw.Line(this, "TPL_ORBhi", false, t0, orbHigh, t1, orbHigh, Brushes.SlateGray, DashStyleHelper.Dash, 1);
					Draw.Line(this, "TPL_ORBlo", false, t0, orbLow,  t1, orbLow,  Brushes.SlateGray, DashStyleHelper.Dash, 1);
					Draw.Text(this, "TPL_ORBhiT", false, "ORB high " + Px(orbHigh), t1, orbHigh, 8, Brushes.SlateGray, new SimpleFont("Consolas", 9), System.Windows.TextAlignment.Left, Brushes.Transparent, Brushes.Transparent, 0);
					Draw.Text(this, "TPL_ORBloT", false, "ORB low  " + Px(orbLow),  t1, orbLow, -8, Brushes.SlateGray, new SimpleFont("Consolas", 9), System.Windows.TextAlignment.Left, Brushes.Transparent, Brushes.Transparent, 0);
				}
				// exit area for hold-to-close plans: the flat time
				if (ExitMode == TplExitMode.HoldToClose)
					Draw.VerticalLine(this, "TPL_flat", ChartTimeForEt(ToEt(Time[0]), FlatHHmm), Brushes.DimGray, DashStyleHelper.Dash, 1);
			}
			catch { }
		}

		private void RemoveForming()
		{
			foreach (string t in new[] { "TPL_g_en", "TPL_g_st", "TPL_g_box", "TPL_g_tx", "TPL_g_t1" }) RemoveDrawObject(t);
		}

		// Shows the prospective plan while a setup is forming (price outside a range but not yet confirmed).
		private void UpdateForming(bool inRth, bool inOrb, int etMin, double atr)
		{
			statusLine = "";
			bool window = inRth && etMin >= HHmmToMin(FirstEntryHHmm) && etMin <= HHmmToMin(LastEntryHHmm) && atr > 0;
			if (active != null) { statusLine = "ACTIVE plan - managing"; RemoveForming(); return; }
			if (!window || tradesToday >= MaxPlansPerDay) { statusLine = inRth ? "no entries now (outside entry window or daily limit reached)" : "outside RTH"; RemoveForming(); return; }

			// candidate: overnight range first, then ORB
			string rangeName = null; double hi = 0, lo = 0, extreme = 0; int outside = 0; bool armed = false;
			if (EnableRangeFadeON && onDone && onOutsideBars != 0)
			{ rangeName = "ON-range"; hi = onHigh; lo = onLow; extreme = onExtreme; outside = onOutsideBars; armed = outside < 0 ? onArmedLong : onArmedShort; }
			else if (EnableRangeFadeORB && orbDone && !inOrb && orbOutsideBars != 0)
			{ rangeName = "ORB"; hi = orbHigh; lo = orbLow; extreme = orbExtreme; outside = orbOutsideBars; armed = outside < 0 ? orbArmedLong : orbArmedShort; }

			if (rangeName == null)
			{
				string rng = EnableRangeFadeON && onDone ? string.Format("inside ON range {0} - {1}", Px(onLow), Px(onHigh)) : "waiting for a range";
				statusLine = "WATCHING: " + rng + " - no setup forming";
				RemoveForming(); return;
			}
			if (!armed) { statusLine = string.Format("{0}: this side already traded today - waiting for price to return inside", rangeName); RemoveForming(); return; }

			int dir = outside < 0 ? 1 : -1;
			int n = Math.Abs(outside);
			double stop  = RoundTick(dir > 0 ? extreme - StopAtrMult * atr : extreme + StopAtrMult * atr);
			double entry = RoundTick(Close[0]);
			double risk  = Math.Abs(entry - stop);
			double edge  = dir > 0 ? lo : hi;
			if (n >= MinBarsOutside)
			{
				statusLine = string.Format("{0}: conditions met but plan filtered (R:R / stop size) - watching", rangeName);
				RemoveForming(); return;
			}
			string exitTxt = ExitMode == TplExitMode.HoldToClose ? "hold to " + (FlatHHmm / 100) + ":" + (FlatHHmm % 100).ToString("00") + " ET"
							: ExitMode == TplExitMode.Target1 ? "T1 " + Px(edge) : "T2 " + Px((hi + lo) / 2);
			double perCt = risk * Instrument.MasterInstrument.PointValue;
			int qty = perCt > 0 ? Math.Min(MaxContracts, (int)Math.Floor(RiskPerTrade / perCt)) : 0;
			string sizing = qty >= 1 ? string.Format("{0} ct @ ${1} risk", qty, RiskPerTrade)
									 : string.Format("SKIP: stop {0:0} pts = ${1:0}/ct > ${2} risk", risk, perCt, RiskPerTrade);
			statusLine = string.Format("FORMING {0} {1}: {2}/{3} bars outside - triggers if next bar closes {4} {5} | entry ~{6}  stop ~{7} ({8:0.00} pts)  exit {9} | {10}",
				dir > 0 ? "LONG" : "SHORT", rangeName, n, MinBarsOutside, dir > 0 ? "below" : "above", Px(edge), Px(entry), Px(stop), risk, exitTxt, sizing);

			if (ShowForming)
			{
				try
				{
					DateTime t0 = Time[0], t1 = Time[0].Add(TimeSpan.FromTicks(BarSpan().Ticks * Math.Max(4, PlanExtendBars / 2)));
					Brush c = qty >= 1 ? (dir > 0 ? Brushes.LimeGreen : Brushes.OrangeRed) : Brushes.Gray;
					Draw.Rectangle(this, "TPL_g_box", false, t0, entry, t1, stop, Brushes.Transparent, riskFill, qty >= 1 ? 60 : 30);
					Draw.Line(this, "TPL_g_en", false, t0, entry, t1, entry, c,            DashStyleHelper.Dash, 1);
					Draw.Line(this, "TPL_g_st", false, t0, stop,  t1, stop,  Brushes.Red,  DashStyleHelper.Dot,  1);
					Draw.Line(this, "TPL_g_t1", false, t0, edge,  t1, edge,  Brushes.Gold, DashStyleHelper.Dot,  1);
					Draw.Text(this, "TPL_g_tx", false,
						string.Format("forming {0} {1}\nentry ~{2}\nstop  ~{3}  ({4:0} pts)\nexit  {5}\n{6}", dir > 0 ? "LONG" : "SHORT", rangeName, Px(entry), Px(stop), risk, exitTxt, sizing),
						t1, stop, 0, c, new SimpleFont("Consolas", 9), System.Windows.TextAlignment.Left, Brushes.Transparent, Brushes.Black, 50);
				}
				catch { }
			}
			if (State == State.Realtime && AlertOnForming && formingAlertedSide != dir)
			{
				formingAlertedSide = dir;
				try { Alert("TPLforming" + CurrentBar, Priority.Medium, string.Format("{0} {1} fade forming: entry ~{2} stop ~{3}", Instrument.MasterInstrument.Name, dir > 0 ? "LONG" : "SHORT", Px(entry), Px(stop)),
					Path.Combine(Core.Globals.InstallDir, "sounds", "Alert3.wav"), 30, Brushes.Black, Brushes.Gold); } catch { }
			}
		}

		private void DrawStats()
		{
			var sb = new StringBuilder();
			sb.AppendLine(string.Format("TradePlanLevels  {0} plans resolved", history.Count));
			if (!string.IsNullOrEmpty(statusLine)) sb.AppendLine("  " + statusLine);
			if (skippedToday > 0) sb.AppendLine("  today: " + lastSkipNote);
			foreach (SetupKind k in Enum.GetValues(typeof(SetupKind)))
			{
				var h = history.Where(x => x.Kind == k).ToList();
				if (h.Count == 0) continue;
				int t2 = h.Count(x => x.Result == Outcome.Target2);
				int t1 = h.Count(x => x.T1Hit);
				int st = h.Count(x => x.Result == Outcome.Stop);
				double avgR = h.Average(x => x.RMultiple);
				sb.AppendLine(string.Format("  {0,-13} n={1,4}  T1 {2,3:P0}  T2 {3,3:P0}  stopped {4,3:P0}  avg R {5:+0.00;-0.00}  ({6} L / {7} S)",
					KindName(k), h.Count, (double)t1 / h.Count, (double)t2 / h.Count, (double)st / h.Count, avgR, h.Count(x => x.Dir > 0), h.Count(x => x.Dir < 0)));
			}
			if (active != null)
				sb.AppendLine(string.Format("  ACTIVE: {0} {1}  entry {2}  stop {3}  T1 {4}  T2 {5}", active.Dir > 0 ? "LONG" : "SHORT", KindName(active.Kind), Px(active.Entry), Px(active.Stop), Px(active.T1), Px(active.T2)));
			sb.Append("  (bar-level tracking: a bar touching both stop and target counts as a stop)");
			Draw.TextFixed(this, "TPLStats", sb.ToString(), PanelPosition, Brushes.White, new SimpleFont("Consolas", 10), Brushes.Transparent, Brushes.Black, 60);
		}
		#endregion

		#region Properties
		[Browsable(false)] [XmlIgnore] public Series<double> Signal     { get { return Values[0]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> EntryPrice { get { return Values[1]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> StopPrice  { get { return Values[2]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> Target1    { get { return Values[3]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> Target2    { get { return Values[4]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> ActiveDir  { get { return Values[5]; } }

		[NinjaScriptProperty] [Display(Name = "Range fade: opening range", GroupName = "1. Setups", Order = 1)] public bool EnableRangeFadeORB { get; set; }
		[NinjaScriptProperty] [Display(Name = "Range fade: overnight range", GroupName = "1. Setups", Order = 2)] public bool EnableRangeFadeON { get; set; }
		[NinjaScriptProperty] [Display(Name = "Gap fill", GroupName = "1. Setups", Order = 3)] public bool EnableGapFill { get; set; }
		[NinjaScriptProperty] [Display(Name = "Reclaim (continuation)", GroupName = "1. Setups", Order = 4)] public bool EnableReclaim { get; set; }

		[NinjaScriptProperty] [Range(5, 120)]  [Display(Name = "Opening range minutes", GroupName = "2. Rules", Order = 1)] public int OrbMinutes { get; set; }
		[NinjaScriptProperty] [Range(1, 20)]   [Display(Name = "Bars closed outside range before fade", GroupName = "2. Rules", Order = 2)] public int MinBarsOutside { get; set; }
		[NinjaScriptProperty] [Range(0.1, 5)]  [Display(Name = "Stop = extreme +/- ATR x", GroupName = "2. Rules", Order = 3)] public double StopAtrMult { get; set; }
		[NinjaScriptProperty] [Range(1, 400)]  [Display(Name = "Minimum stop (ticks)", GroupName = "2. Rules", Order = 4)] public int MinStopTicks { get; set; }
		[NinjaScriptProperty] [Range(0.5, 10)] [Display(Name = "Minimum R:R to T2", GroupName = "2. Rules", Order = 5)] public double MinRR { get; set; }
		[NinjaScriptProperty] [Range(0.1, 10)] [Display(Name = "Gap fill: max gap (ATR x)", GroupName = "2. Rules", Order = 6)] public double GapMaxAtr { get; set; }
		[NinjaScriptProperty] [Range(1, 400)]  [Display(Name = "Gap fill: min gap (ticks)", GroupName = "2. Rules", Order = 7)] public int MinGapTicks { get; set; }
		[NinjaScriptProperty] [Range(0, 2359)] [Display(Name = "First entry (HHmm ET)", GroupName = "2. Rules", Order = 8)] public int FirstEntryHHmm { get; set; }
		[NinjaScriptProperty] [Range(0, 2359)] [Display(Name = "Last entry (HHmm ET)", GroupName = "2. Rules", Order = 9)] public int LastEntryHHmm { get; set; }
		[NinjaScriptProperty] [Range(0, 2359)] [Display(Name = "Flat by (HHmm ET)", GroupName = "2. Rules", Order = 10)] public int FlatHHmm { get; set; }
		[NinjaScriptProperty] [Range(1, 20)]   [Display(Name = "Max plans per day", GroupName = "2. Rules", Order = 11)] public int MaxPlansPerDay { get; set; }
		[NinjaScriptProperty] [Display(Name = "Exit mode", Description = "HoldToClose = exit at 'Flat by' time; the study favoured this", GroupName = "2. Rules", Order = 12)] public TplExitMode ExitMode { get; set; }

		[Range(1, 100000)] [Display(Name = "Risk per trade ($)", GroupName = "3. Sizing", Order = 1)] public double RiskPerTrade { get; set; }
		[Range(1, 500)]    [Display(Name = "Max contracts", GroupName = "3. Sizing", Order = 2)] public int MaxContracts { get; set; }

		[Range(0, 100)]  [Display(Name = "Extend open plan (bars)", GroupName = "4. Display", Order = 1)] public int PlanExtendBars { get; set; }
		[Range(0, 400)]  [Display(Name = "Draw plans for last N days (0 = all)", GroupName = "4. Display", Order = 2)] public int HistoryDays { get; set; }
		[Display(Name = "Show statistics panel", GroupName = "4. Display", Order = 3)] public bool ShowStats { get; set; }
		[Display(Name = "Panel position", GroupName = "4. Display", Order = 4)] public TextPosition PanelPosition { get; set; }
		[Display(Name = "Sound + popup on new plan (live)", GroupName = "4. Display", Order = 5)] public bool SoundAlerts { get; set; }
		[Display(Name = "Draw session ranges (entry zones)", GroupName = "4. Display", Order = 6)] public bool ShowRanges { get; set; }
		[Display(Name = "Draw forming setup (entry/stop preview)", GroupName = "4. Display", Order = 7)] public bool ShowForming { get; set; }
		[Display(Name = "Alert when a setup starts forming (live)", GroupName = "4. Display", Order = 8)] public bool AlertOnForming { get; set; }
		[Display(Name = "Only fade against the overnight move (test)", Description = "Off by default. Long fades only after a down overnight move, short fades only after an up move.", GroupName = "2. Rules", Order = 20)] public bool OnlyFadeAgainstOvernightMove { get; set; }
		#endregion
	}
}

#region NinjaScript generated code. Neither change nor remove.

namespace NinjaTrader.NinjaScript.Indicators
{
	public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
	{
		private TradePlanLevels[] cacheTradePlanLevels;
		public TradePlanLevels TradePlanLevels(bool enableRangeFadeORB, bool enableRangeFadeON, bool enableGapFill, bool enableReclaim, int orbMinutes, int minBarsOutside, double stopAtrMult, int minStopTicks, double minRR, double gapMaxAtr, int minGapTicks, int firstEntryHHmm, int lastEntryHHmm, int flatHHmm, int maxPlansPerDay, TplExitMode exitMode)
		{
			return TradePlanLevels(Input, enableRangeFadeORB, enableRangeFadeON, enableGapFill, enableReclaim, orbMinutes, minBarsOutside, stopAtrMult, minStopTicks, minRR, gapMaxAtr, minGapTicks, firstEntryHHmm, lastEntryHHmm, flatHHmm, maxPlansPerDay, exitMode);
		}

		public TradePlanLevels TradePlanLevels(ISeries<double> input, bool enableRangeFadeORB, bool enableRangeFadeON, bool enableGapFill, bool enableReclaim, int orbMinutes, int minBarsOutside, double stopAtrMult, int minStopTicks, double minRR, double gapMaxAtr, int minGapTicks, int firstEntryHHmm, int lastEntryHHmm, int flatHHmm, int maxPlansPerDay, TplExitMode exitMode)
		{
			if (cacheTradePlanLevels != null)
				for (int idx = 0; idx < cacheTradePlanLevels.Length; idx++)
					if (cacheTradePlanLevels[idx] != null && cacheTradePlanLevels[idx].EnableRangeFadeORB == enableRangeFadeORB && cacheTradePlanLevels[idx].EnableRangeFadeON == enableRangeFadeON && cacheTradePlanLevels[idx].EnableGapFill == enableGapFill && cacheTradePlanLevels[idx].EnableReclaim == enableReclaim && cacheTradePlanLevels[idx].OrbMinutes == orbMinutes && cacheTradePlanLevels[idx].MinBarsOutside == minBarsOutside && cacheTradePlanLevels[idx].StopAtrMult == stopAtrMult && cacheTradePlanLevels[idx].MinStopTicks == minStopTicks && cacheTradePlanLevels[idx].MinRR == minRR && cacheTradePlanLevels[idx].GapMaxAtr == gapMaxAtr && cacheTradePlanLevels[idx].MinGapTicks == minGapTicks && cacheTradePlanLevels[idx].FirstEntryHHmm == firstEntryHHmm && cacheTradePlanLevels[idx].LastEntryHHmm == lastEntryHHmm && cacheTradePlanLevels[idx].FlatHHmm == flatHHmm && cacheTradePlanLevels[idx].MaxPlansPerDay == maxPlansPerDay && cacheTradePlanLevels[idx].ExitMode == exitMode && cacheTradePlanLevels[idx].EqualsInput(input))
						return cacheTradePlanLevels[idx];
			return CacheIndicator<TradePlanLevels>(new TradePlanLevels(){ EnableRangeFadeORB = enableRangeFadeORB, EnableRangeFadeON = enableRangeFadeON, EnableGapFill = enableGapFill, EnableReclaim = enableReclaim, OrbMinutes = orbMinutes, MinBarsOutside = minBarsOutside, StopAtrMult = stopAtrMult, MinStopTicks = minStopTicks, MinRR = minRR, GapMaxAtr = gapMaxAtr, MinGapTicks = minGapTicks, FirstEntryHHmm = firstEntryHHmm, LastEntryHHmm = lastEntryHHmm, FlatHHmm = flatHHmm, MaxPlansPerDay = maxPlansPerDay, ExitMode = exitMode }, input, ref cacheTradePlanLevels);
		}
	}
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
	public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
	{
		public Indicators.TradePlanLevels TradePlanLevels(bool enableRangeFadeORB, bool enableRangeFadeON, bool enableGapFill, bool enableReclaim, int orbMinutes, int minBarsOutside, double stopAtrMult, int minStopTicks, double minRR, double gapMaxAtr, int minGapTicks, int firstEntryHHmm, int lastEntryHHmm, int flatHHmm, int maxPlansPerDay, TplExitMode exitMode)
		{
			return indicator.TradePlanLevels(Input, enableRangeFadeORB, enableRangeFadeON, enableGapFill, enableReclaim, orbMinutes, minBarsOutside, stopAtrMult, minStopTicks, minRR, gapMaxAtr, minGapTicks, firstEntryHHmm, lastEntryHHmm, flatHHmm, maxPlansPerDay, exitMode);
		}

		public Indicators.TradePlanLevels TradePlanLevels(ISeries<double> input , bool enableRangeFadeORB, bool enableRangeFadeON, bool enableGapFill, bool enableReclaim, int orbMinutes, int minBarsOutside, double stopAtrMult, int minStopTicks, double minRR, double gapMaxAtr, int minGapTicks, int firstEntryHHmm, int lastEntryHHmm, int flatHHmm, int maxPlansPerDay, TplExitMode exitMode)
		{
			return indicator.TradePlanLevels(input, enableRangeFadeORB, enableRangeFadeON, enableGapFill, enableReclaim, orbMinutes, minBarsOutside, stopAtrMult, minStopTicks, minRR, gapMaxAtr, minGapTicks, firstEntryHHmm, lastEntryHHmm, flatHHmm, maxPlansPerDay, exitMode);
		}
	}
}

namespace NinjaTrader.NinjaScript.Strategies
{
	public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
	{
		public Indicators.TradePlanLevels TradePlanLevels(bool enableRangeFadeORB, bool enableRangeFadeON, bool enableGapFill, bool enableReclaim, int orbMinutes, int minBarsOutside, double stopAtrMult, int minStopTicks, double minRR, double gapMaxAtr, int minGapTicks, int firstEntryHHmm, int lastEntryHHmm, int flatHHmm, int maxPlansPerDay, TplExitMode exitMode)
		{
			return indicator.TradePlanLevels(Input, enableRangeFadeORB, enableRangeFadeON, enableGapFill, enableReclaim, orbMinutes, minBarsOutside, stopAtrMult, minStopTicks, minRR, gapMaxAtr, minGapTicks, firstEntryHHmm, lastEntryHHmm, flatHHmm, maxPlansPerDay, exitMode);
		}

		public Indicators.TradePlanLevels TradePlanLevels(ISeries<double> input , bool enableRangeFadeORB, bool enableRangeFadeON, bool enableGapFill, bool enableReclaim, int orbMinutes, int minBarsOutside, double stopAtrMult, int minStopTicks, double minRR, double gapMaxAtr, int minGapTicks, int firstEntryHHmm, int lastEntryHHmm, int flatHHmm, int maxPlansPerDay, TplExitMode exitMode)
		{
			return indicator.TradePlanLevels(input, enableRangeFadeORB, enableRangeFadeON, enableGapFill, enableReclaim, orbMinutes, minBarsOutside, stopAtrMult, minStopTicks, minRR, gapMaxAtr, minGapTicks, firstEntryHHmm, lastEntryHHmm, flatHHmm, maxPlansPerDay, exitMode);
		}
	}
}

#endregion
