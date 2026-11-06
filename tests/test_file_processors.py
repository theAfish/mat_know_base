import io
from pathlib import Path
from types import SimpleNamespace
import zipfile

from openpyxl import Workbook
import pandas as pd

from mkb.processors.dataframe_processor import ExcelProcessor
from mkb.processors.pdf_mineru_api import MinerUApiPDFProcessor


class WorkbookFile(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.bytes_read = 0

    def read(self, size=-1):
        # ZIP footer reads may ask for the remainder near EOF. A complete
        # compressed workbook read from the start is never necessary.
        if size < 0:
            assert len(self.getbuffer()) - self.tell() <= 128 * 1024
        data = super().read(size)
        self.bytes_read += len(data)
        return data


def test_xlsx_processor_accepts_seekable_file_and_path_without_reading_embedded_binary(tmp_path, monkeypatch):
    path = tmp_path / "measurements.xlsx"
    workbook = Workbook()
    workbook.active.append(["sample", "density"])
    workbook.active.append(["A1", 3.21])
    workbook.save(path)
    workbook.close()
    with zipfile.ZipFile(path, "a", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("xl/media/large-unused-image.bin", b"x" * (2 * 1024 * 1024))
    with path.open("rb") as original:
        content = WorkbookFile(original.read())
    def forbidden(*args, **kwargs):
        raise AssertionError("Path.read_bytes is not file-based conversion")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    processor = ExcelProcessor()
    from_handle = processor.process_file(content, path.name)
    assert from_handle.error is None
    assert content.bytes_read < path.stat().st_size
    assert not content.closed
    from_path = processor.process_file(path)
    assert from_path.error is None
    assert pd.read_parquet(io.BytesIO(from_path.content)).to_dict("records") == [{"sample": "A1", "density": 3.21}]


def test_pdf_cloud_upload_spools_nonseekable_input_and_passes_a_file_to_http(monkeypatch):
    from mkb.processors import pdf_mineru_api
    class Nonseekable:
        position = 0
        def read(self, amount):
            assert 0 < amount <= 256 * 1024
            size = min(amount, 1024 * 1024 - self.position)
            self.position += size
            return b"x" * size
    observed = []
    def put(url, *, data, timeout):
        assert data.seekable()
        data.fileno()  # Disk-backed spool, rather than BytesIO or a bytes body.
        size = 0
        while chunk := data.read(8192):
            size += len(chunk)
        observed.append((data, size))
        return SimpleNamespace(status_code=200)
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        archive.writestr("full.md", "# Converted")
    processor = MinerUApiPDFProcessor(api_base="https://invalid.test", token="fixture")
    monkeypatch.setattr(processor, "_request_upload_url", lambda _: ("batch", "https://upload.invalid"))
    monkeypatch.setattr(processor, "_poll_until_done", lambda _: "https://result.invalid")
    monkeypatch.setattr(pdf_mineru_api.requests, "put", put)
    monkeypatch.setattr(pdf_mineru_api, "_http_get_bytes", lambda *args, **kwargs: result.getvalue())
    converted = processor.process_file(Nonseekable(), "report.pdf")
    assert converted.error is None
    assert observed[0][1] == 1024 * 1024
    assert observed[0][0].closed
