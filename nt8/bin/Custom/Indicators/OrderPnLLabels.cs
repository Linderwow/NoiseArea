#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.Linq;
using System.Windows.Input;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.NinjaScript;
using SharpDX;
using SharpDX.DirectWrite;
#endregion

public enum OrderPnLLabelSide     { Right, Left, NextToLastBar }
public enum OrderPnLLabelVertical { AboveLine, BelowLine, OnLine }
public enum OrderPnLDistanceUnit  { Points, Ticks }

namespace NinjaTrader.NinjaScript.Indicators
{
	public class OrderPnLLabels : Indicator
	{
		private readonly List<Account> subscribed = new List<Account>();
		private ChartScale lastScale;
		private bool   dragging;
		private double dragPrice = double.NaN;
		private int    dragQty;
		private bool   mouseHooked;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name                     = "OrderPnLLabels";
				Description              = "Shows projected P&L and the distance in points (or ticks) on working PT/SL order lines, plus a live readout while dragging.";
				Calculate                = Calculate.OnPriceChange;
				IsOverlay                = true;
				DrawOnPricePanel         = true;
				DisplayInDataBox         = false;
				PaintPriceMarkers        = false;
				IsSuspendedWhileInactive = false;

				AccountName        = string.Empty;
				LabelFontSize      = 12;
				LabelSide          = OrderPnLLabelSide.Right;
				LabelVertical      = OrderPnLLabelVertical.AboveLine;
				OffsetPx           = 30;
				VerticalGapPx      = 2;
				DragToleranceTicks = 8;
				ShowTicks          = true;
				DistanceUnit       = OrderPnLDistanceUnit.Points;
			}
			else if (State == State.DataLoaded)
			{
				lock (Account.All)
				{
					foreach (Account a in Account.All)
					{
						a.OrderUpdate    += OnAccountOrderUpdate;
						a.PositionUpdate += OnAccountPositionUpdate;
						subscribed.Add(a);
					}
				}
			}
			else if (State == State.Historical)
			{
				if (ChartControl != null && !mouseHooked)
				{
					ChartControl.Dispatcher.InvokeAsync(() =>
					{
						if (ChartControl == null) return;
						ChartControl.PreviewMouseLeftButtonDown += OnMouseDown;
						ChartControl.PreviewMouseMove           += OnMouseMove;
						ChartControl.PreviewMouseLeftButtonUp   += OnMouseUp;
						mouseHooked = true;
					});
				}
			}
			else if (State == State.Terminated)
			{
				foreach (Account a in subscribed)
				{
					a.OrderUpdate    -= OnAccountOrderUpdate;
					a.PositionUpdate -= OnAccountPositionUpdate;
				}
				subscribed.Clear();

				if (ChartControl != null && mouseHooked)
				{
					ChartControl.Dispatcher.InvokeAsync(() =>
					{
						if (ChartControl == null) return;
						ChartControl.PreviewMouseLeftButtonDown -= OnMouseDown;
						ChartControl.PreviewMouseMove           -= OnMouseMove;
						ChartControl.PreviewMouseLeftButtonUp   -= OnMouseUp;
					});
					mouseHooked = false;
				}
			}
		}

		protected override void OnBarUpdate() { }

		#region Account events
		private void OnAccountOrderUpdate(object sender, OrderEventArgs e)       { ForceRefresh(); }
		private void OnAccountPositionUpdate(object sender, PositionEventArgs e) { ForceRefresh(); }
		#endregion

		#region Position / order lookup
		private bool IsThisInstrument(Instrument i)
		{
			return i != null && Instrument != null && i.FullName == Instrument.FullName;
		}

		private Position FindPosition(out Account account)
		{
			account = null;
			foreach (Account a in subscribed)
			{
				if (!string.IsNullOrWhiteSpace(AccountName) && a.Name != AccountName)
					continue;

				lock (a.Positions)
				{
					Position p = a.Positions.FirstOrDefault(x => IsThisInstrument(x.Instrument) && x.MarketPosition != MarketPosition.Flat);
					if (p != null) { account = a; return p; }
				}
			}
			return null;
		}

		private static bool IsWorking(Order o)
		{
			return o.OrderState == OrderState.Working
				|| o.OrderState == OrderState.PartFilled
				|| o.OrderState == OrderState.Accepted
				|| o.OrderState == OrderState.Submitted
				|| o.OrderState == OrderState.ChangePending
				|| o.OrderState == OrderState.ChangeSubmitted
				|| o.OrderState == OrderState.TriggerPending;
		}

		private static bool IsExit(Order o, Position pos)
		{
			if (pos.MarketPosition == MarketPosition.Long)
				return o.OrderAction == OrderAction.Sell || o.OrderAction == OrderAction.SellShort;
			return o.OrderAction == OrderAction.BuyToCover || o.OrderAction == OrderAction.Buy;
		}

		private static double OrderPrice(Order o)
		{
			return o.OrderType == OrderType.Limit ? o.LimitPrice : o.StopPrice;
		}

		private List<Order> ExitOrders(Account acct, Position pos)
		{
			var result = new List<Order>();
			if (acct == null || pos == null) return result;
			lock (acct.Orders)
			{
				foreach (Order o in acct.Orders)
					if (IsThisInstrument(o.Instrument) && IsWorking(o) && IsExit(o, pos))
						result.Add(o);
			}
			return result;
		}

		private List<KeyValuePair<double, int>> StackedExits(Account acct, Position pos)
		{
			double tick = Instrument.MasterInstrument.TickSize;
			var byPrice = new Dictionary<long, KeyValuePair<double, int>>();
			foreach (Order o in ExitOrders(acct, pos))
			{
				double price = OrderPrice(o);
				if (price <= 0) continue;
				long key = (long)Math.Round(price / tick);
				int remaining = Math.Max(0, o.Quantity - o.Filled);
				KeyValuePair<double, int> cur;
				int sum = byPrice.TryGetValue(key, out cur) ? cur.Value + remaining : remaining;
				byPrice[key] = new KeyValuePair<double, int>(price, sum);
			}
			return byPrice.Values
				.Select(kv => new KeyValuePair<double, int>(kv.Key, Math.Min(kv.Value, pos.Quantity)))
				.ToList();
		}
		#endregion

		#region Mouse (live drag readout)
		private double PriceAtMouse(MouseEventArgs e)
		{
			if (lastScale == null || ChartControl == null) return double.NaN;
			System.Windows.Point p = e.GetPosition(ChartControl as System.Windows.IInputElement);
			int y = ChartingExtensions.ConvertToVerticalPixels(p.Y, ChartControl.PresentationSource);
			return lastScale.GetValueByY(y);
		}

		private void OnMouseDown(object sender, MouseButtonEventArgs e)
		{
			double price = PriceAtMouse(e);
			if (double.IsNaN(price)) return;

			Account acct;
			Position pos = FindPosition(out acct);
			if (pos == null) return;

			double tol = DragToleranceTicks * Instrument.MasterInstrument.TickSize;
			var near = StackedExits(acct, pos)
				.Where(kv => Math.Abs(kv.Key - price) <= tol)
				.OrderBy(kv => Math.Abs(kv.Key - price))
				.ToList();

			if (near.Count == 0) return;

			dragging  = true;
			dragQty   = near[0].Value;
			dragPrice = Instrument.MasterInstrument.RoundToTickSize(price);
			ForceRefresh();
		}

		private void OnMouseMove(object sender, MouseEventArgs e)
		{
			if (!dragging) return;
			if (e.LeftButton != MouseButtonState.Pressed) { dragging = false; ForceRefresh(); return; }

			double price = PriceAtMouse(e);
			if (double.IsNaN(price)) return;
			dragPrice = Instrument.MasterInstrument.RoundToTickSize(price);
			ForceRefresh();
		}

		private void OnMouseUp(object sender, MouseButtonEventArgs e)
		{
			if (!dragging) return;
			dragging = false;
			ForceRefresh();
		}
		#endregion

		#region Rendering
		private string FormatPnL(double priceDiff, int qty, out bool profit)
		{
			double pv    = Instrument.MasterInstrument.PointValue;
			double tick  = Instrument.MasterInstrument.TickSize;
			double pnl   = priceDiff * qty * pv;
			double ticks = Math.Round(priceDiff / tick);
			profit = pnl >= 0;

			string s = (pnl >= 0 ? "+$" : "-$") + Math.Abs(pnl).ToString("N2");
			if (ShowTicks)
			{
				if (DistanceUnit == OrderPnLDistanceUnit.Ticks)
					s += "  (" + (ticks >= 0 ? "+" : "") + ticks.ToString("0") + "t)";
				else
				{
					double points = ticks * tick;
					s += "  (" + (points >= 0 ? "+" : "-") + Math.Abs(points).ToString("0.##", CultureInfo.InvariantCulture) + " pts)";
				}
			}
			return s;
		}

		private void DrawLabel(ChartControl chartControl, string text, float y, bool profit, bool highlight, TextFormat tf)
		{
			using (var layout = new TextLayout(NinjaTrader.Core.Globals.DirectWriteFactory, text, tf, 600, tf.FontSize + 6))
			{
				float w     = layout.Metrics.Width + 10;
				float h     = layout.Metrics.Height + 4;
				float left  = ChartPanel.X;
				float right = ChartPanel.X + ChartPanel.W;

				float x;
				switch (LabelSide)
				{
					case OrderPnLLabelSide.Left:
						x = left + OffsetPx;
						break;
					case OrderPnLLabelSide.NextToLastBar:
						float barX = chartControl.GetXByBarIndex(ChartBars, ChartBars.ToIndex);
						x = barX + chartControl.GetBarPaintWidth(ChartBars) / 2f + OffsetPx;
						break;
					default:
						x = right - OffsetPx - w;
						break;
				}
				x = Math.Max(left + 2, Math.Min(x, right - w - 2));

				float top;
				switch (LabelVertical)
				{
					case OrderPnLLabelVertical.BelowLine: top = y + VerticalGapPx + 1;     break;
					case OrderPnLLabelVertical.OnLine:    top = y - h / 2f;               break;
					default:                              top = y - h - VerticalGapPx;    break;
				}

				var bgMedia = profit ? System.Windows.Media.Brushes.DarkGreen : System.Windows.Media.Brushes.DarkRed;
				var fgMedia = highlight ? System.Windows.Media.Brushes.Yellow : System.Windows.Media.Brushes.White;

				using (SharpDX.Direct2D1.Brush bg = bgMedia.ToDxBrush(RenderTarget))
				using (SharpDX.Direct2D1.Brush fg = fgMedia.ToDxBrush(RenderTarget))
				{
					RenderTarget.FillRectangle(new RectangleF(x, top, w, h), bg);
					if (highlight)
						RenderTarget.DrawRectangle(new RectangleF(x, top, w, h), fg, 1.5f);
					RenderTarget.DrawTextLayout(new Vector2(x + 5, top + 2), layout, fg);
				}
			}
		}

		protected override void OnRender(ChartControl chartControl, ChartScale chartScale)
		{
			base.OnRender(chartControl, chartScale);
			lastScale = chartScale;

			Account acct;
			Position pos = FindPosition(out acct);
			if (pos == null) return;

			int dir = pos.MarketPosition == MarketPosition.Long ? 1 : -1;

			using (var tf = new TextFormat(NinjaTrader.Core.Globals.DirectWriteFactory, "Arial", FontWeight.Bold, FontStyle.Normal, LabelFontSize))
			{
				foreach (var kv in StackedExits(acct, pos))
				{
					double price = kv.Key;
					int qty = kv.Value;
					bool profit;
					string text = FormatPnL((price - pos.AveragePrice) * dir, qty, out profit);
					DrawLabel(chartControl, text, chartScale.GetYByValue(price), profit, false, tf);
				}

				if (dragging && !double.IsNaN(dragPrice))
				{
					bool profit;
					string text = "If filled: " + FormatPnL((dragPrice - pos.AveragePrice) * dir, dragQty, out profit);
					DrawLabel(chartControl, text, chartScale.GetYByValue(dragPrice), profit, true, tf);
				}
			}
		}
		#endregion

		#region Properties
		[Display(Name = "Account name (blank = auto)", Order = 1, GroupName = "Parameters")]
		public string AccountName { get; set; }

		[Display(Name = "Label side", Order = 2, GroupName = "Placement")]
		public OrderPnLLabelSide LabelSide { get; set; }

		[Display(Name = "Label vertical", Order = 3, GroupName = "Placement")]
		public OrderPnLLabelVertical LabelVertical { get; set; }

		[Range(0, 2000)]
		[Display(Name = "Horizontal offset (px)", Order = 4, GroupName = "Placement")]
		public int OffsetPx { get; set; }

		[Range(0, 50)]
		[Display(Name = "Gap from line (px)", Order = 5, GroupName = "Placement")]
		public int VerticalGapPx { get; set; }

		[Range(8, 30)]
		[Display(Name = "Font size", Order = 6, GroupName = "Appearance")]
		public int LabelFontSize { get; set; }

		[Display(Name = "Show distance", Order = 7, GroupName = "Appearance")]
		public bool ShowTicks { get; set; }

		[Display(Name = "Distance in", Order = 8, GroupName = "Appearance")]
		public OrderPnLDistanceUnit DistanceUnit { get; set; }

		[Range(1, 100)]
		[Display(Name = "Drag grab tolerance (ticks)", Order = 9, GroupName = "Parameters")]
		public int DragToleranceTicks { get; set; }
		#endregion
	}
}

#region NinjaScript generated code. Neither change nor remove.

namespace NinjaTrader.NinjaScript.Indicators
{
	public partial class Indicator : NinjaTrader.Gui.NinjaScript.IndicatorRenderBase
	{
		private OrderPnLLabels[] cacheOrderPnLLabels;
		public OrderPnLLabels OrderPnLLabels()
		{
			return OrderPnLLabels(Input);
		}

		public OrderPnLLabels OrderPnLLabels(ISeries<double> input)
		{
			if (cacheOrderPnLLabels != null)
				for (int idx = 0; idx < cacheOrderPnLLabels.Length; idx++)
					if (cacheOrderPnLLabels[idx] != null &&  cacheOrderPnLLabels[idx].EqualsInput(input))
						return cacheOrderPnLLabels[idx];
			return CacheIndicator<OrderPnLLabels>(new OrderPnLLabels(), input, ref cacheOrderPnLLabels);
		}
	}
}

namespace NinjaTrader.NinjaScript.MarketAnalyzerColumns
{
	public partial class MarketAnalyzerColumn : MarketAnalyzerColumnBase
	{
		public Indicators.OrderPnLLabels OrderPnLLabels()
		{
			return indicator.OrderPnLLabels(Input);
		}

		public Indicators.OrderPnLLabels OrderPnLLabels(ISeries<double> input )
		{
			return indicator.OrderPnLLabels(input);
		}
	}
}

namespace NinjaTrader.NinjaScript.Strategies
{
	public partial class Strategy : NinjaTrader.Gui.NinjaScript.StrategyRenderBase
	{
		public Indicators.OrderPnLLabels OrderPnLLabels()
		{
			return indicator.OrderPnLLabels(Input);
		}

		public Indicators.OrderPnLLabels OrderPnLLabels(ISeries<double> input )
		{
			return indicator.OrderPnLLabels(input);
		}
	}
}

#endregion
