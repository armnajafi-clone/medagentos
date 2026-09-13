"""Plugin discovery.

D-10: filesystem discovery under ``plugins/`` for the MVP, entry points later.
Discovery is deliberately explicit — a plugin is loaded because it is on the
plugin path, not because it happened to be installed.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import pkgutil
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from ..core.capabilities import CapabilityRegistry
from ..core.errors import ValidationError
from ..core.workflow import WorkflowRegistry
from .base import MedicalPlugin


def discover_plugins(search_paths: Sequence[Path | str]) -> list[MedicalPlugin]:
    """Instantiate every ``MedicalPlugin`` subclass found under the given paths.

    A package is treated as a plugin if importing it yields exactly one concrete
    ``MedicalPlugin`` subclass. Zero means it is not a plugin; more than one is
    ambiguous and refused rather than guessed at.
    """
    found: list[MedicalPlugin] = []
    for raw in search_paths:
        root = Path(raw).resolve()
        if not root.is_dir():
            continue
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        for module_info in pkgutil.iter_modules([str(root)]):
            if module_info.name.startswith("_"):
                continue
            found.extend(_load_from_module(module_info.name))
    return found


def _load_from_module(module_name: str) -> list[MedicalPlugin]:
    module = importlib.import_module(module_name)
    classes = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, MedicalPlugin)
        and obj is not MedicalPlugin
        and not inspect.isabstract(obj)
        # Only classes defined by this package, not ones it imported.
        and (obj.__module__ == module_name or obj.__module__.startswith(module_name + "."))
    ]
    if not classes:
        return []
    if len(classes) > 1:
        raise ValidationError(
            f"module {module_name!r} defines {len(classes)} plugin classes; "
            f"a plugin package must define exactly one",
            details={"classes": sorted(c.__name__ for c in classes)},
        )
    return [classes[0]()]


class PluginLoader:
    """Loads plugins into a pair of registries and remembers what it loaded."""

    def __init__(
        self, capabilities: CapabilityRegistry, workflows: WorkflowRegistry
    ) -> None:
        self._capabilities = capabilities
        self._workflows = workflows
        self._loaded: dict[str, MedicalPlugin] = {}

    @property
    def loaded(self) -> dict[str, MedicalPlugin]:
        return dict(self._loaded)

    def load(self, plugin: MedicalPlugin) -> MedicalPlugin:
        name = plugin.metadata.name
        if name in self._loaded:
            raise ValidationError(
                f"plugin {name!r} is already loaded at version "
                f"{self._loaded[name].metadata.version}"
            )
        plugin.register_into(self._capabilities, self._workflows)
        self._loaded[name] = plugin
        return plugin

    def load_all(self, plugins: Iterable[MedicalPlugin]) -> list[MedicalPlugin]:
        return [self.load(plugin) for plugin in plugins]

    def load_from_paths(self, search_paths: Sequence[Path | str]) -> list[MedicalPlugin]:
        return self.load_all(discover_plugins(search_paths))

    def describe(self) -> list[dict]:
        return [
            {
                "name": p.metadata.name,
                "version": p.metadata.version,
                "description": p.metadata.description,
                "specialty": p.metadata.specialty,
                "modalities": list(p.metadata.modalities),
                "limitations": list(p.metadata.limitations),
                "references": list(p.metadata.references),
                "capabilities": [c.qualified_name for c in p.capabilities()],
                "workflows": [w.qualified_name for w in p.workflows()],
            }
            for p in self._loaded.values()
        ]
