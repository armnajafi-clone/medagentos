# MedAgentOS Architecture

This document is the architecture review the design corpus asks for in
`10_CLAUDE_OPUS_EXECUTION/MASTER_PROMPT.md` and
`07_CLAUDE_OPUS_EXECUTION/FINAL_EXECUTION_GUIDE.md`. It records the layer
design, the decisions the corpus left open and how we closed them, and the
risks we knowingly accepted.

## 1. Layers

```
┌─────────────────────────────────────────────────────────┐
│ API Layer            medagentos.api                     │  HTTP only
├─────────────────────────────────────────────────────────┤
│ Service Layer        medagentos.services                │  use cases
├─────────────────────────────────────────────────────────┤
│ Core Runtime         medagentos.core                    │  domain independent
│   entities · workflow engine · capability registry      │
│   tool executor · artifact manager · trace engine       │
├─────────────────────────────────────────────────────────┤
│ Core Interfaces      medagentos.core.ports              │  protocols only
├─────────────────────────────────────────────────────────┤
│ Infrastructure       medagentos.infra                   │  db · storage · vector
└─────────────────────────────────────────────────────────┘

Medical knowledge:  plugins/*        (never in core)
Model access:       plugins/*/adapters  behind ModelAdapter
```

Dependency direction is strictly downward. Core defines *ports* (Python
`Protocol`s); infrastructure provides *adapters* for them; services wire the two
together. Core imports nothing from `api`, `services`, `infra`, or `plugins`.
`tests/unit/test_architecture.py` enforces this by static import analysis, so
the rule fails the build rather than the review.

## 2. Core systems

| System | Responsibility | Does not |
|---|---|---|
| **Case Manager** | lifecycle of `MedicalCase` and `Study` | interpret medical content |
| **Capability Registry** | what medical capabilities exist, their versions and I/O contracts | know what they mean |
| **Tool Executor** | run one tool call, enforce its contract, emit trace events | choose which tool |
| **Artifact Manager** | identity, versioning, provenance and location of every artifact | store bytes itself (delegates to `ObjectStore`) |
| **Trace Engine** | append-only record of every step, input digest, output digest, timing, error | interpret the trace |
| **Evaluation Engine** | model, workflow and trust metrics over completed runs | gate clinical use |
| **Workflow Runtime** | deterministic execution of a versioned step graph, including pause for human review | contain medical logic |

## 3. Decisions the corpus left open

The design documents specify *what* and *why* but not always *how*. These are
the choices we made, with the reasoning, so they can be challenged later.

| # | Open question | Decision | Rationale |
|---|---|---|---|
| D-01 | Implementation language | Python 3.11+ | The entire referenced ecosystem (MONAI, nnU-Net, MedSAM, PyTorch) is Python. |
| D-02 | How "domain independent" is Core? | Core has **zero third-party dependencies** | Makes ADR-001 mechanical rather than aspirational, keeps the test suite runnable with a bare interpreter, and prevents infrastructure from leaking upward. |
| D-03 | Persistence in dev and test | `Repository` port with three adapters: in-memory, SQLite (stdlib), PostgreSQL | The corpus mandates PostgreSQL for deployment; tests must not require a database. SQLite is the dev default, PostgreSQL is the compose/production default. |
| D-04 | Object storage in dev and test | `ObjectStore` port with local-filesystem and S3/MinIO adapters | Same reasoning as D-03. `docker compose` uses MinIO. |
| D-05 | Sync or async Core | Core is **synchronous**; the API layer is async, long work is handed to the worker | Deterministic, trivially testable execution. Concurrency is an infrastructure concern, not a workflow-semantics concern. |
| D-06 | Workflow definition format | Code-first: typed step functions registered into a versioned `WorkflowDefinition` | YAML would need its own type system and expression language. Code gives us types and tests for free. A declarative loader can be added later without changing the runtime. |
| D-07 | Trace format | Native `TraceEvent` model persisted to `trace_events`, OpenTelemetry-shaped fields | The corpus cites OpenTelemetry as *concepts*, not a dependency. An OTel exporter is additive. |
| D-08 | How does human approval pause a run? | Runs are durable state machines. A `human_review` step transitions the run to `AWAITING_APPROVAL` and returns; approval resumes from the recorded checkpoint. | ADR-006 requires review to be a workflow node, which requires runs to survive across requests. |
| D-09 | Reproducibility | Explicit run seed + content-addressed artifact digests + recorded adapter versions | The research direction measures reproducibility; it has to be a property of the runtime, not a claim. |
| D-10 | Plugin distribution | Filesystem discovery under `plugins/` for MVP, entry-point discovery later | Avoids premature packaging work before the plugin interface has stabilised. |

## 4. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Scope.** Six core systems, a plugin ecosystem, an agent and a benchmark is more than an MVP. | High | Build the brain MRI vertical slice end to end first; every later feature must be justified by that slice or a second plugin. |
| **Safety and regulatory posture.** Anything that emits medical findings invites clinical interpretation. | High | Not a medical device; enforced human review node; every finding carries provenance, uncertainty and stated limitations; refuse to ship a workflow that produces a report without a review node. |
| **Reproducibility vs GPU nondeterminism.** Seeded runs still diverge across CUDA versions and hardware. | Medium | Record hardware, driver, adapter and model versions in the trace; report reproducibility per environment rather than absolutely. |
| **Trace volume.** Full tracing of imaging workflows produces large event streams. | Medium | Digests and references in the trace, never payloads; payloads live in object storage. Retention policy deferred, gap documented. |
| **Plugin trust.** Plugins execute arbitrary code inside the runtime. | Medium | MVP treats plugins as trusted first-party code. Sandboxing is an explicit non-goal for now and must be solved before third-party plugins are accepted. |
| **PHI and de-identification.** The corpus does not address patient data handling. | High (deferred) | MVP is synthetic and public research data only. De-identification, audit and access control are a documented gap, not a solved problem. |

## 5. Non-goals

- Autonomous medical decision making.
- Clinical deployment or any claim of clinical validity.
- Microservices before the modular monolith is under strain.
- Kubernetes, GPU scheduling and enterprise security before MVP validation.
