"""
Data processing pipeline for converting raw assets into structured formats.

This module provides processors for different file types:
- Textual data (PDF, DOCX, TXT) → Markdown
- Tabular data (CSV, XLSX) → Parquet + metadata
- Image files → Metadata extraction
"""

from mkb.processors.base import ProcessingResult, Processor
from mkb.processors.coordinator import process_asset

__all__ = ["Processor", "ProcessingResult", "process_asset"]
