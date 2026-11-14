"""Stateful workflow planner and lifecycle coordinator (Task 20).

Constructs multi-step workflows with planned setup actions, variable
extraction, deterministic dependency ordering, and reverse-order cleanup.
"""

from __future__ import annotations

import re
from typing import Any

from apiscope.contract.models import CanonicalContract, CanonicalOperation
from apiscope.fixtures.dependency_graph import DependencyGraph
from apiscope.fixtures.models import StatefulWorkflow, WorkflowAction


class StatefulWorkflowPlanner:
    """Plans stateful API workflows with setup, test execution, and cleanup actions."""

    __test__ = False

    def __init__(self, contract: CanonicalContract) -> None:
        """Initialize workflow planner with canonical contract context."""
        self.contract = contract
        self.op_map = {
            f"{op.method.upper()} {op.path}": op for op in contract.operations
        }

        # Build mapping of Entity -> Producer Operation (POST) and Cleanup (DELETE)
        self.producers_by_entity: dict[str, CanonicalOperation] = {}
        self.cleanups_by_entity: dict[str, CanonicalOperation] = {}
        self._index_operations()

    @staticmethod
    def _norm_entity(name: str) -> str:
        """Normalize entity name for robust key matching."""
        return name.lower().replace("-", "_").rstrip("s")

    def plan_workflow_for_operation(
        self,
        target_method: str,
        target_path: str,
        title: str = "",
        description: str = "",
        base_url_var: str = "{{base_url}}",
        mutation_authorized: bool = False,
    ) -> StatefulWorkflow:
        """Plan a complete stateful workflow to test a target operation.

        Args:
            target_method: HTTP method of the target operation to test.
            target_path: Path of the target operation.
            title: Title for the workflow.
            description: Description of the workflow.
            base_url_var: Base URL variable placeholder (default '{{base_url}}').
            mutation_authorized: Whether mutating setup actions are authorized.

        Returns:
            StatefulWorkflow with planned setup, test, and cleanup sequences.
        """
        clean_method = target_method.upper()
        target_sig = f"{clean_method} {target_path}"
        op = self._find_operation(clean_method, target_path)

        wf_title = title or f"Stateful Workflow: {target_sig}"
        wf_desc = (
            description or f"Prepared stateful lifecycle testing for {target_sig}."
        )

        setup_steps: list[WorkflowAction] = []
        cleanup_steps: list[WorkflowAction] = []
        required_fixtures: list[str] = []
        provided_fixtures: list[str] = []
        blockers: list[str] = []

        if op is None:
            return StatefulWorkflow(
                workflow_id=f"WF-{clean_method}-{self._slugify(target_path)}",
                title=wf_title,
                description=wf_desc,
                status="BLOCKED",
                blocker_reasons=[
                    f"Target operation '{target_sig}' is not declared in contract."
                ],
            )

        # 1. Determine entity requirements
        consumed_entities = self._detect_required_entities(
            clean_method, target_path, op
        )
        required_fixtures.extend(consumed_entities)

        # 2. Plan setup steps using DependencyGraph
        graph = DependencyGraph()
        for entity in consumed_entities:
            ent_key = self._norm_entity(entity)
            producer_op = self.producers_by_entity.get(ent_key)
            if producer_op is None:
                blockers.append(
                    f"Required entity '{entity}' has no registered POST "
                    f"producer operation."
                )
                continue

            # Check if producer itself has dependencies (e.g. Product requires Supplier)
            sub_entities = self._detect_required_entities(
                producer_op.method, producer_op.path, producer_op
            )
            # Remove self-recursion
            sub_entities = [e for e in sub_entities if self._norm_entity(e) != ent_key]

            cleanup_op = self.cleanups_by_entity.get(ent_key)
            cleanup_sig = (
                f"{cleanup_op.method} {cleanup_op.path}" if cleanup_op else None
            )

            graph.add_node(
                node_id=f"create_{ent_key}",
                entity=entity,
                method=producer_op.method,
                path=producer_op.path,
                produces=[entity],
                consumes=sub_entities,
                cleanup_op=cleanup_sig,
            )

            for sub in sub_entities:
                sub_key = self._norm_entity(sub)
                sub_prod = self.producers_by_entity.get(sub_key)
                if sub_prod is not None:
                    sub_clean = self.cleanups_by_entity.get(sub_key)
                    sub_clean_sig = (
                        f"{sub_clean.method} {sub_clean.path}" if sub_clean else None
                    )
                    graph.add_node(
                        node_id=f"create_{sub_key}",
                        entity=sub,
                        method=sub_prod.method,
                        path=sub_prod.path,
                        produces=[sub],
                        consumes=[],
                        cleanup_op=sub_clean_sig,
                    )
                    graph.add_dependency(f"create_{ent_key}", f"create_{sub_key}")

        if blockers:
            return StatefulWorkflow(
                workflow_id=f"WF-{clean_method}-{self._slugify(target_path)}",
                title=wf_title,
                description=wf_desc,
                required_fixtures=required_fixtures,
                status="BLOCKED",
                blocker_reasons=blockers,
            )

        # 3. Topological sort of setup nodes
        try:
            sorted_nodes = graph.topological_sort()
        except ValueError as err:
            return StatefulWorkflow(
                workflow_id=f"WF-{clean_method}-{self._slugify(target_path)}",
                title=wf_title,
                description=wf_desc,
                required_fixtures=required_fixtures,
                status="BLOCKED",
                blocker_reasons=[str(err)],
            )

        # 4. Generate setup steps and extract variables
        step_counter = 1
        for node in sorted_nodes:
            node_key = self._norm_entity(node.entity)
            var_name = f"chain_{node_key}_id"
            provided_fixtures.append(var_name)

            # Substitute parent dependencies in body if needed
            body_template: dict[str, Any] = {}
            prod_op = self._find_operation(node.method, node.path)
            if prod_op and prod_op.request_body and prod_op.request_body.schema:
                props = prod_op.request_body.schema.get("properties", {})
                for p_name, p_schema in props.items():
                    if p_name.lower().endswith("id") and p_name != "id":
                        ref_key = self._norm_entity(
                            p_name.replace("Id", "").replace("_id", "")
                        )
                        body_template[p_name] = f"{{{{chain_{ref_key}_id}}}}"
                    elif p_schema.get("type") == "string":
                        body_template[p_name] = f"test_{p_name}"
                    elif p_schema.get("type") == "integer":
                        body_template[p_name] = 1

            setup_action = WorkflowAction(
                step_number=step_counter,
                name=f"[SETUP:{step_counter}] Create {node.entity}",
                action_type="SETUP",
                method=node.method,
                url=f"{base_url_var}{node.path}",
                body=body_template if body_template else None,
                extract={var_name: "$.id"},
                assertions=[{"type": "status", "equals": 201}],
                risk="risk:mutating",
                can_rollback=node.cleanup_op is not None,
                rollback_endpoint=node.cleanup_op,
            )
            setup_steps.append(setup_action)
            step_counter += 1

        # 5. Build target test step
        test_url_path = target_path
        for entity in consumed_entities:
            ent_key = self._norm_entity(entity)
            var_name = f"{{{{chain_{ent_key}_id}}}}"
            test_url_path = re.sub(r"\{id\}", var_name, test_url_path)
            test_url_path = re.sub(
                rf"\{{{ent_key}Id\}}", var_name, test_url_path, flags=re.IGNORECASE
            )

        test_body: dict[str, Any] | None = None
        if op.request_body and op.request_body.schema:
            test_body = {}
            for p_name, p_schema in op.request_body.schema.get(
                "properties", {}
            ).items():
                if p_name.lower().endswith("id") and p_name != "id":
                    ref_key = self._norm_entity(
                        p_name.replace("Id", "").replace("_id", "")
                    )
                    test_body[p_name] = f"{{{{chain_{ref_key}_id}}}}"
                elif p_schema.get("type") == "string":
                    test_body[p_name] = f"test_{p_name}"
                elif p_schema.get("type") == "integer":
                    test_body[p_name] = 1

        test_risk = "risk:read_only"
        if clean_method in ("POST", "PUT", "PATCH"):
            test_risk = "risk:mutating"
        elif clean_method == "DELETE":
            test_risk = "risk:destructive"

        test_action = WorkflowAction(
            step_number=step_counter,
            name=f"[TEST] {clean_method} {target_path}",
            action_type="TEST",
            method=clean_method,
            url=f"{base_url_var}{test_url_path}",
            body=test_body,
            assertions=[
                {"type": "status", "equals": 200 if clean_method == "GET" else 201}
            ],
            risk=test_risk,
        )
        test_steps = [test_action]
        step_counter += 1

        # 6. Generate cleanup steps in reverse order (LIFO)
        for node in reversed(sorted_nodes):
            if node.cleanup_op:
                clean_method_part, clean_path_part = node.cleanup_op.split(" ", 1)
                node_key = self._norm_entity(node.entity)
                var_name = f"{{{{chain_{node_key}_id}}}}"
                resolved_clean_path = re.sub(r"\{[^}]+\}", var_name, clean_path_part)

                cleanup_action = WorkflowAction(
                    step_number=step_counter,
                    name=f"[CLEANUP:{step_counter}] Delete {node.entity}",
                    action_type="CLEANUP",
                    method=clean_method_part,
                    url=f"{base_url_var}{resolved_clean_path}",
                    assertions=[{"type": "status", "equals": 204}],
                    risk="risk:destructive",
                    can_rollback=False,
                )
                cleanup_steps.append(cleanup_action)
                step_counter += 1

        # Overall workflow readiness status
        status = "READY" if mutation_authorized else "PLANNED"

        return StatefulWorkflow(
            workflow_id=f"WF-{clean_method}-{self._slugify(target_path)}",
            title=wf_title,
            description=wf_desc,
            setup_steps=setup_steps,
            test_steps=test_steps,
            cleanup_steps=cleanup_steps,
            required_fixtures=required_fixtures,
            provided_fixtures=provided_fixtures,
            status=status,
            is_mutation_authorized=mutation_authorized,
        )

    def _index_operations(self) -> None:
        """Index operations by entity name for setup and cleanup discovery."""
        for op in self.contract.operations:
            clean_m = op.method.upper()

            # Check if this is a producer operation (POST on collection)
            if clean_m == "POST" and "{" not in op.path:
                if op.module:
                    self.producers_by_entity[self._norm_entity(op.module)] = op
                path_root = self._extract_path_root(op.path)
                if path_root:
                    self.producers_by_entity[self._norm_entity(path_root)] = op

            # Check if this is a cleanup operation (DELETE /api/{entity}/{id})
            if clean_m == "DELETE" and "{" in op.path:
                if op.module:
                    self.cleanups_by_entity[self._norm_entity(op.module)] = op
                path_root = self._extract_path_root(op.path)
                if path_root:
                    self.cleanups_by_entity[self._norm_entity(path_root)] = op

    def _extract_path_root(self, path: str) -> str:
        """Extract primary entity root from path (e.g. /api/products -> products)."""
        parts = [p for p in path.strip("/").split("/") if not p.startswith("{")]
        return parts[1] if len(parts) > 1 else (parts[0] if parts else "")

    def _detect_required_entities(
        self,
        _method: str,
        path: str,
        op: CanonicalOperation,
    ) -> list[str]:
        """Detect entity types required as inputs to an operation."""
        entities: list[str] = []

        # 1. Path parameters (e.g. /api/products/{id} -> Product)
        if "{" in path:
            path_root = self._extract_path_root(path)
            ent_key = (
                self._norm_entity(path_root)
                if path_root
                else (self._norm_entity(op.module) if op.module else "entity")
            )
            prod = self.producers_by_entity.get(ent_key)
            ent_name = (
                prod.module.capitalize()
                if (prod and prod.module)
                else ent_key.capitalize()
            )
            if ent_name not in entities:
                entities.append(ent_name)

        # 2. Body foreign keys (e.g. productId -> Product, supplierId -> Supplier)
        if op.request_body and op.request_body.schema:
            props = op.request_body.schema.get("properties", {})
            for p_name in props:
                if p_name.lower().endswith("id") and p_name != "id":
                    root = (
                        p_name.replace("Id", "").replace("_id", "").replace("Ids", "")
                    )
                    ent_key = self._norm_entity(root)
                    prod = self.producers_by_entity.get(ent_key)
                    ent_name = (
                        prod.module.capitalize()
                        if (prod and prod.module)
                        else ent_key.capitalize()
                    )
                    if ent_name not in entities:
                        entities.append(ent_name)

        return entities

    def _find_operation(
        self,
        method: str,
        path: str,
    ) -> CanonicalOperation | None:
        """Match an HTTP method and path to a CanonicalOperation."""
        direct_sig = f"{method.upper()} {path}"
        if direct_sig in self.op_map:
            return self.op_map[direct_sig]

        for sig, op in self.op_map.items():
            if not sig.startswith(method.upper() + " "):
                continue
            escaped = re.escape(op.path)
            pattern = "^" + re.sub(r"\\\{[^}]+\\\}", r"[^/]+", escaped) + "$"
            if re.fullmatch(pattern, path):
                return op

        return None

    def _slugify(self, text: str) -> str:
        """Create a URL-safe slug from text."""
        clean = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-")
        return clean.upper()
