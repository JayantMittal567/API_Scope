"""Dependency graph and stateful workflow ordering engine (Task 20).

Models producer-consumer relationships between operations, constructs
deterministic topological execution order, detects missing producers,
identifies dependency cycles, and scopes extracted variable names safely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DependencyNode:
    """A node in the dependency graph representing an operation or entity."""

    __test__ = False

    node_id: str
    entity: str
    method: str
    path: str
    produces: list[str] = field(default_factory=list)  # Entity IDs produced
    consumes: list[str] = field(default_factory=list)  # Entity IDs consumed
    cleanup_op: str | None = None  # e.g. "DELETE /api/products/{id}"
    metadata: dict[str, Any] = field(default_factory=dict)


class DependencyGraph:
    """Constructs and analyzes stateful API dependency workflows."""

    __test__ = False

    def __init__(self) -> None:
        """Initialize an empty dependency graph."""
        self.nodes: dict[str, DependencyNode] = {}
        self.edges: dict[str, set[str]] = {}  # node_id -> set of dependencies

    def add_node(
        self,
        node_id: str,
        entity: str,
        method: str,
        path: str,
        produces: list[str] | None = None,
        consumes: list[str] | None = None,
        cleanup_op: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DependencyNode:
        """Add a node to the graph."""
        node = DependencyNode(
            node_id=node_id,
            entity=entity,
            method=method.upper(),
            path=path,
            produces=list(produces or []),
            consumes=list(consumes or []),
            cleanup_op=cleanup_op,
            metadata=dict(metadata or {}),
        )
        self.nodes[node_id] = node
        if node_id not in self.edges:
            self.edges[node_id] = set()
        return node

    def add_dependency(self, consumer_id: str, producer_id: str) -> None:
        """Declare that consumer_id depends on producer_id."""
        if consumer_id not in self.edges:
            self.edges[consumer_id] = set()
        self.edges[consumer_id].add(producer_id)

    def auto_link_producers_and_consumers(self) -> None:
        """Link consumers to producer nodes supplying required entities."""
        # Map each produced entity to its producer node_ids
        producer_map: dict[str, list[str]] = {}
        for nid, node in self.nodes.items():
            for prod in node.produces:
                producer_map.setdefault(prod.lower(), []).append(nid)

        for cid, consumer in self.nodes.items():
            for req in consumer.consumes:
                prods = producer_map.get(req.lower(), [])
                for pid in prods:
                    if pid != cid:
                        self.add_dependency(cid, pid)

    def detect_cycles(self) -> list[list[str]]:
        """Detect any circular dependencies using depth-first search.

        Returns:
            List of cycle paths (e.g. [['A', 'B', 'A']]).
        """
        visited: set[str] = set()
        rec_stack: list[str] = []
        cycles: list[list[str]] = []

        def _dfs(node: str) -> None:
            visited.add(node)
            rec_stack.append(node)

            for neighbor in self.edges.get(node, set()):
                if neighbor not in self.nodes:
                    continue
                if neighbor not in visited:
                    _dfs(neighbor)
                elif neighbor in rec_stack:
                    # Found cycle: extract cycle path
                    idx = rec_stack.index(neighbor)
                    cycle_path = rec_stack[idx:] + [neighbor]
                    cycles.append(cycle_path)

            rec_stack.pop()

        for n in self.nodes:
            if n not in visited:
                _dfs(n)

        return cycles

    def find_missing_producers(self) -> list[str]:
        """Identify consumed entity requirements that lack a registered producer."""
        all_produced: set[str] = set()
        for node in self.nodes.values():
            for prod in node.produces:
                all_produced.add(prod.lower())

        missing: list[str] = []
        for node in self.nodes.values():
            for req in node.consumes:
                if req.lower() not in all_produced:
                    msg = (
                        f"Node '{node.node_id}' ({node.method} {node.path}) consumes "
                        f"entity '{req}' but no registered producer exists."
                    )
                    if msg not in missing:
                        missing.append(msg)

        return missing

    def topological_sort(self) -> list[DependencyNode]:
        """Compute deterministic topological execution order.

        Returns:
            List of DependencyNode instances ordered so producers precede consumers.

        Raises:
            ValueError: If the dependency graph contains circular references.
        """
        cycles = self.detect_cycles()
        if cycles:
            cycle_desc = " -> ".join(cycles[0])
            raise ValueError(f"Circular dependency detected in workflow: {cycle_desc}")

        # Compute in-degrees (number of dependencies for each node)
        in_degree: dict[str, int] = dict.fromkeys(self.nodes, 0)
        # Edge: consumer depends on producer => producer must execute before consumer
        # Directed graph for execution: producer -> consumer
        graph: dict[str, set[str]] = {nid: set() for nid in self.nodes}

        for consumer_id, producers in self.edges.items():
            for producer_id in producers:
                if producer_id in self.nodes and consumer_id in self.nodes:
                    graph[producer_id].add(consumer_id)
                    in_degree[consumer_id] += 1

        # Kahn's algorithm: queue nodes with in-degree 0 (producers first)
        queue = sorted([nid for nid, deg in in_degree.items() if deg == 0])
        ordered: list[DependencyNode] = []

        while queue:
            curr_id = queue.pop(0)
            ordered.append(self.nodes[curr_id])

            for dependent in sorted(graph.get(curr_id, set())):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
            queue.sort()

        if len(ordered) != len(self.nodes):
            remaining = set(self.nodes) - {n.node_id for n in ordered}
            raise ValueError(f"Dependency resolution stalled for nodes: {remaining}")

        return ordered
