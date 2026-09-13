"""Capability Registry and Tool Executor.

TOOL_CALLING.md fixes the flow:

    Agent -> Capability Registry -> Tool Executor -> Medical Capability

Nothing may skip a link. An agent cannot execute code; it can only name a
capability. The registry decides whether that capability exists and what its
contract is. The executor enforces the contract and traces the call.

Core knows a capability has a name, a version and an I/O contract. It never
knows that ``brain_mri.segmentation`` segments a brain — that meaning lives in
the plugin (ADR-001).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .entities import JsonDict
from .errors import CapabilityNotFoundError, ToolContractError, ValidationError
from .ids import digest_json
from .trace import TraceEngine

ToolFunction = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class IOField:
    """One entry of a capability's input or output contract."""

    name: str
    type: str
    """One of ``string``, ``number``, ``integer``, ``boolean``, ``object``, ``array``."""
    description: str = ""
    required: bool = True

    _PYTHON_TYPES = {
        "string": (str,),
        "number": (int, float),
        "integer": (int,),
        "boolean": (bool,),
        "object": (dict,),
        "array": (list, tuple),
    }

    def check(self, value: Any) -> str | None:
        """Return an error message if ``value`` does not match, otherwise ``None``."""
        expected = self._PYTHON_TYPES.get(self.type)
        if expected is None:
            return f"unknown contract type {self.type!r}"
        # bool is a subclass of int; a boolean is not an acceptable number.
        if self.type in ("number", "integer") and isinstance(value, bool):
            return f"{self.name}: expected {self.type}, got boolean"
        if not isinstance(value, expected):
            return f"{self.name}: expected {self.type}, got {type(value).__name__}"
        return None


@dataclass(frozen=True, slots=True)
class Capability:
    """A medical capability offered by a plugin.

    ``handler`` is the plugin's tool function. The registry stores it; only the
    Tool Executor ever calls it.
    """

    name: str
    version: str
    description: str
    handler: ToolFunction
    inputs: tuple[IOField, ...] = ()
    outputs: tuple[IOField, ...] = ()
    plugin: str = ""
    model_adapter: str | None = None
    """Adapter id and version, recorded in the trace so a run can be reproduced."""
    tags: tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.name}@{self.version}"

    def validate_inputs(self, payload: JsonDict) -> None:
        problems = [
            f"{f.name}: required field is missing"
            for f in self.inputs
            if f.required and f.name not in payload
        ]
        problems += [
            message
            for f in self.inputs
            if f.name in payload and (message := f.check(payload[f.name])) is not None
        ]
        if problems:
            raise ToolContractError(
                f"inputs do not satisfy the contract of {self.qualified_name}",
                details={"violations": problems},
            )

    def validate_outputs(self, payload: Any) -> None:
        if not self.outputs:
            return
        if not isinstance(payload, dict):
            raise ToolContractError(
                f"{self.qualified_name} must return an object, got {type(payload).__name__}"
            )
        problems = [
            f"{f.name}: required field is missing"
            for f in self.outputs
            if f.required and f.name not in payload
        ]
        problems += [
            message
            for f in self.outputs
            if f.name in payload and (message := f.check(payload[f.name])) is not None
        ]
        if problems:
            raise ToolContractError(
                f"{self.qualified_name} returned an output that violates its contract",
                details={"violations": problems},
            )


class CapabilityRegistry:
    """What capabilities exist, at which versions.

    Registering the same name twice at the same version is an error rather than
    an overwrite: silently shadowing a medical capability is exactly the kind of
    thing that should be loud.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, dict[str, Capability]] = {}

    def register(self, capability: Capability) -> Capability:
        if not capability.name or not capability.version:
            raise ValidationError("a capability requires both a name and a version")
        versions = self._by_name.setdefault(capability.name, {})
        if capability.version in versions:
            raise ValidationError(
                f"capability {capability.qualified_name} is already registered "
                f"by plugin {versions[capability.version].plugin!r}"
            )
        versions[capability.version] = capability
        return capability

    def get(self, name: str, version: str | None = None) -> Capability:
        """Resolve a capability, defaulting to its highest registered version."""
        versions = self._by_name.get(name)
        if not versions:
            raise CapabilityNotFoundError(f"no capability named {name!r} is registered")
        if version is None:
            newest = max(versions, key=_version_key)
            return versions[newest]
        if version not in versions:
            raise CapabilityNotFoundError(
                f"capability {name!r} has no version {version!r}",
                details={"available": sorted(versions)},
            )
        return versions[version]

    def has(self, name: str, version: str | None = None) -> bool:
        try:
            self.get(name, version)
        except CapabilityNotFoundError:
            return False
        return True

    def list(self, *, tag: str | None = None, plugin: str | None = None) -> list[Capability]:
        found = [
            capability
            for versions in self._by_name.values()
            for capability in versions.values()
            if (tag is None or tag in capability.tags)
            and (plugin is None or capability.plugin == plugin)
        ]
        return sorted(found, key=lambda c: (c.name, _version_key(c.version)))

    def describe(self) -> list[JsonDict]:
        """Machine-readable catalogue, for the agent and for ``GET /capabilities``.

        Handlers are deliberately absent: the agent must go through the executor.
        """
        return [
            {
                "name": c.name,
                "version": c.version,
                "description": c.description,
                "plugin": c.plugin,
                "tags": list(c.tags),
                "model_adapter": c.model_adapter,
                "inputs": [
                    {"name": f.name, "type": f.type, "required": f.required,
                     "description": f.description}
                    for f in c.inputs
                ],
                "outputs": [
                    {"name": f.name, "type": f.type, "required": f.required,
                     "description": f.description}
                    for f in c.outputs
                ],
            }
            for c in self.list()
        ]

    def extend(self, capabilities: Iterable[Capability]) -> None:
        for capability in capabilities:
            self.register(capability)


class ToolExecutor:
    """The only place a capability handler is ever invoked.

    Every call validates the input contract, emits a trace event, validates the
    output contract, and emits an outcome event. There is no unchecked and no
    untraced path, which is what makes "every execution creates trace events"
    true rather than merely intended.
    """

    def __init__(self, registry: CapabilityRegistry, trace: TraceEngine) -> None:
        self._registry = registry
        self._trace = trace

    def can(self, name: str, version: str | None = None) -> bool:
        """Whether a capability is available.

        Lets a step degrade gracefully when an optional capability is absent —
        VECTOR_SEARCH.md requires a runtime with no evidence index to keep
        executing workflows rather than failing them.
        """
        return self._registry.has(name, version)

    def call(
        self,
        name: str,
        payload: JsonDict,
        *,
        version: str | None = None,
        context: Any = None,
    ) -> Any:
        capability = self._registry.get(name, version)
        capability.validate_inputs(payload)

        with self._trace.tool(
            capability.qualified_name,
            inputs=payload,
            plugin=capability.plugin,
            model_adapter=capability.model_adapter,
            input_digest_algorithm="sha256",
        ) as span:
            result = (
                capability.handler(context=context, **payload)
                if _accepts_context(capability.handler)
                else capability.handler(**payload)
            )
            capability.validate_outputs(result)
            span.set_output(_summarise(result))
        return result


def _accepts_context(handler: ToolFunction) -> bool:
    """Whether a handler opted into receiving the execution context."""
    import inspect

    try:
        return "context" in inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return False


def _summarise(result: Any) -> Any:
    """Reduce a tool result to something safe to digest into a trace event.

    Payloads never enter the trace; only their shape and digest do.
    """
    if isinstance(result, dict):
        return {key: digest_json(value) if _is_bulky(value) else value
                for key, value in result.items()}
    return digest_json(result) if _is_bulky(result) else result


def _is_bulky(value: Any) -> bool:
    if isinstance(value, (bytes, bytearray)):
        return True
    if isinstance(value, (list, tuple)):
        return len(value) > 32
    if isinstance(value, str):
        return len(value) > 512
    return False


def _version_key(version: str) -> tuple[int, ...]:
    """Order versions numerically so 0.10.0 sorts after 0.9.0.

    Non-numeric components sort as 0, which is good enough for pre-release tags
    and avoids a dependency on a version-parsing library in Core.
    """
    parts = []
    for component in version.split("."):
        digits = "".join(c for c in component if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)
