#region Using declarations
using System;
using System.Collections.Generic;
using System.Windows.Media;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
using System.ComponentModel.DataAnnotations;
#endregion

//  ============================================================================
//  BigPlayerFlow v1.1 (2026-09-02) — large-lot aggressor flow, on the candles.
//  ----------------------------------------------------------------------------
//  WHAT IT SHOWS
//   Every print >= BigSize contracts is classified by aggressor side (trade at or
//   above the ask = buyer paid up; at or below the bid = seller hit down; between
//   the quotes = ambiguous, skipped) and summed PER BAR.
//     ON THE CANDLES (v1.1): each bar whose net big-lot flow >= MarkThreshold gets
//       a marker — a triangle BELOW the bar when big players were net BUYERS,
//       ABOVE the bar when net SELLERS. Brightness scales with how lopsided it was
//       (a faint tag = marginal, a bright one = heavily one-sided).
//     HUGE SINGLE PRINTS: a print >= HugeSize gets its own dot at the trade price.
//     SUBPANEL: CumBigDelta (running session total) + BarBigDelta (per-bar bars).
//
//  WHAT IT DOES NOT DO — read before trading off it (RESEARCH_LOG.md):
//   On 74M actual MNQ trades (Databento 2025-2026) every order-flow signal in this
//   family measured |IC| < 0.055 vs forward returns — ~0.7 bps against a 2.3 bps
//   cost bar. Barclay & Warner ("stealth trading", J.Fin.Econ. 1993): informed
//   institutions SPLIT orders, so the biggest prints skew toward hedgers, rollers
//   and forced flows, not smart money. Situational awareness — who is aggressive
//   right now — never an entry trigger, and never wired into MNQPropBook.
//
//  USAGE / HISTORY
//   Live: works out of the box. HISTORICAL BARS NEED TICK REPLAY, or past candles
//   show NO marks (a one-time Output message says so):
//     Tools > Options > Market data > tick "Show Tick Replay"; then right-click
//     chart > Reload All Historical Data; then Data Series dialog > tick "Tick
//     Replay" for this series. Marks then paint on every historical candle too.
//   Sizing: MNQ Big=10/Huge=25 (default). MES trades bigger: Big=25/Huge=60.
//   NQ (full) trades smaller lots relative: Big=5/Huge=10.
//  ============================================================================

namespace NinjaTrader.NinjaScript.Indicators
{
	public class BigPlayerFlow : Indicator
	{
		private double bid, ask;
		private double cumBig;                  // running signed big-lot volume (session)
		private double barBig;                  // this bar's signed big-lot volume
		private readonly Queue<string> hugeTags = new Queue<string>();
		private bool   histWarned;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Description   = "Large-lot aggressor flow marked above/below each candle (awareness, not a signal — see header).";
				Name          = "BigPlayerFlow";
				Calculate     = Calculate.OnEachTick;
				IsOverlay     = false;          // lines live in a subpanel; marks are drawn on the price panel
				DrawOnPricePanel = true;
				DisplayInDataBox = true;
				PaintPriceMarkers = false;
				IsSuspendedWhileInactive = false;

				BigSize        = 10;
				HugeSize       = 25;
				MarkThreshold  = 10;            // min |net big-lot volume| in a bar to tag it
				SaturateAt     = 60;            // net volume at which the mark is full-brightness
				ResetEachSession = true;
				ShowBarMarks   = true;
				MarkHugePrints = true;
				MaxHugeDots    = 400;

				AddPlot(new Stroke(Brushes.DodgerBlue, 2), PlotStyle.Line, "CumBigDelta");
				AddPlot(new Stroke(Brushes.Gray,       3), PlotStyle.Bar,  "BarBigDelta");
				AddLine(new Stroke(Brushes.DimGray, DashStyleHelper.Dash, 1), 0, "Zero");
			}
		}

		protected override void OnMarketData(MarketDataEventArgs e)
		{
			if (e.MarketDataType == MarketDataType.Ask) { ask = e.Price; return; }
			if (e.MarketDataType == MarketDataType.Bid) { bid = e.Price; return; }
			if (e.MarketDataType != MarketDataType.Last || e.Volume < BigSize) return;
			if (ask <= 0 || bid <= 0) return;                     // no quote yet — can't classify

			int dir = e.Price >= ask ? +1 : (e.Price <= bid ? -1 : 0);
			if (dir == 0) return;                                 // between the quotes: ambiguous

			cumBig += dir * e.Volume;
			barBig += dir * e.Volume;

			// a single HUGE print gets a dot right at its trade price
			if (MarkHugePrints && e.Volume >= HugeSize && CurrentBar >= 0)
			{
				string tag = "BPFdot_" + CurrentBar + "_" + Time[0].Ticks;
				Draw.Dot(this, tag, false, 0, e.Price, dir > 0 ? Brushes.Lime : Brushes.Red);
				hugeTags.Enqueue(tag);
				while (hugeTags.Count > MaxHugeDots) RemoveDrawObject(hugeTags.Dequeue());
			}
		}

		protected override void OnBarUpdate()
		{
			if (IsFirstTickOfBar)
			{
				barBig = 0;
				if (ResetEachSession && Bars.IsFirstBarOfSession) cumBig = 0;
			}

			if (State == State.Historical && !histWarned && !Bars.IsTickReplay)
			{
				histWarned = true;
				Print(Name + ": Tick Replay is OFF — historical candles will show NO marks (live paints from now). "
					+ "Enable it: Data Series dialog > Tick Replay, then Reload All Historical Data.");
			}

			// PER-BAR MARK on the price panel: below = big buyers, above = big sellers.
			// One tag per bar (keyed by CurrentBar) — updates live each tick, freezes at close.
			if (ShowBarMarks && CurrentBar >= 0)
			{
				string tag = "BPFbar_" + CurrentBar;
				if (Math.Abs(barBig) >= MarkThreshold)
				{
					double frac = Math.Min(Math.Abs(barBig) / Math.Max(SaturateAt, 1.0), 1.0);
					byte a = (byte)(90 + 165 * frac);            // faint -> bright with lopsidedness
					if (barBig > 0)
					{
						Brush b = new SolidColorBrush(Color.FromArgb(a, 0, 220, 60));  b.Freeze();
						Draw.TriangleUp(this, tag, false, 0, Low[0] - 2 * TickSize, b);
					}
					else
					{
						Brush b = new SolidColorBrush(Color.FromArgb(a, 235, 40, 40)); b.Freeze();
						Draw.TriangleDown(this, tag, false, 0, High[0] + 2 * TickSize, b);
					}
				}
				else
				{
					RemoveDrawObject(tag);                       // fell back below threshold intrabar
				}
			}

			Values[0][0] = cumBig;
			Values[1][0] = barBig;
		}

		#region Properties
		[NinjaScriptProperty] [Range(1, 10000)]
		[Display(Name = "Big print size (contracts)", Order = 1, GroupName = "Thresholds",
			Description = "Prints at/above this size count. MNQ ~10, MES ~25, NQ ~5.")]
		public int BigSize { get; set; }

		[NinjaScriptProperty] [Range(1, 10000)]
		[Display(Name = "Huge print size (own dot)", Order = 2, GroupName = "Thresholds")]
		public int HugeSize { get; set; }

		[NinjaScriptProperty] [Range(1, 100000)]
		[Display(Name = "Bar mark threshold (net contracts)", Order = 3, GroupName = "Thresholds",
			Description = "Minimum |net big-lot volume| in a bar before it gets an above/below mark.")]
		public int MarkThreshold { get; set; }

		[NinjaScriptProperty] [Range(1, 100000)]
		[Display(Name = "Full-brightness at (net contracts)", Order = 4, GroupName = "Thresholds",
			Description = "Net big-lot volume at which a bar's mark reaches full opacity.")]
		public int SaturateAt { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Show above/below bar marks", Order = 5, GroupName = "Behavior")]
		public bool ShowBarMarks { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Dot each huge single print", Order = 6, GroupName = "Behavior")]
		public bool MarkHugePrints { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Reset cumulative at session start", Order = 7, GroupName = "Behavior")]
		public bool ResetEachSession { get; set; }

		[NinjaScriptProperty] [Range(10, 10000)]
		[Display(Name = "Max huge dots kept", Order = 8, GroupName = "Behavior")]
		public int MaxHugeDots { get; set; }
		#endregion
	}
}
