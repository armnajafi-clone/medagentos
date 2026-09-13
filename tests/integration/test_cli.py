"""The CLI, driven the way a user drives it."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from tests.support import ROOT  # noqa: F401

from medagentos.cli import main

EXAMPLE = str(ROOT / "examples" / "brain_mri" / "case.json")


def run_cli(*argv: str) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(list(argv))
    return code, buffer.getvalue()


class TestListing(unittest.TestCase):
    def test_workflows_lists_the_reference_pipeline(self) -> None:
        code, output = run_cli("workflows")
        self.assertEqual(code, 0)
        self.assertIn("brain_mri_reference@0.1.0", output)
        self.assertIn("human review gate", output)

    def test_capabilities_names_the_model_adapter(self) -> None:
        code, output = run_cli("capabilities")
        self.assertEqual(code, 0)
        self.assertIn("brain_mri.synthetic_segmentation@0.1.0", output)

    def test_plugins_surfaces_the_limitations(self) -> None:
        code, output = run_cli("plugins")
        self.assertEqual(code, 0)
        self.assertIn("not a clinical diagnosis system", output)


class TestRun(unittest.TestCase):
    def test_a_run_without_approval_stops_at_the_gate(self) -> None:
        code, output = run_cli("run", "brain_mri_reference", "--input", EXAMPLE)
        self.assertEqual(code, 2, "paused is neither success nor failure")
        self.assertIn("awaiting_approval", output)

    def test_a_run_with_approval_succeeds(self) -> None:
        code, output = run_cli(
            "run", "brain_mri_reference", "--input", EXAMPLE, "--approve", "dr-reviewer"
        )
        self.assertEqual(code, 0)
        self.assertIn("succeeded", output)

    def test_an_invalid_input_fails_with_a_reason(self) -> None:
        code, output = run_cli(
            "run", "brain_mri_reference", "--input", EXAMPLE, "--set", "sequence=DWI"
        )
        self.assertEqual(code, 1)
        self.assertIn("input_out_of_scope", output)

    def test_json_output_carries_the_run_and_its_trace(self) -> None:
        code, output = run_cli(
            "run", "brain_mri_reference", "--input", EXAMPLE, "--approve", "r", "--json"
        )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["run"]["status"], "succeeded")
        self.assertEqual(len(payload["run"]["completed_steps"]), 7)
        self.assertGreater(len(payload["trace"]), 20)

    def test_the_run_is_reproducible_across_invocations(self) -> None:
        _, first = run_cli(
            "run", "brain_mri_reference", "--input", EXAMPLE, "--approve", "r", "--json"
        )
        _, second = run_cli(
            "run", "brain_mri_reference", "--input", EXAMPLE, "--approve", "r", "--json"
        )
        a, b = json.loads(first)["run"]["outputs"], json.loads(second)["run"]["outputs"]
        self.assertEqual(a["regions"], b["regions"])
        self.assertEqual(a["lesion_load_ml"], b["lesion_load_ml"])

    def test_an_unknown_workflow_reports_an_error(self) -> None:
        code, _ = run_cli("run", "no_such_workflow")
        self.assertEqual(code, 1)

    def test_no_command_prints_help(self) -> None:
        code, output = run_cli()
        self.assertEqual(code, 2)
        self.assertIn("usage", output.lower())


if __name__ == "__main__":
    unittest.main()
