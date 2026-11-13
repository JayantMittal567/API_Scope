"""Data Access Layer for ApiScope.

Provides SQLite persistence management, connection utilities, and base
repository interfaces.
"""

from apiscope.data.connection import (
    DatabaseConfig,
    DatabaseManager,
    create_connection,
    get_connection,
)
from apiscope.data.repositories import (
    MAX_HISTORY_LIMIT,
    RunRepository,
    initialize_schema,
)
from apiscope.data.repository import BaseRepository

__all__ = [
    "BaseRepository",
    "DatabaseConfig",
    "DatabaseManager",
    "MAX_HISTORY_LIMIT",
    "RunRepository",
    "create_connection",
    "get_connection",
    "initialize_schema",
]
