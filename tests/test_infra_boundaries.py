"""Core imports and reusable processors stay independent of deployment adapters."""

import importlib.util
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def _without_deployment_imports(script: str, *, block_requests: bool = True):
    source_root = Path(__file__).resolve().parents[1] / "src"
    prefix = f"""
import importlib.abc
import sys
sys.path.insert(0, {str(source_root)!r})

class RejectDeploymentImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = (
            'sqlalchemy', 'asyncpg', 'psycopg', 'pgvector', 'google', 'litellm',
            'boto3', 'fastapi', 'pydantic_settings', 'mkb.config', 'mkb.db',
            'mkb.services', 'mkb.agents', 'mkb.api', 'mkb.runtime_settings',
        )
        if {block_requests!r}:
            blocked += ('requests',)
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise AssertionError('unexpected deployment import: ' + fullname)

sys.meta_path.insert(0, RejectDeploymentImports())
"""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(prefix) + textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return result


def test_root_filesystem_and_text_processor_work_with_only_core_dependencies():
    _without_deployment_imports(
        """
        import tempfile
        import mkb
        from mkb.adapters import FileObjectStore
        from mkb.doctor import main as diagnose
        from mkb.processors.base import ProcessingResult
        from mkb.processors.text_processor import TextProcessor
        from mkb.processors.types import ProcessingType

        assert diagnose([]) == 0
        converted = TextProcessor().process(b'alpha beta', 'notes.txt')
        assert converted.is_success()
        assert converted.processing_type == ProcessingType.MARKDOWN
        assert converted.content == b'# notes.txt\\n\\nalpha beta'
        assert ProcessingResult is type(converted)
        with tempfile.TemporaryDirectory() as directory:
            store = FileObjectStore(directory)
            store.put_bytes('processed', 'notes.md', converted.content)
            assert store.get_bytes('processed', 'notes.md') == converted.content
            store.close()
        with mkb.KnowledgeBase.from_repositories(mkb.RepositoryBundle()) as kb:
            assert kb.database is None
            assert isinstance(kb.extraction, mkb.ExtractionService)
        """
    )


def test_mineru_cloud_processor_requires_no_database_or_application_settings():
    if importlib.util.find_spec("requests") is None:
        pytest.skip("MinerU cloud processor requests dependency is not installed")
    _without_deployment_imports(
        """
        from mkb.processors.pdf_mineru_api import MinerUApiPDFProcessor
        from mkb.processors.types import ProcessingType

        processor = MinerUApiPDFProcessor(api_base='https://example.org', token='test')
        assert processor.can_process('application/pdf', 'paper.pdf')
        assert processor.get_processing_type() == ProcessingType.MARKDOWN
        """,
        block_requests=False,
    )


def test_entire_custom_repository_extraction_runs_without_optional_backends():
    extraction_tests = Path(__file__).with_name("test_extraction_core.py")
    _without_deployment_imports(
        f"""
        from pathlib import Path
        import runpy
        import tempfile

        integration = runpy.run_path({str(extraction_tests)!r})
        with tempfile.TemporaryDirectory() as directory:
            integration['test_ocr_projection_processors_and_provenance_without_database'](
                Path(directory)
            )
        """
    )


def test_cli_help_does_not_initialize_application_or_database():
    result = _without_deployment_imports(
        """
        import runpy
        sys.argv = ['mkb', '--help']
        runpy.run_module('mkb.cli', run_name='__main__')
        """
    )
    assert "usage:" in result.stdout


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [([], ("127.0.0.2", 9001)), (["--host", "localhost", "--port", "9999"], ("localhost", 9999))],
)
def test_cli_api_reads_environment_defaults_after_parsing_and_preserves_overrides(
    monkeypatch, arguments, expected
):
    from mkb import MKBConfig, cli

    received = []
    monkeypatch.setattr(
        MKBConfig,
        "from_environment",
        classmethod(lambda cls: MKBConfig(api_host="127.0.0.2", api_port=9001)),
    )
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(cli, "cmd_api", lambda args: received.append((args.host, args.port)))
    monkeypatch.setattr(sys, "argv", ["mkb", "api", *arguments])
    cli.main()
    assert received == [expected]
