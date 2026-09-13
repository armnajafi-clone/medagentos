# Writing a plugin

A plugin adds a medical capability without changing Core. If adding a specialty
requires a Core edit, the design has failed — that is what
`tests/unit/test_architecture.py` exists to catch.

## Anatomy

```
plugins/your_plugin/
    __init__.py          exports the plugin class
    plugin.py            metadata, capabilities, workflows
    capabilities.py      the medical logic
    workflow.py          the pipeline that composes it
    adapters/            model adapters
    tests/               the plugin's own tests
```

## The three things a plugin provides

```python
class YourPlugin(MedicalPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="chest_xray",
            version="0.1.0",
            description="...",
            specialty="thoracic radiology",
            modalities=("CR", "DX"),
            limitations=("Research use only.", "Frontal projections only."),
        )

    def capabilities(self) -> list[Capability]: ...
    def workflows(self) -> list[WorkflowDefinition]: ...
```

`limitations` is not decoration. Every report the plugin produces states them,
because `SAFETY_MODEL.md` requires limitations to be explicit rather than
inferred.

## Capabilities declare a contract

```python
Capability(
    name="chest_xray.classify",
    version="0.1.0",
    description="Classify frontal chest radiographs.",
    handler=classify,
    inputs=(IOField("image_digest", "string"), IOField("view", "string")),
    outputs=(IOField("labels", "array"), IOField("confidence", "number")),
    model_adapter=adapter.info.qualified_name,
)
```

The Tool Executor validates inputs *before* the handler runs and outputs after,
and traces both. A handler that receives invalid input is a bug in the contract,
not something the handler needs to defend against.

## Models go behind an adapter

Never call a model from a workflow step. The chain is
`Capability -> Tool -> ModelAdapter -> Model`, which is what lets a model be
replaced, version-tracked and traced:

```python
class YourAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(AdapterInfo(
            id="chest_xray.densenet", version="0.1.0",
            framework="pytorch", device="cuda", deterministic=False,
        ))

    def load(self) -> None:
        import torch                      # heavy imports live here, not at module scope
        self._model = torch.load(...)

    def predict(self, payload: dict) -> dict:
        return {"labels": [...], "confidence": 0.9}
```

`load` runs once, on first use. `GPU_STRATEGY.md` is explicit that a container
loads its model at start and then serves inferences; reloading per request is
the mistake this design prevents.

## Ship a synthetic adapter too

The brain MRI plugin's default adapter is synthetic and deterministic. This is a
pattern worth copying:

- CI can run the whole pipeline with no GPU and no download.
- The claim that models are swappable is demonstrated, not asserted.
- Reproducibility experiments get a component whose output is exactly stable, so
  any divergence measured elsewhere is attributable to the real model.

## Findings must be supportable

A `Finding` with no `evidence` is counted as a trust defect by the evaluation
engine. Give every finding the measurement or reference it rests on:

```python
Finding(
    code="opacity.lower_lobe",
    description="...",
    confidence=0.82,
    evidence=(Evidence(kind="measurement", source=adapter_id, artifact_id=mask.id, data={...}),),
    limitations=PLUGIN_LIMITATIONS,
    produced_by=f"chest_xray.classify@0.1.0 via {adapter_id}",
)
```

## Workflows that produce reports need a review gate

```python
WorkflowDefinition(
    name="chest_xray_reference",
    version="0.1.0",
    description="...",
    produces_report=True,
    steps=(..., WorkflowStep("human_review", human_review, requires_approval=True)),
)
```

Omit the gate and the definition raises at construction. ADR-006 is a
constructor invariant.

## Discovery

Drop the package under `plugins/` and it is found. `PluginLoader` refuses a
package that defines more than one plugin class rather than guessing which one
was meant.
