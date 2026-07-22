"""
Image / vision tools for the projection agent.

Adds the ability to:

- ``list_project_images``: enumerate image artifacts available across a
  project's processed-Markdown bundles (e.g. figures, embedded sequence
  panels, gel images extracted by MinerU).
- ``read_image_with_ocr``: extract text from a project image with
  Tesseract OCR.
- ``read_image_with_vision``: send the image to a multimodal LLM and
  answer a focused question about it (e.g. "Transcribe the protein
  sequence shown in this figure").

Image references accept either:
- a bare filename (``c74d...02.jpg``),
- a path relative to a processed-asset bundle (``images/c74d...02.jpg``),
- or the full markdown reference (``images/c74d...02.jpg``).

The tools resolve the reference by scanning every processed Markdown
bundle attached to ``project_id`` for a matching artifact.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

from mkb.agents._utils import ensure_llm_env
from mkb.agents.runtime import AgentRuntime, bind_tools
from mkb.agents.tools._ids import invalid_identifier_message, parse_uuidish
from mkb.config import settings
from mkb.db.models import (
    Asset,
    ProcessedAsset,
    ProcessingType,
    ProjectAsset,
)

logger = logging.getLogger(__name__)


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}


def _normalize_image_ref(image_ref: str) -> str:
    """Strip markdown wrappers and folder prefixes from an image reference."""
    ref = (image_ref or "").strip().strip("\"'")
    # Drop ![](...) markdown wrappers, if any.
    if ref.startswith("![") and "](" in ref and ref.endswith(")"):
        ref = ref.split("](", 1)[1].rstrip(")")
    # Use just the basename — artifacts are unique by content hash.
    return Path(ref).name.lower()


def _iter_project_images(session, project_id):
    """Yield (processed_asset, asset, relpath) for every image artifact."""
    links = session.query(ProjectAsset).filter_by(project_id=project_id).all()
    asset_ids = [link.asset_id for link in links]
    if not asset_ids:
        return

    rows = (
        session.query(ProcessedAsset, Asset)
        .join(Asset, Asset.asset_id == ProcessedAsset.asset_id)
        .filter(ProcessedAsset.asset_id.in_(asset_ids))
        .filter(ProcessedAsset.processing_type == ProcessingType.MARKDOWN)
        .all()
    )

    for processed, asset in rows:
        metadata = processed.conversion_metadata or {}
        artifact_files = metadata.get("artifact_files") or []
        for relpath in artifact_files:
            if Path(relpath).suffix.lower() in _IMAGE_EXTS:
                yield processed, asset, relpath


def _read_artifact_bytes(
    processed: ProcessedAsset, relpath: str, *, runtime: AgentRuntime
) -> bytes:
    """Read an artifact for a processed asset; try local first, then S3."""
    metadata = processed.conversion_metadata or {}
    local_dir = metadata.get("local_dir")
    if local_dir:
        local_path = Path(local_dir) / relpath
        if local_path.exists():
            return local_path.read_bytes()

    s3_prefix = processed.s3_key.rsplit("/", 1)[0]
    if runtime.object_store is None:
        raise RuntimeError("Image tools require an object store")
    return runtime.object_store.get_bytes(processed.s3_bucket, f"{s3_prefix}/{relpath}")


def _resolve_project_image(project_id, image_ref: str, *, runtime: AgentRuntime):
    """Find an image artifact across a project by basename match."""
    target = _normalize_image_ref(image_ref)
    if not target:
        return None

    with runtime.database.session() as session:
        for processed, asset, relpath in _iter_project_images(session, project_id):
            if Path(relpath).name.lower() == target:
                try:
                    blob = _read_artifact_bytes(processed, relpath, runtime=runtime)
                except Exception as exc:
                    logger.warning(
                        "Failed to read image %s for asset %s: %s",
                        relpath, asset.asset_id, exc,
                    )
                    continue
                return {
                    "bytes": blob,
                    "relpath": relpath,
                    "source_filename": asset.filename,
                    "processed_asset_id": str(processed.processed_asset_id),
                }
    return None


def _mime_for_ext(relpath: str) -> str:
    ext = Path(relpath).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")


# =====================================================================
# Public tools
# =====================================================================


def list_project_images(
    project_id: str, max_results: int = 200, *, runtime: AgentRuntime
) -> dict:
    """List every image artifact extracted from the project's papers.

    Use this before calling ``read_image_with_ocr`` or
    ``read_image_with_vision`` when you encounter a markdown reference
    like ``![](images/<hash>.jpg)`` in ``get_project_markdown`` /
    ``get_frame_content`` output and need to inspect the figure to
    extract data (sequences, gel bands, schema diagrams, etc.).

    Args:
        project_id: Project whose images to list.
        max_results: Hard cap on returned entries (defaults to 200).

    Returns:
        Dict with ``images`` (list of {image_ref, relpath,
        source_filename, processed_asset_id}), ``total``, and
        ``truncated``.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    limit = max(1, min(int(max_results), 500))
    entries: list[dict] = []
    total = 0

    with runtime.database.session() as session:
        for processed, asset, relpath in _iter_project_images(session, pid):
            total += 1
            if len(entries) >= limit:
                continue
            entries.append({
                "image_ref": Path(relpath).name,
                "relpath": relpath,
                "source_filename": asset.filename,
                "processed_asset_id": str(processed.processed_asset_id),
            })

    return {
        "project_id": str(pid),
        "images": entries,
        "total": total,
        "truncated": total > len(entries),
    }


def read_image_with_ocr(
    project_id: str, image_ref: str, *, runtime: AgentRuntime
) -> dict:
    """Run Tesseract OCR on a project image and return the extracted text.

    Use this when the image is likely text-rich (protein sequences,
    captions, tables-as-images). It is fast and does not consume LLM
    tokens. For figures/diagrams that need *interpretation*, prefer
    ``read_image_with_vision``.

    Args:
        project_id: Owning project.
        image_ref: Filename or relative path of the image as it appears in
            the markdown (``images/<hash>.jpg`` or just ``<hash>.jpg``).

    Returns:
        Dict with ``text``, ``source_filename``, ``image_ref``.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    image = _resolve_project_image(pid, image_ref, runtime=runtime)
    if image is None:
        return {"error": f"Image {image_ref!r} not found in project {project_id}."}

    try:
        import pytesseract  # type: ignore
        from PIL import Image
    except ImportError:
        return {
            "error": (
                "OCR unavailable: install Pillow and pytesseract "
                "(and the Tesseract system package) to enable read_image_with_ocr."
            ),
        }

    try:
        img = Image.open(io.BytesIO(image["bytes"]))
        text = pytesseract.image_to_string(img) or ""
    except Exception as exc:
        return {"error": f"OCR failed for {image_ref}: {exc}"}

    return {
        "image_ref": image["image_ref"] if "image_ref" in image else Path(image["relpath"]).name,
        "relpath": image["relpath"],
        "source_filename": image["source_filename"],
        "text": text.strip(),
        "char_count": len(text.strip()),
    }


def read_image_with_vision(
    project_id: str,
    image_ref: str,
    question: str,
    model: str = "",
    *,
    runtime: AgentRuntime,
) -> dict:
    """Ask a multimodal LLM a focused question about a project image.

    Use this for figures that require *interpretation* — schematic
    diagrams, gel images, plots, chemical structures, multi-panel
    figures, hand-drawn or stylized sequence inserts that defeat OCR.

    Keep ``question`` narrowly scoped to what the projection schema
    needs (e.g. "Transcribe the amino acid sequence shown. Output only
    the one-letter codes."). The model is called with a single image
    plus your question; it does **not** see the rest of the paper.

    Args:
        project_id: Owning project.
        image_ref: Filename or relative path of the image
            (``images/<hash>.jpg`` or just ``<hash>.jpg``).
        question: Specific question for the multimodal model.
        model: Optional override for the vision model (LiteLLM-style id).
            Defaults to ``settings.vision_model`` or ``settings.extraction_model``.

    Returns:
        Dict with ``answer``, ``model`` used, ``source_filename``,
        ``image_ref``.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    if not (question or "").strip():
        return {"error": "question must be a non-empty string."}

    image = _resolve_project_image(pid, image_ref, runtime=runtime)
    if image is None:
        return {"error": f"Image {image_ref!r} not found in project {project_id}."}

    try:
        import litellm  # type: ignore
    except ImportError:
        return {"error": "litellm is not installed."}

    ensure_llm_env()

    model_id = (model or "").strip() or settings.vision_model or settings.extraction_model
    mime = _mime_for_ext(image["relpath"])
    data_uri = f"data:{mime};base64,{base64.b64encode(image['bytes']).decode('ascii')}"

    try:
        completion = litellm.completion(
            model=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question.strip()},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            temperature=0,
        )
        answer = completion["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("read_image_with_vision failed (model=%s): %s", model_id, exc)
        return {
            "error": (
                f"Vision model call failed: {exc}. "
                f"Ensure {model_id!r} supports image inputs "
                "(set MKB_VISION_MODEL to a multimodal model id, e.g. openai/qwen-vl-plus)."
            ),
            "model": model_id,
        }

    return {
        "image_ref": Path(image["relpath"]).name,
        "relpath": image["relpath"],
        "source_filename": image["source_filename"],
        "model": model_id,
        "answer": (answer or "").strip(),
    }


VISION_TOOLS = [
    list_project_images,
    read_image_with_ocr,
    read_image_with_vision,
]


def vision_tools(runtime: AgentRuntime):
    """Return image tools bound to one client runtime."""

    return bind_tools(VISION_TOOLS, runtime)
