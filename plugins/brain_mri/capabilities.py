"""Capabilities offered by the brain MRI plugin.

Each capability is a thin, contract-checked wrapper over a model adapter or a
pure computation. All of the neuroimaging knowledge in this system lives in this
file and its neighbours — never in ``medagentos.core``.
"""

from __future__ import annotations

from typing import Any

from medagentos.core.capabilities import Capability, IOField

from .adapters.synthetic import SyntheticSegmentationAdapter

#: Sequences the plugin knows how to handle. A volume of any other sequence is
#: rejected at validation rather than silently processed as if it were T1.
SUPPORTED_SEQUENCES = ("T1", "T1CE", "T2", "FLAIR")

#: Below this, a finding is not reported as an observation on its own; it is
#: still recorded in the trace. A research threshold, not a clinical one.
MIN_REPORTABLE_CONFIDENCE = 0.6

#: Lesion load above which the workflow flags the case for closer human
#: attention. Deliberately a plugin constant, not a Core concept.
LESION_LOAD_ATTENTION_ML = 5.0

_segmentation_adapter = SyntheticSegmentationAdapter()


def validate_volume(
    volume_digest: str, sequence: str, voxel_spacing_mm: list, modality: str = "MR"
) -> dict[str, Any]:
    """Check that an input volume is something this plugin can process.

    Returns a structured verdict rather than raising, so that an invalid input
    produces a traced, inspectable result instead of a stack trace.
    """
    problems: list[str] = []
    if modality.upper() != "MR":
        problems.append(f"modality {modality!r} is not MR")
    if sequence.upper() not in SUPPORTED_SEQUENCES:
        problems.append(
            f"sequence {sequence!r} is not supported; expected one of {', '.join(SUPPORTED_SEQUENCES)}"
        )
    if len(voxel_spacing_mm) != 3:
        problems.append(f"voxel spacing must have three components, got {len(voxel_spacing_mm)}")
    elif any((not isinstance(s, (int, float))) or s <= 0 for s in voxel_spacing_mm):
        problems.append(f"voxel spacing must be positive in every axis, got {voxel_spacing_mm}")
    elif max(voxel_spacing_mm) > 5.0:
        # Very anisotropic or very coarse acquisitions are out of scope rather
        # than silently degraded.
        problems.append(f"voxel spacing {voxel_spacing_mm} is coarser than this plugin supports")
    if not volume_digest.startswith("sha256:"):
        problems.append("volume_digest must be a sha256 content digest")

    return {"valid": not problems, "problems": problems, "sequence": sequence.upper()}


def preprocess_volume(
    volume_digest: str, sequence: str, voxel_spacing_mm: list
) -> dict[str, Any]:
    """Normalise a volume for the segmentation model.

    In the reference implementation this records the intended preprocessing
    rather than performing it, because the synthetic adapter consumes a digest,
    not voxels. A MONAI-backed adapter replaces this body with a real transform
    chain; the contract and the trace shape stay identical.
    """
    target_spacing = [1.0, 1.0, 1.0]
    steps = [
        f"resample {voxel_spacing_mm} -> {target_spacing} mm",
        "reorient to RAS",
        "skull strip",
        "z-score intensity normalisation",
    ]
    return {
        "normalised_digest": volume_digest,
        "target_spacing_mm": target_spacing,
        "applied_steps": steps,
        "sequence": sequence.upper(),
    }


def segment_volume(normalised_digest: str, sequence: str = "T1") -> dict[str, Any]:
    """Segment anatomical regions. Delegates to the model adapter, never to a model."""
    result = _segmentation_adapter(
        {"volume_digest": normalised_digest, "sequence": sequence.upper()}
    )
    return {
        "regions": result["regions"],
        "lesion_load_ml": result["lesion_load_ml"],
        "mask_digest": result["mask_digest"],
        "voxel_count": result["voxel_count"],
        "confidence": result["confidence"],
        "adapter": result["adapter"],
    }


def extract_findings(regions: dict, lesion_load_ml: float, confidence: float) -> dict[str, Any]:
    """Turn segmentation output into structured observations.

    Every observation carries its own confidence and its supporting measurement.
    An observation the model is not confident about is *withheld from the report
    but kept in the output*, so a reviewer can see what was suppressed and why —
    silent suppression would be worse than a low-confidence finding.
    """
    observations: list[dict[str, Any]] = []

    total_volume = sum(r["volume_ml"] for r in regions.values())
    left = regions.get("left_hemisphere", {}).get("volume_ml", 0.0)
    right = regions.get("right_hemisphere", {}).get("volume_ml", 0.0)
    asymmetry = abs(left - right) / max(left + right, 1e-9) * 2

    if lesion_load_ml >= LESION_LOAD_ATTENTION_ML:
        observations.append(
            {
                "code": "lesion.load.elevated",
                "description": (
                    f"Segmented lesion load of {lesion_load_ml:.2f} ml exceeds the "
                    f"{LESION_LOAD_ATTENTION_ML:.1f} ml attention threshold used by this workflow."
                ),
                "severity": "moderate",
                "confidence": confidence,
                "measurement": {"lesion_load_ml": lesion_load_ml},
                "reportable": confidence >= MIN_REPORTABLE_CONFIDENCE,
            }
        )

    if asymmetry > 0.05:
        observations.append(
            {
                "code": "morphometry.hemispheric_asymmetry",
                "description": (
                    f"Hemispheric volume difference of {asymmetry * 100:.1f}% "
                    f"(left {left:.1f} ml, right {right:.1f} ml)."
                ),
                "severity": "low",
                "confidence": min(confidence, regions.get("left_hemisphere", {}).get("confidence", confidence)),
                "measurement": {"asymmetry_ratio": round(asymmetry, 4), "left_ml": left, "right_ml": right},
                "reportable": True,
            }
        )

    observations.append(
        {
            "code": "morphometry.total_volume",
            "description": f"Total segmented volume {total_volume:.1f} ml across {len(regions)} regions.",
            "severity": "informational",
            "confidence": confidence,
            "measurement": {"total_volume_ml": round(total_volume, 2), "regions": len(regions)},
            "reportable": True,
        }
    )

    return {
        "observations": observations,
        "reportable_count": sum(1 for o in observations if o["reportable"]),
        "withheld_count": sum(1 for o in observations if not o["reportable"]),
    }


def build_capabilities() -> list[Capability]:
    """The plugin's capability catalogue."""
    return [
        Capability(
            name="brain_mri.validate",
            version="0.1.0",
            description="Check that an MRI volume is within this plugin's supported scope.",
            handler=validate_volume,
            inputs=(
                IOField("volume_digest", "string", "content digest of the input volume"),
                IOField("sequence", "string", "MRI sequence, e.g. T1 or FLAIR"),
                IOField("voxel_spacing_mm", "array", "voxel spacing in millimetres, three axes"),
                IOField("modality", "string", "acquisition modality", required=False),
            ),
            outputs=(IOField("valid", "boolean"), IOField("problems", "array")),
            plugin="brain_mri",
            tags=("validation",),
        ),
        Capability(
            name="brain_mri.preprocess",
            version="0.1.0",
            description="Normalise an MRI volume for segmentation.",
            handler=preprocess_volume,
            inputs=(
                IOField("volume_digest", "string"),
                IOField("sequence", "string"),
                IOField("voxel_spacing_mm", "array"),
            ),
            outputs=(IOField("normalised_digest", "string"), IOField("applied_steps", "array")),
            plugin="brain_mri",
            model_adapter="monai.transforms@planned",
            tags=("preprocessing",),
        ),
        Capability(
            name="brain_mri.segment",
            version="0.1.0",
            description="Segment anatomical regions and estimate lesion load.",
            handler=segment_volume,
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
            model_adapter=_segmentation_adapter.info.qualified_name,
            tags=("segmentation",),
        ),
        Capability(
            name="brain_mri.extract_findings",
            version="0.1.0",
            description="Derive structured observations from a segmentation result.",
            handler=extract_findings,
            inputs=(
                IOField("regions", "object"),
                IOField("lesion_load_ml", "number"),
                IOField("confidence", "number"),
            ),
            outputs=(IOField("observations", "array"),),
            plugin="brain_mri",
            tags=("analysis",),
        ),
    ]
