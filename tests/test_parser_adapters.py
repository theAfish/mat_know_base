"""Optional parser adapters do not load host OCR or database dependencies."""

from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from mkb.adapters.parsers import ProcessorParser, TesseractParser
from mkb.exceptions import BackendUnavailableError, ProviderError, ValidationError
from mkb.processors.base import ProcessingResult
from mkb.processors.text_processor import TextProcessor
from mkb.processors.types import ProcessingType
from mkb.sdk import KnowledgeBase


def test_text_processor_bridge_is_usable_from_the_public_parser_registry():
    kb = KnowledgeBase()
    adapter = ProcessorParser(
        name="documents", processor=TextProcessor(), source_types=frozenset({"text/plain"})
    )
    kb.parsers.register_adapter(adapter)
    result = kb.parsers.parse(
        "documents",
        b"temperature = 300",
        source_type="text/plain",
        parameters={"filename": "result.txt"},
    )
    assert result.text == "# result.txt\n\ntemperature = 300"
    assert result.metadata == {"method": "plain_text", "detected_encoding": "utf-8"}


def test_parser_module_and_plain_text_processor_work_without_optional_imports():
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = f"""
import importlib.abc
import sys
sys.path.insert(0, {str(source_root)!r})
class RejectOptionalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        names = ('sqlalchemy', 'PIL', 'pytesseract', 'docx', 'mkb.config', 'mkb.db', 'mkb.services', 'google')
        if any(fullname == name or fullname.startswith(name + '.') for name in names):
            raise AssertionError('unexpected import: ' + fullname)
sys.meta_path.insert(0, RejectOptionalImports())
from mkb.adapters.parsers import ProcessorParser, TesseractParser
from mkb.processors.text_processor import TextProcessor
parser = ProcessorParser('plain', TextProcessor(), frozenset({{'text/plain'}}))
assert parser.parse(b'hello', parameters={{'filename': 'note.md'}}).text == 'hello'
assert TesseractParser().name == 'tesseract'
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def _processor(result):
    return ProcessorParser(
        name="custom",
        processor=SimpleNamespace(process=lambda *_: result),
        source_types=frozenset({"text/plain"}),
    )


def test_processor_failure_and_invalid_output_are_explicit():
    with pytest.raises(ProviderError, match="custom.*malformed input"):
        _processor(
            ProcessingResult(ProcessingType.MARKDOWN, "md", b"", error="malformed input")
        ).parse(b"input")
    with pytest.raises(ValidationError, match="text or Markdown"):
        _processor(ProcessingResult(ProcessingType.DATAFRAME, "parquet", b"content")).parse(
            b"input"
        )
    with pytest.raises(ValidationError, match="UTF-8"):
        _processor(ProcessingResult(ProcessingType.MARKDOWN, "md", b"\xff")).parse(b"input")
    with pytest.raises(ValidationError, match="content must be bytes"):
        _processor(ProcessingResult(ProcessingType.MARKDOWN, "md", "not bytes")).parse(b"input")


def test_processor_exception_is_chained_and_options_are_validated_before_processing():
    calls = []

    def fail(*args):
        calls.append(args)
        raise RuntimeError("converter offline")

    adapter = ProcessorParser("custom", SimpleNamespace(process=fail), frozenset({"text/plain"}))
    with pytest.raises(ValidationError, match="filename"):
        adapter.parse(b"data", parameters={"filename": " "})
    with pytest.raises(ValidationError, match="Unknown processor"):
        adapter.parse(b"data", parameters={"unknown": 1})
    with pytest.raises(ValidationError, match="bytes"):
        adapter.parse("data")
    assert calls == []
    with pytest.raises(ProviderError) as captured:
        adapter.parse(b"data", parameters={"filename": "source.txt"})
    assert calls == [(b"data", "source.txt")]
    assert isinstance(captured.value.__cause__, RuntimeError)


def _fake_ocr(monkeypatch, *, fail=False):
    calls = []

    class Image:
        width = 640
        height = 480

        def __enter__(self):
            return self

        def __exit__(self, *_):
            calls.append("closed")

    def open_image(stream):
        calls.append(stream.read())
        return Image()

    def image_to_string(image, *, lang, timeout):
        calls.append((image.width, image.height, lang, timeout))
        if fail:
            raise RuntimeError("tesseract timed out")
        return "measured = 12"

    pillow = ModuleType("PIL")
    pillow.Image = SimpleNamespace(open=open_image)
    pytesseract = ModuleType("pytesseract")
    pytesseract.image_to_string = image_to_string
    monkeypatch.setitem(sys.modules, "PIL", pillow)
    monkeypatch.setitem(sys.modules, "pytesseract", pytesseract)
    return calls


def test_tesseract_forwards_language_timeout_and_closes_image(monkeypatch):
    calls = _fake_ocr(monkeypatch)
    parser = TesseractParser(language="eng", timeout=30)
    result = parser.parse(b"fake image", parameters={"language": "chi_sim+eng", "timeout": "8.5"})
    assert result.text == "measured = 12"
    assert result.metadata == {"width": 640, "height": 480, "parser": "tesseract"}
    assert calls == [b"fake image", (640, 480, "chi_sim+eng", 8.5), "closed"]


def test_tesseract_execution_error_preserves_cause_and_closes_image(monkeypatch):
    calls = _fake_ocr(monkeypatch, fail=True)
    with pytest.raises(ProviderError, match="Tesseract OCR failed") as captured:
        TesseractParser().parse(b"fake image")
    assert isinstance(captured.value.__cause__, RuntimeError)
    assert calls[-1] == "closed"


def test_tesseract_missing_optional_dependency_has_install_guidance(monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(BackendUnavailableError, match=r"mat-know-base\[ocr\]"):
        TesseractParser().parse(b"image")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), "bad", None, True])
def test_tesseract_rejects_invalid_timeouts_before_loading_ocr(monkeypatch, timeout):
    calls = _fake_ocr(monkeypatch)
    with pytest.raises(ValidationError, match="finite positive"):
        TesseractParser().parse(b"image", parameters={"timeout": timeout})
    assert calls == []


def test_tesseract_validates_configuration_and_inputs(monkeypatch):
    calls = _fake_ocr(monkeypatch)
    with pytest.raises(ValidationError, match="language"):
        TesseractParser(language=" ")
    with pytest.raises(ValidationError, match="source_types"):
        TesseractParser(source_types=frozenset())
    with pytest.raises(ValidationError, match="finite positive"):
        TesseractParser(timeout=float("nan"))
    with pytest.raises(ValidationError, match="language"):
        TesseractParser().parse(b"image", parameters={"language": None})
    with pytest.raises(ValidationError, match="Unknown OCR"):
        TesseractParser().parse(b"image", parameters={"dpi": 300})
    with pytest.raises(ValidationError, match="bytes"):
        TesseractParser().parse("image")
    assert calls == []
