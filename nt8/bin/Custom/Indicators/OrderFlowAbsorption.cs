//
// OrderFlowAbsorption — free, open-source tape-based absorption detector with an ADAPTIVE threshold,
// drawn as a clean badge to the left of the candle (matching OrderFlowAlerts).
//
// WHAT IT FINDS
//   Absorption = a level takes a heavy load of aggressive volume but DOES NOT break.
//     • Bid absorption (BULLISH, teal badge): sellers hit the bid, it holds  → a big buyer absorbs.
//     • Ask absorption (BEARISH, magenta badge): buyers lift the offer, it holds → a big seller absorbs.
//   The badge sits at the absorbed price, just left of the candle, with the absorbed volume.
//
// ADAPTIVE THRESHOLD
//   Each time an inside level moves, we bank how much volume it absorbed first; a rolling average of those is
//   the typical "level volume." Absorption fires when a level holds >= SizeMultiple x that (never below MinFloor),
//   so it self-scales overnight vs RTH. Fixed mode uses AbsorptionVolume instead.
//
// HONEST LIMITS: read from executed trades, NOT the spoofy raw L2 book. Real-time only; aggressor reconstructed
// from bid/ask; MNQ's book is thin. Awareness tool, NOT a predictive signal.
//
#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
	public class OrderFlowAbsorption : Indicator
	{
		private class Hit
		{
			public int    Bar;
			public double Price;
			public bool   Bid;	// true = bid absorption (bullish), false = ask absorption (bearish)
			public long   Volume;
		}

		private double lowPrice;	// lowest price currently being tested for bid absorption
		private long   lowSellVol;	// aggressive selling stacked into that low
		private double highPrice;	// highest price currently being tested for ask absorption
		private long   highBuyVol;	// aggressive buying stacked into that high

		private Queue<long> levelVolumes;
		private long        levelVolSum;

		private readonly object gate = new object();
		private readonly List<Hit> hits = new List<Hit>();
		private long alertSeq;

		// de-clutter: collapse same-side badges within this price/time window into one strongest mark
		private const double ClusterTicks = 4;
		private const int    ClusterBars  = 3;

		// a level is only ABSORPTION once price reverses this many ticks off it (confirms it held)
		private const double ConfirmTicks = 4;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name						= "OrderFlowAbsorption";
				Description					= "Tape-based absorption detector — fires only when a level HOLDS against aggressive volume — with an adaptive threshold, drawn as a badge left of the candle. Real-time only; awareness, not a signal.";
				IsOverlay					= true;
				IsSuspendedWhileInactive	= true;
				DrawOnPricePanel			= true;
				Calculate					= Calculate.OnEachTick;

				AdaptiveMode		= true;
				SizeMultiple		= 3.0;
				LookbackLevels		= 200;
				MinFloor			= 25;
				AbsorptionVolume	= 150;
				LeftOffsetPixels	= 12;
				PlayAlertSound		= false;
			}
			else if (State == State.Configure)
			{
				ResetLevels();
				levelVolumes = new Queue<long>();
				levelVolSum  = 0;
				lock (gate)
					hits.Clear();
			}
		}

		protected override void OnBarUpdate() { }

		protected override void OnMarketData(MarketDataEventArgs e)
		{
			if (e.MarketDataType != MarketDataType.Last || CurrentBar < 0)
				return;

			double tick = (Bars != null && Bars.Instrument != null) ? Bars.Instrument.MasterInstrument.TickSize : 0.25;
			double p    = e.Price;
			long   thr  = CurrentThreshold();

			// BULLISH: heavy SELLING into a LOW that then holds and bounces back up.
			// A new lower low resets the tally (still falling = not held); a bounce of ConfirmTicks confirms it.
			if (lowPrice == 0 || p < lowPrice)
			{
				if (lowSellVol > 0) RecordLevel(lowSellVol);
				lowPrice   = p;
				lowSellVol = 0;
			}
			if (p <= e.Bid && p <= lowPrice + tick)
				lowSellVol += e.Volume;
			if (lowPrice > 0 && p >= lowPrice + ConfirmTicks * tick && lowSellVol >= thr)
			{
				AddHit(true, lowPrice, lowSellVol);
				lowPrice   = p;
				lowSellVol = 0;
			}

			// BEARISH: heavy BUYING into a HIGH that then holds and rejects back down.
			if (highPrice == 0 || p > highPrice)
			{
				if (highBuyVol > 0) RecordLevel(highBuyVol);
				highPrice  = p;
				highBuyVol = 0;
			}
			if (p >= e.Ask && p >= highPrice - tick)
				highBuyVol += e.Volume;
			if (highPrice > 0 && p <= highPrice - ConfirmTicks * tick && highBuyVol >= thr)
			{
				AddHit(false, highPrice, highBuyVol);
				highPrice  = p;
				highBuyVol = 0;
			}
		}

		private long CurrentThreshold()
		{
			if (!AdaptiveMode)
				return AbsorptionVolume;
			if (levelVolumes == null || levelVolumes.Count < 20)
				return MinFloor;
			double avg = (double)levelVolSum / levelVolumes.Count;
			return (long)Math.Max(MinFloor, avg * SizeMultiple);
		}

		private void RecordLevel(long volume)
		{
			if (volume <= 0 || levelVolumes == null)
				return;
			levelVolumes.Enqueue(volume);
			levelVolSum += volume;
			while (levelVolumes.Count > LookbackLevels)
				levelVolSum -= levelVolumes.Dequeue();
		}

		private void AddHit(bool isBid, double price, long volume)
		{
			double tick  = (Bars != null && Bars.Instrument != null) ? Bars.Instrument.MasterInstrument.TickSize : 0.25;
			bool   added = false;

			lock (gate)
			{
				// fold this hit into a nearby recent badge of the same side, if one exists
				Hit merge = null;
				for (int i = hits.Count - 1; i >= 0 && hits.Count - i <= 60; i--)
				{
					Hit h = hits[i];
					if (h.Bid == isBid
						&& Math.Abs(h.Price - price) <= ClusterTicks * tick
						&& CurrentBar - h.Bar <= ClusterBars)
					{
						merge = h;
						break;
					}
				}

				if (merge != null)
				{
					if (volume > merge.Volume) { merge.Volume = volume; merge.Price = price; }
					merge.Bar = CurrentBar;
				}
				else
				{
					hits.Add(new Hit { Bar = CurrentBar, Price = price, Bid = isBid, Volume = volume });
					if (hits.Count > 1000)
						hits.RemoveRange(0, hits.Count - 1000);
					added = true;
				}
			}

			if (added && PlayAlertSound)
				Alert("abs" + (++alertSeq), Priority.High,
					(isBid ? "BID absorption " : "ASK absorption ") + volume + " @ " + price.ToString(),
					"Alert2.wav", 10, Brushes.DimGray, isBid ? Brushes.DarkCyan : Brushes.DarkMagenta);
		}

		protected override void OnRender(ChartControl chartControl, ChartScale chartScale)
		{
			if (Bars == null || ChartBars == null || RenderTarget == null || IsInHitTest)
				return;

			Hit[] snapshot;
			lock (gate)
				snapshot = hits.ToArray();

			int fromIdx = ChartBars.FromIndex;
			int toIdx   = ChartBars.ToIndex;

			SharpDX.Direct2D1.Brush bidDx   = Brushes.DarkCyan.ToDxBrush(RenderTarget);
			SharpDX.Direct2D1.Brush askDx   = Brushes.DarkMagenta.ToDxBrush(RenderTarget);
			SharpDX.Direct2D1.Brush whiteDx = Brushes.White.ToDxBrush(RenderTarget);
			SharpDX.DirectWrite.TextFormat textFormat =
				new SharpDX.DirectWrite.TextFormat(NinjaTrader.Core.Globals.DirectWriteFactory, "Arial", 10f);

			foreach (Hit h in snapshot)
			{
				if (h.Bar < fromIdx || h.Bar > toIdx)
					continue;

				float rightX = chartControl.GetXByBarIndex(ChartBars, h.Bar) - (float)LeftOffsetPixels;
				float y      = chartScale.GetYByValue(h.Price);
				DrawBadge(rightX, y, h.Volume, h.Bid ? bidDx : askDx, whiteDx, textFormat);
			}

			bidDx.Dispose();
			askDx.Dispose();
			whiteDx.Dispose();
			textFormat.Dispose();
		}

		// rounded badge whose RIGHT edge ends at rightX, vertically centred on the absorbed price
		private void DrawBadge(float rightX, float centerY, long volume,
			SharpDX.Direct2D1.Brush fill, SharpDX.Direct2D1.Brush textBrush, SharpDX.DirectWrite.TextFormat textFormat)
		{
			SharpDX.DirectWrite.TextLayout layout = new SharpDX.DirectWrite.TextLayout(
				NinjaTrader.Core.Globals.DirectWriteFactory, volume.ToString(), textFormat, 80f, 18f);

			const float padX = 5f, badgeH = 16f;
			float badgeW = layout.Metrics.Width + 2f * padX;
			float bx     = rightX - badgeW;
			float by     = centerY - badgeH / 2f;

			SharpDX.Direct2D1.RoundedRectangle rr = new SharpDX.Direct2D1.RoundedRectangle
			{
				Rect    = new SharpDX.RectangleF(bx, by, badgeW, badgeH),
				RadiusX = 3f,
				RadiusY = 3f
			};
			RenderTarget.FillRoundedRectangle(ref rr, fill);
			RenderTarget.DrawTextLayout(new SharpDX.Vector2(bx + padX, by + (badgeH - layout.Metrics.Height) / 2f), layout, textBrush);

			layout.Dispose();
		}

		private void ResetLevels() { lowPrice = 0; lowSellVol = 0; highPrice = 0; highBuyVol = 0; }

		#region Properties
		[Display(Name = "Adaptive threshold", GroupName = "NinjaScriptParameters", Order = 0)]
		public bool AdaptiveMode { get; set; }

		[Range(1.0, double.MaxValue)]
		[Display(Name = "Adaptive: x typical level", GroupName = "NinjaScriptParameters", Order = 1)]
		public double SizeMultiple { get; set; }

		[Range(20, int.MaxValue)]
		[Display(Name = "Adaptive: lookback levels", GroupName = "NinjaScriptParameters", Order = 2)]
		public int LookbackLevels { get; set; }

		[Range(1, int.MaxValue)]
		[Display(Name = "Adaptive: minimum volume", GroupName = "NinjaScriptParameters", Order = 3)]
		public int MinFloor { get; set; }

		[Range(1, int.MaxValue)]
		[Display(Name = "Fixed volume (adaptive off)", GroupName = "NinjaScriptParameters", Order = 4)]
		public int AbsorptionVolume { get; set; }

		[Range(0, int.MaxValue)]
		[Display(Name = "Left offset (pixels)", GroupName = "NinjaScriptParameters", Order = 5)]
		public int LeftOffsetPixels { get; set; }

		[Display(Name = "Play alert sound", GroupName = "NinjaScriptParameters", Order = 6)]
		public bool PlayAlertSound { get; set; }
		#endregion
	}
}

#region NinjaScript generated code. Neither change nor remove.

namespace NinjaTrader.NinjaScript.Indicators
{
	public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
	{
		private OrderFlowAbsorption[] cacheOrderFlowAbsorption;
		public OrderFlowAbsorption OrderFlowAbsorption()
		{
			return OrderFlowAbsorption(Input);
		}

		public OrderFlowAbsorption OrderFlowAbsorption(ISeries<double> input)
		{
			if (cacheOrderFlowAbsorption != null)
				for (int idx = 0; idx < cacheOrderFlowAbsorption.Length; idx++)
					if (cacheOrderFlowAbsorption[idx] != null &&  cacheOrderFlowAbsorption[idx].EqualsInput(input))
						return cacheOrderFlowAbsorption[idx];
			return CacheIndicator<OrderFlowAbsorption>(new OrderFlowAbsorption(), input, ref cacheOrderFlowAbsorption);
		}
	}
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
	public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
	{
		public Indicators.OrderFlowAbsorption OrderFlowAbsorption()
		{
			return indicator.OrderFlowAbsorption(Input);
		}

		public Indicators.OrderFlowAbsorption OrderFlowAbsorption(ISeries<double> input )
		{
			return indicator.OrderFlowAbsorption(input);
		}
	}
}

namespace NinjaTrader.NinjaScript.Strategies
{
	public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
	{
		public Indicators.OrderFlowAbsorption OrderFlowAbsorption()
		{
			return indicator.OrderFlowAbsorption(Input);
		}

		public Indicators.OrderFlowAbsorption OrderFlowAbsorption(ISeries<double> input )
		{
			return indicator.OrderFlowAbsorption(input);
		}
	}
}

#endregion
