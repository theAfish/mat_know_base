"""
Processor for dataframe files: CSV, XLSX, JSON with tabular data, etc.
Detects tabular structure and converts to standardized formats.
"""

import io
import json
import logging

import pandas as pd

from mkb.db.models import ProcessingType
from mkb.processors.base import DataframeProcessor, ProcessingResult

logger = logging.getLogger(__name__)


def dataframe_to_result(
    df: pd.DataFrame,
    source_format: str,
    extra_metadata: dict | None = None,
) -> ProcessingResult:
    """Convert pandas DataFrame to Parquet bytes."""
    try:
        output = io.BytesIO()
        df.to_parquet(output, index=False)
        parquet_bytes = output.getvalue()

        metadata = {
            "method": source_format,
            "num_rows": len(df),
            "num_columns": len(df.columns),
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        return ProcessingResult(
            processing_type=ProcessingType.DATAFRAME,
            output_format="parquet",
            content=parquet_bytes,
            conversion_metadata=metadata,
        )
    except Exception as e:
        return ProcessingResult(
            processing_type=ProcessingType.DATAFRAME,
            output_format="parquet",
            content=b"",
            error=f"Dataframe conversion failed: {str(e)}",
        )


class CSVProcessor(DataframeProcessor):
    """Handles CSV and similar delimited files."""
    
    supported_mime_types = [
        "text/csv",
        "text/plain",  # Many CSV files are marked as text/plain
    ]
    
    def can_process(self, mime_type: str, filename: str) -> bool:
        lower_name = filename.lower()
        return lower_name.endswith((".csv", ".tsv")) and (
            mime_type in self.supported_mime_types or
            mime_type.startswith("text/")
        )
    
    def process(self, data: bytes, filename: str) -> ProcessingResult:
        """Convert CSV to Parquet with metadata."""
        try:
            # Detect delimiter
            text = data.decode('utf-8')
            delimiter = self._detect_delimiter(text)
            
            # Parse with pandas
            df = pd.read_csv(io.StringIO(text), delimiter=delimiter)
            
            return dataframe_to_result(df, "csv")
        except Exception as e:
            return ProcessingResult(
                processing_type=self.get_processing_type(),
                output_format="parquet",
                content=b"",
                error=f"CSV processing failed: {str(e)}"
            )
    
    @staticmethod
    def _detect_delimiter(text: str, sample_lines: int = 5) -> str:
        """Auto-detect CSV delimiter from sample."""
        delimiters = [",", ";", "\t", "|", " "]
        lines = text.split("\n")[:sample_lines]
        
        delimiter_counts = {}
        for delim in delimiters:
            delimiter_counts[delim] = sum(
                line.count(delim) for line in lines if line
            )
        
        # Return most common delimiter (or default to comma)
        return max(delimiter_counts, key=delimiter_counts.get) or ","


class ExcelProcessor(DataframeProcessor):
    """Handles XLSX and XLS files."""
    
    supported_mime_types = [
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    ]
    
    def can_process(self, mime_type: str, filename: str) -> bool:
        lower_name = filename.lower()
        return (
            lower_name.endswith((".xlsx", ".xls"))
            or mime_type in self.supported_mime_types
        )
    
    def process(self, data: bytes, filename: str) -> ProcessingResult:
        """Convert Excel to Parquet with metadata about all sheets."""
        try:
            # Read all sheets
            excel_file = io.BytesIO(data)
            xls = pd.ExcelFile(excel_file)
            
            # For MVP, combine first sheet (or could store metadata about all)
            first_sheet = xls.sheet_names[0] if xls.sheet_names else None
            if not first_sheet:
                raise ValueError("Excel file has no sheets")
            
            df = pd.read_excel(excel_file, sheet_name=first_sheet)
            
            metadata = {
                "method": "excel",
                "num_sheets": len(xls.sheet_names),
                "sheet_names": xls.sheet_names,
                "primary_sheet": first_sheet,
            }
            
            return dataframe_to_result(df, "excel", extra_metadata=metadata)
        except Exception as e:
            return ProcessingResult(
                processing_type=self.get_processing_type(),
                output_format="parquet",
                content=b"",
                error=f"Excel processing failed: {str(e)}"
            )


class JSONProcessor(DataframeProcessor):
    """Handles JSON files with tabular structure."""
    
    supported_mime_types = ["application/json"]
    
    def can_process(self, mime_type: str, filename: str) -> bool:
        return (
            filename.lower().endswith(".json")
            or mime_type == "application/json"
        )
    
    def process(self, data: bytes, filename: str) -> ProcessingResult:
        """Convert JSON to Parquet if it has tabular structure."""
        try:
            text = data.decode('utf-8')
            parsed = json.loads(text)
            
            # Try to convert to DataFrame
            df = self._parse_to_dataframe(parsed)
            if df is None or df.empty:
                raise ValueError("JSON does not have a tabular structure")
            
            return dataframe_to_result(df, "json")
        except Exception as e:
            return ProcessingResult(
                processing_type=ProcessingType.DATAFRAME,
                output_format="parquet",
                content=b"",
                error=f"JSON processing failed: {str(e)}"
            )
    
    @staticmethod
    def _parse_to_dataframe(obj) -> pd.DataFrame | None:
        """Try to parse JSON object as DataFrame."""
        try:
            if isinstance(obj, list):
                if all(isinstance(item, dict) for item in obj):
                    return pd.DataFrame(obj)
            elif isinstance(obj, dict):
                # Check if it looks like a table (keys are columns)
                if all(isinstance(v, (list, dict)) for v in obj.values()):
                    return pd.DataFrame(obj)
        except Exception:
            pass
        return None
