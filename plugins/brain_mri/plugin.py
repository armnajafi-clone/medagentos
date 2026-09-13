"""Plugin registration for the brain MRI vertical slice."""

from __future__ import annotations

from medagentos.core.capabilities import Capability
from medagentos.core.workflow import WorkflowDefinition
from medagentos.plugin import MedicalPlugin, PluginMetadata

from .capabilities import build_capabilities
from .workflow import WORKFLOW_LIMITATIONS, build_workflow


class BrainMRIPlugin(MedicalPlugin):
    """Neuroimaging capabilities and the reference workflow that composes them."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="brain_mri",
            version="0.1.0",
            description=(
                "Reference brain MRI research workflow demonstrating the full "
                "MedAgentOS architecture on a deterministic synthetic model."
            ),
            specialty="neuroimaging",
            modalities=("MR",),
            references=(
                "MONAI: https://monai.io",
                "nnU-Net: Isensee et al., Nature Methods 2021",
                "MedSAM: Ma et al., Nature Communications 2024",
            ),
            limitations=WORKFLOW_LIMITATIONS,
        )

    def capabilities(self) -> list[Capability]:
        return build_capabilities()

    def workflows(self) -> list[WorkflowDefinition]:
        return [build_workflow()]
