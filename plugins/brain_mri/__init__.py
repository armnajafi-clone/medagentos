"""Brain MRI plugin: the reference vertical slice.

BRAIN_MRI_PIPELINE.md asks for one complete medical AI workflow that exercises
every layer of the architecture:

    MRI input -> validation -> preprocessing -> segmentation
              -> finding extraction -> evidence retrieval
              -> report generation -> human review

This is a **research workflow, not a clinical diagnosis system**. It ships with
a deterministic synthetic segmentation adapter so the whole pipeline runs
reproducibly on CPU with no model download, which is what makes it usable as a
reference and as a test fixture. A real MONAI or nnU-Net adapter drops in behind
the same ``ModelAdapter`` interface without touching the workflow.
"""

from .plugin import BrainMRIPlugin

__all__ = ["BrainMRIPlugin"]
