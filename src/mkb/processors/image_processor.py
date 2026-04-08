"""
Processor for image files.
Extracts metadata and optionally performs OCR.
"""

import io
import json
import logging
from pathlib import Path

from mkb.db.models import ProcessingType
from mkb.processors.base import ImageProcessor, ProcessingResult

logger = logging.getLogger(__name__)


class BasicImageProcessor(ImageProcessor):
    """
    Basic image processor that extracts metadata.
    Can optionally perform OCR if Tesseract is available.
    """
    
    supported_mime_types = [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/tiff",
        "image/bmp",
        "image/webp",
    ]
    
    def can_process(self, mime_type: str, filename: str) -> bool:
        lower_name = filename.lower()
        return (
            mime_type in self.supported_mime_types
            or lower_name.endswith((".jpg", ".jpeg", ".png", ".gif", ".tiff", ".bmp", ".webp"))
        )
    
    def process(self, data: bytes, filename: str) -> ProcessingResult:
        """Extract image metadata and optionally perform OCR."""
        try:
            metadata = self._extract_metadata(data)
            
            # Try OCR if available
            ocr_text = None
            try:
                ocr_text = self._extract_text_with_ocr(data)
            except Exception as e:
                logger.warning(f"OCR failed for {filename}: {e}")
            
            # Create output JSON with metadata and OCR results
            output_dict = {
                "filename": filename,
                "metadata": metadata,
            }
            if ocr_text:
                output_dict["ocr_text"] = ocr_text
            
            output_json = json.dumps(output_dict, indent=2).encode('utf-8')
            
            return ProcessingResult(
                processing_type=self.get_processing_type(),
                output_format="json",
                content=output_json,
                conversion_metadata=metadata
            )
        except Exception as e:
            return ProcessingResult(
                processing_type=self.get_processing_type(),
                output_format="json",
                content=b"",
                error=f"Image processing failed: {str(e)}"
            )
    
    @staticmethod
    def _extract_metadata(data: bytes) -> dict:
        """Extract basic image metadata using PIL."""
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS
        except ImportError:
            raise ImportError("Pillow not installed. Install with: pip install Pillow")
        
        img = Image.open(io.BytesIO(data))
        
        metadata = {
            "format": img.format,
            "width": img.width,
            "height": img.height,
            "mode": img.mode,  # Color space (RGB, RGBA, etc.)
        }
        
        # Extract EXIF data if available
        try:
            exif_data = img._getexif()
            if exif_data:
                exif_dict = {}
                for tag_id, value in exif_data.items():
                    tag_name = TAGS.get(tag_id, tag_id)
                    exif_dict[tag_name] = str(value)
                metadata["exif"] = exif_dict
        except Exception:
            pass
        
        return metadata
    
    @staticmethod
    def _extract_text_with_ocr(data: bytes) -> str | None:
        """
        Extract text using Tesseract OCR.
        Returns None if OCR is not available or fails.
        """
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            logger.warning("pytesseract or Pillow not installed for OCR")
            return None
        
        try:
            img = Image.open(io.BytesIO(data))
            text = pytesseract.image_to_string(img)
            return text if text.strip() else None
        except Exception as e:
            logger.warning(f"OCR extraction failed: {e}")
            return None
