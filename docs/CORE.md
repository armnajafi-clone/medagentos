# The Core Runtime

`medagentos.core` is the domain-independent heart of the system. It has no
third-party dependencies and no medical knowledge, and both of those properties
are checked by `tests/unit/test_architecture.py`.

## Modules

| Module | Contents |
|---|---|
| `entities.py` | The seven entities of `CORE_DESIGN.md` plus `TraceEvent`, as frozen dataclasses |
| `ids.py` | Sortable prefixed identifiers and stable content digests |
| `errors.py` | The three error categories of `ERROR_HANDLING.md`, each carrying code, message and trace id |
| `ports.py` | The interfaces Core owns and infrastructure implements |
| `trace.py` | Trace Engine — the only producer of trace events |
| `artifacts.py` | Artifact Manager — identity, versioning, provenance, location |
| `capabilities.py` | Capability Registry and Tool Executor |
| `workflow.py` | Workflow Runtime — definitions, registry, deterministic engine |

## Writing a step

A step is a function from a `WorkflowContext` to a mapping merged into run state:

```python
def preprocess(context: WorkflowContext) -> dict:
    volume_id = context.require("volume_id")
    result = context.tools.call("brain_mri.preprocess", {"volume_id": volume_id})
    return {"normalised_id": result["artifact_id"]}
```

What a step may reach:

- `context.inputs` / `context.require(key)` / `context.get(key, default)` — run state
- `context.tools.call(name, payload)` — the **only** way to reach a model
- `context.artifacts` — create and read artifacts
- `context.rng` — a seeded RNG, so the step stays reproducible
- `context.trace` — extra trace events, if the automatic ones are not enough
- `context.request_approval(reason)` — pause the run for a human

What a step must not do: open a database connection, call an HTTP service
directly, or import a model library. Anything external arrives as a capability,
so that it is contract-checked and traced.

## Determinism

Given the same workflow version, the same inputs and the same seed, a run
executes the same steps and produces the same state digests. Three mechanisms
hold this up:

1. **Seeded RNG per run.** `context.rng` is `random.Random(run.seed)`.
2. **Order-independent digests.** `digest_json` sorts keys, so `{"a":1,"b":2}`
   and `{"b":2,"a":1}` hash identically.
3. **Content-addressed artifacts.** Identical bytes land on identical storage
   keys, so a re-run that produces the same output is provably the same output.

Model nondeterminism (CUDA kernels, hardware differences) is outside this
guarantee. The trace records adapter and model versions so that reproducibility
can be reported *per environment* rather than claimed absolutely.

## Tracing

Every step and every tool call emits a start event and exactly one outcome
event, including on failure. Events carry digests and references, never
payloads. There is no code path that executes a capability without tracing it:
`ToolExecutor.call` is the single entry point, and it traces unconditionally.

## Error handling

Three categories, matching `ERROR_HANDLING.md`:

| Category | Base class | Meaning |
|---|---|---|
| `api` | `ApiError` | the caller sent something invalid |
| `workflow` | `WorkflowError` | execution failed |
| `system` | `SystemError_` | database or storage failed |

A failing step does not propagate out of the engine. The run is marked `FAILED`,
persisted with its error code and message, traced, and returned — so the caller
always receives a trace id, which is what `ERROR_HANDLING.md` requires.

`ApprovalRequired` is the exception that is not a failure: it is the control-flow
signal that a run reached a human review node.

## Human review

A `WorkflowStep` marked `requires_approval=True` pauses the run *before* it
executes. The run is saved as `AWAITING_APPROVAL` with its completed steps
recorded, and `engine.resume(run_id, state=...)` continues from that checkpoint
with `state["__approvals__"] = {step_name: reviewer}`.

A `WorkflowDefinition` declared with `produces_report=True` and no approval step
raises at construction time. ADR-006 is a constructor invariant, not a
convention.
