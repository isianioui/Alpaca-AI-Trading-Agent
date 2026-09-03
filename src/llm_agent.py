"""
The reasoning core of the trading agent — powered by Groq's OpenAI-compatible
chat completions API (genuinely free tier, no credit card required:
https://console.groq.com/keys), running GPT-OSS-120B by default (Llama 3.3
70B is no longer offered on Groq's model list as of this writing --
GPT-OSS-120B is the closest available strong general-purpose model; swap
GROQ_MODEL to whatever's current at https://console.groq.com/docs/models
if that changes again).

Sends the model a structured snapshot of account state, current position
(if any), and technical indicators for a single symbol, and asks for JSON
output via Groq's JSON mode (response_format={"type": "json_object"}).
Groq's JSON mode guarantees syntactically valid JSON but not schema
conformance the way Gemini's response_schema parameter did, so the schema
itself is spelled out in the prompt (see _json_response_instructions()) and
the parsed response is validated against the same Pydantic schema classes
below before being trusted -- a malformed/incomplete response raises
(ValidationError or JSONDecodeError) the same way a Gemini API error used
to, and is caught by the same per-symbol try/except in trading_agent.py /
options_trading_agent.py, so one bad call never crashes a whole cycle.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from groq import Groq
from pydantic import BaseModel, Field

DEFAULT_MODEL = "openai/gpt-oss-120b"
REQUEST_TIMEOUT_SECONDS = 60.0


class TradeDecisionSchema(BaseModel):
    action: str = Field(description="One of: buy, sell, hold")
    confidence: float = Field(description="Confidence in this decision, 0.0 (low) to 1.0 (high)")
    reasoning: str = Field(description="2-4 sentence plain-English explanation referencing the "
                                        "concrete indicator values provided")
    risk_note: str = Field(description="One sentence naming the biggest risk to this call being wrong")


SYSTEM_PROMPT = """You are a disciplined, risk-aware trading research assistant helping to \
drive a PAPER TRADING agent (no real money at risk). You are given a snapshot of account \
state, an optional current position, and technical indicators for one ticker.

Rules you must follow:
- Base your call ONLY on the numeric data provided. Do not invent facts about the company.
- Prefer "hold" when signals are mixed or weak. Conviction should be earned, not assumed.
- Never recommend a position larger than what the account/risk data implies is available.
- You are advisory only — a separate risk manager will size or reject the trade regardless \
  of what you recommend.
- Respond ONLY with the structured JSON decision. action must be exactly one of: "buy", \
  "sell", "hold".
"""


def _json_response_instructions(schema_model: type[BaseModel]) -> str:
    """Groq's JSON mode (response_format={'type': 'json_object'}) only
    guarantees valid JSON syntax, not conformance to a particular shape --
    unlike Gemini's response_schema parameter, there's no out-of-band way to
    pin the schema, so it has to be spelled out in the prompt itself."""
    return (
        "Respond with a single JSON object only -- no markdown code fences, no explanation "
        "before or after it -- matching exactly this JSON Schema:\n\n"
        f"{json.dumps(schema_model.model_json_schema(), indent=2)}"
    )


def _parse_json_response(raw_text: str) -> dict:
    return json.loads(raw_text.strip())


@dataclass
class TradeDecision:
    symbol: str
    action: str
    confidence: float
    reasoning: str
    risk_note: str
    raw_features: dict = field(default_factory=dict)


class LLMTradingAgent:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        key = api_key or os.getenv("GROQ_API_KEY")
        if not key:
            raise ValueError(
                "Missing Groq API key. Set GROQ_API_KEY in your .env file "
                "(get a genuinely free key at https://console.groq.com/keys — no card required)."
            )
        self.client = Groq(api_key=key, timeout=REQUEST_TIMEOUT_SECONDS)
        self.model = model or os.getenv("GROQ_MODEL", DEFAULT_MODEL)

    def decide(
        self,
        symbol: str,
        features: dict,
        account_context: dict,
        current_position: Optional[dict] = None,
    ) -> TradeDecision:
        user_payload = {
            "symbol": symbol,
            "indicators": features,
            "account": account_context,
            "current_position": current_position or "none",
        }

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": _json_response_instructions(TradeDecisionSchema)},
                {"role": "user", "content": (
                    "Here is the current market snapshot. Decide buy, sell, or hold for "
                    f"{symbol} and record your decision.\n\n{json.dumps(user_payload, indent=2)}"
                )},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )

        raw = _parse_json_response(response.choices[0].message.content)
        validated = TradeDecisionSchema(**raw)

        return TradeDecision(
            symbol=symbol,
            action=validated.action.lower().strip(),
            confidence=float(validated.confidence),
            reasoning=validated.reasoning,
            risk_note=validated.risk_note,
            raw_features=features,
        )


class OptionsDecisionSchema(BaseModel):
    action: str = Field(description="One of: open_covered_call, open_cash_secured_put, "
                                     "close_position, hold")
    contract_symbol: str = Field(description="Exact OCC option symbol copied from the candidate "
                                              "data to act on, or an empty string when action is hold")
    confidence: float = Field(description="Confidence in this decision, 0.0 (low) to 1.0 (high)")
    reasoning: str = Field(description="2-4 sentence plain-English explanation referencing the "
                                        "concrete candidate/indicator values provided")
    risk_note: str = Field(description="One sentence naming the biggest risk to this call being wrong")


OPTIONS_SYSTEM_PROMPT = """You are a disciplined, risk-aware options-trading research assistant \
driving a PAPER TRADING agent (no real money at risk). You may propose EXACTLY ONE of two \
defined-risk, beginner-safe options strategies, or hold/close:

- "open_covered_call": sell a call against shares ALREADY OWNED (100+ shares of the underlying). \
  Only propose this if covered_call_candidate is a real contract (not "not_eligible").
- "open_cash_secured_put": sell a put fully backed by cash on hand to buy 100 shares if assigned. \
  Only propose this if cash_secured_put_candidate is a real contract (not "not_eligible").
- "close_position": close the position described in current_option_position, if one is given.
- "hold": do nothing.

You are FORBIDDEN from proposing any other options strategy — no naked/uncovered calls, no \
naked puts without cash backing, no spreads, no straddles, no buying options outright to open. \
These are undefined-risk or speculative and are permanently out of scope for this agent. If \
neither candidate is eligible and there is no current_option_position, you MUST respond hold.

You are advisory only — a separate risk manager independently re-verifies eligibility and sizing, \
and will reject anything outside the two allowed strategies regardless of what you recommend.

Respond ONLY with the structured JSON decision. action must be exactly one of: "open_covered_call", \
"open_cash_secured_put", "close_position", "hold". contract_symbol must be copied exactly from the \
candidate data given (or "" for hold) — never invent a symbol.
"""


@dataclass
class OptionsTradeDecision:
    symbol: str
    action: str
    contract_symbol: str
    confidence: float
    reasoning: str
    risk_note: str
    raw_context: dict = field(default_factory=dict)


class OptionsLLMAgent:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        key = api_key or os.getenv("GROQ_API_KEY")
        if not key:
            raise ValueError(
                "Missing Groq API key. Set GROQ_API_KEY in your .env file "
                "(get a genuinely free key at https://console.groq.com/keys — no card required)."
            )
        self.client = Groq(api_key=key, timeout=REQUEST_TIMEOUT_SECONDS)
        self.model = model or os.getenv("GROQ_MODEL", DEFAULT_MODEL)

    def decide_option(
        self,
        symbol: str,
        underlying_features: dict,
        account_context: dict,
        covered_call_candidate: Optional[dict],
        cash_secured_put_candidate: Optional[dict],
        current_option_position: Optional[dict] = None,
    ) -> OptionsTradeDecision:
        user_payload = {
            "symbol": symbol,
            "underlying_indicators": underlying_features,
            "account": account_context,
            "covered_call_candidate": covered_call_candidate or "not_eligible",
            "cash_secured_put_candidate": cash_secured_put_candidate or "not_eligible",
            "current_option_position": current_option_position or "none",
        }

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": OPTIONS_SYSTEM_PROMPT},
                {"role": "system", "content": _json_response_instructions(OptionsDecisionSchema)},
                {"role": "user", "content": (
                    "Here is the current options snapshot. Decide the appropriate options "
                    f"action for {symbol}.\n\n{json.dumps(user_payload, indent=2, default=str)}"
                )},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )

        raw = _parse_json_response(response.choices[0].message.content)
        validated = OptionsDecisionSchema(**raw)

        return OptionsTradeDecision(
            symbol=symbol,
            action=validated.action.lower().strip(),
            contract_symbol=validated.contract_symbol.strip(),
            confidence=float(validated.confidence),
            reasoning=validated.reasoning,
            risk_note=validated.risk_note,
            raw_context=user_payload,
        )
