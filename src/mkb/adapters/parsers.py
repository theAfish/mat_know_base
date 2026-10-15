"""Opt-in OCR and existing document-processor bridges for the parser port."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mkb.exceptions import BackendUnavailableError, ProviderError, ValidationError
from mkb.extraction import ParsedContent

if TYPE_CHECKING:
    from mkb.processors.base import Processor


def _text_option(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string")
    return value.strip()


def _timeout(value: Any) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError("OCR timeout must be a finite positive number") from exc
    if isinstance(value, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValidationError("OCR timeout must be a finite positive number")
    return timeout


def _source_types(values) -> frozenset[str]:
    if isinstance(values, (str, bytes)) or not values:
        raise ValidationError(
            "Parser source_types must contain at least one MIME type or extension"
        )
    return frozenset(_text_option(value, "Parser source type").lower() for value in values)


@dataclass(frozen=True)
class TesseractParser:
    """Image OCR using optional Pillow/pytesseract and a host Tesseract binary."""

    name: str = "tesseract"
    language: str = "eng"
    timeout: float = 60
    source_types: frozenset[str] = frozenset(
        {"image/png", "image/jpeg", "image/tiff", "image/bmp", "image/webp", "image/gif"}
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text_option(self.name, "Parser name"))
        object.__setattr__(self, "language", _text_option(self.language, "OCR language"))
        object.__setattr__(self, "timeout", _timeout(self.timeout))
        object.__setattr__(self, "source_types", _source_types(self.source_types))

    def parse(self, content: bytes, *, parameters: dict[str, Any] | None = None) -> ParsedContent:
        if not isinstance(content, bytes):
            raise ValidationError("OCR content must be bytes")
        options = dict(parameters or {})
        unknown = set(options) - {"language", "timeout"}
        if unknown:
            raise ValidationError(f"Unknown OCR parameters: {sorted(unknown)}")
        timeout = _timeout(options.get("timeout", self.timeout))
        language = _text_option(options.get("language", self.language), "OCR language")
        try:
            from PIL import Image
            import pytesseract
        except ImportError as exc:
            raise BackendUnavailableError(
                "Install mat-know-base[ocr] to use TesseractParser"
            ) from exc
        try:
            with Image.open(io.BytesIO(content)) as image:
                text = pytesseract.image_to_string(image, lang=language, timeout=timeout)
                metadata = {"width": image.width, "height": image.height, "parser": self.name}
        except Exception as exc:
            raise ProviderError(
                "Tesseract OCR failed; check the image and Tesseract installation"
            ) from exc
        return ParsedContent(text=text, metadata=metadata)


@dataclass(frozen=True)
class ProcessorParser:
    """Adapt a text/Markdown Processor (e.g. MinerU API or DOCX) to ContentParser.

    The processor must be configured explicitly by the caller. Auxiliary images
    remain available through its native ProcessingResult; this bridge consumes
    the primary textual output for extraction.
    """

    name: str
    processor: Processor
    source_types: frozenset[str]
    filename: str = "document.pdf"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text_option(self.name, "Parser name"))
        object.__setattr__(self, "filename", _text_option(self.filename, "Processor filename"))
        object.__setattr__(self, "source_types", _source_types(self.source_types))
        if not callable(getattr(self.processor, "process", None)):
            raise ValidationError("Processor must provide a process(content, filename) method")

    def parse(self, content: bytes, *, parameters: dict[str, Any] | None = None) -> ParsedContent:
        if not isinstance(content, bytes):
            raise ValidationError("Processor content must be bytes")
        options = dict(parameters or {})
        unknown = set(options) - {"filename"}
        if unknown:
            raise ValidationError(f"Unknown processor parameters: {sorted(unknown)}")
        filename = _text_option(options.get("filename", self.filename), "Processor filename")
        try:
            result = self.processor.process(content, filename)
        except Exception as exc:
            raise ProviderError(f"Document processor {self.name!r} failed") from exc
        if not result.is_success():
            raise ProviderError(f"Document processor {self.name!r} failed: {result.error}")
        if result.output_format not in {"md", "txt", "text", "markdown"}:
            raise ValidationError("ProcessorParser requires text or Markdown output")
        if not isinstance(result.content, bytes):
            raise ValidationError("Processor output content must be bytes")
        try:
            text = result.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("Processor output must be UTF-8 text") from exc
        return ParsedContent(text=text, metadata=dict(result.conversion_metadata or {}))
