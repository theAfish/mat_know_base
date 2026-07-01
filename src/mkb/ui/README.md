# Legacy Streamlit UI

The React frontend is the active user interface. This package is
compatibility-only while older helpers, tests, and local workflows are migrated.

Do not add new product behavior here. Shared behavior should move to backend
services, pure helpers, or the React/FastAPI surface, then this package can be
retired once remaining tests stop importing Streamlit page modules.

