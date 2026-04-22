from __future__ import annotations

import json
from typing import Any

from tpm_sim.model_client import build_model_client


JUDGE_PROMPT_PACK_VERSION = "tpm_judge_prompt_v3"

JUDGE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "direct_answer": {"type": "string"},
        "executive_summary": {"type": "string"},
        "top_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "explanation": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "explanation", "evidence_refs"],
            },
        },
        "counterfactual_path": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "explanation": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "explanation", "evidence_refs"],
            },
        },
        "supporting_data": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "explanation": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "explanation", "evidence_refs"],
            },
        },
        "limitations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "explanation": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "explanation", "evidence_refs"],
            },
        },
    },
    "required": ["direct_answer", "executive_summary", "top_findings", "counterfactual_path", "supporting_data", "limitations"],
}


def summarize_with_judge(
    judge_input_bundle: dict[str, Any],
    *,
    fallback: dict[str, Any],
    judge_client: Any | None = None,
    judge_model: str | None = None,
) -> dict[str, Any]:
    if not judge_model:
        return fallback
    client = judge_client or build_model_client("openai")
    prompt = build_judge_prompt(judge_input_bundle)
    response = client.generate_structured(
        schema_name="tpm_judged_summary",
        schema=JUDGE_OUTPUT_SCHEMA,
        prompt_spec=prompt,
        config={"model": judge_model},
    )
    payload = json.loads(response.text)
    _validate_judge_output(payload, judge_input_bundle["allowed_evidence_refs"])
    return {
        "source": "llm_judge",
        "prompt_pack_version": JUDGE_PROMPT_PACK_VERSION,
        "model": judge_model,
        **payload,
    }


def build_judge_prompt(judge_input_bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "system": (
            "You are an explanatory TPM benchmark judge. The deterministic evaluator has already decided the official "
            "score and evidence. Your job is only to write a concise, reviewer-friendly TPM diagnosis that cites the "
            "provided evidence references. Lead with a direct answer to how the model performed as a TPM in this scenario. "
            "Prioritize the deterministic root-cause findings, stakeholder engagement, missed opportunities, and "
            "reference-path divergence when explaining what went wrong. Explicitly call out negative evidence such as "
            "critical actors never contacted or direct questions left unanswered. Do not invent events, hidden state, "
            "causal explanations, or unsupported recommendations. Never change the official outcome or score."
        ),
        "user": json.dumps(judge_input_bundle, indent=2, sort_keys=True),
        "metadata": {
            "prompt_pack_version": JUDGE_PROMPT_PACK_VERSION,
            "kind": "tpm_summary_explanation",
        },
    }


def _validate_judge_output(payload: dict[str, Any], allowed_evidence_refs: list[str]) -> None:
    allowed = set(allowed_evidence_refs)
    for section in ("top_findings", "counterfactual_path", "supporting_data", "limitations"):
        for item in payload.get(section, []):
            refs = item.get("evidence_refs", [])
            if any(ref not in allowed for ref in refs):
                raise ValueError(f"Judge output referenced unsupported evidence refs: {refs}")
