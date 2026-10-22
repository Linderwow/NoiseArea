//
// PriorDayOHLCLondon
// Copy of NinjaTrader's built-in PriorDayOHLC with London session high/low added.
// Kept as a separate file so NinjaTrader updates (which overwrite @ files) don't erase it.
//
// London H/L: built live during the London window (default 3:00-8:00 am ET), then held
// flat for the rest of the day until the next London session starts.
// Window times are entered in US Eastern time and converted from your NinjaTrader time zone,
// so daylight-saving changes are handled automatically.
//
#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
	public class PriorDayOHLCLondon : Indicator
	{
		private DateTime 				currentDate		=	Core.Globals.MinDate;
		private double					currentClose;
		private double					currentHigh;
		private double					currentLow;
		private double					currentOpen;
		private double					priorDayClose;
		private double					priorDayHigh;
		private double					priorDayLow;
		private double					priorDayOpen;
		private	Data.SessionIterator	sessionIterator;

		// London session tracking
		private TimeZoneInfo			etZone;
		private bool					prevBarInLondon;
		private bool					hasLondon;
		private double					londonHigh;
		private double					londonLow;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Description					= "Prior day OHLC plus London session high/low.";
				Name						= "PriorDayOHLCLondon";
				IsAutoScale					= false;
				IsOverlay					= true;
				IsSuspendedWhileInactive	= true;
				DrawOnPricePanel			= false;

				// Defaults match the current chart setup (high + low shown, open/close hidden)
				ShowClose					= false;
				ShowLow						= true;
				ShowHigh					= true;
				ShowOpen					= false;

				ShowLondon					= true;
				LondonStartHHmm				= 300;	// 3:00 am ET
				LondonEndHHmm				= 800;	// 8:00 am ET

				// Neutral grays: prior day = lighter gray, London = darker slate gray
				AddPlot(new Stroke(Brushes.Gray,		DashStyleHelper.Dash,	1),	PlotStyle.Hash, "Prior Open");
				AddPlot(new Stroke(Brushes.DarkGray,							2),	PlotStyle.Hash, "Prior High");
				AddPlot(new Stroke(Brushes.DarkGray,	DashStyleHelper.Dash,	2),	PlotStyle.Hash, "Prior Low");
				AddPlot(new Stroke(Brushes.Gray,		DashStyleHelper.Dash,	1),	PlotStyle.Hash, "Prior Close");
				AddPlot(new Stroke(Brushes.SlateGray,							2),	PlotStyle.Hash, "LON High");
				AddPlot(new Stroke(Brushes.SlateGray,	DashStyleHelper.Dash,	2),	PlotStyle.Hash, "LON Low");
			}
			else if (State == State.Configure)
			{
				currentDate 		= Core.Globals.MinDate;
				currentClose		= 0;
				currentHigh			= 0;
				currentLow			= 0;
				currentOpen			= 0;
				priorDayClose		= 0;
				priorDayHigh		= 0;
				priorDayLow			= 0;
				priorDayOpen		= 0;
				sessionIterator		= null;

				prevBarInLondon		= false;
				hasLondon			= false;
				londonHigh			= 0;
				londonLow			= 0;
			}
			else if (State == State.DataLoaded)
			{
				sessionIterator = new Data.SessionIterator(Bars);
				try   { etZone = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time"); }
				catch { etZone = null; }
			}
			else if (State == State.Historical && !Bars.BarsType.IsIntraday)
			{
				Draw.TextFixed(this, "NinjaScriptInfo", Custom.Resource.PriorDayOHLCIntradayError, TextPosition.BottomRight);
				Log(Custom.Resource.PriorDayOHLCIntradayError, LogLevel.Error);
			}
		}

		protected override void OnBarUpdate()
		{
			if (!Bars.BarsType.IsIntraday)
				return;

			UpdatePriorDay();
			UpdateLondon();
		}

		#region Prior day (unchanged from NinjaTrader's PriorDayOHLC)
		private void UpdatePriorDay()
		{
			if (currentDate != sessionIterator.GetTradingDay(Time[0]) || currentOpen == 0)
			{
				priorDayOpen	= currentOpen;
				priorDayHigh	= currentHigh;
				priorDayLow		= currentLow;
				priorDayClose	= currentClose;

				if (ShowOpen)	PriorOpen[0]	= priorDayOpen;
				if (ShowHigh)	PriorHigh[0]	= priorDayHigh;
				if (ShowLow)	PriorLow[0]		= priorDayLow;
				if (ShowClose)	PriorClose[0]	= priorDayClose;

				currentOpen 	=	Open[0];
				currentHigh 	=	High[0];
				currentLow		=	Low[0];
				currentClose	=	Close[0];

				currentDate 	=	sessionIterator.GetTradingDay(Time[0]);
			}
			else
			{
				currentHigh 	=	Math.Max(currentHigh, High[0]);
				currentLow		=	Math.Min(currentLow, Low[0]);
				currentClose	=	Close[0];

				if (ShowOpen)	PriorOpen[0] = priorDayOpen;
				if (ShowHigh)	PriorHigh[0] = priorDayHigh;
				if (ShowLow)	PriorLow[0]  = priorDayLow;
				if (ShowClose)	PriorClose[0] = priorDayClose;
			}
		}
		#endregion

		#region London session
		private static int ToMinutes(int hhmm)
		{
			return (hhmm / 100) * 60 + (hhmm % 100);
		}

		// NinjaTrader time-stamps bars at their close, so a bar belongs to the window when
		// its close time is after the start and at or before the end.
		private bool InLondonWindow(DateTime barCloseTime)
		{
			DateTime et = barCloseTime;
			if (etZone != null)
			{
				try { et = TimeZoneInfo.ConvertTime(barCloseTime, Core.Globals.GeneralOptions.TimeZoneInfo, etZone); }
				catch { et = barCloseTime; }
			}

			int t     = et.Hour * 60 + et.Minute;
			int start = ToMinutes(LondonStartHHmm);
			int end   = ToMinutes(LondonEndHHmm);

			if (start <= end)
				return t > start && t <= end;
			return t > start || t <= end;	// window crossing midnight
		}

		private void UpdateLondon()
		{
			bool inLondon = InLondonWindow(Time[0]);

			if (inLondon)
			{
				if (!prevBarInLondon && IsFirstTickOfBar)
				{
					// First bar of a new London session
					londonHigh = High[0];
					londonLow  = Low[0];
					hasLondon  = true;
				}
				else
				{
					londonHigh = Math.Max(londonHigh, High[0]);
					londonLow  = Math.Min(londonLow,  Low[0]);
				}
			}

			if (IsFirstTickOfBar || State == State.Historical)
				prevBarInLondon = inLondon;

			if (ShowLondon && hasLondon)
			{
				LondonHigh[0] = londonHigh;
				LondonLow[0]  = londonLow;
			}
		}
		#endregion

		#region Properties
		[Browsable(false)]
		[XmlIgnore]
		public Series<double> PriorOpen => Values[0];

		[Browsable(false)]
		[XmlIgnore]
		public Series<double> PriorHigh => Values[1];

		[Browsable(false)]
		[XmlIgnore]
		public Series<double> PriorLow => Values[2];

		[Browsable(false)]
		[XmlIgnore]
		public Series<double> PriorClose => Values[3];

		[Browsable(false)]
		[XmlIgnore]
		public Series<double> LondonHigh => Values[4];

		[Browsable(false)]
		[XmlIgnore]
		public Series<double> LondonLow => Values[5];

		[Display(ResourceType = typeof(Custom.Resource), Name = "ShowClose", GroupName = "NinjaScriptParameters", Order = 0)]
		public bool ShowClose { get; set; }

		[Display(ResourceType = typeof(Custom.Resource), Name = "ShowHigh", GroupName = "NinjaScriptParameters", Order = 1)]
		public bool ShowHigh { get; set; }

		[Display(ResourceType = typeof(Custom.Resource), Name = "ShowLow", GroupName = "NinjaScriptParameters", Order = 2)]
		public bool ShowLow { get; set; }

		[Display(ResourceType = typeof(Custom.Resource), Name = "ShowOpen", GroupName = "NinjaScriptParameters", Order = 3)]
		public bool ShowOpen { get; set; }

		[Display(Name = "Show London high/low", GroupName = "London session", Order = 10)]
		public bool ShowLondon { get; set; }

		[Range(0, 2359)]
		[Display(Name = "London start (HHmm, ET)", Description = "e.g. 300 = 3:00 am ET", GroupName = "London session", Order = 11)]
		public int LondonStartHHmm { get; set; }

		[Range(0, 2359)]
		[Display(Name = "London end (HHmm, ET)", Description = "e.g. 800 = 8:00 am ET", GroupName = "London session", Order = 12)]
		public int LondonEndHHmm { get; set; }
		#endregion

		public override string FormatPriceMarker(double price) => Instrument.MasterInstrument.FormatPrice(price);
	}
}

#region NinjaScript generated code. Neither change nor remove.

namespace NinjaTrader.NinjaScript.Indicators
{
	public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
	{
		private PriorDayOHLCLondon[] cachePriorDayOHLCLondon;
		public PriorDayOHLCLondon PriorDayOHLCLondon()
		{
			return PriorDayOHLCLondon(Input);
		}

		public PriorDayOHLCLondon PriorDayOHLCLondon(ISeries<double> input)
		{
			if (cachePriorDayOHLCLondon != null)
				for (int idx = 0; idx < cachePriorDayOHLCLondon.Length; idx++)
					if (cachePriorDayOHLCLondon[idx] != null &&  cachePriorDayOHLCLondon[idx].EqualsInput(input))
						return cachePriorDayOHLCLondon[idx];
			return CacheIndicator<PriorDayOHLCLondon>(new PriorDayOHLCLondon(), input, ref cachePriorDayOHLCLondon);
		}
	}
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
	public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
	{
		public Indicators.PriorDayOHLCLondon PriorDayOHLCLondon()
		{
			return indicator.PriorDayOHLCLondon(Input);
		}

		public Indicators.PriorDayOHLCLondon PriorDayOHLCLondon(ISeries<double> input )
		{
			return indicator.PriorDayOHLCLondon(input);
		}
	}
}

namespace NinjaTrader.NinjaScript.Strategies
{
	public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
	{
		public Indicators.PriorDayOHLCLondon PriorDayOHLCLondon()
		{
			return indicator.PriorDayOHLCLondon(Input);
		}

		public Indicators.PriorDayOHLCLondon PriorDayOHLCLondon(ISeries<double> input )
		{
			return indicator.PriorDayOHLCLondon(input);
		}
	}
}

#endregion
