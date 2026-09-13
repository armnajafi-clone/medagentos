# Implementation Roadmap

Built incrementally, vertical slice first. Every phase ships code, tests and
documentation together; a phase is not done until `scripts/check.sh` passes.

Legend: ✅ done · 🚧 in progress · ⬜ planned

| Phase | Branch | Contents | Status |
|---|---|---|---|
| **0. Repository engineering** | `feature/repo-scaffold` | Structure per `REPOSITORY_STRUCTURE.md`, licence, README, architecture review, roadmap, contribution guide, check script | ✅ |
| **1. Core runtime** | `feature/core-runtime` | Domain entities, ports, trace engine, artifact manager, capability registry, tool executor, workflow engine, architecture-enforcing tests | ✅ |
| **2. Plugin SDK + brain MRI slice** | `feature/brain-mri-slice` | Plugin contract, discovery, model-adapter layer, the eight-step reference workflow on a deterministic synthetic adapter | ✅ |
| **3. Persistence and storage** | `feature/persistence` | Repository and ObjectStore ports with in-memory, SQLite and local-filesystem adapters; PostgreSQL/MinIO adapters behind extras | ✅ |
| **4. Service and API layer** | `feature/api-layer` | Case, workflow, trace and artifact services; the five endpoints of `API_CONTRACT.md`; structured error handling with trace ids | ✅ |
| **5. Evaluation engine** | `feature/evaluation` | Model metrics (Dice, IoU, AUROC), workflow metrics, trust metrics (trace completeness, evidence coverage, unsupported claims) | ✅ |
| **6. Agent orchestration** | `feature/agent-layer` | Planner that selects a workflow and reports missing inputs; never calls a model or a tool directly | ✅ |
| **7. Docker and CI** | `feature/docker-ci` | Compose stack (api, worker, postgres, minio, qdrant, ui), Dockerfiles, GitHub Actions pipeline | ✅ |
| **8. Human review loop** | `feature/human-review` | Durable pause/resume, approval API, rejection with reasons | ⬜ |
| **9. Real model adapters** | `feature/monai-adapter` | MONAI preprocessing and an nnU-Net/MedSAM segmentation adapter behind the existing interface | ⬜ |
| **10. Vector evidence** | `feature/vector-evidence` | Qdrant-backed evidence retrieval as an optional capability | ⬜ |
| **11. Benchmark** | `feature/medworkflowbench` | The `MedWorkflowBench` comparison of a direct model pipeline against the traced workflow | ⬜ |

## Deferred, deliberately

Recorded so they are choices rather than oversights — see the risk table in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

- PHI handling, de-identification, access control and audit.
- Plugin sandboxing and third-party plugin trust.
- Trace retention and compaction policy.
- Kubernetes, GPU scheduling, multi-tenancy.
- A real UI. The `ui` compose service is a placeholder until the API stabilises.
