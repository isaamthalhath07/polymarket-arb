"""Local-LLM dependency classifier via Ollama's OpenAI-compatible endpoint.

Talks to any OpenAI-compatible local server (Ollama, LM Studio, llama.cpp server, vLLM).
Default host is Ollama on ``http://localhost:11434/v1``. Cost is zero — no usage tracking.

The static instructions go in the system role; per-pair JSON goes in the user role. We
request JSON output via ``response_format={"type": "json_object"}``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .reduce import prompt_payload, reduce_to_top4

# Condensed (~1.8k token) rubric for local CPU inference. Falls back to the full rubric
# if the local file is missing. The full 10k-token prompt is impractical on CPU — prompt
# ingestion alone takes 10+ minutes per call.
_LOCAL_PROMPT = Path(__file__).resolve().parent.parent / "polymarket_dependency_prompt_local.md"
_FULL_PROMPT = Path(__file__).resolve().parent.parent / "polymarket_dependency_prompt.md"
PROMPT_PATH = _LOCAL_PROMPT if _LOCAL_PROMPT.exists() else _FULL_PROMPT


def _load_instructions() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _build_user_payload(m1: dict, m2: dict) -> str:
    a = json.dumps(prompt_payload(reduce_to_top4(m1)), indent=2)
    b = json.dumps(prompt_payload(reduce_to_top4(m2)), indent=2)
    return f"Market 1:\n{a}\n\nMarket 2:\n{b}"


class Classifier:
    def __init__(
        self,
        model: str | None = None,
        client: Any | None = None,
        *,
        host: str | None = None,
        request_timeout: float = 900.0,
    ) -> None:
        self.model = model or os.environ.get("CLASSIFIER_MODEL", "qwen2.5:3b-instruct")
        if client is None:
            from openai import OpenAI

            base_url = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434/v1")
            client = OpenAI(base_url=base_url, api_key="ollama", timeout=request_timeout)
        self.client = client
        self.instructions = _load_instructions()

    def classify(self, m1: dict, m2: dict) -> tuple[dict, float]:
        """Return (parsed_result, cost_usd). Cost is always 0 for local inference."""
        user_payload = _build_user_payload(m1, m2)
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.instructions},
                {"role": "user", "content": user_payload},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=2048,
            # Keep the model resident in RAM between calls so we pay the cold-load once.
            extra_body={"keep_alive": "30m"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            return (
                {
                    "step5_self_check": {
                        "abstain": True,
                        "abstain_reason": "classifier returned invalid JSON",
                    }
                },
                0.0,
            )
        return result, 0.0
