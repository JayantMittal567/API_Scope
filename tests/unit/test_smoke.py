"""Foundational smoke tests for ApiScope initialization and core baseline."""

import sqlite3

from typer.testing import CliRunner

import apiscope
from apiscope.cli.main import app
from apiscope.data.connection import get_connection

runner = CliRunner()


def test_package_import_and_version() -> None:
    """Verify that the apiscope package is importable and exposes version."""
    assert apiscope.__version__ == "0.1.0"


def test_cli_version_command() -> None:
    """Verify that the Typer CLI entrypoint executes and reports the version."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "ApiScope version 0.1.0" in result.stdout


def test_cli_help_command() -> None:
    """Verify that the Typer CLI entrypoint displays help information."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ApiScope: Automated API Testing" in result.stdout


def test_sqlite_in_memory_connection(in_memory_db: sqlite3.Connection) -> None:
    """Verify SQLite connection configures foreign keys and row factory."""
    cursor = in_memory_db.execute("PRAGMA foreign_keys;")
    row = cursor.fetchone()
    assert row is not None
    # foreign_keys PRAGMA returns 1 when enabled
    assert row[0] == 1

    # Verify sqlite3.Row dict-like column access
    cursor = in_memory_db.execute("SELECT 42 AS answer;")
    result = cursor.fetchone()
    assert result is not None
    assert result["answer"] == 42


def test_sqlite_context_manager() -> None:
    """Verify get_connection context manager handles transaction and cleanup."""
    with get_connection(":memory:") as conn:
        conn.execute("CREATE TABLE test_items (id INTEGER PRIMARY KEY, name TEXT);")
        conn.execute("INSERT INTO test_items (name) VALUES ('smoke_test');")
        cursor = conn.execute("SELECT COUNT(*) AS total FROM test_items;")
        row = cursor.fetchone()
        assert row is not None
        assert row["total"] == 1
