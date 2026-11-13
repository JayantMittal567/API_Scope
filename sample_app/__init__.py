"""Sample E-Commerce Target Application with 12 Planted Bugs (Module M13).

Provides a deterministic in-memory FastAPI target application for verifying
ApiScope's rule-based test generator (M5), health monitoring (M8), zombie
API detection (M9), and silent-failure detection (M10).
"""

from sample_app.app import app, create_sample_app, reset_state

__all__ = ["app", "create_sample_app", "reset_state"]
