"""
llm_client.py
Shared LLM caller for all AetherCode agents.

Tries Groq first when GROQ_API_KEY is set. On rate-limit, token
exhaustion, or truncated responses (finish_reason=length), automatically
falls back to Gemini when GEMINI_API_KEY is configured.
"""

import json
import logging
import re
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

GROQ_MODEL = "openai/gpt-oss-20b"
GEMINI_MODEL = "gemini-flash-latest"

# Groq free/on_demand TPM for gpt-oss-20b: input + max_tokens must stay under this.
GROQ_REQUEST_TOKEN_BUDGET = 7500
GROQ_MIN_OUTPUT_TOKENS = 1024

# Substrings that indicate Groq should not be retried — switch provider instead
_FALLBACK_HINTS = (
    "rate limit",
    "rate_limit",
    "quota",
    "too many requests",
    "tokens per",
    "token limit",
    "insufficient",
    "exceeded",
    "finish_reason=length",
    "max_tokens",
    "request too large",
    "413",
    "429",
    "503",
    "capacity",
    "org_rate",
)


def _estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars per token) — good enough to stay under Groq TPM."""
    return max(1, len(text) // 4)


def _fit_prompt_for_groq(system_prompt: str, user_prompt: str, requested_max_tokens: int) -> tuple[str, int]:
    """
    Shrink the user prompt and output budget so Groq's
    (prompt tokens + max_tokens) stays under GROQ_REQUEST_TOKEN_BUDGET.
    Gemini still receives the original full prompt.
    """
    system_tokens = _estimate_tokens(system_prompt)
    budget = GROQ_REQUEST_TOKEN_BUDGET - system_tokens
    if budget < GROQ_MIN_OUTPUT_TOKENS + 256:
        budget = GROQ_MIN_OUTPUT_TOKENS + 256

    max_output = min(requested_max_tokens, 2048, budget - 256)
    max_output = max(GROQ_MIN_OUTPUT_TOKENS, max_output)

    user_token_budget = budget - max_output
    user_char_budget = user_token_budget * 4
    if len(user_prompt) > user_char_budget:
        user_prompt = (
            user_prompt[:user_char_budget]
            + "\n\n[truncated to fit Groq token limit]"
        )
        logger.info(
            "Truncated Groq user prompt to ~%s tokens (output budget %s)",
            user_token_budget,
            max_output,
        )

    return user_prompt, max_output


def _should_fallback_to_gemini(exc: Exception) -> bool:
    """True when the error looks like a Groq quota / token / rate issue."""
    msg = str(exc).lower()
    if any(hint in msg for hint in _FALLBACK_HINTS):
        return True
    status = getattr(exc, "status_code", None)
    return status in (413, 429, 503)


def _parse_json_from_text(raw: str) -> dict[str, Any]:
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        raise ValueError(f"No JSON object found in LLM response: {raw!r}")
    return json.loads(json_match.group(0))


def _call_groq(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    from groq import Groq

    client = Groq(api_key=settings.GROQ_API_KEY)
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as e:
        if _should_fallback_to_gemini(e):
            raise ValueError(f"Groq API limit/error: {e}") from e
        raise

    raw_content = response.choices[0].message.content
    if not raw_content:
        finish_reason = response.choices[0].finish_reason
        raise ValueError(
            f"LLM returned empty content (finish_reason={finish_reason}). "
            f"This usually means max_tokens was too low for a reasoning model."
        )

    return _parse_json_from_text(raw_content)


def _call_gemini(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    import google.generativeai as genai

    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        GEMINI_MODEL,
        system_instruction=system_prompt,
    )
    response = model.generate_content(
        user_prompt,
        generation_config={
            "response_mime_type": "application/json",
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        },
    )
    return json.loads(response.text)


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """
    Call an LLM and return a parsed JSON dict.

    Order: Groq (prompt fitted under TPM budget) → Gemini on
    rate/token errors with the original full prompt → raise if all fail.
    """
    errors: list[str] = []

    if settings.GROQ_API_KEY:
        groq_user, groq_max_tokens = _fit_prompt_for_groq(
            system_prompt, user_prompt, max_tokens
        )
        try:
            return _call_groq(
                system_prompt,
                groq_user,
                temperature=temperature,
                max_tokens=groq_max_tokens,
            )
        except Exception as e:
            errors.append(f"Groq: {e}")
            if settings.GEMINI_API_KEY:
                logger.warning("Groq unavailable (%s) — switching to Gemini", e)
            else:
                raise RuntimeError(" | ".join(errors)) from e

    if settings.GEMINI_API_KEY:
        try:
            return _call_gemini(
                system_prompt,
                user_prompt,
                temperature=temperature,
                max_tokens=max(max_tokens, 8192),
            )
        except Exception as e:
            errors.append(f"Gemini: {e}")
            raise RuntimeError(
                "All configured LLM providers failed. " + " | ".join(errors)
            ) from e

    raise RuntimeError(
        "No LLM provider configured. Set GROQ_API_KEY or GEMINI_API_KEY in .env."
    )
