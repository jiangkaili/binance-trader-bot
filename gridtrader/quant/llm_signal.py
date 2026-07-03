"""LLM-based market analysis signal (v10).

Calls an OpenAI-compatible LLM API with current market indicators and
recent price action, asking it to assess market regime and provide a
trading recommendation. Acts as a confluence/override layer on top of
the existing RSI + ADX + funding rate signals.

Design:
  - Called every N ticks (default 6 = 1 hour at 5m bars)
  - Caches last response between calls
  - Can override ADX filter when LLM assesses trend is exhausting
  - Can provide standalone signals when RSI is FLAT
  - Graceful fallback to FLAT on API errors

LLM 角色：智能市场分析层。
  - 每 N 个 tick 调用一次（默认 6 = 5分钟K线下每小时一次）
  - 缓存上次响应
  - 可覆盖 ADX 过滤（当 LLM 判断趋势正在衰竭时）
  - 可在 RSI 为 FLAT 时提供独立信号
  - API 错误时优雅降级为 FLAT
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum

import pandas as pd


class LLMSignal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"


@dataclass
class LLMAssessment:
    """Result of one LLM market analysis call. / 一次LLM市场分析的结果。"""
    signal: LLMSignal = LLMSignal.FLAT
    confidence: float = 0.0       # 0.0 ~ 1.0 / 置信度
    regime: str = "unknown"       # trending / ranging / exhausting / 未知
    reasoning: str = ""           # one-sentence explanation / 一句话解释
    ts: float = 0.0               # unix timestamp of the call / 调用时的unix时间戳
    error: str = ""               # non-empty if API failed / API失败时非空

    @property
    def is_valid(self) -> bool:
        """True if the assessment was successful (no error). / 评估成功（无错误）时为True。"""
        return not self.error and self.ts > 0

    @property
    def is_stale(self) -> bool:
        """True if assessment is older than 2x the call interval. / 评估是否过期（超过调用间隔的2倍）。"""
        if not self.ts:
            return True
        return (time.time() - self.ts) > 7200  # 2 hours / 2小时


# The system prompt sets the LLM's role and strategy context.
# / 系统提示词设定 LLM 的角色和策略上下文。
_SYSTEM_PROMPT = """\
You are a quantitative trading analyst monitoring BTCUSDT perpetual futures on Binance.

Strategy context: Mean-reversion using RSI extremes (BUY when RSI<20=oversold, SELL when RSI>80=overbought).
Risk parameters: stop-loss -1.5%, take-profit +3.0%, 5x leverage, 15 USDT margin.
The ADX trend filter normally blocks trades when ADX>25 (strong trend), but you can override it.

Your job:
1. Assess whether the market is trending or ranging, and whether any trend is exhausting.
2. Determine if a mean-reversion trade is appropriate right now.
3. If RSI is near extremes but ADX>25, decide whether the trend is exhausting enough to justify overriding the ADX filter.
4. Provide a clear trading recommendation.

Important:
- Only recommend BUY or SELL with confidence>0.5 when you have a clear rationale.
- When uncertain, return FLAT.
- Consider funding rate z-score: positive z means longs are paying shorts (longs overcrowded), negative z means shorts overcrowded.
- RSI extremes during strong trends often signal continuation, NOT reversal — be cautious.

Respond ONLY with valid JSON, no markdown, no explanation outside JSON:
{"signal": "BUY" or "SELL" or "FLAT", "confidence": 0.0-1.0, "regime": "trending" or "ranging" or "exhausting", "reasoning": "one sentence in Chinese"}
"""


def _build_user_prompt(
    price: float,
    rsi: float,
    adx: float,
    ema200: float,
    funding_z: float,
    rsi_signal: str,
    adx_threshold: float,
    recent_bars: pd.DataFrame,
) -> str:
    """Build the user prompt with current market data. / 用当前市场数据构建用户提示词。"""
    # Determine market status / 判断市场状态
    adx_status = f"BLOCKED (ADX>{adx_threshold:.0f})" if adx > adx_threshold else f"OK (ADX≤{adx_threshold:.0f})"
    ema_side = "above" if price > ema200 else "below"

    # Funding interpretation / 资金费率解读
    if funding_z > 1.0:
        fund_desc = f"longs overcrowded (z={funding_z:.2f})"
    elif funding_z < -1.0:
        fund_desc = f"shorts overcrowded (z={funding_z:.2f})"
    else:
        fund_desc = f"neutral (z={funding_z:.2f})"

    # Recent price action table (last 12 bars) / 近期价格走势表（最近12根K线）
    bars_to_show = min(12, len(recent_bars))
    recent = recent_bars.tail(bars_to_show)
    lines = []
    for ts, row in recent.iterrows():
        ts_str = ts.strftime("%H:%M") if hasattr(ts, "strftime") else str(ts)
        lines.append(f"  {ts_str}  O={row['open']:.0f} H={row['high']:.0f} L={row['low']:.0f} C={row['close']:.0f} V={row['volume']:.1f}")
    candles_table = "\n".join(lines)

    # Price change over recent window / 近期窗口价格变化
    if len(recent) >= 2:
        pct_change = (recent.iloc[-1]["close"] - recent.iloc[0]["close"]) / recent.iloc[0]["close"] * 100
        trend_desc = f"+{pct_change:.2f}%" if pct_change >= 0 else f"{pct_change:.2f}%"
    else:
        trend_desc = "N/A"

    return f"""\
Current market data (BTCUSDT perpetual, 5m bars):
- Price: {price:.0f} USDT
- RSI(7): {rsi:.1f}
- ADX(14): {adx:.1f} — ADX filter: {adx_status}
- EMA200: {ema200:.0f} (price {ema_side} EMA)
- Funding z-score: {funding_z:.2f} — {fund_desc}
- RSI signal: {rsi_signal}
- Recent {bars_to_show}-bar change: {trend_desc}

Recent 5m candles:
{candles_table}

Assess the market and provide your recommendation as JSON."""


def _parse_llm_response(text: str) -> LLMAssessment:
    """Parse the LLM's JSON response. / 解析LLM的JSON响应。"""
    # Strip markdown code fences if present / 去除可能的markdown代码块标记
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    # Sometimes LLMs add text before/after JSON, try to extract / LLM有时在JSON前后加文字，尝试提取
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return LLMAssessment(error=f"JSON parse failed: {text[:200]}")

    sig_str = str(data.get("signal", "FLAT")).upper().strip()
    try:
        signal = LLMSignal(sig_str) if sig_str in ("BUY", "SELL", "FLAT") else LLMSignal.FLAT
    except ValueError:
        signal = LLMSignal.FLAT

    confidence = float(data.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))

    regime = str(data.get("regime", "unknown")).lower().strip()
    reasoning = str(data.get("reasoning", "")).strip()

    return LLMAssessment(
        signal=signal,
        confidence=confidence,
        regime=regime,
        reasoning=reasoning,
        ts=time.time(),
    )


class LLMAnalyzer:
    """LLM market analyzer with caching and error handling.

    Uses OpenAI-compatible API (works with Volcengine ARK, DeepSeek, OpenAI, etc.)
    / LLM市场分析器，带缓存和错误处理。
    使用OpenAI兼容API（兼容火山引擎ARK、DeepSeek、OpenAI等）。
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        interval_ticks: int = 6,
        can_override_adx: bool = True,
        min_confidence: float = 0.5,
        timeout: float = 30.0,
        no_proxy: bool = True,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.interval_ticks = interval_ticks
        self.can_override_adx = can_override_adx
        self.min_confidence = min_confidence
        self.timeout = timeout
        self.no_proxy = no_proxy  # bypass proxy for LLM API (Chinese services don't need VPN) / LLM API绕过代理（国内服务不需要VPN）

        self._last: LLMAssessment = LLMAssessment()
        self._ticks_since_call: int = 0
        self._call_count: int = 0
        self._error_count: int = 0

    @property
    def last_assessment(self) -> LLMAssessment:
        return self._last

    @property
    def should_call(self) -> bool:
        """True if enough ticks have passed since last call. / 距上次调用是否已过足够tick。"""
        return self._ticks_since_call >= self.interval_ticks

    def assess(
        self,
        price: float,
        rsi: float,
        adx: float,
        ema200: float,
        funding_z: float,
        rsi_signal: str,
        adx_threshold: float,
        recent_bars: pd.DataFrame,
    ) -> LLMAssessment:
        """Call the LLM API for a market assessment. / 调用LLM API进行市场评估。"""
        self._ticks_since_call = 0
        self._call_count += 1

        if not self.api_url or not self.api_key or not self.model:
            result = LLMAssessment(error="LLM not configured (missing api_url/api_key/model)")
            self._last = result
            return result

        user_prompt = _build_user_prompt(
            price, rsi, adx, ema200, funding_z, rsi_signal, adx_threshold, recent_bars
        )

        try:
            import requests

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,  # low temperature for consistency / 低温度保证一致性
                "max_tokens": 300,
            }

            # Bypass proxy for LLM API if configured / 如配置则绕过代理访问LLM API
            session = requests.Session()
            if self.no_proxy:
                session.trust_env = False  # ignore HTTP_PROXY/HTTPS_PROXY env vars / 忽略代理环境变量

            url = f"{self.api_url}/chat/completions"
            resp = session.post(
                url, headers=headers, json=payload, timeout=self.timeout,
                proxies={} if self.no_proxy else None,
            )
            resp.raise_for_status()
            data = resp.json()

            # Extract content from OpenAI-compatible response / 从OpenAI兼容响应中提取内容
            content = data["choices"][0]["message"]["content"]
            result = _parse_llm_response(content)

            if result.error:
                self._error_count += 1

            self._last = result
            return result

        except Exception as e:
            self._error_count += 1
            result = LLMAssessment(error=f"{type(e).__name__}: {e}")
            self._last = result
            return result

    def tick(self) -> None:
        """Increment tick counter. Call assess() when should_call is True. / 递增tick计数器。"""
        self._ticks_since_call += 1

    def get_cached_or_assess(
        self,
        price: float,
        rsi: float,
        adx: float,
        ema200: float,
        funding_z: float,
        rsi_signal: str,
        adx_threshold: float,
        recent_bars: pd.DataFrame,
    ) -> LLMAssessment:
        """Return cached assessment if fresh, otherwise call assess(). / 缓存有效则返回缓存，否则调用assess()。"""
        self.tick()
        if self.should_call or not self._last.is_valid or self._last.is_stale:
            return self.assess(
                price, rsi, adx, ema200, funding_z, rsi_signal, adx_threshold, recent_bars
            )
        return self._last

    def stats(self) -> dict:
        """Return usage statistics. / 返回使用统计。"""
        return {
            "calls": self._call_count,
            "errors": self._error_count,
            "last_signal": self._last.signal.value if self._last.is_valid else "N/A",
            "last_regime": self._last.regime if self._last.is_valid else "N/A",
            "last_confidence": self._last.confidence if self._last.is_valid else 0.0,
        }
