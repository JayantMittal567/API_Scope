"""Web presentation layer for ApiScope.

Houses the FastAPI backend server, Jinja2 template rendering, and REST endpoints
for the ApiScope web dashboard.
"""

from __future__ import annotations

from apiscope.web.app import (
    AiGenerateSuiteRequest,
    ApproveSuiteRequest,
    DashboardConfig,
    TriggerRunRequest,
    create_web_app,
)

__all__ = [
    "AiGenerateSuiteRequest",
    "ApproveSuiteRequest",
    "DashboardConfig",
    "TriggerRunRequest",
    "create_web_app",
]
