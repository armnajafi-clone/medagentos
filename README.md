# MedAgentOS

**An open-source medical AI workflow runtime.**

Models for medical imaging are everywhere. The missing layer is the *reliable
execution infrastructure* around them: orchestration, artifact tracking,
traceability, evaluation, and human review. MedAgentOS is that layer.

> MedAgentOS is **not** a medical chatbot, **not** an autonomous doctor, and
> **not** a clinical diagnosis system. It is research and engineering
> infrastructure. Every workflow that produces a report ends at a human.

## Core philosophy

**Workflow first, agent second.** A workflow is a deterministic, versioned,
fully traced graph of steps. An agent may *choose* a workflow; it never replaces
one, and it never touches a model directly.

## Architecture

```
User / UI
    |
API Layer            HTTP only, no medical logic
    |
Service Layer        application use cases
    |
Workflow Runtime     deterministic execution + tracing
    |
Core Systems         Case Manager · Capability Registry · Tool Executor
                     Artifact Manager · Trace Engine · Evaluation Engine
    |
Medical Plugins      all medical knowledge lives here
    |
Model Adapters       version-tracked, swappable
    |
AI Models
```

Two rules hold the design together:

1. **Core is domain independent.** `medagentos.core` knows nothing about MRI,
   diagnosis, or any specialty. Adding chest X-ray support must not change Core.
2. **Dependencies point one way.** `api -> services -> core interfaces -> infra`.
   Both rules are executable: see `tests/unit/test_architecture.py`.

## Status

Early alpha, built incrementally. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for
what is implemented and what is next, and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for the design and its open questions.

## Quick start

The Core runtime has **zero third-party dependencies** — it runs on a bare
Python 3.11+ interpreter.

```bash
git clone https://github.com/armnajafi-clone/medagentos.git
cd medagentos
python -m unittest discover -s tests -t .      # run the test suite, no install needed
```

To run the reference workflow end to end on synthetic data:

```bash
PYTHONPATH=src:plugins python -m medagentos.cli run brain_mri_reference \
    --input examples/brain_mri/case.json
```

The full stack (API, worker, PostgreSQL, MinIO, Qdrant, UI) is Docker-first:

```bash
docker compose up
```

## Reference workflow: brain MRI

A complete vertical slice that exercises every layer of the architecture:

```
MRI input -> validation -> preprocessing -> segmentation -> finding extraction
          -> evidence retrieval -> report generation -> human review
```

It ships with a deterministic synthetic model adapter so the whole pipeline is
reproducible on CPU with no downloads. Real adapters (MONAI, nnU-Net, MedSAM)
plug in behind the same interface.

## Documentation

| Document | Contents |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design, layers, and the risks we accepted |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Implementation phases and current status |
| [`docs/design/`](docs/design/) | The original design corpus this system is built from |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Workflow, architectural rules, definition of done |

## License

Apache-2.0. See [`LICENSE`](LICENSE).
