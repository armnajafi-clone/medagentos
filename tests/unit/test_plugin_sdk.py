"""Plugin SDK: discovery, registration, and the adapter indirection."""

from __future__ import annotations

import unittest

from tests.support import ROOT  # noqa: F401

from medagentos.core.capabilities import Capability, CapabilityRegistry, IOField
from medagentos.core.errors import ValidationError
from medagentos.core.workflow import WorkflowDefinition, WorkflowRegistry, WorkflowStep
from medagentos.plugin import AdapterInfo, MedicalPlugin, ModelAdapter, PluginLoader, PluginMetadata


class CountingAdapter(ModelAdapter):
    """Records how many times it was loaded, to prove loading happens once."""

    def __init__(self) -> None:
        super().__init__(AdapterInfo(id="test.counting", version="1.0.0", framework="synthetic"))
        self.load_count = 0
        self.predict_count = 0

    def load(self) -> None:
        self.load_count += 1

    def predict(self, payload: dict) -> dict:
        self.predict_count += 1
        return {"echo": payload.get("value")}


class DemoPlugin(MedicalPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="demo",
            version="1.0.0",
            description="A plugin used to test the SDK.",
            specialty="testing",
            limitations=("Not a real capability.",),
        )

    def capabilities(self) -> list[Capability]:
        return [
            Capability(
                name="demo.echo",
                version="1.0.0",
                description="",
                handler=lambda value: {"echo": value},
                inputs=(IOField("value", "string"),),
            )
        ]

    def workflows(self) -> list[WorkflowDefinition]:
        return [
            WorkflowDefinition(
                name="demo_flow",
                version="1.0.0",
                description="",
                steps=(WorkflowStep("noop", lambda context: {}),),
            )
        ]


class TestModelAdapter(unittest.TestCase):
    def test_the_model_loads_once_and_is_reused(self) -> None:
        # GPU_STRATEGY.md: load at container start, then serve inferences.
        adapter = CountingAdapter()
        self.assertFalse(adapter.is_loaded)

        for _ in range(5):
            adapter({"value": 1})

        self.assertEqual(adapter.load_count, 1, "the model must not reload per request")
        self.assertEqual(adapter.predict_count, 5)

    def test_results_carry_the_adapter_version(self) -> None:
        # So a finding can always name the model that produced it.
        result = CountingAdapter()({"value": "x"})
        self.assertEqual(result["adapter"], "test.counting@1.0.0")

    def test_an_adapter_reports_whether_it_is_deterministic(self) -> None:
        info = AdapterInfo(id="a", version="1", deterministic=True, device="cpu")
        self.assertTrue(info.deterministic)
        self.assertEqual(info.qualified_name, "a@1")


class TestPluginRegistration(unittest.TestCase):
    def setUp(self) -> None:
        self.capabilities = CapabilityRegistry()
        self.workflows = WorkflowRegistry()
        self.loader = PluginLoader(self.capabilities, self.workflows)

    def test_loading_installs_capabilities_and_workflows(self) -> None:
        self.loader.load(DemoPlugin())
        self.assertTrue(self.capabilities.has("demo.echo"))
        self.assertEqual(self.workflows.get("demo_flow").version, "1.0.0")

    def test_registration_stamps_the_plugin_name_onto_its_contributions(self) -> None:
        self.loader.load(DemoPlugin())
        self.assertEqual(self.capabilities.get("demo.echo").plugin, "demo")
        self.assertEqual(self.workflows.get("demo_flow").plugin, "demo")

    def test_loading_the_same_plugin_twice_is_refused(self) -> None:
        self.loader.load(DemoPlugin())
        with self.assertRaises(ValidationError):
            self.loader.load(DemoPlugin())

    def test_the_description_surfaces_limitations(self) -> None:
        self.loader.load(DemoPlugin())
        described = self.loader.describe()[0]
        self.assertEqual(described["limitations"], ["Not a real capability."])
        self.assertEqual(described["capabilities"], ["demo.echo@1.0.0"])


class TestDiscovery(unittest.TestCase):
    def test_the_repository_plugins_directory_is_discovered(self) -> None:
        from medagentos.plugin import discover_plugins

        found = discover_plugins([ROOT / "plugins"])
        names = {p.metadata.name for p in found}
        self.assertIn("brain_mri", names)

    def test_discovery_ignores_a_directory_that_is_not_a_plugin(self) -> None:
        from medagentos.plugin import discover_plugins

        self.assertEqual(discover_plugins([ROOT / "docs"]), [])

    def test_a_missing_path_is_not_an_error(self) -> None:
        from medagentos.plugin import discover_plugins

        self.assertEqual(discover_plugins([ROOT / "does_not_exist"]), [])


if __name__ == "__main__":
    unittest.main()
