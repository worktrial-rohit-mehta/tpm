from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Optional


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def autoload_project_dotenv(
    *,
    start_dir: Optional[str | Path] = None,
    project_root_path: Optional[str | Path] = None,
    override_process_env: bool = False,
) -> dict[str, object]:
    root = Path(project_root_path).resolve() if project_root_path else project_root()
    cwd = Path(start_dir).resolve() if start_dir else Path.cwd().resolve()
    preexisting = set(os.environ)
    loaded_paths: list[str] = []
    loaded_values: dict[str, str] = {}
    seen: set[Path] = set()

    for candidate in _candidate_paths(cwd, root):
        if candidate in seen or not candidate.exists():
            continue
        seen.add(candidate)
        applied = _load_env_file(candidate, preexisting, override_process_env)
        if applied:
            loaded_paths.append(str(candidate))
            loaded_values.update(applied)

    return {"loaded_paths": loaded_paths, "loaded_values": loaded_values}


def _candidate_paths(cwd: Path, root: Path) -> list[Path]:
    paths: list[Path] = []
    if cwd == root or root in cwd.parents:
        chain: list[Path] = []
        current = cwd
        while True:
            chain.append(current)
            if current == root:
                break
            current = current.parent
        for directory in reversed(chain):
            paths.append(directory / ".env")
    else:
        paths.append(root / ".env")
        if cwd != root:
            paths.append(cwd / ".env")
    return paths


def _load_env_file(path: Path, preexisting: set[str], override_process_env: bool) -> dict[str, str]:
    applied: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        parsed = _parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if not override_process_env and key in preexisting:
            continue
        os.environ[key] = value
        applied[key] = value
    return applied


def _parse_env_line(raw_line: str) -> Optional[tuple[str, str]]:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].strip()
    if "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if not value:
        return key, ""
    if value[0] in {"'", '"'}:
        try:
            parsed = shlex.split(f"_={value}", posix=True)
            if parsed:
                return key, parsed[0].split("=", 1)[1]
        except ValueError:
            pass
        return key, value.strip("'\"")
    comment_index = value.find(" #")
    if comment_index != -1:
        value = value[:comment_index].rstrip()
    return key, value
