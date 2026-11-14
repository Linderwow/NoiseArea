# NoiseArea

Automated MNQ (Micro Nasdaq-100 futures) intraday momentum strategy for NinjaTrader 8.
It trades the published noise-area method (Zarattini, Aziz & Barbon 2024) on 5-minute bars:
a decision every 30 minutes from 10:00 to 15:30 ET, always flat by 16:00 ET, fixed published parameters.

| File | What |
|---|---|
| `nt8/bin/Custom/Strategies/NoiseAreaStrategy.cs` | the strategy: orders, daily loss stop, desync guard, heartbeat, trade log |
| `nt8/bin/Custom/Indicators/NoiseAreaMomentum.cs` | the rules: noise area, VWAP, decisions, exports; the strategy hosts it |

## Install

1. With NinjaTrader 8 closed, copy the two files into the same paths under `Documents\NinjaTrader 8\bin\Custom\`.
2. Start NinjaTrader, open New > NinjaScript Editor and press F5. It must compile without errors.
3. On an MNQ 5-minute chart (data series loading 500 days, trading hours from the instrument settings), add `NoiseAreaStrategy`.
   Calculate on bar close, start behavior "Immediately submit, synchronize account", one instance per account and instrument,
   enabled before 09:35 ET.

## Rules that keep it safe

- No manual trades on the strategy's account: the desync guard stops the strategy.
- Hide Chart Trader on the strategy chart.
- Test on a simulation account first.
