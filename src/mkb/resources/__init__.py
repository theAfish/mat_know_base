"""Read-only resources shipped with the MKB distribution."""

from importlib.resources import files
from pathlib import Path


def alembic_paths() -> tuple[Path, Path]:
    """Return the bundled Alembic configuration and script directory.

    An editable source checkout uses the same files by a path relative to this
    module. Installed wheels use the copies added by the package build hook.
    """
    bundled = files(__package__).joinpath("migrations")
    config = Path(str(bundled.joinpath("alembic.ini")))
    scripts = Path(str(bundled.joinpath("alembic")))
    if config.is_file() and scripts.is_dir():
        return config, scripts
    root = Path(__file__).resolve().parents[3]
    config = root / "alembic.ini"
    scripts = root / "alembic"
    if not config.is_file() or not scripts.is_dir():
        raise FileNotFoundError("MKB Alembic resources are unavailable")
    return config, scripts


__all__ = ["alembic_paths"]
