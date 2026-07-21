"""Small build hook that bundles the legacy Alembic tree as package resources."""

from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPyWithMigrations(build_py):
    def run(self):
        package_target = Path(self.build_lib) / "mkb"
        if package_target.exists():
            shutil.rmtree(package_target)
        super().run()
        root = Path(__file__).parent
        target = package_target / "resources" / "migrations"
        shutil.copytree(
            root / "alembic",
            target / "alembic",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copy2(root / "alembic.ini", target / "alembic.ini")


setup(cmdclass={"build_py": BuildPyWithMigrations})
