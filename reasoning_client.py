"""OpenAI-compatible client for the second model used by LLM reasoning."""
import asyncio
import json
import os
from urllib.parse import urlsplit

import httpx

from .context import ROOT
from .reasoning_models import ReasoningOutput


DEFAULT_PROVIDER_RATES = {
    "api.openai.com": {"input": 0.15, "output": 0.60},
    "api.deepseek.com": {"input": 0.27, "output": 1.10},
}


class ReasoningClient:
    """A deliberately separate client from the Kimi pipeline client.

    The default points to OpenAI gpt-4o-mini. Any OpenAI-compatible endpoint
    can be selected with REASONING_API_KEY, REASONING_BASE_URL and
    REASONING_MODEL.
    """

    def __init__(
        self,
        key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_output_tokens: int = 4000,
    ):
        self.key = (key if key is not None else os.getenv("REASONING_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv("REASONING_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.model = model or os.getenv("REASONING_MODEL", "gpt-4o-mini")
        self.max_output_tokens = max_output_tokens
        self.usage: list[dict] = []
        self.system_prompt = (ROOT / "prompts" / "reasoning.md").read_text(encoding="utf-8-sig")
        self.provider = urlsplit(self.base_url).hostname or "unknown"

    @staticmethod
    def _optional_rate(name: str, default: float | None) -> float | None:
        value = os.getenv(name, "").strip()
        if not value:
            return default
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be a number of USD per million tokens.") from exc

    def cost_info(self, usage: dict) -> dict:
        """Return raw usage and a clearly labelled estimate for one batch call."""
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        defaults = DEFAULT_PROVIDER_RATES.get(self.provider, {})
        input_rate = self._optional_rate(
            "REASONING_INPUT_COST_PER_MILLION", defaults.get("input")
        )
        output_rate = self._optional_rate(
            "REASONING_OUTPUT_COST_PER_MILLION", defaults.get("output")
        )
        pricing_basis = (
            "configured"
            if os.getenv("REASONING_INPUT_COST_PER_MILLION", "").strip()
            or os.getenv("REASONING_OUTPUT_COST_PER_MILLION", "").strip()
            else "default_provider_estimate"
            if self.provider in DEFAULT_PROVIDER_RATES
            else "not_configured"
        )
        estimate = None
        if input_rate is not None and output_rate is not None:
            estimate = round(
                (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000,
                8,
            )
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_usd": estimate,
            "pricing_usd_per_million": {
                "input": input_rate,
                "output": output_rate,
            },
            "pricing_basis": pricing_basis,
            "note": (
                "Estimate based on returned token usage and configured/default rates; "
                "the provider invoice is authoritative."
            ),
        }

    def validate_distinct_model(self) -> None:
        """Fail closed when the second model is identical to the Kimi model."""
        kimi_model = os.getenv("MOONSHOT_MODEL", "kimi-k3").strip().casefold()
        kimi_base = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1").rstrip("/").casefold()
        if self.model.casefold() == kimi_model and self.base_url.casefold() == kimi_base:
            raise ValueError(
                "The LLM reasoning stage must use a different model from the Kimi stage. "
                "Set REASONING_API_KEY, REASONING_BASE_URL and REASONING_MODEL."
            )

    async def generate(self, payload: dict) -> ReasoningOutput:
        if not self.key or self.key.startswith("YOUR_"):
            raise ValueError("Set REASONING_API_KEY in the project .env file.")
        if not self.base_url.startswith("https://"):
            raise ValueError("REASONING_BASE_URL must use HTTPS.")
        self.validate_distinct_model()

        json_object_mode = "api.deepseek.com" in self.base_url
        response_format = (
            {"type": "json_object"}
            if json_object_mode
            else {
                "type": "json_schema",
                "json_schema": {
                    "name": "llm_reasoning",
                    "strict": True,
                    "schema": ReasoningOutput.model_json_schema(),
                },
            }
        )
        message_payload = {
            **payload,
            # DeepSeek's json_object mode does not receive the schema through
            # response_format, so the schema must be visible to the model.
            "output_schema": ReasoningOutput.model_json_schema(),
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(message_payload, ensure_ascii=False),
                },
            ],
            "response_format": response_format,
            "max_tokens": self.max_output_tokens,
        }

        async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
            response = None
            for attempt in range(3):
                try:
                    response = await client.post(
                        self.base_url + "/chat/completions",
                        headers={"Authorization": "Bearer " + self.key},
                        json=body,
                    )
                except httpx.RequestError:
                    if attempt == 2:
                        raise ValueError("Reasoning API connection failed or timed out.") from None
                    await asyncio.sleep(1 + attempt)
                    continue
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(1 + attempt)
                    continue
                break

        if response is None or response.status_code != 200:
            status = getattr(response, "status_code", "unknown")
            provider_message = ""
            if response is not None:
                try:
                    payload = response.json()
                    provider_message = str(
                        payload.get("error", {}).get("message")
                        or payload.get("message")
                        or payload.get("detail")
                        or ""
                    )[:300]
                except (ValueError, TypeError):
                    provider_message = response.text[:300]
            detail = f" Provider message: {provider_message}" if provider_message else ""
            raise ValueError(
                f"Reasoning API HTTP {status}.{detail} Check REASONING_API_KEY, "
                "REASONING_BASE_URL and REASONING_MODEL."
            )

        data = response.json()
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("Reasoning model did not finish normally; incomplete JSON was rejected.")

        result = ReasoningOutput.model_validate_json(choice["message"].get("content") or "{}")
        self.usage.append(
            {
                "stage": "llm_reasoning",
                "model": self.model,
                "provider": self.provider,
                "usage": data.get("usage", {}),
            }
        )
        return result
