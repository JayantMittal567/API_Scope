"""Safe, read-only local source repository inspection engine.

Extracts route mappings, entity models, role-based authorization annotations,
and business rules from Python (FastAPI/Flask) and Java (Spring Boot) projects
without executing code, modifying files, or exposing sensitive credentials.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from apiscope.core.exceptions import DefinitionError, PathTraversalError
from apiscope.core.logging_config import redact_sensitive_text
from apiscope.intelligence.models import (
    DiscoveredBusinessRule,
    EvidenceReference,
    EvidenceSourceType,
)

MAX_FILE_BYTES: int = 512 * 1024  # 500 KB per file
MAX_TOTAL_BYTES: int = 10 * 1024 * 1024  # 10 MB total
MAX_SCANNED_FILES: int = 1000

IGNORED_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "target",
        "build",
        "dist",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".idea",
        ".vscode",
        ".apiscope",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "bin",
        "obj",
        ".gradle",
    }
)

SECRET_FILE_PATTERNS: tuple[str, ...] = (
    ".env",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
    "credentials",
    "secret",
)

BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".class",
        ".jar",
        ".war",
        ".ear",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".pdf",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp3",
        ".mp4",
        ".pyc",
        ".pyo",
        ".pyd",
    }
)


@dataclass(frozen=True)
class DiscoveredSourceEndpoint:
    """Endpoint operation discovered directly from source controller mapping."""

    path: str
    method: str
    controller_name: str
    method_name: str
    file_path: str
    line_number: int
    required_roles: list[str] = field(default_factory=list)
    docstring: str = ""
    evidence: EvidenceReference | None = None


@dataclass(frozen=True)
class DiscoveredSourceModel:
    """Domain or request model extracted from source code."""

    name: str
    file_path: str
    line_number: int
    fields: list[str] = field(default_factory=list)
    is_entity: bool = False
    evidence: EvidenceReference | None = None


@dataclass
class DiscoveredSourceFindings:
    """Aggregated findings discovered from local repository inspection."""

    source_language: str = "unknown"
    endpoints: list[DiscoveredSourceEndpoint] = field(default_factory=list)
    models: list[DiscoveredSourceModel] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    business_rules: list[DiscoveredBusinessRule] = field(default_factory=list)
    doc_notes: list[str] = field(default_factory=list)
    scanned_files_count: int = 0
    total_scanned_bytes: int = 0


class SourceCodeScanner:
    """Confined, read-only static analysis scanner for project source trees."""

    def __init__(self, repo_path: str | Path) -> None:
        """Initialize SourceCodeScanner with canonical root confinement.

        Args:
            repo_path: Directory path to local target repository.

        Raises:
            DefinitionError: If repository does not exist or is not a directory.
            PathTraversalError: If traversal sequence or symlink escape detected.
        """
        raw_p = Path(repo_path)
        if ".." in raw_p.parts:
            raise PathTraversalError(
                f"Directory traversal forbidden in repository path: '{repo_path}'"
            )

        if not raw_p.exists():
            raise DefinitionError(
                f"Target repository directory not found: '{repo_path}'"
            )
        if not raw_p.is_dir():
            raise DefinitionError(
                f"Target repository path is not a directory: '{repo_path}'"
            )

        self.repo_root = raw_p.resolve()

    def _is_safe_path(self, path: Path) -> bool:
        """Validate path resides safely inside the canonical repo root."""
        try:
            resolved = path.resolve()
            return resolved == self.repo_root or self.repo_root in resolved.parents
        except Exception:
            return False

    def _is_secret_or_ignored_file(self, path: Path) -> bool:
        """Check if file matches ignored patterns, binary types, or secret filenames."""
        name_lower = path.name.lower()

        # Check ignored directories in ancestry
        for part in path.parts:
            if part.lower() in IGNORED_DIRECTORIES:
                return True

        # Check binary extensions
        if path.suffix.lower() in BINARY_EXTENSIONS:
            return True

        # Check secret file patterns
        return any(sec in name_lower for sec in SECRET_FILE_PATTERNS)

    def scan(self) -> DiscoveredSourceFindings:
        """Safely traverse and parse repository files, returning discovered findings."""
        findings = DiscoveredSourceFindings()
        java_files: list[Path] = []
        py_files: list[Path] = []
        doc_files: list[Path] = []

        total_bytes = 0
        file_count = 0

        for root, dirs, files in os.walk(self.repo_root):
            # Prune ignored directories in-place for performance
            dirs[:] = [
                d
                for d in dirs
                if d.lower() not in IGNORED_DIRECTORIES and not d.startswith(".")
            ]

            for file_name in sorted(files):
                if file_count >= MAX_SCANNED_FILES:
                    break

                file_path = Path(root) / file_name
                if not self._is_safe_path(file_path):
                    continue

                if self._is_secret_or_ignored_file(file_path):
                    continue

                try:
                    stat = file_path.stat()
                    if stat.st_size > MAX_FILE_BYTES:
                        continue
                    if total_bytes + stat.st_size > MAX_TOTAL_BYTES:
                        break
                    total_bytes += stat.st_size
                    file_count += 1
                except Exception:
                    continue

                suffix = file_path.suffix.lower()
                if suffix == ".java":
                    java_files.append(file_path)
                elif suffix == ".py":
                    py_files.append(file_path)
                elif suffix in (".md", ".txt") and (
                    "readme" in file_name.lower() or "doc" in str(file_path).lower()
                ):
                    doc_files.append(file_path)

        findings.scanned_files_count = file_count
        findings.total_scanned_bytes = total_bytes

        # Determine primary language
        if java_files and len(java_files) >= len(py_files):
            findings.source_language = "java/spring-boot"
        elif py_files:
            findings.source_language = "python"
        elif java_files:
            findings.source_language = "java"

        # Scan Java / Spring Boot files
        for j_file in java_files:
            self._scan_java_file(j_file, findings)

        # Scan Python files
        for p_file in py_files:
            self._scan_python_file(p_file, findings)

        # Scan Documentation files
        for d_file in doc_files:
            self._scan_doc_file(d_file, findings)

        # Deduplicate discovered roles
        all_roles = set(findings.roles)
        for ep in findings.endpoints:
            all_roles.update(ep.required_roles)
        findings.roles = sorted(all_roles)

        return findings

    def _scan_java_file(
        self, file_path: Path, findings: DiscoveredSourceFindings
    ) -> None:
        """Parse Spring Boot annotations and JPA entities from a Java file."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        rel_path = file_path.relative_to(self.repo_root).as_posix()
        lines = content.splitlines()

        # 1. Check if class is a Spring Controller
        is_controller = "@RestController" in content or "@Controller" in content
        is_entity = "@Entity" in content or "@Table" in content

        class_match = re.search(
            r"public\s+(?:class|interface|record)\s+([A-Za-z0-9_]+)", content
        )
        class_name = class_match.group(1) if class_match else file_path.stem

        # Extract base path from class-level @RequestMapping
        base_path = ""
        base_req_match = re.search(
            r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']', content
        )
        if base_req_match:
            base_path = base_req_match.group(1).rstrip("/")

        # Extract endpoints from controller methods
        if is_controller:
            class_line_idx = 0
            for idx, line in enumerate(lines, start=1):
                if re.search(
                    r"public\s+(?:class|interface|record)\s+([A-Za-z0-9_]+)", line
                ):
                    class_line_idx = idx
                    break

            mapping_regex = re.compile(
                r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\s*"
                r'(?:\(\s*(?:value\s*=\s*|path\s*=\s*)?["\']?([^"\')\s]*)["\']?)?',
                re.MULTILINE,
            )

            for line_idx, line in enumerate(lines, start=1):
                if line_idx <= class_line_idx:
                    continue
                m = mapping_regex.search(line)
                if m:
                    annot_type = m.group(1)
                    sub_path = m.group(2) or ""
                    if sub_path.startswith('"') or sub_path.startswith("'"):
                        sub_path = sub_path.strip("\"'")

                    method = "GET"
                    if annot_type == "PostMapping":
                        method = "POST"
                    elif annot_type == "PutMapping":
                        method = "PUT"
                    elif annot_type == "DeleteMapping":
                        method = "DELETE"
                    elif annot_type == "PatchMapping":
                        method = "PATCH"

                    full_path = f"{base_path}/{sub_path.lstrip('/')}".rstrip("/")
                    if not full_path:
                        full_path = "/"

                    # Check method name in subsequent line
                    method_name = "handler"
                    method_end_idx = line_idx
                    for offset, next_line in enumerate(
                        lines[line_idx : min(len(lines), line_idx + 6)], start=1
                    ):
                        fn_match = re.search(
                            r"public\s+[\w<>,\[\]\s]+\s+([a-zA-Z0-9_]+)\s*\(", next_line
                        )
                        if fn_match:
                            method_name = fn_match.group(1)
                            method_end_idx = line_idx + offset
                            break

                    # Scan annotations associated with this method
                    # (backward until boundary, and forward up to method declaration)
                    roles: list[str] = []
                    start_scan_idx = line_idx - 1
                    for b_offset in range(1, 6):
                        curr_0 = line_idx - 1 - b_offset
                        if curr_0 < 0:
                            break
                        prev_text = lines[curr_0]
                        if "}" in prev_text or ";" in prev_text:
                            break
                        start_scan_idx = curr_0

                    for cand_line in lines[start_scan_idx:method_end_idx]:
                        role_match = re.search(
                            r'@(?:PreAuthorize|Secured|RolesAllowed)\s*\([^)]*?(?:hasRole|ROLE_)?["\']([A-Za-z0-9_]+)["\']',
                            cand_line,
                        )
                        if role_match:
                            roles.append(
                                role_match.group(1).replace("ROLE_", "").title()
                            )

                    snippet = redact_sensitive_text(line.strip())
                    ev = EvidenceReference(
                        source_type=EvidenceSourceType.SOURCE_CODE,
                        source_ref=f"{rel_path}:{line_idx}",
                        snippet=snippet[:100],
                        confidence=1.0,
                        explanation=(
                            f"Spring Boot @{annot_type} on {class_name}.{method_name}"
                        ),
                    )

                    findings.endpoints.append(
                        DiscoveredSourceEndpoint(
                            path=full_path,
                            method=method,
                            controller_name=class_name,
                            method_name=method_name,
                            file_path=rel_path,
                            line_number=line_idx,
                            required_roles=roles,
                            evidence=ev,
                        )
                    )

        # 2. Check Entity Models
        if is_entity:
            fields: list[str] = []
            field_regex = re.compile(r"private\s+[\w<>,\[\]]+\s+([a-zA-Z0-9_]+)\s*;")
            for f_match in field_regex.finditer(content):
                fields.append(f_match.group(1))

            findings.models.append(
                DiscoveredSourceModel(
                    name=class_name,
                    file_path=rel_path,
                    line_number=1,
                    fields=fields,
                    is_entity=True,
                    evidence=EvidenceReference(
                        source_type=EvidenceSourceType.SOURCE_CODE,
                        source_ref=f"{rel_path}:1",
                        snippet=f"@Entity class {class_name} ({len(fields)} fields)",
                        confidence=1.0,
                        explanation="JPA @Entity declaration",
                    ),
                )
            )

    def _scan_python_file(
        self, file_path: Path, findings: DiscoveredSourceFindings
    ) -> None:
        """Parse FastAPI/Flask route decorators and Pydantic models using Python AST."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content, filename=file_path.name)
        except Exception:
            return

        rel_path = file_path.relative_to(self.repo_root).as_posix()

        if findings.source_language == "python":
            if "fastapi" in content.lower():
                findings.source_language = "python/fastapi"
            elif "flask" in content.lower():
                findings.source_language = "python/flask"

        for node in ast.walk(tree):
            # Check function definitions with route decorators
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call) and isinstance(
                        deco.func, ast.Attribute
                    ):
                        attr_name = deco.func.attr.lower()
                        if attr_name in (
                            "get",
                            "post",
                            "put",
                            "delete",
                            "patch",
                            "route",
                        ):
                            method = (
                                "GET" if attr_name == "route" else attr_name.upper()
                            )
                            route_path = "/"
                            if deco.args and isinstance(deco.args[0], ast.Constant):
                                route_path = str(deco.args[0].value)

                            doc = ast.get_docstring(node) or ""
                            ev = EvidenceReference(
                                source_type=EvidenceSourceType.SOURCE_CODE,
                                source_ref=f"{rel_path}:{node.lineno}",
                                snippet=f"@{deco.func.attr}('{route_path}')",
                                confidence=1.0,
                                explanation=f"Route decorator on {node.name}",
                            )

                            findings.endpoints.append(
                                DiscoveredSourceEndpoint(
                                    path=route_path,
                                    method=method,
                                    controller_name=file_path.stem,
                                    method_name=node.name,
                                    file_path=rel_path,
                                    line_number=node.lineno,
                                    docstring=doc,
                                    evidence=ev,
                                )
                            )

            # Check Pydantic BaseModel classes
            elif isinstance(node, ast.ClassDef):
                is_pydantic = any(
                    isinstance(base, ast.Name) and "BaseModel" in base.id
                    for base in node.bases
                )
                if is_pydantic:
                    fields = [
                        stmt.target.id
                        for stmt in node.body
                        if isinstance(stmt, ast.AnnAssign)
                        and isinstance(stmt.target, ast.Name)
                    ]
                    findings.models.append(
                        DiscoveredSourceModel(
                            name=node.name,
                            file_path=rel_path,
                            line_number=node.lineno,
                            fields=fields,
                            is_entity=False,
                            evidence=EvidenceReference(
                                source_type=EvidenceSourceType.SOURCE_CODE,
                                source_ref=f"{rel_path}:{node.lineno}",
                                snippet=f"class {node.name}(BaseModel)",
                                confidence=1.0,
                                explanation="Pydantic model declaration",
                            ),
                        )
                    )

    def _scan_doc_file(
        self, file_path: Path, findings: DiscoveredSourceFindings
    ) -> None:
        """Extract business context and documented roles from markdown files."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        rel_path = file_path.relative_to(self.repo_root).as_posix()
        clean_content = redact_sensitive_text(content)

        # Look for role definitions in documentation
        role_matches = re.findall(
            r"\b(Admin|Manager|Viewer|Editor|Customer|User|Auditor)\b",
            clean_content,
        )
        for r in role_matches:
            findings.roles.append(r.title())

        # Extract first paragraph or overview
        first_section = clean_content.strip().split("\n\n")[0]
        if len(first_section) > 10:
            findings.doc_notes.append(
                f"[{rel_path}] {first_section[:200].replace(chr(10), ' ')}"
            )
