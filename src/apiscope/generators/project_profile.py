"""Project profile domain models and YAML loader for AI test generation context.

Covers: FR-49, NFR-02, NFR-04.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from apiscope.core.exceptions import DefinitionError, PathTraversalError
from apiscope.definitions.collection import validate_safe_path

MAX_PROFILE_FILE_BYTES: int = 1_048_576  # 1 MB


@dataclass(frozen=True)
class RoleRule:
    """Security role boundary and IDOR evaluation rule."""

    __test__ = False

    role_name: str
    token_variable: str
    allowed_endpoints: list[str] = field(default_factory=list)
    forbidden_endpoints: list[str] = field(default_factory=list)
    idor_check: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert role rule to dictionary representation."""
        res: dict[str, Any] = {
            "role_name": self.role_name,
            "token_variable": self.token_variable,
            "allowed_endpoints": list(self.allowed_endpoints),
            "forbidden_endpoints": list(self.forbidden_endpoints),
        }
        if self.idor_check is not None:
            res["idor_check"] = dict(self.idor_check)
        return res


@dataclass(frozen=True)
class StateTransitionRule:
    """Invalid entity lifecycle state transition rule."""

    __test__ = False

    entity: str
    description: str
    endpoint: str
    invalid_state_id: int | str
    invalid_state_name: str
    expected_status_range: list[int] = field(default_factory=lambda: [400, 409])
    body: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert state transition rule to dictionary representation."""
        res: dict[str, Any] = {
            "entity": self.entity,
            "description": self.description,
            "endpoint": self.endpoint,
            "invalid_state_id": self.invalid_state_id,
            "invalid_state_name": self.invalid_state_name,
            "expected_status_range": list(self.expected_status_range),
        }
        if self.body is not None:
            res["body"] = dict(self.body)
        return res


@dataclass(frozen=True)
class BusinessInvariantRule:
    """Multi-step business logic invariant rule (e.g. single-use, idempotency)."""

    __test__ = False

    rule_id: str
    module: str
    description: str
    endpoint: str
    rule_type: str  # "single_use", "idempotency", "custom_negative", "custom_positive"
    payload: dict[str, Any] = field(default_factory=dict)
    first_expected_status: int = 200
    repeat_expected_status_range: list[int] = field(default_factory=lambda: [400, 409])

    def to_dict(self) -> dict[str, Any]:
        """Convert business invariant rule to dictionary representation."""
        return {
            "rule_id": self.rule_id,
            "module": self.module,
            "description": self.description,
            "endpoint": self.endpoint,
            "rule_type": self.rule_type,
            "payload": dict(self.payload),
            "first_expected_status": self.first_expected_status,
            "repeat_expected_status_range": list(self.repeat_expected_status_range),
        }


@dataclass(frozen=True)
class ProjectProfile:
    """High-level domain project profile for context-aware AI test generation."""

    __test__ = False

    project_name: str
    domain: str
    roles: list[RoleRule] = field(default_factory=list)
    state_transitions: list[StateTransitionRule] = field(default_factory=list)
    business_invariants: list[BusinessInvariantRule] = field(default_factory=list)
    sensitive_fields_denylist: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert project profile to dictionary representation."""
        return {
            "project_name": self.project_name,
            "domain": self.domain,
            "roles": [r.to_dict() for r in self.roles],
            "state_transitions": [s.to_dict() for s in self.state_transitions],
            "business_invariants": [b.to_dict() for b in self.business_invariants],
            "sensitive_fields_denylist": list(self.sensitive_fields_denylist),
            "notes": list(self.notes),
        }


def parse_project_profile(data: Any) -> ProjectProfile:
    """Parse and validate raw dictionary into a structured ProjectProfile.

    Args:
        data: Parsed YAML dictionary mapping.

    Returns:
        Validated ProjectProfile model instance.

    Raises:
        DefinitionError: If structure or required fields are invalid.
    """
    if not isinstance(data, dict):
        raise DefinitionError("Project profile root must be a YAML dictionary mapping.")

    project_name = data.get("project_name")
    if not isinstance(project_name, str) or not project_name.strip():
        raise DefinitionError("Project profile requires a non-empty 'project_name'.")

    domain = data.get("domain", "default")
    if not isinstance(domain, str) or not domain.strip():
        domain = "default"

    # 1. Parse Roles
    raw_roles = data.get("roles", [])
    if not isinstance(raw_roles, list):
        raise DefinitionError("Project profile 'roles' must be a list.")
    roles: list[RoleRule] = []
    for idx, r in enumerate(raw_roles):
        if not isinstance(r, dict):
            raise DefinitionError(f"Role rule at index {idx} must be a dictionary.")
        role_name = r.get("role_name")
        if not isinstance(role_name, str) or not role_name.strip():
            raise DefinitionError(f"Role rule at index {idx} requires 'role_name'.")
        token_var = r.get("token_variable", f"{{{role_name}_token}}")
        allowed = r.get("allowed_endpoints", [])
        if not isinstance(allowed, list):
            allowed = []
        forbidden = r.get("forbidden_endpoints", [])
        if not isinstance(forbidden, list):
            forbidden = []
        idor = r.get("idor_check")
        if idor is not None and not isinstance(idor, dict):
            raise DefinitionError(
                f"Role '{role_name}' idor_check must be a dictionary."
            )

        roles.append(
            RoleRule(
                role_name=role_name.strip(),
                token_variable=str(token_var).strip(),
                allowed_endpoints=[str(x).strip() for x in allowed if str(x).strip()],
                forbidden_endpoints=[
                    str(x).strip() for x in forbidden if str(x).strip()
                ],
                idor_check=idor,
            )
        )

    # 2. Parse State Transitions
    raw_transitions = data.get("state_transitions", [])
    if not isinstance(raw_transitions, list):
        raise DefinitionError("Project profile 'state_transitions' must be a list.")
    state_transitions: list[StateTransitionRule] = []
    for idx, s in enumerate(raw_transitions):
        if not isinstance(s, dict):
            raise DefinitionError(
                f"State transition rule at index {idx} must be a dictionary."
            )
        entity = s.get("entity", "Entity")
        desc = s.get("description", f"Invalid state transition on {entity}")
        endpoint = s.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise DefinitionError(
                f"State transition at index {idx} requires non-empty 'endpoint'."
            )
        invalid_id = s.get("invalid_state_id", 0)
        invalid_name = s.get("invalid_state_name", "INVALID")
        status_range = s.get("expected_status_range", [400, 409])
        if not isinstance(status_range, list) or not status_range:
            status_range = [400, 409]
        body = s.get("body")
        if body is not None and not isinstance(body, dict):
            body = None

        state_transitions.append(
            StateTransitionRule(
                entity=str(entity).strip(),
                description=str(desc).strip(),
                endpoint=str(endpoint).strip(),
                invalid_state_id=invalid_id,
                invalid_state_name=str(invalid_name).strip(),
                expected_status_range=[int(code) for code in status_range],
                body=body,
            )
        )

    # 3. Parse Business Invariants
    raw_invariants = data.get("business_invariants", [])
    if not isinstance(raw_invariants, list):
        raise DefinitionError("Project profile 'business_invariants' must be a list.")
    business_invariants: list[BusinessInvariantRule] = []
    for idx, b in enumerate(raw_invariants):
        if not isinstance(b, dict):
            raise DefinitionError(
                f"Business invariant rule at index {idx} must be a dictionary."
            )
        rule_id = b.get("rule_id", f"rule_{idx + 1}")
        module = b.get("module", domain)
        desc = b.get("description", f"Business invariant rule {rule_id}")
        endpoint = b.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise DefinitionError(
                f"Business invariant at index {idx} requires non-empty 'endpoint'."
            )
        rule_type = b.get("rule_type", "single_use")
        payload = b.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        first_status = int(b.get("first_expected_status", 200))
        repeat_range = b.get("repeat_expected_status_range", [400, 409])
        if not isinstance(repeat_range, list) or not repeat_range:
            repeat_range = [400, 409]

        business_invariants.append(
            BusinessInvariantRule(
                rule_id=str(rule_id).strip(),
                module=str(module).strip(),
                description=str(desc).strip(),
                endpoint=str(endpoint).strip(),
                rule_type=str(rule_type).strip(),
                payload=payload,
                first_expected_status=first_status,
                repeat_expected_status_range=[int(code) for code in repeat_range],
            )
        )

    # 4. Parse sensitive fields denylist & notes
    raw_denylist = data.get("sensitive_fields_denylist", [])
    if not isinstance(raw_denylist, list):
        raw_denylist = []
    denylist = [str(x).strip() for x in raw_denylist if str(x).strip()]

    raw_notes = data.get("notes", [])
    if not isinstance(raw_notes, list):
        raw_notes = []
    notes = [str(x).strip() for x in raw_notes if str(x).strip()]

    return ProjectProfile(
        project_name=project_name.strip(),
        domain=domain.strip(),
        roles=roles,
        state_transitions=state_transitions,
        business_invariants=business_invariants,
        sensitive_fields_denylist=denylist,
        notes=notes,
    )


def load_project_profile(
    file_path: str | Path, base_dir: Path | None = None
) -> ProjectProfile:
    """Load and parse a ProjectProfile from a YAML file on disk.

    Enforces directory traversal safety (NFR-04) and file size ceiling (NFR-02).

    Args:
        file_path: Relative or absolute path to the project profile YAML file.
        base_dir: Optional base directory to confine file resolution.

    Returns:
        Structured, validated ProjectProfile instance.

    Raises:
        PathTraversalError: If the path attempts directory traversal outside base_dir.
        DefinitionError: If the file does not exist, exceeds limit, or is invalid YAML.
    """
    ref_str = str(file_path).strip()
    normalized = ref_str.replace("\\", "/")

    if (
        "../" in normalized
        or normalized.startswith("../")
        or normalized == ".."
        or "/../" in normalized
        or normalized.endswith("/..")
    ):
        raise PathTraversalError(
            f"Forbidden directory traversal sequence detected in '{ref_str}'."
        )

    p = Path(ref_str)
    if ".." in p.parts:
        raise PathTraversalError(
            f"Forbidden directory traversal sequence detected in '{ref_str}'."
        )

    eff_base = (
        base_dir
        if base_dir is not None
        else (p.parent if p.is_absolute() else Path.cwd())
    )
    safe_path = validate_safe_path(base_dir=eff_base, ref_path=file_path)

    if not safe_path.is_file():
        raise DefinitionError(f"Project profile file not found: '{file_path}'")

    file_size = safe_path.stat().st_size
    if file_size > MAX_PROFILE_FILE_BYTES:
        raise DefinitionError(
            f"Project profile file '{file_path}' size ({file_size} bytes) "
            f"exceeds limit of {MAX_PROFILE_FILE_BYTES} bytes."
        )

    try:
        content = safe_path.read_text(encoding="utf-8-sig")
        data = yaml.safe_load(content)
    except Exception as err:
        raise DefinitionError(
            f"Failed to read/parse project profile YAML '{file_path}': {err}"
        ) from err

    return parse_project_profile(data)
