"""Exercise restore safety without connecting to a database or object store."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from scripts.snapshot_manifest import write_manifest


@pytest.mark.parametrize(
    ("create_exit", "restore_exit", "expected_commands"),
    [(0, 0, ["createdb", "psql", "dropdb"]),
     (1, 0, ["createdb"]),
     (0, 1, ["createdb", "psql", "dropdb"])],
)
@pytest.mark.parametrize("password", ["test-only-password", ""])
def test_restore_uses_configured_clients_and_cleans_up_only_owned_databases(
    tmp_path, create_exit, restore_exit, expected_commands, password,
):
    root = Path(__file__).resolve().parents[1]
    payload = tmp_path / "payload"
    (payload / "postgres").mkdir(parents=True)
    (payload / "postgres/dump.sql").write_text("SELECT 1;\n")
    write_manifest(payload, "test", "test")
    archive = tmp_path / "snapshot.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(payload, arcname=".")

    # Stub legacy settings and the SQL check so these shell safety tests need
    # only the base package. Real settings/payload behavior is covered separately.
    python_wrapper = tmp_path / "snapshot-python"
    python_wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os, shlex, sys\n"
        "if sys.argv[1].endswith('snapshot_payload.py'):\n"
        "    if sys.argv[2] == 'settings':\n"
        "        for name in ('PG_HOST', 'PG_PORT', 'PG_USER', 'PG_PASSWORD', "
        "'PG_DATABASE', 'OBJECT_STORE_BACKEND'):\n"
        "            print(name + '=' + shlex.quote(os.environ['MKB_' + name]))\n"
        "        raise SystemExit(0)\n"
        "    if sys.argv[2] == 'verify':\n"
        "        raise SystemExit(0)\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
    )
    python_wrapper.chmod(0o755)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for command in ("createdb", "psql", "dropdb"):
        executable = binaries / command
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "command = Path(sys.argv[0]).name\n"
            "with open(os.environ['COMMAND_LOG'], 'a') as output:\n"
            "    output.write(json.dumps({'command': command, 'args': sys.argv[1:], "
            "'host': os.environ.get('PGHOST'), 'port': os.environ.get('PGPORT'), "
            "'user': os.environ.get('PGUSER'), 'password': os.environ.get('PGPASSWORD')}) + '\\n')\n"
            "if command == 'createdb':\n"
            "    raise SystemExit(int(os.environ['CREATE_EXIT']))\n"
            "if command == 'psql':\n"
            "    sys.stdin.read()\n"
            "    raise SystemExit(int(os.environ['RESTORE_EXIT']))\n"
        )
        executable.chmod(0o755)
    log = tmp_path / "commands.jsonl"
    result = subprocess.run(
        ["bash", str(root / "scripts/unpack_data.sh"), str(archive),
         "--pg-only", "--validate-only"],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
             "PYTHON": str(python_wrapper), "COMMAND_LOG": str(log),
             "CREATE_EXIT": str(create_exit), "RESTORE_EXIT": str(restore_exit),
             "MKB_PG_HOST": "database.example.test", "MKB_PG_PORT": "6543",
             "MKB_PG_USER": "snapshot_user", "MKB_PG_PASSWORD": password,
             "PGPASSWORD": "unrelated-inherited-password",
             "MKB_PG_DATABASE": "live_database", "MKB_OBJECT_STORE_BACKEND": "sql"},
        capture_output=True, text=True,
    )

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [call["command"] for call in calls] == expected_commands
    assert result.returncode == (1 if create_exit or restore_exit else 0), result.stderr
    temporary_database = calls[0]["args"][-1]
    assert temporary_database.startswith("mkb_restore_test_")
    for call in calls:
        assert (call["host"], call["port"], call["user"], call["password"]) == (
            "database.example.test", "6543", "snapshot_user", password,
        )
        assert "test-only-password" not in " ".join(call["args"])
        assert "live_database" not in " ".join(call["args"])
        if call["command"] == "dropdb":
            assert call["args"][-1] == temporary_database
