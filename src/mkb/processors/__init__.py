"""
Data processing pipeline for converting raw assets into structured formats.

This module provides processors for different file types:
- Textual data (PDF, DOCX, TXT) → Markdown
- Tabular data (CSV, XLSX) → Parquet + metadata
- Image files → Metadata extraction
"""

from mkb.processors.base import ProcessingResult, Processor

__all__ = ["Processor", "ProcessingResult", "process_asset"]


def __getattr__(name: str):
    if name == "process_asset":
        from mkb.processors.coordinator import process_asset

        return process_asset
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
