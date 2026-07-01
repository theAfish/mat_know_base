# Development Notes

Use the project virtual environment for Python checks. The repository is
configured with `pythonpath = ["src"]`, so tests can import `mkb` without an
editable install, but the interpreter still needs project dependencies.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cd frontend && npm install
```

Common checks:

```bash
make ci          # Ruff plus pytest collection
make lint        # Python Ruff plus frontend TypeScript check
make check       # Python tests, frontend build, and lint
```

The bare `python3` interpreter may collect imports from `src`, but it will fail
unless dependencies such as `pydantic-settings`, `google-adk`, `pgvector`, and
`streamlit` are installed in that interpreter. Prefer `.venv/bin/python` in
scripts and documentation when reproducibility matters.

Local runtime artifacts live under `data/`, `.debug/`, `logs/`, and
`docker_volumes/`. Projection export YAMLs should be treated as generated output
unless they are deliberately promoted to named fixtures under `examples/` or
`tests/fixtures/`.

