# Legacy Streamlit UI

The React frontend is the active user interface. This package is
compatibility-only while older helpers, tests, and local workflows are migrated.

Do not add new product behavior here. Shared behavior should move to backend
services, pure helpers, or the React/FastAPI surface, then this package can be
retired once remaining tests stop importing Streamlit page modules.
> Retired compatibility source
>
> This directory is excluded from the `mat-know-base` wheel and is no longer
> reachable from the `mkb` CLI. It remains in the repository for the explicit
> export window only. Install `.[streamlit-compat]` to run it directly while
> migrating; new behavior and tests must target backend services and the React UI.

