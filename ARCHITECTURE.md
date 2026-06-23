# Architecture — IO / Strategy Boundary

This bot has a **hard boundary** between two layers. Knowing which side
your change touches saves money.

```
┌─────────────────────────────────────────────────────────────────┐
│  STRATEGY LAYER  — change this freely                           │
│                                                                  │
│  trader/trader.py            policy: when to enter / size / exit │
│  trader/risk.py              R:R, killswitch, loss caps          │
│  trader/state.py             pnl_state, dryrun bookkeeping       │
│  scripts/live_trader.py      legacy v1 main loop                 │
│  scripts/backtest_*.py       offline simulation                  │
│  gridtrader/quant/           indicators / backtest / storage     │
│  config/strategy.yaml        knobs                               │
│                                                                  │
│         ▲ calls only through BinanceFutures.<method>(...)       │
└─────────│───────────────────────────────────────────────────────┘
          │
┌─────────│───────────────────────────────────────────────────────┐
│  IO LAYER  — TREAT AS FROZEN. Change only for API migrations.   │
│                                                                  │
│  trader/exchange.py          BinanceFutures — every REST call    │
│  gridtrader/quant/hmac_client.py   signing + clock-skew recovery │
└──────────────────────────────────────────────────────────────────┘
```

## Why the boundary

A bug in the strategy layer costs you a few trades. A bug in the IO
layer can:

* miss SL/TP placement → naked position → blow up the account
* loop-retry stale orders → exchange ban
* send wrong `reduceOnly` / `positionSide` → flip from hedge to open

The IO layer is **load-bearing** and was extracted on purpose
(Phase 2 refactor, 2026-06). Treat any edit to it with the same
caution as a `git push --force` to master.

## Rules

### Strategy changes (>95% of work)

* OK: tweak entry thresholds, ATR multiplier, R:R, killswitch caps,
  position sizing, log formatting, add new indicators, add scripts that
  consume `BinanceFutures`.
* Touch: `trader/trader.py`, `trader/risk.py`, `gridtrader/quant/*`,
  `config/*`, `scripts/*` (as long as they import `BinanceFutures`).
* **Do NOT** add a new `requests.get("https://fapi...")` or a new
  `hmac.new(...)` anywhere outside `trader/exchange.py`. If you need
  a Binance endpoint that isn't exposed yet, add a method to
  `BinanceFutures` first, in a separate commit, with the contract
  test updated.

### IO changes (rare — only for Binance migrations / new endpoints)

* Must update `tests/test_exchange_contract.py` in the **same commit**.
  Specifically:
  - new public method → add to `PUBLIC_METHODS`
  - new endpoint → add to `EXPECTED_ENDPOINTS`
  - renamed/removed method → review every strategy-layer caller
* PR description should call out: "this touches the IO contract".
* Bump the file's banner comment date.

## Tests that enforce this

```
pytest tests/test_exchange_contract.py    # IO contract  (5 tests, <1s)
pytest tests/test_trader_v2.py            # strategy     (12 tests, <1s)
```

The contract test will fail loudly if:

* a `BinanceFutures` method appears/disappears without updating the contract;
* a Binance endpoint is called that isn't on the approved list;
* SL/TP accidentally regresses to `/fapi/v1/order` (the broken path
  since 2025-12-09);
* a market order forgets `reduceOnly` when requested;
* `dry_run=True` leaks an HTTP call.

## Known facts about Binance USDⓈ-M Futures (load-bearing)

* Since **2025-12-09**: STOP_MARKET / TAKE_PROFIT_MARKET /
  TRAILING_STOP_MARKET MUST be placed via `POST /fapi/v1/algoOrder`
  with `algoType=CONDITIONAL` and `triggerPrice` (not `stopPrice`).
  The legacy `POST /fapi/v1/order` returns `-4120` for these types.
* Algo orders are queried via `GET /fapi/v1/openAlgoOrders` — they
  do NOT appear in `/fapi/v1/openOrders`. Confusing the two is how
  you mistakenly conclude a position is naked when it isn't.
* `closePosition=true` and `quantity` are mutually exclusive in algo
  orders; pick one. `reduceOnly` cannot be sent with `closePosition=true`.
* Clock drift > 1s causes `-1021 Timestamp ahead`. `BinanceFutures`
  auto-resyncs every 30 min and on the next call after a `-1021`.
* WSL can't reach `fapi.binance.com` directly. Production runs on
  Windows with proxy `http://127.0.0.1:12000`. See
  `crypto-trading` skill → `code-change-and-push-workflow.md`.

## Scripts that hold the line

All of these import `BinanceFutures` and never sign their own requests:

* `scripts/check_open_orders.py` — read-only inventory
* `scripts/list_algo_orders.py`  — read-only algo-order listing
* `scripts/place_safety_stop.py` — idempotent SL+TP placement
* `scripts/live_trader.py`       — production main loop (v1)

If you add a script that talks to Binance, do the same.
