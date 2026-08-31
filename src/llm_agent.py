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
