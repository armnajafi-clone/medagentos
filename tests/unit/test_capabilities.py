"""Capability Registry and Tool Executor: contracts are enforced, calls are traced."""

from __future__ import annotations

import unittest

from tests.support import ROOT  # noqa: F401

from medagentos.core.capabilities import (
    Capability,
    CapabilityRegistry,
    IOField,
    ToolExecutor,
)
from medagentos.core.entities import TraceEventType
from medagentos.core.errors import (
    CapabilityNotFoundError,
    ToolContractError,
    ValidationError,
)
from medagentos.core.trace import TraceEngine
from medagentos.infra.memory import InMemoryUnitOfWork


def _segment(volume_id: str, threshold: float = 0.5) -> dict:
    return {"mask_id": f"mask-of-{volume_id}", "volume_ml": 12.5 * threshold}


SEGMENTATION = Capability(
    name="demo.segmentation",
    version="0.1.0",
    description="A deterministic stand-in for a segmentation capability.",
    handler=_segment,
    inputs=(
        IOField("volume_id", "string", "identifier of the input volume"),
        IOField("threshold", "number", "probability cut-off", required=False),
    ),
    outputs=(IOField("mask_id", "string"), IOField("volume_ml", "number")),
    plugin="demo",
    model_adapter="synthetic@0.1.0",
    tags=("segmentation",),
)


class TestCapabilityRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = CapabilityRegistry()
        self.registry.register(SEGMENTATION)

    def test_lookup_by_name_and_version(self) -> None:
        self.assertIs(self.registry.get("demo.segmentation", "0.1.0"), SEGMENTATION)

    def test_unversioned_lookup_returns_the_newest_version(self) -> None:
        newer = Capability(
            name="demo.segmentation", version="0.10.0", description="", handler=_segment
        )
        self.registry.register(newer)
        # 0.10.0 must beat 0.9.0 and 0.1.0: version ordering is numeric, not lexical.
        self.registry.register(
            Capability(name="demo.segmentation", version="0.9.0", description="", handler=_segment)
        )
        self.assertIs(self.registry.get("demo.segmentation"), newer)

    def test_registering_the_same_version_twice_is_refused(self) -> None:
        # Silently shadowing a medical capability is exactly what we must not do.
        with self.assertRaises(ValidationError):
            self.registry.register(SEGMENTATION)

    def test_a_capability_needs_a_name_and_a_version(self) -> None:
        with self.assertRaises(ValidationError):
            self.registry.register(Capability(name="", version="1", description="", handler=_segment))

    def test_unknown_capability_raises(self) -> None:
        with self.assertRaises(CapabilityNotFoundError):
            self.registry.get("demo.nonexistent")

    def test_unknown_version_lists_what_is_available(self) -> None:
        with self.assertRaises(CapabilityNotFoundError) as ctx:
            self.registry.get("demo.segmentation", "9.9.9")
        self.assertIn("0.1.0", ctx.exception.details["available"])

    def test_listing_filters_by_tag_and_plugin(self) -> None:
        self.assertEqual(len(self.registry.list(tag="segmentation")), 1)
        self.assertEqual(len(self.registry.list(tag="classification")), 0)
        self.assertEqual(len(self.registry.list(plugin="demo")), 1)

    def test_catalogue_never_exposes_the_handler(self) -> None:
        # TOOL_CALLING.md: the agent goes through the executor, so it must not be
        # handed anything callable.
        described = self.registry.describe()
        self.assertEqual(len(described), 1)
        self.assertNotIn("handler", described[0])
        self.assertEqual(described[0]["model_adapter"], "synthetic@0.1.0")


class TestContractValidation(unittest.TestCase):
    def test_missing_required_input_is_a_contract_violation(self) -> None:
        with self.assertRaises(ToolContractError) as ctx:
            SEGMENTATION.validate_inputs({})
        self.assertIn("volume_id", str(ctx.exception.details["violations"]))

    def test_optional_input_may_be_absent(self) -> None:
        SEGMENTATION.validate_inputs({"volume_id": "v1"})

    def test_wrong_type_is_a_contract_violation(self) -> None:
        with self.assertRaises(ToolContractError):
            SEGMENTATION.validate_inputs({"volume_id": 42})

    def test_a_boolean_is_not_a_number(self) -> None:
        # bool subclasses int in Python; a contract that says "number" must not
        # silently accept True.
        with self.assertRaises(ToolContractError):
            SEGMENTATION.validate_inputs({"volume_id": "v1", "threshold": True})

    def test_output_contract_is_enforced(self) -> None:
        with self.assertRaises(ToolContractError):
            SEGMENTATION.validate_outputs({"mask_id": "m1"})  # volume_ml missing
        with self.assertRaises(ToolContractError):
            SEGMENTATION.validate_outputs("not an object")


class TestToolExecutor(unittest.TestCase):
    def setUp(self) -> None:
        self.uow = InMemoryUnitOfWork()
        self.trace = TraceEngine("run_test", self.uow.traces)
        self.registry = CapabilityRegistry()
        self.registry.register(SEGMENTATION)
        self.executor = ToolExecutor(self.registry, self.trace)

    def test_call_returns_the_handler_result(self) -> None:
        result = self.executor.call("demo.segmentation", {"volume_id": "v1"})
        self.assertEqual(result["mask_id"], "mask-of-v1")

    def test_every_call_is_traced_with_its_adapter_version(self) -> None:
        self.executor.call("demo.segmentation", {"volume_id": "v1"})
        events = self.trace.events()
        self.assertEqual(
            [e.type for e in events],
            [TraceEventType.TOOL_CALLED, TraceEventType.TOOL_SUCCEEDED],
        )
        self.assertEqual(events[0].attributes["model_adapter"], "synthetic@0.1.0")
        self.assertIsNotNone(events[0].input_digest)

    def test_a_contract_violation_is_refused_before_the_handler_runs(self) -> None:
        calls: list[str] = []

        def spy(volume_id: str) -> dict:
            calls.append(volume_id)
            return {"mask_id": "m", "volume_ml": 1.0}

        self.registry.register(
            Capability(
                name="demo.spy",
                version="0.1.0",
                description="",
                handler=spy,
                inputs=(IOField("volume_id", "string"),),
            )
        )
        with self.assertRaises(ToolContractError):
            self.executor.call("demo.spy", {})
        self.assertEqual(calls, [], "the handler must not run on invalid input")

    def test_a_failing_handler_is_traced_as_a_tool_failure(self) -> None:
        def broken() -> dict:
            raise RuntimeError("model server unreachable")

        self.registry.register(
            Capability(name="demo.broken", version="0.1.0", description="", handler=broken)
        )
        with self.assertRaises(RuntimeError):
            self.executor.call("demo.broken", {})

        last = self.trace.events()[-1]
        self.assertIs(last.type, TraceEventType.TOOL_FAILED)
        self.assertIn("model server unreachable", last.error_message or "")

    def test_bulky_results_are_digested_not_stored_in_the_trace(self) -> None:
        big = list(range(1000))

        def bulky() -> dict:
            return {"embedding": big}

        self.registry.register(
            Capability(name="demo.bulky", version="0.1.0", description="", handler=bulky)
        )
        self.executor.call("demo.bulky", {})
        success = self.trace.events()[-1]
        self.assertIsNotNone(success.output_digest)
        self.assertNotIn("999", str(success.attributes))

    def test_a_handler_can_opt_into_the_execution_context(self) -> None:
        seen: list[object] = []

        def with_context(volume_id: str, context: object = None) -> dict:
            seen.append(context)
            return {"ok": True}

        self.registry.register(
            Capability(
                name="demo.ctx",
                version="0.1.0",
                description="",
                handler=with_context,
                inputs=(IOField("volume_id", "string"),),
            )
        )
        sentinel = object()
        self.executor.call("demo.ctx", {"volume_id": "v"}, context=sentinel)
        self.assertEqual(seen, [sentinel])


if __name__ == "__main__":
    unittest.main()
