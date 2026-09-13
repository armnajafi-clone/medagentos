"""The brain MRI vertical slice, executed end to end.

BRAIN_MRI_PIPELINE.md calls this "a complete vertical slice demonstrating the
architecture". These tests hold it to that: every layer is exercised, and the
safety properties the design corpus demands are asserted rather than assumed.
"""

from __future__ import annotations

import unittest

from tests.support import ROOT, build_engine  # noqa: F401

from brain_mri import BrainMRIPlugin
from medagentos.core.entities import ArtifactType, RunStatus, Severity, TraceEventType
from medagentos.plugin import PluginLoader

VALID_INPUT = {
    "volume_digest": "sha256:" + "ab" * 32,
    "sequence": "T1",
    "voxel_spacing_mm": [1.0, 1.0, 1.2],
    "modality": "MR",
}


class BrainMRITestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine, self.uow, self.workflows, self.capabilities = build_engine()
        self.loader = PluginLoader(self.capabilities, self.workflows)
        self.loader.load(BrainMRIPlugin())

    def run_to_completion(self, inputs: dict | None = None, *, seed: int = 0, case_id: str = "case_1"):
        """Run the workflow through the review gate, approving once."""
        paused = self.engine.start(
            "brain_mri_reference", inputs or dict(VALID_INPUT), seed=seed, case_id=case_id
        )
        self.assertIs(paused.status, RunStatus.AWAITING_APPROVAL, "the review gate must engage")
        state = dict(paused.state)
        state["__approvals__"] = {"human_review": "dr-reviewer"}
        return self.engine.resume(paused.run.id, state=state)


class TestPipeline(BrainMRITestCase):
    def test_the_plugin_registers_its_capabilities_and_workflow(self) -> None:
        self.assertTrue(self.capabilities.has("brain_mri.segment"))
        self.assertEqual(len(self.workflows.get("brain_mri_reference").steps), 7)

    def test_the_run_pauses_at_review_and_completes_after_approval(self) -> None:
        result = self.run_to_completion()
        self.assertIs(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(
            result.run.completed_steps,
            (
                "validate",
                "preprocess",
                "segment",
                "extract_findings",
                "retrieve_evidence",
                "generate_report",
                "human_review",
            ),
        )

    def test_no_report_is_final_without_a_named_reviewer(self) -> None:
        # ADR-006, end to end: the report object itself carries the approval.
        paused = self.engine.start("brain_mri_reference", dict(VALID_INPUT), case_id="case_1")
        self.assertFalse(paused.state["report"].is_approved)

        finished = self.run_to_completion()
        self.assertTrue(finished.state["report"].is_approved)
        self.assertEqual(finished.state["report"].approved_by, "dr-reviewer")

    def test_findings_carry_confidence_evidence_and_limitations(self) -> None:
        # The three things SAFETY_MODEL.md requires of every medical output.
        result = self.run_to_completion()
        findings = result.state["findings"]
        self.assertGreater(len(findings), 0)

        for finding in findings:
            with self.subTest(code=finding.code):
                self.assertTrue(finding.is_supported, "a finding must carry evidence")
                self.assertGreater(finding.confidence, 0.0)
                self.assertTrue(finding.limitations, "a finding must state its limitations")
                self.assertIn("synthetic", finding.produced_by)
                self.assertIsInstance(finding.severity, Severity)

    def test_evidence_names_the_artifact_it_was_measured_from(self) -> None:
        result = self.run_to_completion()
        mask_id = result.state["mask_artifact_id"]
        for finding in result.state["findings"]:
            measurement = next(e for e in finding.evidence if e.kind == "measurement")
            self.assertEqual(measurement.artifact_id, mask_id)

    def test_the_report_states_its_limitations_and_its_provenance(self) -> None:
        result = self.run_to_completion()
        body = result.state["report"].body

        self.assertIn("Research output", body)
        self.assertIn("not a diagnosis", body.lower())
        self.assertIn("Limitations", body)
        self.assertIn("no clinical validity", body.lower())
        self.assertIn("brain_mri.synthetic_segmentation@0.1.0", body)

    def test_the_mask_and_the_report_are_persisted_as_artifacts(self) -> None:
        result = self.run_to_completion()
        artifacts = self.uow.artifacts.list_artifacts(run_id=result.run.id)
        kinds = {a.type for a in artifacts}

        self.assertIn(ArtifactType.MASK, kinds)
        self.assertIn(ArtifactType.REPORT, kinds)

    def test_the_report_artifact_descends_from_the_mask(self) -> None:
        result = self.run_to_completion()
        artifacts = self.uow.artifacts.list_artifacts(run_id=result.run.id)
        report = next(a for a in artifacts if a.type is ArtifactType.REPORT)
        self.assertIn(result.state["mask_artifact_id"], report.parent_ids)

    def test_evidence_retrieval_degrades_when_no_index_is_configured(self) -> None:
        # VECTOR_SEARCH.md: an optional capability must not be a dependency.
        result = self.run_to_completion()
        self.assertEqual(result.state["evidence_source"], "none")
        self.assertIn("No evidence index", result.state["evidence_note"])


class TestTraceability(BrainMRITestCase):
    def test_every_model_call_is_traced_with_its_adapter_version(self) -> None:
        result = self.run_to_completion()
        events = self.uow.traces.list_events(result.run.id)
        tool_calls = [e for e in events if e.type is TraceEventType.TOOL_CALLED]

        self.assertEqual(len(tool_calls), 4, "one per capability the workflow uses")
        segment = next(e for e in tool_calls if e.name.startswith("brain_mri.segment"))
        self.assertEqual(
            segment.attributes["model_adapter"], "brain_mri.synthetic_segmentation@0.1.0"
        )

    def test_the_trace_covers_every_executed_step(self) -> None:
        result = self.run_to_completion()
        events = self.uow.traces.list_events(result.run.id)
        traced = {e.name for e in events if e.type is TraceEventType.STEP_SUCCEEDED}
        self.assertEqual(traced, set(result.run.completed_steps))

    def test_the_approval_appears_in_the_trace(self) -> None:
        result = self.run_to_completion()
        types = [e.type for e in self.uow.traces.list_events(result.run.id)]
        self.assertIn(TraceEventType.APPROVAL_REQUESTED, types)

    def test_the_trace_is_a_single_gapless_sequence_across_the_pause(self) -> None:
        result = self.run_to_completion()
        sequences = [e.sequence for e in self.uow.traces.list_events(result.run.id)]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))


class TestReproducibility(BrainMRITestCase):
    def test_identical_inputs_produce_identical_measurements(self) -> None:
        first = self.run_to_completion(seed=1)
        second = self.run_to_completion(seed=1)
        self.assertEqual(first.state["regions"], second.state["regions"])
        self.assertEqual(first.state["lesion_load_ml"], second.state["lesion_load_ml"])

    def test_identical_outputs_are_stored_once(self) -> None:
        first = self.run_to_completion(seed=1)
        second = self.run_to_completion(seed=1)
        first_mask = self.uow.artifacts.get_artifact(first.state["mask_artifact_id"])
        second_mask = self.uow.artifacts.get_artifact(second.state["mask_artifact_id"])

        self.assertEqual(first_mask.digest, second_mask.digest)
        self.assertEqual(first_mask.location, second_mask.location)

    def test_different_volumes_produce_different_measurements(self) -> None:
        a = self.run_to_completion()
        b = self.run_to_completion({**VALID_INPUT, "volume_digest": "sha256:" + "cd" * 32})
        self.assertNotEqual(a.state["lesion_load_ml"], b.state["lesion_load_ml"])

    def test_the_synthetic_adapter_is_stable_across_processes(self) -> None:
        # Derived from SHA-256, not from `random`, whose stream is not
        # guaranteed stable across interpreter versions.
        from brain_mri.adapters import SyntheticSegmentationAdapter

        result = SyntheticSegmentationAdapter()({"volume_digest": "sha256:fixed"})
        # Golden values. If a change to the adapter moves these, that change
        # breaks reproducibility for anyone holding stored reference outputs and
        # must be a deliberate version bump, not a silent edit.
        self.assertEqual(result["lesion_load_ml"], 8.05)
        self.assertEqual(result["regions"]["ventricles"]["volume_ml"], 346.61)
        self.assertEqual(result["confidence"], 0.7581)


class TestValidation(BrainMRITestCase):
    def test_an_unsupported_sequence_stops_the_run_at_validation(self) -> None:
        result = self.engine.start(
            "brain_mri_reference", {**VALID_INPUT, "sequence": "DWI"}, case_id="case_1"
        )
        self.assertIs(result.status, RunStatus.FAILED)
        self.assertEqual(result.run.error_code, "input_out_of_scope")
        self.assertEqual(result.run.completed_steps, (), "nothing downstream may run")

    def test_a_non_mr_modality_is_refused(self) -> None:
        result = self.engine.start(
            "brain_mri_reference", {**VALID_INPUT, "modality": "CT"}, case_id="case_1"
        )
        self.assertIs(result.status, RunStatus.FAILED)
        self.assertIn("not MR", result.run.error_message)

    def test_coarse_voxel_spacing_is_refused(self) -> None:
        result = self.engine.start(
            "brain_mri_reference", {**VALID_INPUT, "voxel_spacing_mm": [1.0, 1.0, 8.0]}, case_id="c"
        )
        self.assertIs(result.status, RunStatus.FAILED)
        self.assertIn("coarser", result.run.error_message)

    def test_missing_required_input_is_refused_before_a_run_exists(self) -> None:
        from medagentos.core.errors import ValidationError

        with self.assertRaises(ValidationError):
            self.engine.start("brain_mri_reference", {"sequence": "T1"})

    def test_a_malformed_digest_is_refused(self) -> None:
        result = self.engine.start(
            "brain_mri_reference", {**VALID_INPUT, "volume_digest": "not-a-digest"}, case_id="c"
        )
        self.assertIs(result.status, RunStatus.FAILED)


class TestSwappability(BrainMRITestCase):
    """MODEL_ADAPTER_STRATEGY.md: replacing the model must not touch the workflow."""

    def test_a_different_adapter_version_flows_through_untouched_workflow_code(self) -> None:
        from brain_mri.adapters.synthetic import SyntheticSegmentationAdapter
        from medagentos.core.capabilities import Capability, IOField

        replacement = SyntheticSegmentationAdapter(version="0.2.0")

        def segment_with_replacement(normalised_digest: str, sequence: str = "T1") -> dict:
            result = replacement({"volume_digest": normalised_digest, "sequence": sequence})
            return {
                "regions": result["regions"],
                "lesion_load_ml": result["lesion_load_ml"],
                "mask_digest": result["mask_digest"],
                "voxel_count": result["voxel_count"],
                "confidence": result["confidence"],
                "adapter": result["adapter"],
            }

        # Register as a new *version* of the same capability; the workflow asks
        # for the capability by name and therefore picks up the newer one.
        self.capabilities.register(
            Capability(
                name="brain_mri.segment",
                version="0.2.0",
                description="",
                handler=segment_with_replacement,
                inputs=(
                    IOField("normalised_digest", "string"),
                    IOField("sequence", "string", required=False),
                ),
                outputs=(
                    IOField("regions", "object"),
                    IOField("lesion_load_ml", "number"),
                    IOField("mask_digest", "string"),
                    IOField("confidence", "number"),
                ),
                plugin="brain_mri",
                model_adapter=replacement.info.qualified_name,
            )
        )

        result = self.run_to_completion()
        self.assertEqual(
            result.state["segmentation_adapter"], "brain_mri.synthetic_segmentation@0.2.0"
        )
        self.assertIs(result.status, RunStatus.SUCCEEDED)


class TestSafetyInvariants(BrainMRITestCase):
    def test_the_workflow_cannot_be_defined_without_a_review_step(self) -> None:
        from medagentos.core.errors import ValidationError
        from medagentos.core.workflow import WorkflowDefinition

        definition = self.workflows.get("brain_mri_reference")
        unsafe_steps = tuple(s for s in definition.steps if not s.requires_approval)

        with self.assertRaises(ValidationError):
            WorkflowDefinition(
                name="brain_mri_unsafe",
                version="0.1.0",
                description="",
                steps=unsafe_steps,
                produces_report=True,
            )

    def test_the_review_step_refuses_to_run_without_a_recorded_approval(self) -> None:
        # Defence in depth: even if the engine's gate were bypassed, the step
        # itself must not stamp an approval nobody gave.
        from medagentos.core.errors import StepError

        from brain_mri.workflow import human_review

        paused = self.engine.start("brain_mri_reference", dict(VALID_INPUT), case_id="c")
        state = dict(paused.state)  # deliberately no __approvals__

        class _Context:
            def __init__(self, state: dict) -> None:
                self.state = state

            def require(self, key: str):
                return self.state[key]

        with self.assertRaises(StepError) as ctx:
            human_review(_Context(state))
        self.assertEqual(ctx.exception.code, "approval_missing")


if __name__ == "__main__":
    unittest.main()
