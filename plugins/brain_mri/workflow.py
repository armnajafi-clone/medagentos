"""The brain MRI reference workflow.

The eight steps of BRAIN_MRI_PIPELINE.md, written as ordinary functions. Each
one reaches its model through ``context.tools.call``, so every model invocation
is contract-checked and traced without the step having to arrange for it.
"""

from __future__ import annotations

from typing import Any

from medagentos.core.entities import ArtifactType, Evidence, Finding, Report, Severity
from medagentos.core.errors import StepError
from medagentos.core.workflow import WorkflowContext, WorkflowDefinition, WorkflowStep

#: Stated on every report this workflow produces. SAFETY_MODEL.md requires
#: limitations to be explicit; a reader should never have to infer them.
WORKFLOW_LIMITATIONS = (
    "This is a research workflow, not a clinical diagnosis system.",
    "The reference segmentation adapter is synthetic and has no clinical validity.",
    "Only single-sequence volumes are processed; multi-sequence fusion is not implemented.",
    "No comparison against prior studies is performed.",
    "Findings are observations from one model, not a differential diagnosis.",
)

_SEVERITIES = {
    "informational": Severity.INFORMATIONAL,
    "low": Severity.LOW,
    "moderate": Severity.MODERATE,
    "high": Severity.HIGH,
}


def validate(context: WorkflowContext) -> dict[str, Any]:
    """Reject anything outside the plugin's scope before spending compute on it."""
    result = context.tools.call(
        "brain_mri.validate",
        {
            "volume_digest": context.require("volume_digest"),
            "sequence": context.require("sequence"),
            "voxel_spacing_mm": list(context.require("voxel_spacing_mm")),
            "modality": context.get("modality", "MR"),
        },
    )
    if not result["valid"]:
        raise StepError(
            "validate",
            "the input volume is outside this workflow's supported scope: "
            + "; ".join(result["problems"]),
            code="input_out_of_scope",
            details={"problems": result["problems"]},
        )
    return {"sequence": result["sequence"], "validation": result}


def preprocess(context: WorkflowContext) -> dict[str, Any]:
    result = context.tools.call(
        "brain_mri.preprocess",
        {
            "volume_digest": context.require("volume_digest"),
            "sequence": context.require("sequence"),
            "voxel_spacing_mm": list(context.require("voxel_spacing_mm")),
        },
    )
    return {
        "normalised_digest": result["normalised_digest"],
        "preprocessing_steps": result["applied_steps"],
    }


def segment(context: WorkflowContext) -> dict[str, Any]:
    """Run segmentation and persist the mask as a first-class artifact."""
    result = context.tools.call(
        "brain_mri.segment",
        {
            "normalised_digest": context.require("normalised_digest"),
            "sequence": context.require("sequence"),
        },
    )
    mask_artifact = context.artifacts.create_json(
        payload={
            "regions": result["regions"],
            "lesion_load_ml": result["lesion_load_ml"],
            "mask_digest": result["mask_digest"],
            "voxel_count": result["voxel_count"],
        },
        type=ArtifactType.MASK,
        creator=result["adapter"],
        run_id=context.run.id,
        case_id=context.run.case_id,
        metadata={
            "sequence": context.require("sequence"),
            "adapter": result["adapter"],
            "confidence": result["confidence"],
        },
    )
    return {
        "regions": result["regions"],
        "lesion_load_ml": result["lesion_load_ml"],
        "segmentation_confidence": result["confidence"],
        "segmentation_adapter": result["adapter"],
        "mask_artifact_id": mask_artifact.id,
    }


def extract_findings(context: WorkflowContext) -> dict[str, Any]:
    """Turn measurements into ``Finding`` entities, each carrying its evidence."""
    result = context.tools.call(
        "brain_mri.extract_findings",
        {
            "regions": context.require("regions"),
            "lesion_load_ml": context.require("lesion_load_ml"),
            "confidence": context.require("segmentation_confidence"),
        },
    )

    mask_artifact_id = context.require("mask_artifact_id")
    adapter = context.require("segmentation_adapter")
    findings: list[Finding] = []
    for observation in result["observations"]:
        findings.append(
            Finding(
                case_id=context.run.case_id or "",
                run_id=context.run.id,
                code=observation["code"],
                description=observation["description"],
                severity=_SEVERITIES[observation["severity"]],
                confidence=observation["confidence"],
                evidence=(
                    Evidence(
                        kind="measurement",
                        summary="Quantitative measurement from the segmentation mask.",
                        source=adapter,
                        artifact_id=mask_artifact_id,
                        data=observation["measurement"],
                    ),
                ),
                limitations=WORKFLOW_LIMITATIONS,
                produced_by=f"brain_mri.extract_findings@0.1.0 via {adapter}",
                metadata={"reportable": observation["reportable"]},
            )
        )

    return {
        "findings": findings,
        "withheld_findings": result["withheld_count"],
    }


def retrieve_evidence(context: WorkflowContext) -> dict[str, Any]:
    """Attach reference context to each finding.

    VECTOR_SEARCH.md makes retrieval an optional capability, so this step
    degrades rather than fails when no vector index is configured: it records
    that no external evidence was available, which a reviewer can see.
    """
    findings: list[Finding] = context.require("findings")
    if not context.tools.can("evidence.search"):
        return {
            "evidence_source": "none",
            "evidence_note": (
                "No evidence index is configured; findings rest on their own "
                "measurements alone."
            ),
        }

    enriched: list[Finding] = []
    for finding in findings:
        matches = context.tools.call(
            "evidence.search", {"query": finding.description, "limit": 3}
        )
        extra = tuple(
            Evidence(
                kind="literature",
                summary=match["summary"],
                source=match["source"],
                data={"score": match["score"]},
            )
            for match in matches.get("results", [])
        )
        enriched.append(
            Finding(
                id=finding.id,
                case_id=finding.case_id,
                run_id=finding.run_id,
                code=finding.code,
                description=finding.description,
                severity=finding.severity,
                confidence=finding.confidence,
                evidence=finding.evidence + extra,
                limitations=finding.limitations,
                produced_by=finding.produced_by,
                metadata=finding.metadata,
            )
        )
    return {"findings": enriched, "evidence_source": "vector_index"}


def generate_report(context: WorkflowContext) -> dict[str, Any]:
    """Compose a human-readable report and store it as an artifact.

    The report is explicitly *unapproved* at this point. It becomes final only
    after the review step.
    """
    findings: list[Finding] = context.require("findings")
    reportable = [f for f in findings if f.metadata.get("reportable", True)]
    withheld = [f for f in findings if not f.metadata.get("reportable", True)]

    body = _render_report(
        sequence=context.require("sequence"),
        adapter=context.require("segmentation_adapter"),
        confidence=context.require("segmentation_confidence"),
        regions=context.require("regions"),
        lesion_load_ml=context.require("lesion_load_ml"),
        findings=reportable,
        withheld=withheld,
        evidence_note=context.get("evidence_note"),
    )

    artifact = context.artifacts.create_text(
        text=body,
        type=ArtifactType.REPORT,
        creator="brain_mri.report@0.1.0",
        run_id=context.run.id,
        case_id=context.run.case_id,
        parent_ids=(context.require("mask_artifact_id"),),
        metadata={"approved": False, "findings": len(reportable)},
    )

    report = Report(
        case_id=context.run.case_id or "",
        run_id=context.run.id,
        title=f"Brain MRI analysis ({context.require('sequence')})",
        body=body,
        findings=tuple(reportable),
        limitations=WORKFLOW_LIMITATIONS,
    )
    return {"report": report, "report_artifact_id": artifact.id}


def human_review(context: WorkflowContext) -> dict[str, Any]:
    """Record the approval that the runtime paused for.

    The engine only reaches this step once an approval is present, so the step
    itself just stamps the reviewer onto the report.
    """
    approvals = context.state.get("__approvals__") or {}
    reviewer = approvals.get("human_review")
    if not reviewer:
        # Defensive: the engine gates this step, so reaching it unapproved means
        # the gate was bypassed, which must fail loudly rather than publish.
        raise StepError(
            "human_review",
            "the review step ran without a recorded approval",
            code="approval_missing",
        )

    report: Report = context.require("report")
    approved = report.approve(str(reviewer))
    return {"report": approved, "approved_by": approved.approved_by}


def _render_report(
    *,
    sequence: str,
    adapter: str,
    confidence: float,
    regions: dict,
    lesion_load_ml: float,
    findings: list[Finding],
    withheld: list[Finding],
    evidence_note: str | None,
) -> str:
    lines = [
        f"# Brain MRI analysis ({sequence})",
        "",
        "> **Research output.** Produced by an automated workflow for research and",
        "> engineering evaluation. Not a diagnosis and not a clinical report.",
        "",
        "## Provenance",
        "",
        f"- Segmentation adapter: `{adapter}`",
        f"- Overall model confidence: {confidence:.3f}",
        f"- Sequence: {sequence}",
        "",
        "## Measurements",
        "",
        "| Region | Volume (ml) | Confidence |",
        "|---|---:|---:|",
    ]
    lines += [
        f"| {name.replace('_', ' ')} | {values['volume_ml']:.2f} | {values['confidence']:.3f} |"
        for name, values in sorted(regions.items())
    ]
    lines += [
        f"| **lesion load** | **{lesion_load_ml:.2f}** | — |",
        "",
        "## Observations",
        "",
    ]
    if findings:
        for finding in findings:
            lines += [
                f"### {finding.code}",
                "",
                finding.description,
                "",
                f"- Severity: {finding.severity.value}",
                f"- Confidence: {finding.confidence:.3f}",
                f"- Evidence: {len(finding.evidence)} item(s) from `{finding.produced_by}`",
                "",
            ]
    else:
        lines += ["No observation met the reporting threshold.", ""]

    if withheld:
        lines += [
            "## Withheld",
            "",
            f"{len(withheld)} observation(s) fell below the reporting confidence "
            "threshold and are recorded in the run trace rather than here:",
            "",
        ]
        lines += [f"- `{f.code}` (confidence {f.confidence:.3f})" for f in withheld]
        lines += [""]

    if evidence_note:
        lines += ["## Evidence", "", evidence_note, ""]

    lines += ["## Limitations", ""]
    lines += [f"- {limitation}" for limitation in WORKFLOW_LIMITATIONS]
    lines += ["", "## Review", "", "_Pending human review._", ""]
    return "\n".join(lines)


def build_workflow() -> WorkflowDefinition:
    """The eight-step reference pipeline."""
    return WorkflowDefinition(
        name="brain_mri_reference",
        version="0.1.0",
        description=(
            "Reference brain MRI research workflow: validation, preprocessing, "
            "segmentation, finding extraction, evidence retrieval, report "
            "generation and human review."
        ),
        plugin="brain_mri",
        required_inputs=("volume_digest", "sequence", "voxel_spacing_mm"),
        produces_report=True,
        steps=(
            WorkflowStep("validate", validate, "Reject volumes outside the supported scope."),
            WorkflowStep("preprocess", preprocess, "Normalise the volume for the model."),
            WorkflowStep("segment", segment, "Segment regions and estimate lesion load."),
            WorkflowStep("extract_findings", extract_findings, "Derive structured findings."),
            WorkflowStep("retrieve_evidence", retrieve_evidence, "Attach supporting references."),
            WorkflowStep("generate_report", generate_report, "Compose the unapproved report."),
            WorkflowStep(
                "human_review",
                human_review,
                "Human review gate; the run pauses here until a reviewer approves.",
                requires_approval=True,
            ),
        ),
    )
