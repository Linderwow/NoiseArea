//
// OrderFlowAlerts — free, open-source "big print" tape marker with an ADAPTIVE threshold.
//
// Listens to the live trade tape (OnMarketData / Last), flags unusually large aggressive prints, and
// AGGREGATES them per bar. Marks are drawn to the RIGHT of the candle (so they never collide with the
// absorption badges, which sit to the LEFT at the level):
//   • green up-arrow + count, lower-right of the bar  = total big aggressive BUY volume
//   • red   down-arrow + count, upper-right of the bar = total big aggressive SELL volume
// The number is the exact contract total of the big prints on that bar.
//
// THRESHOLD
//   Adaptive (default): rolling average of recent print sizes; "big" = SizeMultiple x that average
//   (never below MinFloor), so it self-scales overnight vs RTH. Fixed mode uses BigPrintSize.
//
// HONEST LIMITS: real-time only; aggressor reconstructed from bid/ask; MNQ's book is thin. Awareness tool.
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
	public class OrderFlowAlerts : Indicator
	{
		private readonly object gate = new object();
		private readonly Dictionary<int, long> barBuyVol  = new Dictionary<int, long>();
		private readonly Dictionary<int, long> barSellVol = new Dictionary<int, long>();

		private Queue<long> sizeWindow;
		private long        sizeSum;
		private long        alertSeq;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name						= "OrderFlowAlerts";
				Description					= "Large aggressive prints aggregated per bar, drawn as a green/red arrow + contract count to the RIGHT of the candle, with an adaptive threshold. Real-time only; awareness, not a signal.";
				IsOverlay					= true;
				IsSuspendedWhileInactive	= true;
				DrawOnPricePanel			= true;
				Calculate					= Calculate.OnEachTick;

				AdaptiveMode		= true;
				SizeMultiple		= 4.0;
				LookbackTrades		= 1000;
				MinFloor			= 10;
				BigPrintSize		= 25;
				RightOffsetPixels	= 12;
				PlayAlertSound		= false;
			}
			else if (State == State.Configure)
			{
				lock (gate)
				{
					barBuyVol.Clear();
					barSellVol.Clear();
				}
				sizeWindow = new Queue<long>();
				sizeSum    = 0;
			}
		}

		protected override void OnBarUpdate() { }

		protected override void OnMarketData(MarketDataEventArgs e)
		{
			if (e.MarketDataType != MarketDataType.Last || CurrentBar < 0)
				return;

			sizeWindow.Enqueue(e.Volume);
			sizeSum += e.Volume;
			while (sizeWindow.Count > LookbackTrades)
				sizeSum -= sizeWindow.Dequeue();

			double threshold;
			if (AdaptiveMode)
			{
				double avg = sizeWindow.Count > 0 ? (double)sizeSum / sizeWindow.Count : 0;
				threshold = Math.Max(MinFloor, avg * SizeMultiple);
			}
			else
				threshold = BigPrintSize;

			if (e.Volume < threshold)
				return;

			bool buy  = e.Price >= e.Ask;
			bool sell = e.Price <= e.Bid;
			if (!buy && !sell)
				return;

			lock (gate)
			{
				Dictionary<int, long> d = buy ? barBuyVol : barSellVol;
				long cur;
				d.TryGetValue(CurrentBar, out cur);
				d[CurrentBar] = cur + e.Volume;
			}

			if (PlayAlertSound)
				Alert("ofa" + (++alertSeq), Priority.Medium,
					(buy ? "Big BUY " : "Big SELL ") + e.Volume + " @ " + e.Price.ToString(),
					"Alert1.wav", 5, Brushes.DimGray, buy ? Brushes.LimeGreen : Brushes.Red);
		}

		protected override void OnRender(ChartControl chartControl, ChartScale chartScale)
		{
			if (Bars == null || ChartBars == null || RenderTarget == null || IsInHitTest)
				return;

			List<KeyValuePair<int, long>> buys, sells;
			lock (gate)
			{
				buys  = new List<KeyValuePair<int, long>>(barBuyVol);
				sells = new List<KeyValuePair<int, long>>(barSellVol);
			}

			int fromIdx = ChartBars.FromIndex;
			int toIdx   = ChartBars.ToIndex;

			SharpDX.Direct2D1.Brush greenDx = Brushes.LimeGreen.ToDxBrush(RenderTarget);
			SharpDX.Direct2D1.Brush redDx   = Brushes.Red.ToDxBrush(RenderTarget);
			SharpDX.DirectWrite.TextFormat textFormat =
				new SharpDX.DirectWrite.TextFormat(NinjaTrader.Core.Globals.DirectWriteFactory, "Arial", 11f);

			foreach (KeyValuePair<int, long> kv in buys)
			{
				if (kv.Key < fromIdx || kv.Key > toIdx)
					continue;
				float x = chartControl.GetXByBarIndex(ChartBars, kv.Key) + RightOffsetPixels;
				float y = chartScale.GetYByValue(Bars.GetLow(kv.Key));
				DrawArrow(x, y, true, kv.Value, greenDx, textFormat);
			}

			foreach (KeyValuePair<int, long> kv in sells)
			{
				if (kv.Key < fromIdx || kv.Key > toIdx)
					continue;
				float x = chartControl.GetXByBarIndex(ChartBars, kv.Key) + RightOffsetPixels;
				float y = chartScale.GetYByValue(Bars.GetHigh(kv.Key));
				DrawArrow(x, y, false, kv.Value, redDx, textFormat);
			}

			greenDx.Dispose();
			redDx.Dispose();
			textFormat.Dispose();
		}

		// arrow centred at (x, y) with the contract count printed to its RIGHT
		private void DrawArrow(float x, float y, bool buy, long volume,
			SharpDX.Direct2D1.Brush brush, SharpDX.DirectWrite.TextFormat textFormat)
		{
			const float half = 6f;

			SharpDX.Direct2D1.PathGeometry geometry = new SharpDX.Direct2D1.PathGeometry(RenderTarget.Factory);
			SharpDX.Direct2D1.GeometrySink sink     = geometry.Open();
			if (buy)
			{
				sink.BeginFigure(new SharpDX.Vector2(x, y - half), SharpDX.Direct2D1.FigureBegin.Filled);
				sink.AddLine(new SharpDX.Vector2(x - half, y + half));
				sink.AddLine(new SharpDX.Vector2(x + half, y + half));
			}
			else
			{
				sink.BeginFigure(new SharpDX.Vector2(x, y + half), SharpDX.Direct2D1.FigureBegin.Filled);
				sink.AddLine(new SharpDX.Vector2(x - half, y - half));
				sink.AddLine(new SharpDX.Vector2(x + half, y - half));
			}
			sink.EndFigure(SharpDX.Direct2D1.FigureEnd.Closed);
			sink.Close();
			RenderTarget.FillGeometry(geometry, brush);
			sink.Dispose();
			geometry.Dispose();

			SharpDX.DirectWrite.TextLayout layout = new SharpDX.DirectWrite.TextLayout(
				NinjaTrader.Core.Globals.DirectWriteFactory, volume.ToString(), textFormat, 80f, 18f);
			float tx = x + half + 4f;
			float ty = y - layout.Metrics.Height / 2f;
			RenderTarget.DrawTextLayout(new SharpDX.Vector2(tx, ty), layout, brush);
			layout.Dispose();
		}

		#region Properties
		[Display(Name = "Adaptive threshold", GroupName = "NinjaScriptParameters", Order = 0)]
		public bool AdaptiveMode { get; set; }

		[Range(1.0, double.MaxValue)]
		[Display(Name = "Adaptive: x recent average", GroupName = "NinjaScriptParameters", Order = 1)]
		public double SizeMultiple { get; set; }

		[Range(20, int.MaxValue)]
		[Display(Name = "Adaptive: lookback trades", GroupName = "NinjaScriptParameters", Order = 2)]
		public int LookbackTrades { get; set; }

		[Range(1, int.MaxValue)]
		[Display(Name = "Adaptive: minimum size", GroupName = "NinjaScriptParameters", Order = 3)]
		public int MinFloor { get; set; }

		[Range(1, int.MaxValue)]
		[Display(Name = "Fixed size (adaptive off)", GroupName = "NinjaScriptParameters", Order = 4)]
		public int BigPrintSize { get; set; }

		[Range(0, int.MaxValue)]
		[Display(Name = "Right offset (pixels)", GroupName = "NinjaScriptParameters", Order = 5)]
		public int RightOffsetPixels { get; set; }

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
		private OrderFlowAlerts[] cacheOrderFlowAlerts;
		public OrderFlowAlerts OrderFlowAlerts()
		{
			return OrderFlowAlerts(Input);
		}

		public OrderFlowAlerts OrderFlowAlerts(ISeries<double> input)
		{
			if (cacheOrderFlowAlerts != null)
				for (int idx = 0; idx < cacheOrderFlowAlerts.Length; idx++)
					if (cacheOrderFlowAlerts[idx] != null &&  cacheOrderFlowAlerts[idx].EqualsInput(input))
						return cacheOrderFlowAlerts[idx];
			return CacheIndicator<OrderFlowAlerts>(new OrderFlowAlerts(), input, ref cacheOrderFlowAlerts);
		}
	}
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
	public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
	{
		public Indicators.OrderFlowAlerts OrderFlowAlerts()
		{
			return indicator.OrderFlowAlerts(Input);
		}

		public Indicators.OrderFlowAlerts OrderFlowAlerts(ISeries<double> input )
		{
			return indicator.OrderFlowAlerts(input);
		}
	}
}

namespace NinjaTrader.NinjaScript.Strategies
{
	public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
	{
		public Indicators.OrderFlowAlerts OrderFlowAlerts()
		{
			return indicator.OrderFlowAlerts(Input);
		}

		public Indicators.OrderFlowAlerts OrderFlowAlerts(ISeries<double> input )
		{
			return indicator.OrderFlowAlerts(input);
		}
	}
}

#endregion
