"""
Base processor classes and interfaces for data conversion.
"""

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

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
    
    @abstractmethod
    def process(self, data: bytes, filename: str) -> ProcessingResult:
        """
        Process raw file data and return conversion result.
        
        Args:
            data: Raw file content
            filename: Original filename (used for context)
            
        Returns:
            ProcessingResult with converted data and metadata
        """
        pass
    
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
