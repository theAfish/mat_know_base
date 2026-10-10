#!/usr/bin/env python3
"""Business snapshot payload helpers; use the project's Python environment."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath


def relative_path(value: str, root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        path = path.relative_to(root)
    if not path.parts or ".." in path.parts or path.parts[0].startswith("."):
        raise ValueError(f"Snapshot data path must be inside the project: {value}")
    (root / path).resolve().relative_to(root.resolve())
    return path


def local_paths(settings, root: Path) -> list[Path]:
    paths = [Path("data")]
    for value in (settings.processed_local_root,):
        path = relative_path(value, root)
        if not any(path == parent or parent in path.parents for parent in paths):
            paths.append(path)
    if settings.object_store_backend == "file":
        path = relative_path(settings.object_store_root, root)
        if not any(path == parent or parent in path.parents for parent in paths):
            paths.append(path)
    return paths


def stage_local(settings, root: Path, destination: Path) -> None:
    paths = local_paths(settings, root)
    configured = Path(settings.runtime_settings_path)
    excluded = {root / "data/runtime_settings.json",
                configured if configured.is_absolute() else root / configured}

    def ignore(directory, names):
        return [name for name in names if Path(directory) / name in excluded]

    for relative in paths:
        source = root / relative
        if source.is_symlink() or (source.is_dir() and any(
            child.is_symlink() for child in source.rglob("*")
        )):
            raise ValueError(f"Snapshot does not support symlinks: {relative}")
        target = destination / "local" / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)
        elif source.is_file() and source not in excluded:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (destination / "local").mkdir(parents=True, exist_ok=True)
    metadata = {
        "object_store_backend": settings.object_store_backend,
        "object_store_root": str(relative_path(settings.object_store_root, root))
        if settings.object_store_backend == "file" else None,
        "local_paths": [str(path) for path in paths],
        "settings_included": False,
    }
    (destination / "snapshot.json").write_text(json.dumps(metadata, indent=2) + "\n")


def restore_local(settings, source: Path, root: Path, *, validate_only: bool = False) -> None:
    """Validate all destinations before copying; preserve target settings."""
    allowed = local_paths(settings, root)
    metadata_path = source.parent / "snapshot.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        if metadata["object_store_backend"] == "file":
            target_root = str(relative_path(settings.object_store_root, root))
            if metadata["object_store_root"] != target_root:
                raise ValueError("Configure the target object_store_root to match the snapshot")
    configured = Path(settings.runtime_settings_path)
    excluded = {root / "data/runtime_settings.json",
                configured if configured.is_absolute() else root / configured}
    entries = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = root / relative
        if target in excluded:
            continue
        if not any(relative == parent or parent in relative.parents
                   or relative in parent.parents for parent in allowed):
            raise ValueError(f"Unexpected local snapshot path: {relative}")
        for parent in (target, *target.parents):
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError(f"Refusing to restore through a symlink: {relative}")
        if path.is_symlink() or (target.exists() and target.is_dir() != path.is_dir()):
            raise ValueError(f"Conflicting restore path: {relative}")
        entries.append((path, target))
    if not validate_only:
        for path, target in entries:
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)


def s3_client(settings):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4", connect_timeout=10, read_timeout=60),
    )


def object_path(root: Path, bucket: str, key: str) -> Path:
    for value in (bucket, key):
        path = PurePosixPath(value)
        if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("Object key cannot be represented safely in a snapshot")
        if path.as_posix() != value:
            raise ValueError("Object key cannot be normalized without data loss")
    return root / bucket / key


def buckets(settings) -> list[str]:
    return list(dict.fromkeys(getattr(settings, f"s3_bucket_{name}")
                              for name in ("raw", "processed", "archive", "temp")))


def mirror_s3(settings, directory: Path, *, restore: bool = False) -> None:
    client = s3_client(settings)
    names = (sorted(path.name for path in directory.iterdir() if path.is_dir())
             if restore else buckets(settings))
    for bucket in names:
        local = object_path(directory, bucket, "placeholder").parent
        if restore:
            from botocore.exceptions import ClientError
            try:
                client.head_bucket(Bucket=bucket)
            except ClientError as exc:
                if str(exc.response["Error"]["Code"]) not in {"404", "NoSuchBucket"}:
                    raise
                client.create_bucket(Bucket=bucket)
        else:
            local.mkdir(parents=True, exist_ok=True)
        remote = {
            item["Key"]: item
            for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket)
            for item in page.get("Contents", [])
        }
        if restore:
            files = {path.relative_to(local).as_posix(): path
                     for path in local.rglob("*") if path.is_file()}
            def upload(item):
                key, path = item
                client.upload_file(str(path), bucket, key)
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(upload, files.items()))
            for key in remote.keys() - files.keys():
                client.delete_object(Bucket=bucket, Key=key)
            count = len(files)
        else:
            def download(item):
                key, metadata = item
                target = object_path(directory, bucket, key)
                target.parent.mkdir(parents=True, exist_ok=True)
                # Conditional GET fails if an object changed after the inventory.
                response = client.get_object(Bucket=bucket, Key=key, IfMatch=metadata["ETag"])
                try:
                    with target.open("wb") as output:
                        shutil.copyfileobj(response["Body"], output)
                finally:
                    response["Body"].close()
                if target.stat().st_size != metadata["Size"]:
                    raise ValueError("S3 object size changed during backup")
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(download, remote.items()))
            count = len(remote)
        print(f"[snapshot] {bucket}: {count} objects", flush=True)


def verify_references(settings, *, snapshot: Path | None = None, database: str | None = None) -> None:
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import make_url

    url = make_url(settings.pg_dsn_sync)
    if database:
        url = url.set(database=database)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            tables = set(inspect(connection).get_table_names())
            refs = set()
            for table in ("assets", "processed_assets"):
                if table in tables:
                    refs.update(tuple(row) for row in connection.execute(text(
                        f"SELECT s3_bucket, s3_key FROM {table}"
                    )))
            backend = settings.object_store_backend
            metadata = {}
            if snapshot and (snapshot / "snapshot.json").exists():
                metadata = json.loads((snapshot / "snapshot.json").read_text())
                backend = metadata["object_store_backend"]
            if backend == "sql":
                stored = set(tuple(row) for row in connection.execute(text(
                    "SELECT bucket, key FROM object_blobs"
                ))) if "object_blobs" in tables else set()
                missing = refs - stored
            elif backend == "s3" and not snapshot:
                client = s3_client(settings)
                stored = set()
                for bucket in buckets(settings):
                    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                        stored.update((bucket, item["Key"]) for item in page.get("Contents", []))
                missing = refs - stored
            else:
                if backend == "s3":
                    root = snapshot / "minio"
                elif snapshot:
                    root = snapshot / "local" / relative_path(metadata.get(
                        "object_store_root", settings.object_store_root
                    ), Path.cwd())
                else:
                    root = Path(settings.object_store_root)
                missing = {(bucket, key) for bucket, key in refs
                           if not object_path(root, bucket, key).is_file()}
            if missing:
                raise ValueError(
                    f"{len(missing)} referenced objects are missing from '{backend}'. "
                    "Check MKB_OBJECT_STORE_BACKEND and migrate or back up the actual storage."
                )
            for table in ("custom_skills", "post_processor_scripts"):
                if table not in tables:
                    continue
                columns = "storage_path, metadata" if table == "custom_skills" else "storage_path"
                for row in connection.execute(text(f"SELECT {columns} FROM {table}")):
                    relative = relative_path(row[0], Path.cwd())
                    target = snapshot / "local" / relative if snapshot else relative
                    if table == "custom_skills":
                        expected = {"SKILL.md", *((row[1] or {}).get("files") or [])}
                        files = [object_path(target.parent, target.name, name) for name in expected]
                    else:
                        files = [target]
                    if any(not path.is_file() for path in files):
                        raise ValueError(f"Missing registered file in {table}: {relative}")
            print(f"[snapshot] Verified {len(refs)} object references and registered skill/script files.")
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["settings", "stage", "backup-s3", "restore-s3",
                                           "verify", "check-local", "restore-local"])
    parser.add_argument("path", type=Path, nargs="?")
    parser.add_argument("--database")
    args = parser.parse_args()
    from mkb.config import settings

    if settings.object_store_backend not in {"sql", "s3", "file"}:
        raise ValueError("Unsupported object storage backend")
    if args.command == "settings":
        root = Path.cwd()
        object_root = (str(relative_path(settings.object_store_root, root))
                       if settings.object_store_backend == "file" else settings.object_store_root)
        for name, value in {"PG_USER": settings.pg_user, "PG_DATABASE": settings.pg_database,
                            "PG_PASSWORD": settings.pg_password, "PG_PORT": settings.pg_port,
                            "OBJECT_STORE_BACKEND": settings.object_store_backend,
                            "OBJECT_STORE_ROOT": object_root,
                            "PROCESSED_LOCAL_ROOT": str(relative_path(settings.processed_local_root, root)),
                            **{f"BUCKET_{kind.upper()}": getattr(settings, f"s3_bucket_{kind}")
                               for kind in ("raw", "processed", "archive", "temp")}}.items():
            print(f"{name}={shlex.quote(str(value))}")
    elif args.command == "stage":
        stage_local(settings, Path.cwd(), args.path)
    elif args.command == "verify":
        verify_references(settings, snapshot=args.path, database=args.database)
    elif args.command in {"check-local", "restore-local"}:
        restore_local(settings, args.path, Path.cwd(), validate_only=args.command == "check-local")
    else:
        mirror_s3(settings, args.path, restore=args.command == "restore-s3")


if __name__ == "__main__":
    main()
