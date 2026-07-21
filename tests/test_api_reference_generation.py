from pathlib import Path
from runpy import run_path


def test_generated_api_reference_is_current():
    root = Path(__file__).resolve().parents[1]
    namespace = run_path(str(root / "scripts/generate_api_reference.py"))

    assert namespace["OUTPUT"].read_text(encoding="utf-8") == namespace["generate"]()
