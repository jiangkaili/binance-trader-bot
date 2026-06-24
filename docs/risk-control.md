# Risk-Control Design

This project treats risk control as the product, not as a footnote.

A strategy can be wrong for hours or days. The system must still fail in a bounded way.

## Design principles

1. **Assume the strategy will be wrong**
   - Indicators overfit.
   - Market regimes change.
   - Backtests undercount slippage, funding, outages, and emotional intervention.

2. **Place protection on the exchange**
   - A Python process can crash.
   - Windows can reboot.
   - The network can disappear.
   - If stop-loss protection only exists in memory, it is not protection.

3. **Separate strategy mistakes from IO mistakes**
   - A strategy bug can lose several trades.
   - An exchange integration bug can leave a naked leveraged position.
   - The IO layer is therefore treated as a contract and tested separately.

4. **Prefer explicit pauses over silent degradation**
   - Kill-switch files, cooldown timestamps, and loss caps should be visible.
   - "Bot did not trade" is a valid outcome when the risk layer says no.

5. **Document failures publicly**
   - Losses, bugs, and manual interventions are part of the experiment.
   - Hiding them makes the project less useful and less credible.

## Current risk layers

| Layer | Mechanism | Failure it limits |
|---|---|---|
| Position size cap | `target_position_usdt` in config | One trade consuming too much margin |
| Leverage cap | `leverage` in config | Small adverse move becoming liquidation risk |
| Exchange-side SL / TP | Binance algo orders | Bot crash / host crash / network outage |
| Code-side SL / TP | Live loop checks price vs entry | Backup while the process is alive |
| Daily loss cap | `daily_loss_pct` | Revenge-trading after a bad session |
| Weekly loss cap | `weekly_loss_pct` | Slow account bleed across days |
| Losing-streak cooldown | `streak_loss_count`, `streak_cooldown_hours` | Same broken condition firing repeatedly |
| Manual kill-switch | `data/KILLSWITCH` | Operator wants graceful pause / no new entries |
| State reconciliation | Exchange queries + local state | Local state drift after restart or manual fills |
| API contract tests | `tests/test_exchange_contract.py` | Accidentally using the wrong Binance endpoint |

## Exchange-side protection

The bot uses Binance USDⓈ-M Futures algo-order endpoints for protective orders.

Important implementation facts:

- Conditional SL / TP orders use `POST /fapi/v1/algoOrder`.
- They require `algoType=CONDITIONAL`.
- The trigger field is `triggerPrice`, not the old `stopPrice` pattern.
- Open protective orders are listed with `GET /fapi/v1/openAlgoOrders`.
- These algo orders do **not** appear in `GET /fapi/v1/openOrders`.

That last point is critical: checking only open orders can make a protected position look naked.

## Kill-switch semantics

`data/KILLSWITCH` means:

- do not open new positions
- preserve existing state
- allow explicit operator review before resuming

It should not be confused with "delete all state and pretend nothing happened". A kill-switch is a controlled pause, not a history eraser.

## What a daily report should answer

Every daily report should answer these questions:

1. Was the bot running?
2. Did the bot have a position?
3. Were exchange-side protective orders present?
4. Did the bot trade today?
5. If it did not trade, was that because of strategy conditions or a risk block?
6. Did any risk layer fire?
7. Did local state match exchange truth?
8. What should be watched next?

## Non-goals

This project does not claim:

- guaranteed profit
- stable yield
- copy-trading signals
- safe use of leverage
- suitability for any user's financial situation

It is a public automation and risk-control experiment.
