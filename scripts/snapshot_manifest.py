#!/usr/bin/env python3
"""Create/verify snapshot manifests and safely extract archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_manifest(root: Path, revision: str, app_version: str) -> None:
    files = {str(path.relative_to(root)): {"sha256": digest(path), "bytes": path.stat().st_size}
             for path in sorted(root.rglob("*")) if path.is_file() and path.name != "manifest.json"}
    manifest = {"format_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "schema_revision": revision, "application_version": app_version, "files": files}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > 100_000:
            raise ValueError("snapshot contains too many members")
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk() or member.isdev():
                raise ValueError(f"unsafe snapshot member: {member.name}")
        bundle.extractall(destination, members=members, filter="data")
    manifest_path = destination / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("snapshot has no versioned manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise ValueError("unsupported snapshot format")
    for name, metadata in manifest.get("files", {}).items():
        path = destination / name
        if not path.is_file() or digest(path) != metadata.get("sha256"):
            raise ValueError(f"snapshot checksum failed: {name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("root", type=Path)
    create.add_argument("--revision", required=True)
    create.add_argument("--app-version", default=os.environ.get("MKB_APP_VERSION", "0.1.0"))
    extract = sub.add_parser("extract")
    extract.add_argument("archive", type=Path)
    extract.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.command == "create":
        write_manifest(args.root, args.revision, args.app_version)
    else:
        safe_extract(args.archive, args.destination)


if __name__ == "__main__":
    main()
