//
// DepthOverlay — free, open-source Level-2 depth ladder on the chart (a lightweight Market Depth Map).
//
// Reads the live order book (OnMarketDepth) and draws the resting size at each price as a horizontal bar
// on the RIGHT edge of the chart — green = resting BIDS (buy walls / support), red = resting OFFERS
// (sell walls / resistance). Bar length is proportional to size, so the big walls jump out, sitting right
// next to your candles and levels.
//
// HONEST LIMITS: real-time only — Level 2 is not stored, so there is no history and no backtest; the book
// spoofs and flashes (sizes appear/pull constantly); and MNQ's book is THIN — the real size is on NQ's
// ladder. Awareness tool, not a signal.
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
	public class DepthOverlay : Indicator
	{
		private readonly object gate = new object();
		private readonly Dictionary<double, long> bidBook = new Dictionary<double, long>();
		private readonly Dictionary<double, long> askBook = new Dictionary<double, long>();

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name						= "DepthOverlay";
				Description					= "Free Level-2 depth ladder on the chart: resting bid/ask size as bars on the right edge. Real-time only; awareness, not a signal.";
				IsOverlay					= true;
				IsSuspendedWhileInactive	= true;
				DrawOnPricePanel			= true;
				Calculate					= Calculate.OnEachTick;

				MaxBarWidth	= 120;
				Opacity		= 40;
			}
			else if (State == State.Configure)
			{
				lock (gate)
				{
					bidBook.Clear();
					askBook.Clear();
				}
			}
		}

		protected override void OnBarUpdate() { }

		// build our own book from the level-two feed
		protected override void OnMarketDepth(MarketDepthEventArgs e)
		{
			if (e.IsReset)
			{
				lock (gate)
				{
					bidBook.Clear();
					askBook.Clear();
				}
				return;
			}

			lock (gate)
			{
				Dictionary<double, long> book = e.MarketDataType == MarketDataType.Ask ? askBook : bidBook;
				if (e.Operation == Cbi.Operation.Remove)
					book.Remove(e.Price);
				else                                   // Add or Update — e.Volume is the new total at that price
					book[e.Price] = e.Volume;
			}
		}

		protected override void OnRender(ChartControl chartControl, ChartScale chartScale)
		{
			if (Bars == null || Bars.Instrument == null || ChartPanel == null || RenderTarget == null || IsInHitTest)
				return;

			List<KeyValuePair<double, long>> bids, asks;
			lock (gate)
			{
				bids = new List<KeyValuePair<double, long>>(bidBook);
				asks = new List<KeyValuePair<double, long>>(askBook);
			}

			// --- TEMP diagnostic: are depth levels actually reaching us? ---
			SharpDX.DirectWrite.TextFormat dbgFmt = new SharpDX.DirectWrite.TextFormat(NinjaTrader.Core.Globals.DirectWriteFactory, "Arial", 13f);
			SharpDX.Direct2D1.Brush dbgBrush = Brushes.Yellow.ToDxBrush(RenderTarget);
			SharpDX.DirectWrite.TextLayout dbgLayout = new SharpDX.DirectWrite.TextLayout(NinjaTrader.Core.Globals.DirectWriteFactory, "DepthOverlay  bids:" + bids.Count + "  asks:" + asks.Count, dbgFmt, 360f, 22f);
			RenderTarget.DrawTextLayout(new SharpDX.Vector2(ChartPanel.X + 10f, ChartPanel.Y + 10f), dbgLayout, dbgBrush);
			dbgLayout.Dispose();
			dbgFmt.Dispose();
			dbgBrush.Dispose();

			double top = chartScale.MaxValue;
			double bot = chartScale.MinValue;

			long maxSize = 0;
			foreach (KeyValuePair<double, long> kv in bids)
				if (kv.Key <= top && kv.Key >= bot && kv.Value > maxSize) maxSize = kv.Value;
			foreach (KeyValuePair<double, long> kv in asks)
				if (kv.Key <= top && kv.Key >= bot && kv.Value > maxSize) maxSize = kv.Value;
			if (maxSize <= 0)
				return;

			float  rightEdge = ChartPanel.X + ChartPanel.W;
			double tickSize  = Bars.Instrument.MasterInstrument.TickSize;
			float  tickH     = Math.Max(1f, Math.Abs(chartScale.GetYByValue(bot) - chartScale.GetYByValue(bot + tickSize)));

			SharpDX.Direct2D1.Brush greenDx = Brushes.SeaGreen.ToDxBrush(RenderTarget);
			SharpDX.Direct2D1.Brush redDx   = Brushes.Firebrick.ToDxBrush(RenderTarget);
			greenDx.Opacity = Opacity / 100f;
			redDx.Opacity   = Opacity / 100f;

			foreach (KeyValuePair<double, long> kv in bids)
			{
				if (kv.Key > top || kv.Key < bot)
					continue;
				float len = (float)((double)kv.Value / maxSize * MaxBarWidth);
				float y   = chartScale.GetYByValue(kv.Key);
				RenderTarget.FillRectangle(new SharpDX.RectangleF(rightEdge - len, y - tickH / 2f, len, tickH), greenDx);
			}

			foreach (KeyValuePair<double, long> kv in asks)
			{
				if (kv.Key > top || kv.Key < bot)
					continue;
				float len = (float)((double)kv.Value / maxSize * MaxBarWidth);
				float y   = chartScale.GetYByValue(kv.Key);
				RenderTarget.FillRectangle(new SharpDX.RectangleF(rightEdge - len, y - tickH / 2f, len, tickH), redDx);
			}

			greenDx.Dispose();
			redDx.Dispose();
		}

		#region Properties
		[Range(20, int.MaxValue)]
		[Display(Name = "Max bar width (pixels)", GroupName = "NinjaScriptParameters", Order = 0)]
		public int MaxBarWidth { get; set; }

		[Range(5, 100)]
		[Display(Name = "Opacity (%)", GroupName = "NinjaScriptParameters", Order = 1)]
		public int Opacity { get; set; }
		#endregion
	}
}

#region NinjaScript generated code. Neither change nor remove.

namespace NinjaTrader.NinjaScript.Indicators
{
	public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
	{
		private DepthOverlay[] cacheDepthOverlay;
		public DepthOverlay DepthOverlay()
		{
			return DepthOverlay(Input);
		}

		public DepthOverlay DepthOverlay(ISeries<double> input)
		{
			if (cacheDepthOverlay != null)
				for (int idx = 0; idx < cacheDepthOverlay.Length; idx++)
					if (cacheDepthOverlay[idx] != null &&  cacheDepthOverlay[idx].EqualsInput(input))
						return cacheDepthOverlay[idx];
			return CacheIndicator<DepthOverlay>(new DepthOverlay(), input, ref cacheDepthOverlay);
		}
	}
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
	public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
	{
		public Indicators.DepthOverlay DepthOverlay()
		{
			return indicator.DepthOverlay(Input);
		}

		public Indicators.DepthOverlay DepthOverlay(ISeries<double> input )
		{
			return indicator.DepthOverlay(input);
		}
	}
}

namespace NinjaTrader.NinjaScript.Strategies
{
	public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
	{
		public Indicators.DepthOverlay DepthOverlay()
		{
			return indicator.DepthOverlay(Input);
		}

		public Indicators.DepthOverlay DepthOverlay(ISeries<double> input )
		{
			return indicator.DepthOverlay(input);
		}
	}
}

#endregion
