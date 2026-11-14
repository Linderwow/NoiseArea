# NoiseArea

Automated MNQ (Micro Nasdaq-100 futures) intraday momentum strategy for NinjaTrader 8.
It trades the published noise-area method (Zarattini, Aziz & Barbon 2024) on 5-minute bars:
a decision every 30 minutes from 10:00 to 15:30 ET, always flat by 16:00 ET, fixed published parameters.

| File | What |
|---|---|
| `nt8/bin/Custom/Strategies/NoiseAreaStrategy.cs` | the strategy: orders, daily loss stop, desync guard, heartbeat, trade log |
| `nt8/bin/Custom/Indicators/NoiseAreaMomentum.cs` | the rules: noise area, VWAP, decisions, exports; the strategy hosts it |
| `nt8/bin/Custom/Indicators/TapeRecorder.cs` | listen-only recorder of trade prints, best bid/ask and every order event and fill |
| `nt8/bin/Custom/Indicators/OrderPnLLabels.cs` | projected P&L and distance on working target and stop order lines |
| `nt8/bin/Custom/Indicators/MarketStructure.cs` | swing-point market structure: breaks of structure and changes of character |
| `nt8/bin/Custom/Indicators/PriorDayOHLCLondon.cs` | prior-day open/high/low/close plus the London session high/low |
| `nt8/bin/Custom/Indicators/RepeaterV2.cs` | repeatable time/price zones or lines |
| `nt8/templates/` | ATM, commission, trading-hours, skin and other NinjaTrader templates |
| `nt8/workspaces/` | the MNQ workspace (charts, strategy chart, recorder chart) |

The strategy needs only `NoiseAreaStrategy.cs` and `NoiseAreaMomentum.cs`. The rest is the chart setup around it.

## Install

1. With NinjaTrader 8 closed, copy the contents of `nt8/` into `Documents\NinjaTrader 8\` (or only the two strategy files).
2. Start NinjaTrader, open New > NinjaScript Editor and press F5. It must compile without errors.
3. On an MNQ 5-minute chart (data series loading 500 days, trading hours from the instrument settings), add `NoiseAreaStrategy`.
   Calculate on bar close, start behavior "Immediately submit, synchronize account", one instance per account and instrument,
   enabled before 09:35 ET.

## Rules that keep it safe

- No manual trades on the strategy's account: the desync guard stops the strategy.
- Hide Chart Trader on the strategy chart.
- Test on a simulation account first.
