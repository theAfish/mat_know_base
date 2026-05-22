"""
PDF processor using MinerU for high-quality PDF to Markdown conversion.

MinerU (https://github.com/opendatalab/MinerU) is specialized for:
- Academic papers and technical documents
- Tables, figures, and layout preservation
- Multi-column text handling
"""

import logging
import tempfile
from pathlib import Path

from mineru.cli.common import do_parse

from mkb import runtime_settings
from mkb.processors.base import ProcessingResult, TextualProcessor

logger = logging.getLogger(__name__)


class PDFProcessor(TextualProcessor):
    """
    Converts PDF files to Markdown using MinerU.

    Dispatches between two backends based on runtime settings:

    - ``local`` (default): uses MinerU's local VLM model (``vlm-transformers``).
    - ``mineru_api``: uses the MinerU cloud API. Requires ``mineru_api_token``.
    """

    supported_mime_types = ["application/pdf"]

    def can_process(self, mime_type: str, filename: str) -> bool:
        return mime_type in self.supported_mime_types or filename.lower().endswith(".pdf")

    def process(self, data: bytes, filename: str) -> ProcessingResult:
        backend = runtime_settings.get_setting("pdf_backend")
        try:
            if backend == "mineru_api":
                return self._process_with_mineru_api(data, filename)
            return self._process_with_mineru(data, filename)
        except Exception as e:
            return ProcessingResult(
                processing_type=self.get_processing_type(),
                output_format="md",
                content=b"",
                error=f"PDF processing failed: {str(e)}"
            )

    def _process_with_mineru_api(self, data: bytes, filename: str) -> ProcessingResult:
        # Imported lazily so installations without API usage incur no cost.
        from mkb.processors.pdf_mineru_api import MinerUApiPDFProcessor

        token = runtime_settings.get_setting("mineru_api_token")
        if not token:
            logger.warning(
                "pdf_backend=mineru_api but no token set; falling back to local backend"
            )
            return self._process_with_mineru(data, filename)

        proc = MinerUApiPDFProcessor(
            api_base=runtime_settings.get_setting("mineru_api_base"),
            token=token,
            model_version=runtime_settings.get_setting("mineru_api_model_version"),
            language=runtime_settings.get_setting("mineru_api_language"),
            enable_ocr=bool(runtime_settings.get_setting("mineru_api_enable_ocr")),
            enable_formula=bool(runtime_settings.get_setting("mineru_api_enable_formula")),
            enable_table=bool(runtime_settings.get_setting("mineru_api_enable_table")),
            timeout=int(runtime_settings.get_setting("mineru_api_timeout")),
        )
        return proc.process(data, filename)

    def _process_with_mineru(self, data: bytes, filename: str) -> ProcessingResult:
        """Process using MinerU's local VLM backend."""
        stem = Path(filename).stem

        with tempfile.TemporaryDirectory() as output_dir:
            do_parse(
                output_dir=output_dir,
                pdf_file_names=[stem],
                pdf_bytes_list=[data],
                p_lang_list=["en"],
                backend="vlm-transformers",
                f_draw_layout_bbox=False,
                f_draw_span_bbox=False,
                f_dump_middle_json=False,
                f_dump_model_output=False,
                f_dump_orig_pdf=False,
                f_dump_content_list=False,
                f_dump_md=True,
            )

            # MinerU writes: output_dir/<stem>/vlm/<stem>.md
            vlm_dir = Path(output_dir) / stem / "vlm"
            md_path = vlm_dir / f"{stem}.md"
            markdown_content = md_path.read_text(encoding="utf-8")

            artifacts: dict[str, bytes] = {}
            images_dir = vlm_dir / "images"
            if images_dir.exists():
                for p in images_dir.rglob("*"):
                    if p.is_file():
                        rel = p.relative_to(vlm_dir).as_posix()
                        artifacts[rel] = p.read_bytes()

            # Optional table extraction outputs written by MinerU (if present)
            tables_dir = vlm_dir / "tables"
            if tables_dir.exists():
                for p in tables_dir.rglob("*"):
                    if p.is_file():
                        rel = p.relative_to(vlm_dir).as_posix()
                        artifacts[rel] = p.read_bytes()

            image_count = len([k for k in artifacts if k.startswith("images/")])
            table_count = len([k for k in artifacts if k.startswith("tables/")])

        return ProcessingResult(
            processing_type=self.get_processing_type(),
            output_format="md",
            content=markdown_content.encode("utf-8"),
            artifacts=artifacts,
            primary_relpath=f"{stem}.md",
            conversion_metadata={
                "method": "mineru-vlm",
                "has_tables": "table" in markdown_content.lower() or table_count > 0,
                "has_figures": "figure" in markdown_content.lower() or image_count > 0,
                "image_files": image_count,
                "table_files": table_count,
            },
        )
