"""The plugin contract.

A plugin is a class exposing metadata, capabilities and workflows. The runtime
asks for those three things and nothing else, so a plugin can be developed,
tested and versioned entirely on its own.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..core.capabilities import Capability, CapabilityRegistry
from ..core.workflow import WorkflowDefinition, WorkflowRegistry


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    """Identity of a plugin, as required by PLUGIN_ECOSYSTEM.md."""

    name: str
    version: str
    description: str
    specialty: str = ""
    """The medical domain, e.g. ``neuroimaging``. Core never reads this; it is
    for humans and for the capability catalogue."""
    authors: tuple[str, ...] = ()
    modalities: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    """Papers, model cards and datasets this plugin builds on."""
    limitations: tuple[str, ...] = ()
    """What this plugin cannot do. Surfaced in every report it produces, because
    SAFETY_MODEL.md requires stated limitations rather than implied ones."""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.name}@{self.version}"


class MedicalPlugin(ABC):
    """Base class for a medical plugin.

    Implement ``metadata``, ``capabilities`` and ``workflows``. The runtime calls
    ``register_into`` once at startup; after that the plugin's code is reached
    only through the Tool Executor.
    """

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata: ...

    @abstractmethod
    def capabilities(self) -> list[Capability]:
        """Capabilities this plugin offers. Each names its model adapter."""

    def workflows(self) -> list[WorkflowDefinition]:
        """Workflows this plugin defines. A capability-only plugin returns none."""
        return []

    def register_into(
        self, capabilities: CapabilityRegistry, workflows: WorkflowRegistry
    ) -> None:
        """Install this plugin's capabilities and workflows into the runtime."""
        for capability in self.capabilities():
            # Stamp provenance so the catalogue can always say where a
            # capability came from, even if the plugin author forgot.
            if not capability.plugin:
                object.__setattr__(capability, "plugin", self.metadata.name)
            capabilities.register(capability)
        for workflow in self.workflows():
            if not workflow.plugin:
                object.__setattr__(workflow, "plugin", self.metadata.name)
            workflows.register(workflow)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.metadata.qualified_name}>"
