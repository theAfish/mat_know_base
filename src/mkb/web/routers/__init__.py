"""FastAPI APIRouter modules per resource domain.

Each module exposes a ``router = APIRouter()`` instance with routes that
keep their original ``/api/...`` paths verbatim. Routers share state via
``mkb.web._state`` (notably the ``jobs`` JobManager singleton).
"""
