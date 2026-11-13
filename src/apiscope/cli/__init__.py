"""CLI presentation layer for ApiScope.

Defines the command-line interface commands and options powered by Typer.
"""

from apiscope.cli.main import (
    EXIT_OK,
    EXIT_TEST_FAILURE,
    EXIT_TOOL_ERROR,
    EXIT_WARNINGS_STRICT,
    app,
    main,
)

__all__ = [
    "EXIT_OK",
    "EXIT_TEST_FAILURE",
    "EXIT_TOOL_ERROR",
    "EXIT_WARNINGS_STRICT",
    "app",
    "main",
]
