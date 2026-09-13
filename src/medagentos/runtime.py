"""Runtime assembly.

One place that wires ports to adapters and loads plugins, so that the CLI, the
API and the tests all boot the same system rather than three similar ones.

This module is the composition root. It is the only place allowed to know both
which adapters exist and which plugins are installed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .core.artifacts import ArtifactManager
from .core.capabilities import CapabilityRegistry
from .core.trace import TraceEngine
from .core.workflow import WorkflowEngine, WorkflowRegistry
from .infra.memory import InMemoryUnitOfWork
from .plugin.discovery import PluginLoader


@dataclass
class Runtime:
    """An assembled MedAgentOS runtime."""

    uow: object
    capabilities: CapabilityRegistry
    workflows: WorkflowRegistry
    engine: WorkflowEngine
    plugins: PluginLoader

    def artifact_manager(self, trace: TraceEngine) -> ArtifactManager:
        return ArtifactManager(self.uow.artifacts, self.uow.objects, trace=trace)


def build_runtime(
    *,
    uow: object | None = None,
    plugin_paths: Sequence[Path | str] | None = None,
) -> Runtime:
    """Assemble a runtime.

    Defaults to fully in-memory infrastructure and the repository's own
    ``plugins/`` directory, which is what makes ``medagentos run`` work on a
    fresh clone with nothing installed and nothing running.
    """
    uow = uow if uow is not None else InMemoryUnitOfWork()
    capabilities = CapabilityRegistry()
    workflows = WorkflowRegistry()
    loader = PluginLoader(capabilities, workflows)

    if plugin_paths is None:
        plugin_paths = [Path(__file__).resolve().parents[2] / "plugins"]
    loader.load_from_paths(plugin_paths)

    engine = WorkflowEngine(
        workflows=workflows,
        capabilities=capabilities,
        artifact_manager_factory=lambda trace: ArtifactManager(
            uow.artifacts, uow.objects, trace=trace
        ),
        trace_engine_factory=lambda run_id: TraceEngine(run_id, uow.traces),
        run_repository=uow.runs,
    )
    return Runtime(
        uow=uow, capabilities=capabilities, workflows=workflows, engine=engine, plugins=loader
    )
