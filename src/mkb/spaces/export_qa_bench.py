"""
Export projection data into mat_agent_bench task YAML files.

Reads `qa_benchmark` purpose projections whose payload follows the
`computational_materials_qa` schema (see `examples/spaces/computational_materials_qa.json`)
and writes one self-contained YAML file per question under
``<out_dir>/<capability>/<id>.yaml`` — ready to drop into the
``question_bank/`` folder of https://github.com/ruoyuwang1995nya/mat_agent_bench.

Each emitted question is treated as fully isolated: only the keys listed in
``MAT_AGENT_BENCH_FIELDS`` are written, and we never cross-reference sibling
items. Internal-only fields such as ``source_evidence`` and the projector-injected
``source_project_id`` are stripped.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Projection, Space

logger = logging.getLogger(__name__)


# Top-level YAML keys, in mat_agent_bench's preferred order.
MAT_AGENT_BENCH_FIELDS: tuple[str, ...] = (
    "id",
    "capability",
    "domain",
    "intent",
    "human_prompt_seed",
    "tags",
    "data_files",
    "reference_answers",
    "scoring_checklist",
)

# Internal-only keys to strip from any emitted item / sub-item.
_STRIPPED_KEYS: frozenset[str] = frozenset(
    {"source_evidence", "source_project_id", "is_core_study_data"}
)

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]")


class QABenchExportError(RuntimeError):
    """Raised when a projection cannot be exported into mat_agent_bench YAML."""


# ── YAML helpers ────────────────────────────────────────────────────────────


def _literal_str_representer(dumper: yaml.Dumper, data: str):
    """Render multi-line strings with the literal block style for readability."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class _BenchDumper(yaml.SafeDumper):
    """Dumper that preserves dict insertion order and uses literal blocks for prose."""


_BenchDumper.add_representer(str, _literal_str_representer)


def _dump_yaml(payload: dict) -> str:
    return yaml.dump(
        payload,
        Dumper=_BenchDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )


# ── Cleanup helpers ─────────────────────────────────────────────────────────


def _scrub(value: Any) -> Any:
    """Recursively drop internal-only keys from extracted data."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in _STRIPPED_KEYS}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _safe_filename(question_id: str) -> str:
    sanitized = _SAFE_ID_RE.sub("_", question_id.strip())
    return sanitized or "unnamed"


def _coerce_questions(data: Any) -> list[dict]:
    """Extract the `questions` list from a projection payload, tolerant of shape drift."""
    if isinstance(data, list):
        return [q for q in data if isinstance(q, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("questions", "qa_pairs", "items"):
        candidate = data.get(key)
        if isinstance(candidate, list):
            return [q for q in candidate if isinstance(q, dict)]
    return []


def _format_question(question: dict) -> dict:
    """Return an ordered dict containing only the mat_agent_bench fields."""
    scrubbed = _scrub(question)
    ordered: dict[str, Any] = {}
    for field in MAT_AGENT_BENCH_FIELDS:
        if field in scrubbed and scrubbed[field] is not None:
            ordered[field] = scrubbed[field]
    return ordered


def _validate_question(question: dict, *, index: int) -> list[str]:
    """Return a list of warning strings for any missing required structure."""
    warnings: list[str] = []
    required = ("id", "capability", "domain", "intent", "human_prompt_seed",
                "reference_answers", "scoring_checklist")
    for field in required:
        if not question.get(field):
            warnings.append(f"questions[{index}]: missing required `{field}`")

    ref_keys = {
        item.get("key")
        for item in question.get("reference_answers", []) or []
        if isinstance(item, dict) and item.get("key")
    }
    checklist_ids = {
        item.get("id")
        for item in question.get("scoring_checklist", []) or []
        if isinstance(item, dict) and item.get("id")
    }
    efficiency_ids = {"turn_budget", "no_retries", "duration_budget", "token_budget_total"}
    unmatched = (checklist_ids - efficiency_ids) - ref_keys
    if unmatched:
        warnings.append(
            f"questions[{index}]: scoring_checklist ids missing matching reference_answers: "
            + ", ".join(sorted(unmatched))
        )

    return warnings


# ── Public API ──────────────────────────────────────────────────────────────


def export_projection_to_yaml(
    projection_id: str | uuid.UUID,
    out_dir: str | Path,
    *,
    overwrite: bool = False,
) -> dict:
    """Export a single projection's questions to mat_agent_bench YAML files.

    Args:
        projection_id: Projection to export.
        out_dir: Root directory; files are written to ``<out_dir>/<capability>/<id>.yaml``.
        overwrite: If False (default) refuse to overwrite an existing file.

    Returns:
        Dict with ``files`` (list of written paths), ``warnings`` (list of strings),
        and ``skipped`` (list of {id, reason} for items not written).
    """
    pid = uuid.UUID(str(projection_id))
    out_root = Path(out_dir)

    with SyncSessionLocal() as session:
        projection = session.query(Projection).filter_by(projection_id=pid).first()
        if not projection:
            raise QABenchExportError(f"Projection {projection_id} not found.")
        space = session.query(Space).filter_by(space_id=projection.space_id).first()
        space_purpose = getattr(space, "purpose", None) if space else None
        space_name = space.name if space else None
        data = projection.data or {}

    if space_purpose and space_purpose != "qa_benchmark":
        raise QABenchExportError(
            f"Projection {projection_id} belongs to space '{space_name}' "
            f"with purpose '{space_purpose}', not 'qa_benchmark'."
        )

    questions = _coerce_questions(data)
    return _write_questions(
        questions,
        out_root=out_root,
        overwrite=overwrite,
        source_label=f"projection {projection_id}",
    )


def export_space_to_yaml(
    space_id_or_name: str | uuid.UUID,
    out_dir: str | Path,
    *,
    overwrite: bool = False,
) -> dict:
    """Export all completed projections for a space.

    Aggregates questions across every projection that belongs to the given space
    (de-duplicating by ``id`` — later items win) and writes them out.
    """
    out_root = Path(out_dir)
    with SyncSessionLocal() as session:
        space = _resolve_space(session, space_id_or_name)
        if not space:
            raise QABenchExportError(f"Space {space_id_or_name} not found.")
        if space.purpose != "qa_benchmark":
            raise QABenchExportError(
                f"Space '{space.name}' has purpose '{space.purpose}', not 'qa_benchmark'."
            )
        projections = (
            session.query(Projection)
            .filter_by(space_id=space.space_id)
            .all()
        )
        payloads = [p.data or {} for p in projections]

    aggregated: dict[str, dict] = {}
    unkeyed: list[dict] = []
    for payload in payloads:
        for q in _coerce_questions(payload):
            qid = q.get("id")
            if isinstance(qid, str) and qid.strip():
                aggregated[qid.strip()] = q
            else:
                unkeyed.append(q)

    merged: Iterable[dict] = list(aggregated.values()) + unkeyed
    return _write_questions(
        list(merged),
        out_root=out_root,
        overwrite=overwrite,
        source_label=f"space {space.name}",
    )


def _resolve_space(session, space_id_or_name) -> Space | None:
    try:
        sid = uuid.UUID(str(space_id_or_name))
        return session.query(Space).filter_by(space_id=sid).first()
    except (ValueError, AttributeError):
        return session.query(Space).filter_by(name=str(space_id_or_name)).first()


def _write_questions(
    questions: list[dict],
    *,
    out_root: Path,
    overwrite: bool,
    source_label: str,
) -> dict:
    written: list[str] = []
    skipped: list[dict] = []
    warnings: list[str] = []

    if not questions:
        warnings.append(f"{source_label}: no questions found in projection data.")
        return {"files": written, "skipped": skipped, "warnings": warnings}

    out_root.mkdir(parents=True, exist_ok=True)

    for idx, question in enumerate(questions):
        formatted = _format_question(question)
        item_warnings = _validate_question(formatted, index=idx)
        warnings.extend(item_warnings)

        qid = formatted.get("id")
        capability = formatted.get("capability") or "unsorted"
        if not qid:
            skipped.append({"index": idx, "reason": "missing id"})
            continue

        target_dir = out_root / _safe_filename(capability)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{_safe_filename(qid)}.yaml"

        if target_path.exists() and not overwrite:
            skipped.append({"id": qid, "reason": f"exists: {target_path}"})
            continue

        target_path.write_text(_dump_yaml(formatted), encoding="utf-8")
        written.append(str(target_path))
        logger.info("Wrote %s", target_path)

    return {"files": written, "skipped": skipped, "warnings": warnings}
