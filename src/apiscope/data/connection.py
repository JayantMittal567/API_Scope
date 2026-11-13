"""SQLite database connection provider and context management.

Handles SQLite connection lifecycle, configuring row factories, enabling
foreign keys, and enforcing transactional integrity without global mutable state.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class DatabaseConfig:
    """Configuration for SQLite database connection."""

    def __init__(self, db_path: str | Path = "apiscope.db") -> None:
        """Initialize database configuration.

        Args:
            db_path: Path to SQLite database file or ':memory:'.
        """
        self.db_path = str(db_path)


class DatabaseManager:
    """Manages SQLite database connections, lifecycle, and filesystem access."""

    def __init__(self, db_path: str | Path = "apiscope.db") -> None:
        """Initialize database manager and ensure directory exists.

        Args:
            db_path: Path to SQLite database file or ':memory:'.
        """
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            parent = Path(self.db_path).parent
            if str(parent) not in ("", "."):
                parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None

    def get_connection(self) -> sqlite3.Connection:
        """Create and configure a new SQLite connection.

        Returns:
            Configured sqlite3.Connection instance.
        """
        self._connection = create_connection(self.db_path)
        return self._connection

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Provide a transactional SQLite connection context.

        Automatically commits on normal completion, rolls back on exceptions,
        and guarantees connection closure.

        Yields:
            Configured sqlite3.Connection instance.
        """
        with get_connection(self.db_path) as conn:
            yield conn

    def close(self) -> None:
        """Explicitly close any open connections held by this manager."""
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def __enter__(self) -> "DatabaseManager":
        """Enter context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager and close resources."""
        self.close()


def create_connection(db_path: str | Path = "apiscope.db") -> sqlite3.Connection:
    """Create and configure a new SQLite connection.

    Configures:
    - row_factory to sqlite3.Row for dict-like column access.
    - PRAGMA foreign_keys = ON to ensure referential integrity.
    - PRAGMA journal_mode = WAL for concurrent performance (file databases only).

    Args:
        db_path: File system path to the database or ':memory:'.

    Returns:
        Configured sqlite3.Connection instance.

    Raises:
        sqlite3.Error: If connection or PRAGMA execution fails.
    """
    path_str = str(db_path)
    conn = sqlite3.connect(path_str)
    conn.row_factory = sqlite3.Row

    # Enforce foreign key constraints
    conn.execute("PRAGMA foreign_keys = ON;")

    # Enable WAL mode for file-based databases to improve concurrent read/write
    if path_str != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL;")

    return conn


@contextmanager
def get_connection(
    db_path: str | Path = "apiscope.db",
) -> Generator[sqlite3.Connection, None, None]:
    """Provide a transactional SQLite connection context.

    Automatically commits on normal completion, rolls back on exceptions,
    and guarantees connection closure.

    Args:
        db_path: File system path to the database or ':memory:'.

    Yields:
        Configured sqlite3.Connection instance.
    """
    conn = create_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
