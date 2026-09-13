"""Shared test helpers.

Kept dependency-free so the suite runs under ``python -m unittest`` on a bare
interpreter as well as under pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "plugins"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from medagentos.core.artifacts import ArtifactManager  # noqa: E402
from medagentos.core.capabilities import CapabilityRegistry  # noqa: E402
from medagentos.core.trace import TraceEngine  # noqa: E402
from medagentos.core.workflow import WorkflowEngine, WorkflowRegistry  # noqa: E402
from medagentos.infra.memory import InMemoryUnitOfWork  # noqa: E402


def build_engine(
    workflows: WorkflowRegistry | None = None,
    capabilities: CapabilityRegistry | None = None,
    uow: InMemoryUnitOfWork | None = None,
) -> tuple[WorkflowEngine, InMemoryUnitOfWork, WorkflowRegistry, CapabilityRegistry]:
    """Assemble a fully in-memory engine, wired exactly as production wires it."""
    uow = uow or InMemoryUnitOfWork()
    workflows = workflows if workflows is not None else WorkflowRegistry()
    capabilities = capabilities if capabilities is not None else CapabilityRegistry()

    engine = WorkflowEngine(
        workflows=workflows,
        capabilities=capabilities,
        artifact_manager_factory=lambda trace: ArtifactManager(
            uow.artifacts, uow.objects, trace=trace
        ),
        trace_engine_factory=lambda run_id: TraceEngine(run_id, uow.traces),
        run_repository=uow.runs,
    )
    return engine, uow, workflows, capabilities
