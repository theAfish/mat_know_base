"""Storage and execution helpers for deterministic post-processor scripts."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import BinaryIO

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import PostProcessorScript


SCRIPTS_ROOT = Path("data/post_processor_scripts")


def _script_to_dict(script: PostProcessorScript) -> dict:
    return {
        "script_id": str(script.script_id),
        "name": script.name,
        "filename": script.filename,
        "created_at": script.created_at.isoformat() if script.created_at else None,
    }


def list_scripts() -> list[dict]:
    with SyncSessionLocal() as session:
        scripts = session.query(PostProcessorScript).order_by(PostProcessorScript.name).all()
        return [_script_to_dict(script) for script in scripts]


def create_script(filename: str, stream: BinaryIO) -> dict:
    safe_name = Path(filename or "").name
    if not safe_name.lower().endswith(".py"):
        raise ValueError("Post-processor script uploads must be Python (.py) files.")
    script_id = uuid.uuid4()
    SCRIPTS_ROOT.mkdir(parents=True, exist_ok=True)
    target = SCRIPTS_ROOT / f"{script_id.hex}_{safe_name}"
    with target.open("wb") as output:
        shutil.copyfileobj(stream, output)
    with SyncSessionLocal() as session:
        script = PostProcessorScript(
            script_id=script_id,
            name=Path(safe_name).stem.replace("_", " ").strip() or safe_name,
            filename=safe_name,
            storage_path=str(target),
        )
        session.add(script)
        session.commit()
        return _script_to_dict(script)


def delete_script(script_id: str | uuid.UUID) -> dict:
    sid = uuid.UUID(str(script_id))
    with SyncSessionLocal() as session:
        script = session.query(PostProcessorScript).filter_by(script_id=sid).first()
        if not script:
            return {"error": f"Post-processor script {script_id} not found."}
        path = Path(script.storage_path)
        name = script.name
        session.delete(script)
        session.commit()
    path.unlink(missing_ok=True)
    return {"ok": True, "deleted": name}


def run_script(script_config: dict | None, payload: dict) -> dict:
    """Run one registered script and validate its script-first decision."""
    if not isinstance(script_config, dict):
        return {"run_agent": True, "context": None, "patch": None, "script": None}
    raw_id = str(script_config.get("script_id") or "").strip()
    if not raw_id:
        raise ValueError("Post-processor script requires a script_id.")
    try:
        sid = uuid.UUID(raw_id)
    except ValueError as exc:
        raise ValueError("Post-processor script_id must be a UUID.") from exc
    with SyncSessionLocal() as session:
        script = session.query(PostProcessorScript).filter_by(script_id=sid).first()
        if not script:
            raise ValueError(f"Post-processor script '{raw_id}' was not found.")
        script_path = Path(script.storage_path).resolve()
        script_info = _script_to_dict(script)
    if not script_path.is_file():
        raise ValueError(f"Post-processor script file '{script_info['filename']}' is missing.")
    try:
        timeout_seconds = int(script_config.get("timeout_seconds", 30))
    except (TypeError, ValueError) as exc:
        raise ValueError("Post-processor script timeout_seconds must be an integer.") from exc
    if not 1 <= timeout_seconds <= 300:
        raise ValueError("Post-processor script timeout_seconds must be between 1 and 300.")
    try:
        completed = subprocess.run(
            [sys.executable, str(script_path)], input=json.dumps(payload), capture_output=True,
            text=True, cwd=script_path.parent, timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"Post-processor script timed out after {timeout_seconds} seconds.") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        raise ValueError(f"Post-processor script exited with code {completed.returncode}{': ' + detail if detail else ''}")
    try:
        decision = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Post-processor script must write one JSON object to standard output.") from exc
    if not isinstance(decision, dict) or not isinstance(decision.get("run_agent"), bool):
        raise ValueError("Post-processor script output must contain boolean 'run_agent'.")
    patch = decision.get("patch")
    if patch is not None:
        if not isinstance(patch, dict) or not str(patch.get("winning_projection_id") or "").strip():
            raise ValueError("Post-processor script patch requires winning_projection_id.")
        if not isinstance(patch.get("updates"), list) or not patch["updates"]:
            raise ValueError("Post-processor script patch requires non-empty updates.")
    return {
        "run_agent": decision["run_agent"],
        "context": decision.get("context"),
        "patch": patch,
        "script": {**script_info, "timeout_seconds": timeout_seconds},
    }