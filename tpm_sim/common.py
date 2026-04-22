from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"
DISPLAY_FORMAT = "%a %Y-%m-%d %H:%M"


def to_iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).strftime(ISO_FORMAT)


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, ISO_FORMAT)


def format_dt(dt: datetime) -> str:
    return dt.strftime(DISPLAY_FORMAT)


def parse_duration(raw: str) -> int:
    value = raw.strip().lower()
    if not value:
        raise ValueError("Duration cannot be empty.")
    if value.endswith("m"):
        return int(value[:-1])
    if value.endswith("h"):
        return int(value[:-1]) * 60
    if value.endswith("d"):
        return int(value[:-1]) * 24 * 60
    return int(value)


def split_pipe_args(raw: str, expected: Optional[int] = None) -> list[str]:
    parts = [part.strip() for part in raw.split("|")]
    if expected is not None and len(parts) != expected:
        raise ValueError(f"Expected {expected} pipe-delimited fields, got {len(parts)}.")
    return parts


def as_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def from_json(value: Optional[str], default: Any = None) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def bucket_value(value: float) -> str:
    if value < 0.34:
        return "low"
    if value < 0.67:
        return "medium"
    return "high"


def advance_by_minutes(dt: datetime, minutes: int) -> datetime:
    return dt + timedelta(minutes=minutes)


def csv_ids(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def summarize_lines(lines: Iterable[str]) -> str:
    return "\n".join(line.rstrip() for line in lines).strip()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def stable_digest(*parts: Any) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        if isinstance(part, bytes):
            data = part
        else:
            data = str(part).encode("utf-8")
        hasher.update(data)
        hasher.update(b"\x1f")
    return hasher.hexdigest()


def stable_float(seed: int, *parts: Any) -> float:
    digest = stable_digest(seed, *parts)
    value = int(digest[:16], 16)
    return value / float(0xFFFFFFFFFFFFFFFF)


def stable_int(seed: int, lower: int, upper: int, *parts: Any) -> int:
    if upper < lower:
        raise ValueError("Upper bound must be >= lower bound.")
    if lower == upper:
        return lower
    span = upper - lower + 1
    return lower + int(stable_float(seed, *parts) * span) % span


def weighted_choice(seed: int, choices: Sequence[dict[str, Any]], *parts: Any) -> dict[str, Any]:
    if not choices:
        raise ValueError("Cannot choose from an empty sequence.")
    total = sum(float(choice.get("weight", 0.0)) for choice in choices)
    if total <= 0:
        return choices[0]
    needle = stable_float(seed, *parts) * total
    running = 0.0
    for choice in choices:
        running += float(choice.get("weight", 0.0))
        if needle <= running:
            return choice
    return choices[-1]


def parse_slot_map(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text or text == "-":
        return {}
    pairs = [part.strip() for part in text.split(",") if part.strip()]
    parsed: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Invalid slot fragment '{pair}'. Expected key=value.")
        key, value = [part.strip() for part in pair.split("=", 1)]
        if value.startswith("[") and value.endswith("]"):
            parsed[key] = [item.strip() for item in value[1:-1].split(";") if item.strip()]
        elif value in {"true", "false"}:
            parsed[key] = value == "true"
        else:
            parsed[key] = value
    return parsed


def bool_text(value: bool) -> str:
    return "yes" if value else "no"
