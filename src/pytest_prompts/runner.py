from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

import httpx

from pytest_prompts.config import settings

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

PRICE_PER_MTOK = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
}


@dataclass(slots=True)
class RunResult:
    output: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    model: str
    prompt_hash: str
    cost_usd: float

    @property
    def tokens_used(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class JudgeResult:
    verdict: bool
    reasoning: str
    criterion: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = PRICE_PER_MTOK.get(model)
    if price is None:
        return 0.0
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def _hash_prompt(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


_JUDGE_SYSTEM = (
    "You are an impartial evaluator. Given an LLM output and a criterion, "
    "decide whether the output satisfies the criterion. "
    "Reply with exactly two lines:\n"
    "VERDICT: YES or NO\n"
    "REASON: one sentence explaining your decision."
)


def _parse_judge_response(text: str) -> tuple[bool, str]:
    """Extract verdict and reasoning from judge response.

    Accepts the structured format (VERDICT:/REASON:) but falls back gracefully:
    - if no VERDICT line, scans for YES/NO anywhere in the text
    - if no REASON line, uses the full response as reasoning
    """
    verdict: bool | None = None
    reasoning: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("VERDICT:"):
            value = stripped[len("VERDICT:"):].strip().upper()
            verdict = value.startswith("YES")
        elif upper.startswith("REASON:"):
            reasoning = stripped[len("REASON:"):].strip()

    if verdict is None:
        upper_text = text.upper()
        if "YES" in upper_text:
            verdict = True
        elif "NO" in upper_text:
            verdict = False
        else:
            verdict = False

    return verdict, reasoning or text.strip()


class Runner:
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.model = model or settings.default_model
        self.api_key = api_key or settings.anthropic_api_key
        self.timeout = timeout if timeout is not None else settings.default_timeout
        self.max_tokens = max_tokens or settings.default_max_tokens

    def run(
        self,
        prompt: str | Path,
        input: str | None = None,
        variables: dict[str, str] | None = None,
        system: str | None = None,
    ) -> RunResult:
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Export it or add it to a .env file."
            )

        prompt_text = self._load_prompt(prompt, variables)
        user_content = (
            f"{prompt_text}\n\n{input}" if input is not None else prompt_text
        )

        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": user_content}],
        }
        if system is not None:
            payload["system"] = system

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        start = time.perf_counter()
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(ANTHROPIC_URL, json=payload, headers=headers)
        latency_ms = int((time.perf_counter() - start) * 1000)

        if response.status_code != 200:
            raise RuntimeError(
                f"Anthropic API {response.status_code}: {response.text[:500]}"
            )

        data = response.json()
        output = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))

        return RunResult(
            output=output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            model=self.model,
            prompt_hash=_hash_prompt(prompt_text),
            cost_usd=_estimate_cost(self.model, input_tokens, output_tokens),
        )

    def judge(self, result: RunResult, criterion: str) -> JudgeResult:
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Export it or add it to a .env file."
            )

        user_content = f"Output:\n{result.output}\n\nCriterion:\n{criterion}"
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": 256,
            "system": _JUDGE_SYSTEM,
            "messages": [{"role": "user", "content": user_content}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(ANTHROPIC_URL, json=payload, headers=headers)

        if response.status_code != 200:
            raise RuntimeError(
                f"Anthropic API {response.status_code}: {response.text[:500]}"
            )

        data = response.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        verdict, reasoning = _parse_judge_response(text)

        return JudgeResult(
            verdict=verdict,
            reasoning=reasoning,
            criterion=criterion,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=_estimate_cost(self.model, input_tokens, output_tokens),
        )

    @staticmethod
    def _load_prompt(
        prompt: str | Path, variables: dict[str, str] | None
    ) -> str:
        if isinstance(prompt, Path) or (
            isinstance(prompt, str) and Path(prompt).is_file()
        ):
            text = Path(prompt).read_text(encoding="utf-8")
        else:
            text = str(prompt)
        if variables:
            text = Template(text).safe_substitute(variables)
        return text


@dataclass(slots=True)
class MockRunner:
    """Deterministic runner for pytest-prompts' own tests."""

    canned_output: str = "ok"
    canned_input_tokens: int = 10
    canned_output_tokens: int = 5
    canned_latency_ms: int = 1
    canned_verdict: bool = True
    canned_reasoning: str = "mock judge"
    model: str = "mock"
    calls: list[dict[str, object]] = field(default_factory=list)
    judge_calls: list[dict[str, object]] = field(default_factory=list)

    def run(
        self,
        prompt: str | Path,
        input: str | None = None,
        variables: dict[str, str] | None = None,
        system: str | None = None,
    ) -> RunResult:
        prompt_text = Runner._load_prompt(prompt, variables)
        self.calls.append(
            {"prompt": prompt_text, "input": input, "system": system}
        )
        return RunResult(
            output=self.canned_output,
            input_tokens=self.canned_input_tokens,
            output_tokens=self.canned_output_tokens,
            latency_ms=self.canned_latency_ms,
            model=self.model,
            prompt_hash=_hash_prompt(prompt_text),
            cost_usd=0.0,
        )

    def judge(self, result: RunResult, criterion: str) -> JudgeResult:
        self.judge_calls.append({"output": result.output, "criterion": criterion})
        return JudgeResult(
            verdict=self.canned_verdict,
            reasoning=self.canned_reasoning,
            criterion=criterion,
            input_tokens=5,
            output_tokens=3,
            cost_usd=0.0,
        )
