"""Smoke tests for basic imports and config."""

from mkb import __version__
from mkb.config import Settings


def test_version():
    assert __version__ == "0.1.0"


def test_settings_defaults():
    s = Settings()
    assert "mkb" in s.pg_dsn
    assert s.s3_bucket_raw == "raw"
