# 📊 Daily Trading Reports

Every day the bot is live, a markdown report is committed to this directory with the day's trades, account balance, strategy reflection, and any bug fixes shipped that day.

> The most recent report sits at the top of the list below. All numbers come from `data/trades.db` (SQLite) and the Binance Futures API at report-generation time — every claim can be re-derived by pulling the repo and running the SQL the report cites.

---

## 📅 Latest Report

**👉 [2026-06-23 — kill-switch bug fix + restart + first clean recovery trade](2026/06/2026-06-23.md)**

Highlights:
- Discovered and fixed a kill-switch bug where backfilled (manually reconciled) trades were poisoning the bot's cumulative-P&L calculation, falsely tripping the −10% circuit breaker
- Bot was paused for ~12 hours before the fix landed
- After fix + restart: opened a fresh LONG BTCUSDT 0.008 @ 63362.9, exchange-side SL @ 62729.3, TP @ 63996.5
- Bot-realized PnL since launch: **+3.34 USDT** across 4 closed trades (100% win rate on bot-managed exits)

---

## 📂 All Reports

### 2026

#### June 2026
- [2026-06-23](2026/06/2026-06-23.md) — kill-switch bug fix; bot resumed live trading

*(More reports will appear here as the bot keeps running.)*

---

## 🔍 How to Read a Daily Report

Each report follows the same structure:

1. **Running State** — process status, last heartbeat, current position, strategy params
2. **Account Snapshot** — wallet balance, available margin, open positions (pulled live from Binance API at report time)
3. **Trade Stats** — every closed trade with entry/exit/P&L, daily breakdown, per-strategy breakdown, fee accounting
4. **Strategy Analysis** — what the data is saying about the current parameters, what's working, what isn't
5. **Code Changes** — any bugs found and fixed that day, with repro steps and the commit SHA
6. **Recommendations** — proposed parameter tweaks or risk improvements (with priority labels)

The intent is that anyone can clone the repo, open the trade DB, and re-run the same queries the report ran — full reproducibility, no hand-waved numbers.

---

## 💡 Want to Reproduce the Numbers?

```bash
# Clone the repo
git clone https://github.com/jiangkaili/binance-trader-bot.git
cd binance-trader-bot

# Open the SQLite trade log
sqlite3 data/trades.db

# All bot-realized PnL (excludes manually-reconciled rows)
SELECT COALESCE(SUM(pnl), 0) FROM trades
WHERE order_id IS NULL OR order_id NOT LIKE 'backfilled_%';

# Every trade
SELECT id, ts, side, qty, price, pnl, order_id FROM trades ORDER BY id;
```

The bot itself is in `scripts/live_trader.py`. The report-generation prompt and template are not (yet) automated — each report is regenerated manually from the same data sources. If you'd like to contribute an automated daily-report script, PRs welcome.
