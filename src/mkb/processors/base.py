"""
Base processor classes and interfaces for data conversion.
"""

import hashlib
import io
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from mkb.db.models import ProcessingType

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    """Result of a processing operation."""
    processing_type: ProcessingType
    output_format: str
    content: bytes
    # Optional extra files generated during conversion (e.g. images/ from MinerU)
    artifacts: dict[str, bytes] | None = None
    # Relative path for the main output file under the processed folder
    primary_relpath: str | None = None
    conversion_metadata: dict | None = None
    error: str | None = None
    
    @property
    def sha256(self) -> str:
        h = hashlib.sha256()
        h.update(self.content)
        artifacts = self.artifacts or {}
        for relpath in sorted(artifacts.keys()):
            h.update(relpath.encode("utf-8"))
            h.update(hashlib.sha256(artifacts[relpath]).digest())
        return h.hexdigest()
    
    @property
    def size_bytes(self) -> int:
        return len(self.content)
    
    def is_success(self) -> bool:
        return self.error is None


class Processor(ABC):
    """
    Abstract base class for data processors.
    Each processor handles conversion of a specific file type to a standard output format.
    """
    
    # MIME types this processor can handle
    supported_mime_types: list[str] = []
    
    @abstractmethod
    def can_process(self, mime_type: str, filename: str) -> bool:
        """Check if this processor can handle the given file."""
        pass
    
    def process(self, data: bytes, filename: str) -> ProcessingResult:
        """Compatibility entry point for callers that already own small bytes."""
        return self.process_file(io.BytesIO(data), filename)

    def process_file(self, source: str | Path | BinaryIO, filename: str | None = None) -> ProcessingResult:
        """Convert a path or seekable handle without buffering the original file."""
        from mkb.file_io import seekable_file
        name = filename or (Path(source).name if isinstance(source, (str, Path)) else "input")
        with seekable_file(source) as stream:
            return self.process_stream(stream, name)

    def process_stream(self, stream: BinaryIO, filename: str) -> ProcessingResult:
        raise NotImplementedError("This processor must implement file-based conversion")
    
    def get_processing_type(self) -> ProcessingType:
        """Get the primary processing type for this processor."""
        raise NotImplementedError()


class TextualProcessor(Processor):
    """Base class for processors that convert textual data to Markdown."""
    
    def get_processing_type(self) -> ProcessingType:
        return ProcessingType.MARKDOWN


class DataframeProcessor(Processor):
    """Base class for processors that handle tabular/dataframe data."""
    
    def get_processing_type(self) -> ProcessingType:
        return ProcessingType.DATAFRAME


class ImageProcessor(Processor):
    """Base class for processors that extract metadata from images."""
    
    def get_processing_type(self) -> ProcessingType:
        return ProcessingType.IMAGE
