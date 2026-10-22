//
// AccurateVolumeProfile — open-source, fully-auditable session volume profile built from EVERY TRADE,
//                         drawn quietly behind the candles. The institutional day-trading set:
//                         RTH + overnight profiles, prior-day value, naked POCs, developing value, composite.
//
// DATA: the indicator adds a 1-tick series of the chart's instrument, so each trade is counted at the exact
//   price it printed. Nothing is estimated or spread across a bar's range (the usual shortcut, which smears
//   volume and moves the POC). Historical ticks load for the chart's "Days to load" — keep it at 5–15 days;
//   a tick series is heavy. Used from a strategy, the strategy must AddDataSeries(BarsPeriodType.Tick, 1) too.
//
// SESSIONS (Session mode):
//   RthAndOvernight — (default) one RTH profile per day (09:30–16:00 ET) and one overnight profile
//                     (18:00 ET prior day – 09:30 ET). 16:00–18:00 ET trades belong to neither.
//                     On an RTH-only chart there are no overnight trades, so only RTH profiles appear.
//   RthOnly         — RTH profiles only.
//   ChartSession    — resets on the chart's Trading Hours template, like AccurateVWAP.
//   ET times are converted from NinjaTrader's time zone (Tools > Options > General), DST included.
//
// LEVELS (standard CBOT / Market Profile rules):
//   row          = TicksPerRow ticks (1 = every price, exact)
//   POC          = row with the most volume; a tie goes to the row nearest the profile's middle. The POC price
//                  is the single most-traded tick inside that row (same tie rule).
//   Value area   = start at the POC row, compare the next two rows above with the next two below, add the
//                  larger pair (up on a tie), repeat until >= ValueArea% of the session's volume.
//   VAH / VAL    = top / bottom tick of the value-area rows.
//   Naked POC    = a finished session's POC that no later trade has touched. A move that skips through the
//                  price counts as a touch; a gap between sessions does not.
//
// ON THE CHART:
//   session profiles  histogram over each session's own bars; value-area rows tinted; POC marked
//   pPOC pVAH pVAL    prior RTH session's value, extended right
//   onPOC onH onL     the overnight session's POC and range, extended right until a new overnight starts
//   nPOC              older naked POCs, faint, extended right until traded
//   DevPOC/VAH/VAL    plots — how value developed through the session (Data Box / strategies read these)
//   left-edge strips  yesterday's RTH profile and the overnight profile pinned to the left edge,
//                     always in view whatever the zoom (tonight's overnight while it builds)
//   margin profile    optional: developing profile or an N-day RTH composite in the chart's right margin
//                     (give the chart a right margin: Chart Properties > Right side margin ≈ 130)
//
// NON-INVASIVE: renders behind the price bars, never changes the auto-scale, is not clickable, no axis
//   markers. Everything above can be switched off or recolored in the indicator's properties.
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
	public enum AvpSessionMode
	{
		RthAndOvernight,
		RthOnly,
		ChartSession
	}

	public enum AvpMarginProfile
	{
		Off,
		Developing,
		Composite
	}

	public class AccurateVolumeProfile : Indicator
	{
		private const float MinBarPixels = 2f;   // histogram rows thinner than this are merged for display only

		private readonly object gate = new object();
		private List<Profile> profiles;
		private Profile active;          // the profile trades are currently added to (null between sessions)
		private Profile plotted;         // the profile the Dev plots showed on the previous primary bar
		private Profile composite;
		private long tradeCount, compositeStamp = -1;
		private EtClock etClock;
		private double tickSize;
		private TimeSpan rthStart, rthEnd, onStart;
		private long lastTick;
		private DateTime lastTime;
		private bool haveLastTick;

		// render-thread resources, created and disposed inside OnRender
		private double[] bucketBuf = new double[256];
		private SharpDX.Direct2D1.Brush histDx, vaDx, pocDx, levelDx, nakedDx, dockHistDx, dockVaDx, dockTextDx;
		private SharpDX.Direct2D1.StrokeStyle dashStyle;
		private SharpDX.DirectWrite.TextFormat labelFormat;
		private float labelRight;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name							= "AccurateVolumeProfile";
				Description						= "Session volume profile from every trade (1-tick series): RTH + overnight profiles, POC / value area, prior-day value, naked POCs, developing value, prior-day and overnight profiles pinned to the left edge, optional composite. Draws behind price.";
				IsOverlay						= true;
				IsAutoScale						= false;
				IsSuspendedWhileInactive		= false;	// every trade must be counted, even while the chart tab is hidden
				DrawOnPricePanel				= true;
				PaintPriceMarkers				= false;
				ShowTransparentPlotsInDataBox	= true;
				Calculate						= Calculate.OnEachTick;
				BarsRequiredToPlot				= 0;

				SessionMode				= AvpSessionMode.RthAndOvernight;
				RthStartTime			= 930;
				RthEndTime				= 1600;
				OvernightStartTime		= 1800;
				TicksPerRow				= 1;
				ValueAreaPercent		= 70;
				SessionsToKeep			= 20;

				ShowSessionProfiles		= true;
				ProfileWidthPercent		= 30;
				ShowPriorLevels			= true;
				ShowNakedPocs			= true;
				ShowLabels				= true;
				MarginProfile			= AvpMarginProfile.Off;
				CompositeSessions		= 5;
				MarginWidth				= 110;
				DrawBehindBars			= true;
				HistogramOpacity		= 22;
				LineOpacity				= 70;
				LabelFontSize			= 10;
				ShowDockedProfiles		= true;
				DockWidth				= 70;
				DockOpacity				= 40;

				HistogramBrush			= Frozen(0x78, 0x80, 0x8C);	// cool gray
				ValueAreaBrush			= Frozen(0x5B, 0x8D, 0xBE);	// muted steel blue
				PocBrush				= Frozen(0xE8, 0x7A, 0x41);	// muted orange
				LevelBrush				= Frozen(0x7F, 0xA7, 0xD1);	// light steel blue

				AddPlot(new Stroke(Frozen(0xE8, 0x7A, 0x41), DashStyleHelper.Dash, 1, 60), PlotStyle.Line, "DevPOC");
				AddPlot(new Stroke(Frozen(0x7F, 0xA7, 0xD1), DashStyleHelper.Dot, 1, 50), PlotStyle.Line, "DevVAH");
				AddPlot(new Stroke(Frozen(0x7F, 0xA7, 0xD1), DashStyleHelper.Dot, 1, 50), PlotStyle.Line, "DevVAL");
			}
			else if (State == State.Configure)
			{
				AddDataSeries(BarsPeriodType.Tick, 1);
			}
			else if (State == State.DataLoaded)
			{
				tickSize		= Instrument.MasterInstrument.TickSize;
				rthStart		= HhmmToTime(RthStartTime);
				rthEnd			= HhmmToTime(RthEndTime);
				onStart			= HhmmToTime(OvernightStartTime);
				etClock			= new EtClock(NinjaTrader.Core.Globals.GeneralOptions.TimeZoneInfo);
				profiles		= new List<Profile>();
				active			= null;
				plotted			= null;
				composite		= null;
				compositeStamp	= -1;
				tradeCount		= 0;
				haveLastTick	= false;
			}
			else if (State == State.Historical)
			{
				if (DrawBehindBars)
					SetZOrder(-1);
			}
		}

		protected override void OnBarUpdate()
		{
			// --- the 1-tick series: every trade, at its exact price ---
			if (BarsInProgress == 1)
			{
				if (IsFirstTickOfBar)
					OnTrade(Closes[1][0], Volumes[1][0], Times[1][0], BarsArray[1].IsFirstBarOfSession);
				return;
			}

			// --- the chart's series: developing POC / VAH / VAL of the profile being built ---
			lock (gate)
			{
				if (active == null || active.Total <= 0)
					return;

				active.Compute(TicksPerRow, ValueAreaPercent);
				DevPoc[0] = Price(active.PocTick);
				DevVah[0] = Price(active.VahTick);
				DevVal[0] = Price(active.ValTick);

				// first bar of a new profile: don't draw the jump from the previous session's value
				if (active != plotted)
				{
					plotted = active;
					PlotBrushes[0][0] = Brushes.Transparent;
					PlotBrushes[1][0] = Brushes.Transparent;
					PlotBrushes[2][0] = Brushes.Transparent;
				}
			}
		}

		private void OnTrade(double price, double volume, DateTime time, bool firstOfChartSession)
		{
			long tick = (long)Math.Round(price / tickSize);

			lock (gate)
			{
				// 1) which profile does this trade belong to?
				int kind;
				DateTime date;
				bool include, newSession;
				if (SessionMode == AvpSessionMode.ChartSession)
				{
					kind		= KindRth;
					date		= time.Date;
					include		= true;
					newSession	= firstOfChartSession;
				}
				else
				{
					include		= Classify(etClock.ToEastern(time), SessionMode, rthStart, rthEnd, onStart, out kind, out date);
					newSession	= active != null && (active.Kind != kind || active.Date != date);
				}

				if (!include || newSession)
					Finish();
				if (include && active == null)
					Start(kind, date, time);

				// 2) naked POCs: did this trade touch a finished session's POC?
				bool gap = !haveLastTick || firstOfChartSession || (time - lastTime).TotalMinutes > 30;
				foreach (Profile p in profiles)
					if (p.Complete && !p.PocTouched && Touches(p.PocTick, lastTick, tick, gap))
						p.PocTouched = true;

				// 3) count it
				if (active != null)
				{
					active.Add(tick, volume);
					active.End = time;
				}
				lastTick		= tick;
				lastTime		= time;
				haveLastTick	= true;
				tradeCount++;
			}
		}

		private void Start(int kind, DateTime date, DateTime time)
		{
			active = new Profile { Kind = kind, Date = date, Start = time, End = time };
			profiles.Add(active);
			while (profiles.Count > SessionsToKeep)
				profiles.RemoveAt(0);
		}

		private void Finish()
		{
			if (active == null)
				return;
			active.Complete = true;
			active.Compute(TicksPerRow, ValueAreaPercent);
			active = null;
		}

		private double Price(long tick)
		{
			return Instrument.MasterInstrument.RoundToTickSize(tick * tickSize);
		}

		private DateTime SessionDate(Profile p)
		{
			return SessionMode == AvpSessionMode.ChartSession ? p.End.Date : p.Date;
		}

		private static TimeSpan HhmmToTime(int hhmm)
		{
			return new TimeSpan(Math.Min(hhmm / 100, 23), Math.Min(hhmm % 100, 59), 0);
		}

		private static SolidColorBrush Frozen(byte r, byte g, byte b)
		{
			SolidColorBrush brush = new SolidColorBrush(Color.FromRgb(r, g, b));
			brush.Freeze();
			return brush;
		}

		#region Rendering
		protected override void OnRender(ChartControl chartControl, ChartScale chartScale)
		{
			base.OnRender(chartControl, chartScale);	// the Dev plots
			if (IsInHitTest || Bars == null || ChartBars == null || ChartPanel == null || RenderTarget == null || profiles == null)
				return;

			lock (gate)
			{
				if (profiles.Count == 0)
					return;

				float left		= ChartPanel.X;
				float right		= ChartPanel.X + ChartPanel.W;
				float halfBar	= (float)chartControl.Properties.BarDistance / 2f;
				labelRight		= right - (MarginProfile == AvpMarginProfile.Off ? 4f : MarginWidth + 10f);

				SharpDX.Direct2D1.AntialiasMode oldMode = RenderTarget.AntialiasMode;
				RenderTarget.AntialiasMode = SharpDX.Direct2D1.AntialiasMode.Aliased;
				histDx		= MakeDx(HistogramBrush, HistogramOpacity);
				vaDx		= MakeDx(ValueAreaBrush, HistogramOpacity);
				pocDx		= MakeDx(PocBrush, LineOpacity);
				levelDx		= MakeDx(LevelBrush, LineOpacity);
				nakedDx		= MakeDx(PocBrush, LineOpacity / 2);
				dockHistDx	= MakeDx(HistogramBrush, DockOpacity);
				dockVaDx	= MakeDx(ValueAreaBrush, DockOpacity);
				dockTextDx	= MakeDx(Brushes.Gray, 90);
				dashStyle	= new SharpDX.Direct2D1.StrokeStyle(NinjaTrader.Core.Globals.D2DFactory,
								new SharpDX.Direct2D1.StrokeStyleProperties { DashStyle = SharpDX.Direct2D1.DashStyle.Dash });
				labelFormat	= new SharpDX.DirectWrite.TextFormat(NinjaTrader.Core.Globals.DirectWriteFactory, "Segoe UI", LabelFontSize);

				try
				{
					// the latest finished RTH and overnight profiles carry the "prior" levels
					Profile priorRth = null, priorOn = null;
					foreach (Profile p in profiles)
						if (p.Complete)
						{
							if (p.Kind == KindOvernight)
								priorOn = p;
							else
								priorRth = p;
						}
					if (active != null && active.Kind == KindOvernight)
						priorOn = null;		// last night's levels are stale once a new overnight is under way

					foreach (Profile p in profiles)
					{
						p.Compute(TicksPerRow, ValueAreaPercent);
						if (p.RowCount == 0)
							continue;

						float x0 = chartControl.GetXByBarIndex(ChartBars, Bars.GetBar(p.Start)) - halfBar;
						float x1 = chartControl.GetXByBarIndex(ChartBars, Bars.GetBar(p.End)) + halfBar;

						if (ShowSessionProfiles && x1 >= left && x0 <= right)
						{
							float width = (x1 - x0) * ProfileWidthPercent / 100f;
							if (!p.Complete)
								width = Math.Max(width, 60f);	// readable while the session is still young
							DrawHistogram(chartScale, p, x0, width, false, histDx, vaDx);
							DrawLevel(chartScale, p.PocTick, x0, x0 + width, pocDx, null, null);
						}

						if (!p.Complete)
							continue;
						float from = Math.Max(x1, left);
						if (from >= right)
							continue;

						if (ShowPriorLevels && p == priorRth)
						{
							DrawLevel(chartScale, p.PocTick, from, right, pocDx, dashStyle, "pPOC");
							DrawLevel(chartScale, p.VahTick, from, right, levelDx, dashStyle, "pVAH");
							DrawLevel(chartScale, p.ValTick, from, right, levelDx, dashStyle, "pVAL");
						}
						else if (ShowPriorLevels && p == priorOn)
						{
							DrawLevel(chartScale, p.PocTick, from, right, pocDx, dashStyle, "onPOC");
							DrawLevel(chartScale, p.HiTick, from, right, levelDx, dashStyle, "onH");
							DrawLevel(chartScale, p.LoTick, from, right, levelDx, dashStyle, "onL");
						}
						else if (ShowNakedPocs && !p.PocTouched)
							DrawLevel(chartScale, p.PocTick, from, right, nakedDx, dashStyle, "nPOC " + SessionDate(p).ToString("M/d"));
					}

					if (ShowDockedProfiles)
					{
						Profile lastOn = null;
						foreach (Profile p in profiles)
							if (p.Kind == KindOvernight)
								lastOn = p;		// this morning's overnight, or tonight's while it builds
						float x = left + 2f;
						foreach (Profile p in new Profile[] { priorRth, lastOn })
						{
							if (p == null || p.RowCount == 0)
								continue;
							DrawHistogram(chartScale, p, x, DockWidth, false, dockHistDx, dockVaDx);
							DrawLevel(chartScale, p.PocTick, x, x + DockWidth, pocDx, null, null);
							string name = p.Kind == KindOvernight ? "Overnight"
								: SessionMode == AvpSessionMode.ChartSession ? "Prior session" : "Prior day";
							using (SharpDX.DirectWrite.TextLayout layout = new SharpDX.DirectWrite.TextLayout(
									NinjaTrader.Core.Globals.DirectWriteFactory, name, labelFormat, 200f, 30f))
								RenderTarget.DrawTextLayout(new SharpDX.Vector2(x, ChartPanel.Y + 2f), layout, dockTextDx);
							x += DockWidth + 8f;
						}
					}

					if (MarginProfile != AvpMarginProfile.Off)
					{
						Profile m = MarginProfile == AvpMarginProfile.Composite ? GetComposite() : (active ?? profiles[profiles.Count - 1]);
						m.Compute(TicksPerRow, ValueAreaPercent);
						if (m.RowCount > 0)
						{
							DrawHistogram(chartScale, m, right - 2f, MarginWidth, true, histDx, vaDx);
							DrawLevel(chartScale, m.PocTick, right - 2f - MarginWidth, right - 2f, pocDx, null, null);
						}
					}
				}
				finally
				{
					RenderTarget.AntialiasMode = oldMode;
					histDx.Dispose();
					vaDx.Dispose();
					pocDx.Dispose();
					levelDx.Dispose();
					nakedDx.Dispose();
					dockHistDx.Dispose();
					dockVaDx.Dispose();
					dockTextDx.Dispose();
					dashStyle.Dispose();
					labelFormat.Dispose();
				}
			}
		}

		// one profile as horizontal bars; rows thinner than MinBarPixels are merged (display only, the levels are exact)
		private void DrawHistogram(ChartScale chartScale, Profile p, float anchorX, float maxWidth, bool fromRight,
			SharpDX.Direct2D1.Brush outside, SharpDX.Direct2D1.Brush inside)
		{
			double rowPx	= chartScale.GetPixelsForDistance(TicksPerRow * tickSize);
			int n			= rowPx >= MinBarPixels ? 1 : (int)Math.Ceiling(MinBarPixels / Math.Max(rowPx, 1e-6));
			long rowHi		= p.RowLo + p.RowCount - 1;
			long b0			= Profile.FloorDiv(p.RowLo, n);
			int buckets		= (int)(Profile.FloorDiv(rowHi, n) - b0 + 1);

			if (bucketBuf.Length < buckets)
				bucketBuf = new double[buckets * 2];
			else
				Array.Clear(bucketBuf, 0, buckets);

			double max = 0;
			for (int i = 0; i < p.RowCount; i++)
			{
				if (p.Rows[i] <= 0)
					continue;
				int j = (int)(Profile.FloorDiv(p.RowLo + i, n) - b0);
				bucketBuf[j] += p.Rows[i];
				if (bucketBuf[j] > max)
					max = bucketBuf[j];
			}
			if (max <= 0)
				return;

			long vaLo	= p.RowLo + p.VaLoRow;
			long vaHi	= p.RowLo + p.VaHiRow;
			double half	= tickSize / 2;
			for (int j = 0; j < buckets; j++)
			{
				if (bucketBuf[j] <= 0)
					continue;
				long r0			= (b0 + j) * n;
				long r1			= r0 + n - 1;
				double bottom	= r0 * TicksPerRow * tickSize - half;
				double top		= ((r1 + 1) * TicksPerRow - 1) * tickSize + half;
				if (top < chartScale.MinValue || bottom > chartScale.MaxValue)
					continue;

				float yTop	= chartScale.GetYByValue(top);
				float h		= chartScale.GetYByValue(bottom) - yTop;
				if (h >= 4)
					h -= 1;		// hairline gap between rows when there's room
				if (h < 1)
					h = 1;
				float len	= Math.Max(1f, (float)(bucketBuf[j] / max * maxWidth));
				float x		= fromRight ? anchorX - len : anchorX;
				RenderTarget.FillRectangle(new SharpDX.RectangleF(x, yTop, len, h), r1 >= vaLo && r0 <= vaHi ? inside : outside);
			}
		}

		private void DrawLevel(ChartScale chartScale, long tick, float xa, float xb, SharpDX.Direct2D1.Brush brush, SharpDX.Direct2D1.StrokeStyle style, string label)
		{
			double price = Price(tick);
			if (price < chartScale.MinValue || price > chartScale.MaxValue || xb <= xa)
				return;

			float y = chartScale.GetYByValue(price);
			if (style == null)
				RenderTarget.DrawLine(new SharpDX.Vector2(xa, y), new SharpDX.Vector2(xb, y), brush, 1f);
			else
				RenderTarget.DrawLine(new SharpDX.Vector2(xa, y), new SharpDX.Vector2(xb, y), brush, 1f, style);

			if (label == null || !ShowLabels)
				return;
			string text = label + " " + Instrument.MasterInstrument.FormatPrice(price);
			using (SharpDX.DirectWrite.TextLayout layout = new SharpDX.DirectWrite.TextLayout(NinjaTrader.Core.Globals.DirectWriteFactory, text, labelFormat, 400f, 40f))
				RenderTarget.DrawTextLayout(new SharpDX.Vector2(labelRight - layout.Metrics.Width, y - layout.Metrics.Height), layout, brush);
		}

		private SharpDX.Direct2D1.Brush MakeDx(Brush brush, int opacity)
		{
			SharpDX.Direct2D1.Brush dx = (brush ?? Brushes.Gray).ToDxBrush(RenderTarget);
			dx.Opacity = opacity / 100f;
			return dx;
		}

		// the last CompositeSessions RTH profiles (today's included) summed price by price; rebuilt only after new trades
		private Profile GetComposite()
		{
			if (composite != null && compositeStamp == tradeCount)
				return composite;

			Profile c = new Profile();
			int used = 0;
			for (int i = profiles.Count - 1; i >= 0 && used < CompositeSessions; i--)
			{
				Profile p = profiles[i];
				if (p.Kind != KindRth || p.Total <= 0)
					continue;
				for (long t = p.LoTick; t <= p.HiTick; t++)
					c.Add(t, p.VolumeAt(t));
				used++;
			}
			composite		= c;
			compositeStamp	= tradeCount;
			return c;
		}
		#endregion

		#region Profile math (plain .NET, no NinjaTrader types — unit-tested on its own)
		private const int KindRth		= 0;	// also used for ChartSession profiles
		private const int KindOvernight	= 1;

		// volume at every tick of one session, plus its POC and value area
		private sealed class Profile
		{
			public int Kind;
			public DateTime Date;			// ET trading date (RTH / overnight modes)
			public DateTime Start, End;		// first and last trade, NinjaTrader time zone
			public bool Complete, PocTouched;
			public long LoTick = long.MaxValue, HiTick = long.MinValue;
			public double Total;

			// results of Compute(); row i covers ticks (RowLo + i) * ticksPerRow ... + ticksPerRow - 1
			public double[] Rows = new double[0];
			public long RowLo;
			public int RowCount, PocRow, VaLoRow, VaHiRow;
			public long PocTick, VahTick, ValTick;

			private double[] vol;			// vol[i] = volume traded at tick baseTick + i
			private long baseTick;
			private bool dirty = true;

			public void Add(long tick, double volume)
			{
				if (volume <= 0)
					return;
				if (vol == null)
				{
					vol			= new double[512];
					baseTick	= tick - 256;
				}
				else if (tick < baseTick || tick >= baseTick + vol.Length)
					Grow(tick);

				vol[tick - baseTick] += volume;
				Total += volume;
				if (tick < LoTick)
					LoTick = tick;
				if (tick > HiTick)
					HiTick = tick;
				dirty = true;
			}

			public double VolumeAt(long tick)
			{
				long i = tick - baseTick;
				return vol != null && i >= 0 && i < vol.Length ? vol[i] : 0;
			}

			private void Grow(long tick)
			{
				long top	= baseTick + vol.Length - 1;
				long span	= Math.Max(top, tick) - Math.Min(baseTick, tick) + 1;
				int length	= (int)Math.Max(vol.Length * 2L, span + 256);
				long newBase	= tick < baseTick ? top - length + 1 : baseTick;	// keep the spare room on the side that grew
				double[] grown	= new double[length];
				Array.Copy(vol, 0, grown, baseTick - newBase, vol.Length);
				vol			= grown;
				baseTick	= newBase;
			}

			public void Compute(int ticksPerRow, double valueAreaPercent)
			{
				if (!dirty)
					return;
				dirty = false;
				if (Total <= 0)
				{
					RowCount = 0;
					return;
				}

				// rows
				RowLo		= FloorDiv(LoTick, ticksPerRow);
				RowCount	= (int)(FloorDiv(HiTick, ticksPerRow) - RowLo + 1);
				if (Rows.Length < RowCount)
					Rows = new double[RowCount + 64];
				else
					Array.Clear(Rows, 0, RowCount);
				for (long t = LoTick; t <= HiTick; t++)
				{
					double v = vol[t - baseTick];
					if (v > 0)
						Rows[FloorDiv(t, ticksPerRow) - RowLo] += v;
				}

				// POC row: most volume; tie -> nearest the middle of the profile, then the lower row
				double midRow = (RowCount - 1) / 2.0;
				int poc = 0;
				for (int i = 1; i < RowCount; i++)
					if (Rows[i] > Rows[poc] || (Rows[i] == Rows[poc] && Math.Abs(i - midRow) < Math.Abs(poc - midRow)))
						poc = i;
				PocRow = poc;

				// POC price: the most-traded tick inside the POC row, same tie rule
				long first		= Math.Max(LoTick, (RowLo + poc) * ticksPerRow);
				long last		= Math.Min(HiTick, (RowLo + poc) * ticksPerRow + ticksPerRow - 1);
				double midTick	= (LoTick + HiTick) / 2.0;
				long best		= first;
				for (long t = first + 1; t <= last; t++)
				{
					double v = vol[t - baseTick], b = vol[best - baseTick];
					if (v > b || (v == b && Math.Abs(t - midTick) < Math.Abs(best - midTick)))
						best = t;
				}
				PocTick = best;

				// value area: CBOT two-rows-at-a-time expansion from the POC
				double target	= Total * valueAreaPercent / 100.0;
				double sum		= Rows[poc];
				int lo = poc, hi = poc;
				while (sum < target && (lo > 0 || hi < RowCount - 1))
				{
					double up = 0, down = 0;
					int upRows = 0, downRows = 0;
					for (int k = 1; k <= 2 && hi + k < RowCount; k++, upRows++)
						up += Rows[hi + k];
					for (int k = 1; k <= 2 && lo - k >= 0; k++, downRows++)
						down += Rows[lo - k];

					if (upRows > 0 && (downRows == 0 || up >= down))
					{
						hi	+= upRows;
						sum	+= up;
					}
					else
					{
						lo	-= downRows;
						sum	+= down;
					}
				}
				VaLoRow	= lo;
				VaHiRow	= hi;
				VahTick	= Math.Min(HiTick, (RowLo + hi) * ticksPerRow + ticksPerRow - 1);
				ValTick	= Math.Max(LoTick, (RowLo + lo) * ticksPerRow);
			}

			public static long FloorDiv(long a, long b)
			{
				long q = a / b;
				if (a % b != 0 && (a < 0) != (b < 0))
					q--;
				return q;
			}
		}

		// NinjaTrader time zone -> US Eastern, DST-exact; the offset is cached per hour (it only changes at DST switches)
		private sealed class EtClock
		{
			private readonly TimeZoneInfo source, eastern;
			private DateTime windowStart = DateTime.MaxValue, windowEnd = DateTime.MinValue;
			private TimeSpan offset;
			private bool windowCached;

			public EtClock(TimeZoneInfo source)
			{
				this.source	= source ?? TimeZoneInfo.Local;
				eastern		= TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
			}

			public DateTime ToEastern(DateTime time)
			{
				time = DateTime.SpecifyKind(time, DateTimeKind.Unspecified);
				if (time < windowStart || time >= windowEnd)
				{
					windowStart	= new DateTime(time.Year, time.Month, time.Day, time.Hour, 0, 0);
					windowEnd	= windowStart.AddHours(1);
					TimeSpan a, b;
					// cache only when the offset is the same at both ends of the hour (not a DST-switch hour)
					windowCached = TryOffset(windowStart, out a) && TryOffset(windowEnd.AddTicks(-1), out b) && a == b;
					if (windowCached)
						offset = a;
				}
				if (windowCached)
					return time + offset;

				TimeSpan exact;
				if (TryOffset(time, out exact))
					offset = exact;
				return time + offset;
			}

			private bool TryOffset(DateTime time, out TimeSpan result)
			{
				try
				{
					result = TimeZoneInfo.ConvertTime(time, source, eastern) - time;
					return true;
				}
				catch (ArgumentException)	// a clock time skipped by a DST switch
				{
					result = TimeSpan.Zero;
					return false;
				}
			}
		}

		// which session an Eastern-time trade belongs to; false = none (RTH-only mode outside RTH, or 16:00–18:00 ET)
		private static bool Classify(DateTime et, AvpSessionMode mode, TimeSpan rthStart, TimeSpan rthEnd, TimeSpan onStart, out int kind, out DateTime date)
		{
			TimeSpan timeOfDay = et.TimeOfDay;
			kind = KindRth;
			date = et.Date;
			if (timeOfDay >= rthStart && timeOfDay < rthEnd)
				return true;
			if (mode != AvpSessionMode.RthAndOvernight)
				return false;

			kind = KindOvernight;
			if (timeOfDay >= onStart)
			{
				date = et.Date.AddDays(1);	// the evening belongs to the next day's overnight (Sunday evening -> Monday)
				return true;
			}
			return timeOfDay < rthStart;
		}

		// a trade at `tick` right after one at `prev` touched `level` if it printed there or moved through it;
		// the previous print itself doesn't count (it belongs to the earlier trade), nor does a jump across a gap
		private static bool Touches(long level, long prev, long tick, bool gap)
		{
			if (tick == level)
				return true;
			if (gap)
				return false;
			if (tick > prev)
				return level > prev && level < tick;
			if (tick < prev)
				return level < prev && level > tick;
			return false;
		}
		#endregion

		#region Properties
		[Browsable(false)] [XmlIgnore] public Series<double> DevPoc { get { return Values[0]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> DevVah { get { return Values[1]; } }
		[Browsable(false)] [XmlIgnore] public Series<double> DevVal { get { return Values[2]; } }

		[Display(Name = "Session mode", Description = "RthAndOvernight: RTH and overnight profiles. RthOnly: RTH only. ChartSession: the chart's Trading Hours sessions.", GroupName = "1. Profile", Order = 0)]
		public AvpSessionMode SessionMode { get; set; }

		[Range(0, 2359)]
		[Display(Name = "RTH start (ET, HHMM)", GroupName = "1. Profile", Order = 1)]
		public int RthStartTime { get; set; }

		[Range(0, 2359)]
		[Display(Name = "RTH end (ET, HHMM)", GroupName = "1. Profile", Order = 2)]
		public int RthEndTime { get; set; }

		[Range(0, 2359)]
		[Display(Name = "Overnight start (ET, HHMM)", GroupName = "1. Profile", Order = 3)]
		public int OvernightStartTime { get; set; }

		[Range(1, 1000)]
		[Display(Name = "Ticks per row", Description = "1 = every price (exact). Larger rows smooth the profile and the POC.", GroupName = "1. Profile", Order = 4)]
		public int TicksPerRow { get; set; }

		[Range(1, 100)]
		[Display(Name = "Value area %", GroupName = "1. Profile", Order = 5)]
		public double ValueAreaPercent { get; set; }

		[Range(2, 200)]
		[Display(Name = "Sessions kept", Description = "Profiles held in memory; also the naked-POC lookback.", GroupName = "1. Profile", Order = 6)]
		public int SessionsToKeep { get; set; }

		[Display(Name = "Session profiles", GroupName = "2. Display", Order = 0)]
		public bool ShowSessionProfiles { get; set; }

		[Range(5, 100)]
		[Display(Name = "Profile width (% of session)", GroupName = "2. Display", Order = 1)]
		public int ProfileWidthPercent { get; set; }

		[Display(Name = "Prior value (pPOC/pVAH/pVAL, onPOC/onH/onL)", GroupName = "2. Display", Order = 2)]
		public bool ShowPriorLevels { get; set; }

		[Display(Name = "Naked POCs", GroupName = "2. Display", Order = 3)]
		public bool ShowNakedPocs { get; set; }

		[Display(Name = "Labels", GroupName = "2. Display", Order = 4)]
		public bool ShowLabels { get; set; }

		[Display(Name = "Right-margin profile", Description = "Off, the developing session, or a composite of the last N RTH sessions. Set the chart's right side margin to make room.", GroupName = "2. Display", Order = 5)]
		public AvpMarginProfile MarginProfile { get; set; }

		[Range(1, 100)]
		[Display(Name = "Composite sessions", GroupName = "2. Display", Order = 6)]
		public int CompositeSessions { get; set; }

		[Range(20, 600)]
		[Display(Name = "Right-margin width (px)", GroupName = "2. Display", Order = 7)]
		public int MarginWidth { get; set; }

		[Display(Name = "Draw behind price bars", GroupName = "2. Display", Order = 8)]
		public bool DrawBehindBars { get; set; }

		[Range(1, 100)]
		[Display(Name = "Histogram opacity %", GroupName = "2. Display", Order = 9)]
		public int HistogramOpacity { get; set; }

		[Range(1, 100)]
		[Display(Name = "Line opacity %", GroupName = "2. Display", Order = 10)]
		public int LineOpacity { get; set; }

		[Range(6, 24)]
		[Display(Name = "Label size", GroupName = "2. Display", Order = 11)]
		public int LabelFontSize { get; set; }

		[Display(Name = "Prior-day and overnight profiles at the left edge", GroupName = "2. Display", Order = 12)]
		public bool ShowDockedProfiles { get; set; }

		[Range(20, 300)]
		[Display(Name = "Left-edge profile width (px)", GroupName = "2. Display", Order = 13)]
		public int DockWidth { get; set; }

		[Range(1, 100)]
		[Display(Name = "Left-edge profile opacity %", GroupName = "2. Display", Order = 14)]
		public int DockOpacity { get; set; }

		[XmlIgnore]
		[Display(Name = "Histogram", GroupName = "3. Colors", Order = 0)]
		public Brush HistogramBrush { get; set; }

		[Browsable(false)]
		public string HistogramBrushSerializable
		{
			get { return Serialize.BrushToString(HistogramBrush); }
			set { HistogramBrush = Serialize.StringToBrush(value); }
		}

		[XmlIgnore]
		[Display(Name = "Value area", GroupName = "3. Colors", Order = 1)]
		public Brush ValueAreaBrush { get; set; }

		[Browsable(false)]
		public string ValueAreaBrushSerializable
		{
			get { return Serialize.BrushToString(ValueAreaBrush); }
			set { ValueAreaBrush = Serialize.StringToBrush(value); }
		}

		[XmlIgnore]
		[Display(Name = "POC", GroupName = "3. Colors", Order = 2)]
		public Brush PocBrush { get; set; }

		[Browsable(false)]
		public string PocBrushSerializable
		{
			get { return Serialize.BrushToString(PocBrush); }
			set { PocBrush = Serialize.StringToBrush(value); }
		}

		[XmlIgnore]
		[Display(Name = "VAH / VAL / overnight range", GroupName = "3. Colors", Order = 3)]
		public Brush LevelBrush { get; set; }

		[Browsable(false)]
		public string LevelBrushSerializable
		{
			get { return Serialize.BrushToString(LevelBrush); }
			set { LevelBrush = Serialize.StringToBrush(value); }
		}
		#endregion
	}
}

#region NinjaScript generated code. Neither change nor remove.

namespace NinjaTrader.NinjaScript.Indicators
{
	public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
	{
		private AccurateVolumeProfile[] cacheAccurateVolumeProfile;
		public AccurateVolumeProfile AccurateVolumeProfile()
		{
			return AccurateVolumeProfile(Input);
		}

		public AccurateVolumeProfile AccurateVolumeProfile(ISeries<double> input)
		{
			if (cacheAccurateVolumeProfile != null)
				for (int idx = 0; idx < cacheAccurateVolumeProfile.Length; idx++)
					if (cacheAccurateVolumeProfile[idx] != null &&  cacheAccurateVolumeProfile[idx].EqualsInput(input))
						return cacheAccurateVolumeProfile[idx];
			return CacheIndicator<AccurateVolumeProfile>(new AccurateVolumeProfile(), input, ref cacheAccurateVolumeProfile);
		}
	}
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
	public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
	{
		public Indicators.AccurateVolumeProfile AccurateVolumeProfile()
		{
			return indicator.AccurateVolumeProfile(Input);
		}

		public Indicators.AccurateVolumeProfile AccurateVolumeProfile(ISeries<double> input )
		{
			return indicator.AccurateVolumeProfile(input);
		}
	}
}

namespace NinjaTrader.NinjaScript.Strategies
{
	public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
	{
		public Indicators.AccurateVolumeProfile AccurateVolumeProfile()
		{
			return indicator.AccurateVolumeProfile(Input);
		}

		public Indicators.AccurateVolumeProfile AccurateVolumeProfile(ISeries<double> input )
		{
			return indicator.AccurateVolumeProfile(input);
		}
	}
}

#endregion
