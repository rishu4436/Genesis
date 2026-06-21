"""Genesis natural-language strategy assistant — CMC facts + LLM reasoning."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

import instructor
from anthropic import AsyncAnthropic
from loguru import logger
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from genesis.core.config import EnvSettings
from genesis.core.models import RulesConfig, Signal, SignalCategory
from genesis.data.cmc_provider import CMCProvider
from genesis.strategy_skill.generator import generate_strategy
from genesis.strategy_skill.models import StrategyConditions

ChatRole = Literal["user", "assistant"]
Intent = Literal["answer", "generate_strategy", "clarify"]

SYSTEM_PROMPT = """You are Genesis — the self-custody autonomous trading agent on BNB Chain.
Speak in first person as Genesis (use "I", never mention other AI brands or model names).

HOW YOU WORK (combine both layers):
1. CMC MCP = live facts (prices, RSI, fear/greed, news, on-chain, funding, narratives). Never invent numbers — cite these.
2. Your reasoning = synthesis. Explain what the data means, connect signals, assess regime/risk, and recommend actions.

You help users:
1. Answer market questions — blend CMC facts with your trading expertise for clear, actionable insight.
2. Generate backtestable Track 2 strategy specs from natural language (few words is fine — infer the rest).

When intent is generate_strategy, fill ALL StrategyConditions fields with sensible defaults:
- primary_asset: uppercase symbol (BNB, CAKE, LINK, etc.)
- timeframe: one of 1m, 5m, 15m, 1h, 4h, 1d
- risk_profile: conservative | moderate | aggressive
- market_regime: bullish | bearish | neutral | volatile (infer from CMC data when possible)
- take_profit_pct: 1–100
- stop_loss_pct: 0.5–50
- focus_signals: subset of technicals, sentiment, onchain, news, derivatives, metadata, discovery
- backtest_limit: 50 default
- idle_cycles: 0 default

For market answers: lead with key CMC numbers, then add 1–2 sentences of interpretation (trend, risk, what to watch).
For strategies: justify regime/risk/TP-SL choices using the CMC snapshot provided.
If the user asks a data question, use intent=answer and do NOT fill conditions.
If the user wants a strategy (even vaguely: "DCA on dips", "aggressive CAKE", "bearish exit rules"), use intent=generate_strategy.
If critical info is missing and you cannot infer it, use intent=clarify and ask one short question.
Keep reply concise (3–6 sentences) and actionable."""


class ChatMessage(BaseModel):
    """Single chat turn for strategy assistant history."""

    role: ChatRole
    content: str


class GrokStrategyIntent(BaseModel):
    """Structured LLM output: answer, clarify, or generate strategy conditions."""

    intent: Intent = Field(description="answer | generate_strategy | clarify")
    reply: str = Field(description="Natural language response shown to the user")
    conditions: StrategyConditions | None = Field(
        default=None,
        description="Required when intent=generate_strategy",
    )


class StrategyChatResponse(BaseModel):
    """API response for Genesis strategy chat."""

    intent: Intent
    reply: str
    conditions: StrategyConditions | None = None
    strategy: dict | None = None
    backtest_preview: dict | None = None
    cmc_context: dict[str, Any] | None = None
    symbols_detected: list[str] = Field(default_factory=list)
    strategy_file: str | None = None
    download_url: str | None = None
    data_sources_used: list[str] = Field(default_factory=list)


def create_instructor_client(env: EnvSettings) -> Any:
    """Create instructor-patched async client for configured LLM provider."""
    provider = env.llm_provider
    api_key = env.get_llm_api_key()

    if provider == "grok":
        client = AsyncOpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
        return instructor.from_openai(client, mode=instructor.Mode.JSON)
    if provider == "openai":
        client = AsyncOpenAI(api_key=api_key)
        return instructor.from_openai(client, mode=instructor.Mode.JSON)
    if provider == "anthropic":
        client = AsyncAnthropic(api_key=api_key)
        return instructor.from_anthropic(client, mode=instructor.Mode.JSON)
    raise ValueError(f"Unsupported LLM provider: {provider}")


def _allowlist_symbols(rules: RulesConfig) -> dict[str, int]:
    return {t.symbol.upper(): t.cmc_id for t in rules.allowed_tokens}


def extract_symbols_from_text(text: str, rules: RulesConfig, *, max_symbols: int = 3) -> list[str]:
    """Find allowlisted token symbols mentioned in user text."""
    allowlist = _allowlist_symbols(rules)
    if not allowlist:
        return []

    upper = text.upper()
    found: list[tuple[int, str]] = []
    for symbol in sorted(allowlist, key=len, reverse=True):
        if re.search(rf"\b{re.escape(symbol)}\b", upper):
            found.append((upper.index(symbol), symbol))

    found.sort(key=lambda x: x[0])
    symbols = []
    for _, sym in found:
        if sym not in symbols:
            symbols.append(sym)
        if len(symbols) >= max_symbols:
            break

    if not symbols and re.search(r"\b(btc|bitcoin|eth|ethereum)\b", upper, re.I):
        for fallback in ("BNB", "CAKE", "LINK"):
            if fallback in allowlist:
                return [fallback]
    return symbols


def _cmc_id_for_symbol(rules: RulesConfig, symbol: str) -> int | None:
    return _allowlist_symbols(rules).get(symbol.upper())


def _compact_signal(signal: Signal) -> dict[str, Any]:
    """Trim a CMC signal for LLM context."""
    entry: dict[str, Any] = {
        "category": signal.category.value,
        "summary": signal.summary,
        "score": round(float(signal.value), 3),
    }
    raw = signal.raw_data if isinstance(signal.raw_data, dict) else {}
    if signal.category == SignalCategory.QUOTE:
        entry["price_usd"] = raw.get("price")
        entry["change_24h_pct"] = raw.get("percent_change_24h", raw.get("percentChange24h"))
        entry["change_7d_pct"] = raw.get("percent_change_7d", raw.get("percentChange7d"))
    elif signal.category == SignalCategory.NEWS and raw.get("headlines"):
        entry["headlines"] = raw.get("headlines")[:3]
    elif signal.category == SignalCategory.SENTIMENT:
        entry["fear_greed_index"] = raw.get("fear_greed_index")
    return entry


def _trim_macro_events(data: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    """Keep a small slice of upcoming macro events for the LLM."""
    rows = data.get("rows", data.get("eventList", []))
    if not isinstance(rows, list):
        return []
    trimmed: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if isinstance(row, dict):
            trimmed.append({k: row[k] for k in list(row)[:6]})
        elif isinstance(row, list):
            trimmed.append({"cells": row[:6]})
    return trimmed


async def _fetch_global_market_block(provider: CMCProvider) -> dict[str, Any]:
    """Market-wide CMC signals fetched in parallel for richer LLM context."""
    block: dict[str, Any] = {}
    metrics_task = provider._get_global_metrics()
    deriv_task = provider.get_global_derivatives_signal()
    narr_task = provider.get_narratives()
    macro_task = provider.get_macro_events()

    metrics, deriv, narr, macro = await asyncio.gather(
        metrics_task, deriv_task, narr_task, macro_task, return_exceptions=True
    )

    if isinstance(metrics, Exception):
        block["metrics_error"] = str(metrics)
    elif isinstance(metrics, dict):
        fg = metrics.get("sentiment", {}).get("fear_greed", {}).get("current", {})
        block["fear_greed_index"] = fg.get("index")
        block["fear_greed_label"] = fg.get("value_classification") or fg.get("classification")
        usd = metrics.get("quote", {}).get("USD", {})
        if isinstance(usd, dict):
            block["total_market_cap_usd"] = usd.get("total_market_cap")
            block["market_cap_change_24h_pct"] = usd.get("total_market_cap_yesterday_percentage_change")

    if isinstance(deriv, Signal):
        block["derivatives"] = _compact_signal(deriv)
    elif isinstance(deriv, Exception):
        block["derivatives_error"] = str(deriv)

    if isinstance(narr, Signal):
        block["narratives"] = _compact_signal(narr)
    elif isinstance(narr, Exception):
        block["narratives_error"] = str(narr)

    if isinstance(macro, Exception):
        block["macro_error"] = str(macro)
    elif isinstance(macro, dict):
        block["upcoming_macro_events"] = _trim_macro_events(macro)

    return block


async def _asset_snapshot(
    provider: CMCProvider,
    rules: RulesConfig,
    symbol: str,
) -> dict[str, Any]:
    """Rich per-asset CMC snapshot — same signal stack as the live agent."""
    cmc_id = _cmc_id_for_symbol(rules, symbol)
    if cmc_id is None:
        return {"symbol": symbol, "error": "not on hackathon allowlist"}

    snapshot: dict[str, Any] = {"symbol": symbol, "cmc_id": cmc_id, "signals": []}
    try:
        signals = await provider.get_all_signals(symbol, cmc_id)
        snapshot["signals"] = [_compact_signal(s) for s in signals]
        quote = next((s for s in signals if s.category == SignalCategory.QUOTE), None)
        if quote and isinstance(quote.raw_data, dict):
            snapshot["price_usd"] = quote.raw_data.get("price")
            snapshot["change_24h_pct"] = quote.raw_data.get(
                "percent_change_24h", quote.raw_data.get("percentChange24h")
            )
    except Exception as e:
        snapshot["fetch_error"] = str(e)

    return snapshot


def _is_market_wide_question(text: str) -> bool:
    """Heuristic: user wants macro/market overview vs a single asset."""
    lower = text.lower()
    market_terms = (
        "market",
        "today",
        "overall",
        "macro",
        "fear",
        "greed",
        "narrative",
        "trending",
        "top mover",
        "crypto market",
        "what's happening",
        "whats happening",
    )
    return any(term in lower for term in market_terms)


async def build_cmc_market_context(
    provider: CMCProvider,
    rules: RulesConfig,
    user_text: str,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """
    Prefetch CMC MCP facts, then Genesis LLM synthesizes them into answers.

    Returns (context, symbols, data_sources_used).
    """
    symbols = extract_symbols_from_text(user_text, rules)
    market_wide = _is_market_wide_question(user_text)
    if not symbols and not market_wide:
        symbols = ["BNB"] if "BNB" in _allowlist_symbols(rules) else []

    sources = ["coinmarketcap_mcp:get_global_metrics_latest"]

    context: dict[str, Any] = {
        "pipeline": "cmc_mcp_facts_then_genesis_reasoning",
        "allowlist_size": len(rules.allowed_tokens),
        "market_wide_question": market_wide,
        "global": {},
        "assets": {},
    }

    global_block = await _fetch_global_market_block(provider)
    context["global"] = global_block
    sources.extend(
        [
            "coinmarketcap_mcp:get_global_crypto_derivatives_metrics",
            "coinmarketcap_mcp:trending_crypto_narratives",
            "coinmarketcap_mcp:get_upcoming_macro_events",
        ]
    )

    if symbols:
        sources.append("coinmarketcap_mcp:per_asset_signals")
        tasks = [_asset_snapshot(provider, rules, sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for sym, result in zip(symbols, results, strict=True):
            if isinstance(result, Exception):
                context["assets"][sym] = {"symbol": sym, "error": str(result)}
            else:
                context["assets"][sym] = result

    sources.append("genesis_llm_reasoning")
    return context, symbols, sources


def _format_history(history: list[ChatMessage]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for msg in history[-8:]:
        messages.append({"role": msg.role, "content": msg.content.strip()})
    return messages


def _build_user_prompt(
    message: str,
    cmc_context: dict[str, Any],
    symbols: list[str],
    rules: RulesConfig,
) -> str:
    allowlist_sample = [t.symbol for t in rules.allowed_tokens[:12]]
    context_json = json.dumps(cmc_context, default=str)
    return (
        f"User message:\n{message.strip()}\n\n"
        f"Detected symbols: {', '.join(symbols) if symbols else 'none (infer from context if generating)'}\n"
        f"Allowlist sample: {', '.join(allowlist_sample)} … ({len(rules.allowed_tokens)} total)\n\n"
        "Instructions: Use the CMC JSON below as ground truth for numbers. "
        "Apply your reasoning to synthesize a helpful answer or strategy parameters.\n\n"
        f"CMC live context:\n{context_json}"
    )


async def handle_strategy_chat(
    env: EnvSettings,
    rules: RulesConfig,
    provider: CMCProvider,
    message: str,
    history: list[ChatMessage] | None = None,
    *,
    include_backtest: bool = True,
    backtest_fn: Any | None = None,
) -> StrategyChatResponse:
    """
    Process natural language: answer CMC questions or generate Track 2 strategy JSON.

    LLM parses intent + conditions; deterministic generator builds the final spec.
    """
    history = history or []
    cmc_context, symbols, data_sources = await build_cmc_market_context(provider, rules, message)

    try:
        client = create_instructor_client(env)
    except ValueError:
        return StrategyChatResponse(
            intent="clarify",
            reply=(
                "I'm not fully online yet — my reasoning layer needs XAI_API_KEY in .env. "
                "CMC can supply live market data, but I need that key to interpret it and "
                "build strategies for you."
            ),
            cmc_context=cmc_context,
            symbols_detected=symbols,
            data_sources_used=[s for s in data_sources if s != "genesis_llm_reasoning"],
        )

    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_format_history(history))
    messages.append(
        {
            "role": "user",
            "content": _build_user_prompt(message, cmc_context, symbols, rules),
        }
    )

    try:
        intent_result: GrokStrategyIntent = await client.chat.completions.create(
            model=env.llm_model,
            response_model=GrokStrategyIntent,
            messages=messages,
            max_retries=2,
        )
    except Exception as e:
        logger.error(f"Genesis strategy chat failed: {e}")
        return StrategyChatResponse(
            intent="clarify",
            reply="I couldn't process that just now — try rephrasing or ask something simpler.",
            cmc_context=cmc_context,
            symbols_detected=symbols,
            data_sources_used=data_sources,
        )

    conditions = intent_result.conditions
    strategy: dict | None = None
    backtest_preview: dict | None = None

    if intent_result.intent == "generate_strategy" and conditions is not None:
        strategy = generate_strategy(conditions, rules)
        if include_backtest and conditions.backtest_limit > 0 and backtest_fn is not None:
            try:
                backtest_preview = await backtest_fn(conditions)
            except Exception as e:
                logger.warning(f"Backtest preview failed: {e}")

    return StrategyChatResponse(
        intent=intent_result.intent,
        reply=intent_result.reply,
        conditions=conditions,
        strategy=strategy,
        backtest_preview=backtest_preview,
        cmc_context=cmc_context,
        symbols_detected=symbols,
        data_sources_used=data_sources,
    )


async def generate_strategy_from_text(
    env: EnvSettings,
    rules: RulesConfig,
    provider: CMCProvider,
    text: str,
    *,
    include_backtest: bool = True,
    backtest_fn: Any | None = None,
) -> StrategyChatResponse:
    """Shortcut: natural language → strategy JSON (forces generate intent)."""
    prompt = (
        f"{text.strip()}\n\n"
        "Generate a complete Track 2 backtestable strategy from the above. "
        "Use intent=generate_strategy."
    )
    return await handle_strategy_chat(
        env,
        rules,
        provider,
        prompt,
        include_backtest=include_backtest,
        backtest_fn=backtest_fn,
    )