"""Smoke tests for basic imports and config."""

from mkb import __version__
from mkb.config import Settings


def test_version():
    assert __version__ == "0.1.0"


def test_settings_defaults():
    s = Settings()
    assert "mkb" in s.pg_dsn
    assert s.s3_bucket_raw == "raw"


def test_generic_llm_aliases_override_defaults(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    monkeypatch.setenv("LLM_API_BASE", "https://example.invalid/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)

    s = Settings()

    assert s.llm_api_key == "generic-key"
    assert s.llm_api_base == "https://example.invalid/v1"
