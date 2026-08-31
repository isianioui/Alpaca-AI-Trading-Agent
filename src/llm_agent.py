"""
The reasoning core of the trading agent — powered by Google's Gemini API
(free tier, no credit card required: https://aistudio.google.com/apikey).

Sends Gemini a structured snapshot of account state, current position
(if any), and technical indicators for a single symbol, and forces a
structured JSON decision back via response_schema so the output is
always machine-parseable and includes a human-readable rationale for
the dashboard / pitch video.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


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
        key = api_key or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise ValueError(
                "Missing Google Gemini API key. Set GOOGLE_API_KEY in your .env file "
                "(get a free key at https://aistudio.google.com/apikey — no card required)."
            )
        self.client = genai.Client(api_key=key)
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

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

        response = self.client.models.generate_content(
            model=self.model,
            contents=(
                "Here is the current market snapshot. Decide buy, sell, or hold for "
                f"{symbol} and record your decision.\n\n{json.dumps(user_payload, indent=2)}"
            ),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=TradeDecisionSchema,
                temperature=0.3,
            ),
        )

        result = json.loads(response.text)

        return TradeDecision(
            symbol=symbol,
            action=result["action"].lower().strip(),
            confidence=float(result["confidence"]),
            reasoning=result["reasoning"],
            risk_note=result["risk_note"],
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
        key = api_key or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise ValueError(
                "Missing Google Gemini API key. Set GOOGLE_API_KEY in your .env file "
                "(get a free key at https://aistudio.google.com/apikey — no card required)."
            )
        self.client = genai.Client(api_key=key)
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

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

        response = self.client.models.generate_content(
            model=self.model,
            contents=(
                "Here is the current options snapshot. Decide the appropriate options "
                f"action for {symbol}.\n\n{json.dumps(user_payload, indent=2, default=str)}"
            ),
            config=types.GenerateContentConfig(
                system_instruction=OPTIONS_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=OptionsDecisionSchema,
                temperature=0.3,
                # A stalled connection can otherwise hang this call indefinitely
                # (observed live) since the SDK has no default timeout.
                http_options=types.HttpOptions(timeout=60_000),
            ),
        )

        result = json.loads(response.text)

        return OptionsTradeDecision(
            symbol=symbol,
            action=result["action"].lower().strip(),
            contract_symbol=result.get("contract_symbol", "").strip(),
            confidence=float(result["confidence"]),
            reasoning=result["reasoning"],
            risk_note=result["risk_note"],
            raw_context=user_payload,
        )
