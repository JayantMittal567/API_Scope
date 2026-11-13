"""Shared pytest fixtures for ApiScope tests."""

import sqlite3
from collections.abc import Generator

import pytest

from apiscope.data.connection import create_connection


@pytest.fixture
def in_memory_db() -> Generator[sqlite3.Connection, None, None]:
    """Provide a fresh, configured in-memory SQLite database connection."""
    conn = create_connection(":memory:")
    try:
        yield conn
    finally:
        conn.close()
