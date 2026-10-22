//
// AccurateVWAP — open-source, fully-auditable session VWAP with volume-weighted std-dev bands
//                and a steel-blue / amber value cloud (price-vs-VWAP).
//
// FORMULA (verify it against any reference / the included Python check):
//   typical price   tp   = (High + Low + Close) / 3
//   VWAP                 = Sum(tp * volume) / Sum(volume)              over the current session
//   variance (vol-wtd)   = Sum(tp^2 * volume) / Sum(volume) - VWAP^2
//   StdDev               = sqrt(max(0, variance))
//   Band k               = VWAP +/- k * StdDev
//
// CLOUD: fills the area between price (Close) and VWAP — steel blue when Close >= VWAP, amber when below.
//        Implemented as two updating regions (2 draw objects, not per-bar) so it stays light.
//
// ANCHOR: resets at the start of each trading session via Bars.IsFirstBarOfSession, which uses the
//   CHART'S Trading Hours template. So RTH-anchored vs full-Globex-anchored VWAP is controlled by the
//   chart session (right-click chart > Properties > Trading Hours). No hidden/closed logic.
//
#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
	public class AccurateVWAP : Indicator
	{
		// cumulative sums for COMPLETED bars in the current session
		private double cumPV;    // Sum(tp * vol)
		private double cumV;     // Sum(vol)
		private double cumPPV;   // Sum(tp^2 * vol)
		// the current (still-forming) bar's contribution, recomputed live
		private double barPV, barV, barPPV;

		// cloud helpers
		private Series<double> aboveSeries;   // = max(Close, VWAP)  -> green fills VWAP..above
		private Series<double> belowSeries;   // = min(Close, VWAP)  -> red   fills below..VWAP
		private Brush cloudUpBrush, cloudDnBrush;
		private Brush slopeUpBrush, slopeDnBrush;   // neutral slope palette (replaces gold/magenta)

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name						= "AccurateVWAP";
				Description					= "Open-source session VWAP + volume-weighted std-dev bands + price/VWAP cloud. Fully auditable; resets on the chart's session.";
				IsOverlay					= true;
				IsSuspendedWhileInactive	= true;
				DrawOnPricePanel			= true;
				Calculate					= Calculate.OnEachTick;

				ShowBands		= true;
				ShowCloud		= true;
				StdDev1			= 1.0;
				StdDev2			= 2.0;
				StdDev3			= 3.0;
				SlopeColoring	= true;

				// Steel & Amber palette: slate-steel base VWAP (slope recolors it live), dim cool-gray bands
				Brush vwapBaseBrush = new SolidColorBrush(Color.FromRgb(0x7E, 0x9B, 0xB0)); vwapBaseBrush.Freeze();	// slate steel
				Brush bandBrush     = new SolidColorBrush(Color.FromRgb(0x5A, 0x66, 0x72)); bandBrush.Freeze();		// cool gray (dim)
				AddPlot(new Stroke(vwapBaseBrush, 3),							PlotStyle.Line, "VWAP");
				AddPlot(new Stroke(bandBrush, DashStyleHelper.Dash, 1),			PlotStyle.Line, "Upper1");
				AddPlot(new Stroke(bandBrush, DashStyleHelper.Dash, 1),			PlotStyle.Line, "Lower1");
				AddPlot(new Stroke(bandBrush, DashStyleHelper.Dot, 1),			PlotStyle.Line, "Upper2");
				AddPlot(new Stroke(bandBrush, DashStyleHelper.Dot, 1),			PlotStyle.Line, "Lower2");
				AddPlot(new Stroke(bandBrush, DashStyleHelper.Dot, 1),			PlotStyle.Line, "Upper3");
				AddPlot(new Stroke(bandBrush, DashStyleHelper.Dot, 1),			PlotStyle.Line, "Lower3");
			}
			else if (State == State.Configure)
			{
				cumPV = cumV = cumPPV = 0;
				barPV = barV = barPPV = 0;
				// Steel & Amber cloud: steel-blue wash above VWAP, amber wash below
				cloudUpBrush = new SolidColorBrush(Color.FromRgb(0x4E, 0x7F, 0xA8));	cloudUpBrush.Freeze();	// steel blue
				cloudDnBrush = new SolidColorBrush(Color.FromRgb(0xB5, 0x88, 0x5A));	cloudDnBrush.Freeze();	// amber
				// Steel & Amber slope: bright steel blue rising, muted amber falling
				slopeUpBrush = new SolidColorBrush(Color.FromRgb(0x5F, 0xA8, 0xD6));	slopeUpBrush.Freeze();	// steel blue (rising)
				slopeDnBrush = new SolidColorBrush(Color.FromRgb(0xD7, 0x9A, 0x5B));	slopeDnBrush.Freeze();	// amber (falling)
			}
			else if (State == State.DataLoaded)
			{
				aboveSeries = new Series<double>(this);
				belowSeries = new Series<double>(this);
			}
		}

		protected override void OnBarUpdate()
		{
			if (CurrentBar < 0)
				return;

			// --- reset accumulators at the first bar of a new session ---
			if (Bars.IsFirstBarOfSession && IsFirstTickOfBar)
			{
				cumPV = cumV = cumPPV = 0;
				barPV = barV = barPPV = 0;
			}

			// --- on the first tick of a NEW bar, lock the just-closed bar's final contribution into the cumulative sums ---
			if (IsFirstTickOfBar)
			{
				if (!Bars.IsFirstBarOfSession)
				{
					cumPV  += barPV;
					cumV   += barV;
					cumPPV += barPPV;
				}
				barPV = barV = barPPV = 0;   // start the new bar fresh
			}

			// --- (re)compute the current bar's contribution live; on bar close these equal the bar's final values ---
			double tp  = (High[0] + Low[0] + Close[0]) / 3.0;
			double vol = Volume[0];
			barPV  = tp * vol;
			barV   = vol;
			barPPV = tp * tp * vol;

			double totV = cumV + barV;
			if (totV <= 0)
				return;

			double vwap = (cumPV + barPV) / totV;
			Vwap[0] = vwap;

			if (ShowBands)
			{
				double meanSq = (cumPPV + barPPV) / totV;
				double var    = meanSq - vwap * vwap;
				double sd     = var > 0 ? Math.Sqrt(var) : 0;

				Upper1[0] = vwap + StdDev1 * sd;	Lower1[0] = vwap - StdDev1 * sd;
				Upper2[0] = vwap + StdDev2 * sd;	Lower2[0] = vwap - StdDev2 * sd;
				Upper3[0] = vwap + StdDev3 * sd;	Lower3[0] = vwap - StdDev3 * sd;
			}

			// --- slope coloring: steel blue when rising, amber when falling ---
			if (SlopeColoring && CurrentBar > 0)
				PlotBrushes[0][0] = vwap >= Vwap[1] ? slopeUpBrush : slopeDnBrush;

			// --- green/red value cloud (fill between price and VWAP) ---
			if (ShowCloud)
			{
				// break the fill across session resets so it doesn't connect across the gap
				if (Bars.IsFirstBarOfSession)
				{
					aboveSeries[0] = vwap;
					belowSeries[0] = vwap;
				}
				else
				{
					aboveSeries[0] = Math.Max(Close[0], vwap);
					belowSeries[0] = Math.Min(Close[0], vwap);
				}

				if (CurrentBar > 0)
				{
					Draw.Region(this, "avwapAbove", CurrentBar, 0, Vwap, aboveSeries, Brushes.Transparent, cloudUpBrush, 22);
					Draw.Region(this, "avwapBelow", CurrentBar, 0, Vwap, belowSeries, Brushes.Transparent, cloudDnBrush, 22);
				}
			}
		}

		#region Properties
		[Browsable(false)] [XmlIgnore] public Series<double> Vwap	=> Values[0];
		[Browsable(false)] [XmlIgnore] public Series<double> Upper1	=> Values[1];
		[Browsable(false)] [XmlIgnore] public Series<double> Lower1	=> Values[2];
		[Browsable(false)] [XmlIgnore] public Series<double> Upper2	=> Values[3];
		[Browsable(false)] [XmlIgnore] public Series<double> Lower2	=> Values[4];
		[Browsable(false)] [XmlIgnore] public Series<double> Upper3	=> Values[5];
		[Browsable(false)] [XmlIgnore] public Series<double> Lower3	=> Values[6];

		[Display(Name = "Show bands", GroupName = "NinjaScriptParameters", Order = 0)]
		public bool ShowBands { get; set; }

		[Display(Name = "Show cloud", GroupName = "NinjaScriptParameters", Order = 1)]
		public bool ShowCloud { get; set; }

		[Range(0, double.MaxValue)]
		[Display(Name = "Std Dev 1", GroupName = "NinjaScriptParameters", Order = 2)]
		public double StdDev1 { get; set; }

		[Range(0, double.MaxValue)]
		[Display(Name = "Std Dev 2", GroupName = "NinjaScriptParameters", Order = 3)]
		public double StdDev2 { get; set; }

		[Range(0, double.MaxValue)]
		[Display(Name = "Std Dev 3", GroupName = "NinjaScriptParameters", Order = 4)]
		public double StdDev3 { get; set; }

		[Display(Name = "Slope coloring", GroupName = "NinjaScriptParameters", Order = 5)]
		public bool SlopeColoring { get; set; }
		#endregion
	}
}

#region NinjaScript generated code. Neither change nor remove.

namespace NinjaTrader.NinjaScript.Indicators
{
	public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
	{
		private AccurateVWAP[] cacheAccurateVWAP;
		public AccurateVWAP AccurateVWAP()
		{
			return AccurateVWAP(Input);
		}

		public AccurateVWAP AccurateVWAP(ISeries<double> input)
		{
			if (cacheAccurateVWAP != null)
				for (int idx = 0; idx < cacheAccurateVWAP.Length; idx++)
					if (cacheAccurateVWAP[idx] != null &&  cacheAccurateVWAP[idx].EqualsInput(input))
						return cacheAccurateVWAP[idx];
			return CacheIndicator<AccurateVWAP>(new AccurateVWAP(), input, ref cacheAccurateVWAP);
		}
	}
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
	public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
	{
		public Indicators.AccurateVWAP AccurateVWAP()
		{
			return indicator.AccurateVWAP(Input);
		}

		public Indicators.AccurateVWAP AccurateVWAP(ISeries<double> input )
		{
			return indicator.AccurateVWAP(input);
		}
	}
}

namespace NinjaTrader.NinjaScript.Strategies
{
	public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
	{
		public Indicators.AccurateVWAP AccurateVWAP()
		{
			return indicator.AccurateVWAP(Input);
		}

		public Indicators.AccurateVWAP AccurateVWAP(ISeries<double> input )
		{
			return indicator.AccurateVWAP(input);
		}
	}
}

#endregion
