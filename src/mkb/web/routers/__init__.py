"""FastAPI APIRouter modules per resource domain.

Each module exposes a ``router = APIRouter()`` instance with routes that
keep their original ``/api/...`` paths verbatim. Routers share state via
application services obtained from ``mkb.web.dependencies``.
"""
