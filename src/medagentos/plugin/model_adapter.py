"""Model adapters.

MODEL_ADAPTER_STRATEGY.md: models must not connect directly to workflows. The
chain is ``Capability -> Tool -> Model Adapter -> Model``, which buys three
things: a model can be replaced without touching the workflow, its version is
recorded on every call, and every model presents the same interface.

An adapter is also the boundary where a heavy optional dependency (torch, MONAI)
is imported. Importing this module must stay free of that cost.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AdapterInfo:
    """What the trace records about the model behind a capability.

    Reproducibility (D-09) is only meaningful if a trace says *which* model ran,
    at which version, on what hardware, and whether it is deterministic.
    """

    id: str
    version: str
    framework: str = ""
    """e.g. ``synthetic``, ``monai``, ``pytorch``."""
    model_name: str = ""
    device: str = "cpu"
    deterministic: bool = False
    """Whether an identical input reproduces an identical output on this device."""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.id}@{self.version}"


class ModelAdapter(ABC):
    """One model behind one stable interface.

    Subclasses load their model in ``load`` and keep it loaded: GPU_STRATEGY.md
    is explicit that the container should load once and then serve inferences,
    never reload per request.
    """

    def __init__(self, info: AdapterInfo) -> None:
        self._info = info
        self._loaded = False

    @property
    def info(self) -> AdapterInfo:
        return self._info

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def ensure_loaded(self) -> None:
        """Load on first use and never again."""
        if not self._loaded:
            self.load()
            self._loaded = True

    def load(self) -> None:
        """Acquire weights and warm the model. Overridden by adapters that need it."""

    @abstractmethod
    def predict(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run inference. Returns a plain dict so the tool contract can check it."""

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_loaded()
        result = self.predict(payload)
        # Provenance travels with the result, so a finding can always name the
        # model that produced it without the workflow having to remember.
        result.setdefault("adapter", self._info.qualified_name)
        return result

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self._info.qualified_name} on {self._info.device}>"
