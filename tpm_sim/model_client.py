from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Protocol


@dataclass
class ModelResponse:
    text: str
    raw: dict[str, Any]
    usage: dict[str, Any]
    latency_ms: int
    refusal: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelClient(Protocol):
    def generate_text(self, prompt_spec: dict[str, Any], config: dict[str, Any]) -> ModelResponse:
        ...

    def generate_structured(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        prompt_spec: dict[str, Any],
        config: dict[str, Any],
    ) -> ModelResponse:
        ...


class FixtureModelClient:
    def __init__(self, fixtures_root: str | Path):
        self.fixtures_root = Path(fixtures_root)

    def _fixture_path(self, prompt_spec: dict[str, Any]) -> Path:
        scenario_id = prompt_spec["scenario_id"]
        artifact = prompt_spec["artifact"]
        path = self.fixtures_root / scenario_id / artifact
        if not path.exists():
            if artifact == "trajectories.json":
                trajectory_dir = self.fixtures_root / scenario_id / "trajectories"
                if trajectory_dir.exists():
                    return trajectory_dir
            raise FileNotFoundError(f"Missing fixture artifact: {path}")
        return path

    def generate_text(self, prompt_spec: dict[str, Any], config: dict[str, Any]) -> ModelResponse:
        path = self._fixture_path(prompt_spec)
        if path.is_dir():
            payload = {item.name: item.read_text() for item in sorted(path.glob("*.tpm"))}
            text = json.dumps(payload, indent=2, sort_keys=True)
        else:
            text = path.read_text()
        return ModelResponse(text=text, raw={"fixture_path": str(path)}, usage={}, latency_ms=0, refusal=None)

    def generate_structured(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        prompt_spec: dict[str, Any],
        config: dict[str, Any],
    ) -> ModelResponse:
        response = self.generate_text(prompt_spec, config)
        json.loads(response.text)
        return response


class OpenAIResponsesModelClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for the OpenAI Responses model client.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai package is required. Install the project with the OpenAI extra.") from exc
        kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self.client = OpenAI(**kwargs)

    def generate_text(self, prompt_spec: dict[str, Any], config: dict[str, Any]) -> ModelResponse:
        messages = _prompt_messages(prompt_spec)
        request: dict[str, Any] = {
            "model": config["model"],
            "input": messages,
            "store": False,
            "metadata": prompt_spec.get("metadata") or {},
        }
        if "temperature" in config:
            request["temperature"] = config["temperature"]
        if "top_p" in config:
            request["top_p"] = config["top_p"]
        started = time.perf_counter()
        response = self.client.responses.create(**request)
        latency_ms = int((time.perf_counter() - started) * 1000)
        raw = _serialize_response(response)
        text = getattr(response, "output_text", None) or _extract_output_text(raw)
        refusal = _extract_refusal(raw)
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        return ModelResponse(text=text or "", raw=raw, usage=usage, latency_ms=latency_ms, refusal=refusal)

    def generate_structured(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        prompt_spec: dict[str, Any],
        config: dict[str, Any],
    ) -> ModelResponse:
        messages = _prompt_messages(prompt_spec)
        request: dict[str, Any] = {
            "model": config["model"],
            "input": messages,
            "store": False,
            "metadata": prompt_spec.get("metadata") or {},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        if "temperature" in config:
            request["temperature"] = config["temperature"]
        if "top_p" in config:
            request["top_p"] = config["top_p"]
        started = time.perf_counter()
        response = self.client.responses.create(**request)
        latency_ms = int((time.perf_counter() - started) * 1000)
        raw = _serialize_response(response)
        text = getattr(response, "output_text", None) or _extract_output_text(raw)
        refusal = _extract_refusal(raw)
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        if text:
            json.loads(text)
        return ModelResponse(text=text or "", raw=raw, usage=usage, latency_ms=latency_ms, refusal=refusal)


def build_model_client(adapter: str, **kwargs: Any) -> ModelClient:
    if adapter == "openai":
        return OpenAIResponsesModelClient(api_key=kwargs.get("api_key"), base_url=kwargs.get("base_url"))
    if adapter == "fixture":
        fixtures_root = kwargs.get("fixtures_root")
        if not fixtures_root:
            raise RuntimeError("fixtures_root is required for the fixture model client.")
        return FixtureModelClient(fixtures_root)
    raise RuntimeError(f"Unknown model client adapter '{adapter}'.")


def _prompt_messages(prompt_spec: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    system = prompt_spec.get("system")
    if system:
        messages.append({"role": "system", "content": str(system)})
    for item in prompt_spec.get("messages", []):
        messages.append({"role": item["role"], "content": str(item["content"])})
    user = prompt_spec.get("user")
    if user:
        messages.append({"role": "user", "content": str(user)})
    if not messages:
        raise RuntimeError("Prompt spec must include at least one message.")
    return messages


def _serialize_response(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if isinstance(response, dict):
        return response
    return {"response": str(response)}


def _extract_output_text(raw: dict[str, Any]) -> str:
    output = raw.get("output", [])
    chunks: list[str] = []
    for item in output:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(content.get("text", ""))
    return "".join(chunks)


def _extract_refusal(raw: dict[str, Any]) -> Optional[str]:
    output = raw.get("output", [])
    for item in output:
        for content in item.get("content", []):
            if content.get("type") == "refusal":
                return content.get("refusal") or content.get("text") or ""
    return None
