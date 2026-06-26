# 推广文案 — binance-trader-bot

## V2EX / 即刻 (中文)

**标题：** 开源了一个币安合约交易机器人，用回测数据驱动策略迭代

**正文：**

搞了个开源项目，不是教你炒币赚钱的，是一个真实的工程实验：

- Binance USDT-M 合约实盘跑着，小资金（36 USDT）
- RSI 均值回归策略，5m K线
- 交易所端止损止盈（bot 挂了保护单还在）
- 多层风控：日亏上限、周亏上限、连亏冷却、手动熔断
- SQLite 记录每笔交易，每日复盘报告
- 60 天回测驱动参数优化

最近做了一轮策略升级，回测结果：

| 配置 | 交易数 | 胜率 | 总PnL |
|---|---|---|---|
| 旧 v4 (RSI 20/80) | 219 | 42.5% | -49.55 USDT |
| 新 v5 (RSI 12/88+冷却) | 69 | 52.2% | +24.90 USDT |

结论：BTC 近期震荡市，20/80 太松假信号多，收紧到 12/88 + 交易后冷却1小时，从亏变赚。

重点不是收益，是整个过程：回测 → 发现问题 → 改参数 → 再回测 → 上线 → 每日复盘。所有代码、报告、策略变更记录都公开在 GitHub。

GitHub: https://github.com/jiangkaili/binance-trader-bot
在线展示: https://jiangkaili.github.io/binance-trader-bot/

技术栈：Python, Binance Futures API, SQLite, RSI mean-reversion

欢迎挑刺、提 issue、star。

---

## Reddit r/algotrading (English)

**Title:** I open-sourced my Binance futures trading bot — 60-day backtest drove a strategy overhaul from -49 to +25 USDT

**Body:**

Not a "get rich" bot. It's an engineering experiment: can an automated system trade crypto futures without losing control?

**What it does:**
- Binance USDT-M Futures, BTCUSDT, 5m candles
- RSI mean-reversion strategy with exchange-side SL/TP
- Multi-layer risk control: daily/weekly loss caps, losing-streak cooldown, manual kill-switch, post-trade cooldown
- SQLite trade journal, daily reports committed to git
- Small account (~37 USDT), 10x leverage

**The backtest story:**

I ran a 60-day backtest on real 5m candles with fee/slippage approximation. The old config (RSI 20/80, SL 0.6%, TP 0.9%) was bleeding: 219 trades, 42.5% win rate, -49.55 USDT. Too many false signals in a choppy market.

Tightened to RSI 12/88, SL 0.5%, TP 1.0%, added a 12-bar post-trade cooldown. Result: 69 trades, 52.2% win rate, +24.90 USDT.

| Config | Trades | Win% | PnL | Expectancy/trade |
|---|---|---|---|---|
| v4 (20/80, 0.6/0.9) | 219 | 42.5% | -49.55 | -0.226 |
| v5 (12/88, 0.5/1.0, cooldown) | 69 | 52.2% | +24.90 | +0.361 |

**Key lesson:** In a high-volatility choppy market, RSI 20/80 isn't extreme enough. Only true extremes (12/88) signal actual overbought/oversold. Less trading = more profit.

**Tech:** Python, Binance `/fapi/v1/algoOrder` for exchange-side SL/TP, SQLite, configurable YAML params.

GitHub: https://github.com/jiangkaili/binance-trader-bot
Live dashboard: https://jiangkaili.github.io/binance-trader-bot/

Happy to answer questions about the architecture, risk control design, or the backtest methodology. Not selling anything, not giving financial advice.

---

## X/Twitter (English, thread)

**Tweet 1:**
Open-sourced my Binance futures trading bot 🤖

Not a "get rich" scheme — it's an engineering experiment with real money, real risk control, and honest daily reports.

Just did a strategy overhaul based on 60-day backtests:

v4 → v5: from -49 to +25 USDT 📊

🧵 Thread 👇

https://github.com/jiangkaili/binance-trader-bot

**Tweet 2:**
The problem: RSI 20/80 was too loose for BTC's choppy market. 219 trades in 60 days, 42.5% win rate, bleeding money.

The fix: tightened to RSI 12/88 + 12-bar post-trade cooldown.

Result: 69 trades, 52.2% win rate, positive expectancy.

Less trading = more profit. 🎯

**Tweet 3:**
Risk control is the real product:

✅ Exchange-side SL/TP (survives bot crashes)
✅ Daily & weekly loss caps
✅ Losing-streak cooldown (24h pause after 3 losses)
✅ Post-trade cooldown (1h between trades)
✅ Manual kill-switch
✅ SQLite trade journal + daily reports

**Tweet 4:**
Everything is public:
- All code on GitHub
- Strategy version history with reasoning
- Daily reports committed to git
- Backtest methodology documented
- Failure postmortems included

If you're into #algotrading, #Python, or risk-control engineering, come take a look:

https://github.com/jiangkaili/binance-trader-bot

---

## Reddit r/CryptoCurrency (English)

**Title:** Open-sourced a Binance futures trading bot with exchange-side risk control — backtest drove a -49 to +25 USDT strategy turnaround

**Body:**

Built a trading bot for Binance USDT-M futures. Not selling signals or promising profits — it's an open engineering experiment.

The bot runs live with ~37 USDT, 10x leverage, on BTCUSDT. The focus is on risk control and transparent reporting:

- Exchange-side stop-loss/take-profit (survives bot crashes)
- Daily/weekly loss caps, losing-streak cooldown
- Post-trade cooldown to avoid re-entering during chop
- SQLite trade journal, daily reports on GitHub
- 60-day backtest with fee/slippage approximation

Strategy went through a data-driven overhaul:

Old (RSI 20/80): 219 trades, 42.5% win, -49.55 USDT
New (RSI 12/88 + cooldown): 69 trades, 52.2% win, +24.90 USDT

All code, backtests, and daily reports are public:
https://github.com/jiangkaili/binance-trader-bot

Not financial advice. Crypto futures can cause total capital loss.

---

## 发帖检查清单

- [ ] V2EX → /t/create → 选择「奇思妙想」或「分享创造」节点 → 粘贴中文文案
- [ ] Reddit r/algotrading → submit → 粘贴英文文案
- [ ] Reddit r/CryptoCurrency → submit → 粘贴英文文案
- [ ] X/Twitter → 发推文 thread（4条）
- [ ] 即刻 → 粘贴中文文案
- [ ] Hacker News → Show HN → 简短标题 "Show HN: Open-source Binance futures bot with data-driven strategy iteration"
