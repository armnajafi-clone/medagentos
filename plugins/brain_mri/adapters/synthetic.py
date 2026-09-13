"""A deterministic synthetic segmentation adapter.

Why a synthetic model is the *default* rather than an afterthought:

- The reference workflow must run in CI, on a laptop, with no GPU and no
  multi-gigabyte download. Otherwise nobody runs it and it rots.
- MODEL_ADAPTER_STRATEGY.md's claim that a model can be swapped without touching
  the workflow is only credible if there are at least two adapters. This is the
  first; a MONAI adapter is the second.
- Reproducibility experiments need a component whose output is known to be
  exactly reproducible, so that any divergence measured elsewhere is attributable
  to the real model rather than to the harness.

It produces plausibly-shaped output from the content digest of its input. It is
not a model and it has no clinical meaning whatsoever.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Any

from medagentos.plugin import AdapterInfo, ModelAdapter

#: Structures the synthetic adapter reports on. Chosen to look like a real
#: neuroimaging segmentation output so downstream code is exercised realistically.
REGIONS = (
    "left_hemisphere",
    "right_hemisphere",
    "ventricles",
    "white_matter",
    "grey_matter",
)


class SyntheticSegmentationAdapter(ModelAdapter):
    """Derives a stable pseudo-segmentation from the input digest.

    Identical input always yields identical output, on any machine and any
    Python build: the values come from SHA-256, not from ``random``, whose
    stream is not guaranteed stable across interpreter versions.
    """

    def __init__(self, version: str = "0.1.0") -> None:
        super().__init__(
            AdapterInfo(
                id="brain_mri.synthetic_segmentation",
                version=version,
                framework="synthetic",
                model_name="digest-derived-pseudo-segmentation",
                device="cpu",
                deterministic=True,
                metadata={
                    "clinical_validity": "none",
                    "purpose": "architecture reference and reproducibility baseline",
                },
            )
        )

    def predict(self, payload: dict[str, Any]) -> dict[str, Any]:
        volume_digest = str(payload.get("volume_digest", ""))
        if not volume_digest:
            raise ValueError("the synthetic adapter requires a volume_digest to derive from")

        stream = _DigestStream(volume_digest)
        regions = {}
        for region in REGIONS:
            regions[region] = {
                # Volumes in a range that reads like adult brain morphometry, so
                # downstream formatting and thresholds are exercised realistically.
                "volume_ml": round(120.0 + stream.next_float(region) * 480.0, 2),
                "confidence": round(0.55 + stream.next_float(region + ":conf") * 0.44, 4),
            }

        lesion_load = round(stream.next_float("lesion") * 12.0, 2)
        return {
            "regions": regions,
            "lesion_load_ml": lesion_load,
            "mask_digest": stream.derived("mask"),
            "voxel_count": 1_000_000 + int(stream.next_float("voxels") * 500_000),
            "confidence": round(0.6 + stream.next_float("overall") * 0.35, 4),
        }


class _DigestStream:
    """Deterministic float source keyed by a seed digest and a label.

    Keying by label rather than by call order means adding a region later does
    not shift the values of the existing ones — which keeps stored reference
    outputs valid across plugin revisions.
    """

    __slots__ = ("_seed",)

    def __init__(self, seed: str) -> None:
        self._seed = seed.encode("utf-8")

    def _hash(self, label: str) -> bytes:
        return hashlib.sha256(self._seed + b"|" + label.encode("utf-8")).digest()

    def next_float(self, label: str) -> float:
        """A stable value in [0, 1) derived from the seed and the label."""
        (value,) = struct.unpack(">Q", self._hash(label)[:8])
        return value / float(1 << 64)

    def derived(self, label: str) -> str:
        return "sha256:" + self._hash(label).hex()
