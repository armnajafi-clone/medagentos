# Contributing to MedAgentOS

MedAgentOS is research and engineering infrastructure for medical AI workflows.
It is **not** a clinical product. Contributions must respect that boundary.

## Development workflow

Every change follows the flow defined in `docs/design/.../DEVELOPMENT_WORKFLOW.md`:

```
Issue -> Design -> Implementation -> Tests -> Documentation -> Review
```

Branch names:

| Prefix     | Use                        |
|------------|----------------------------|
| `feature/` | new capability             |
| `bugfix/`  | defect repair              |
| `docs/`    | documentation only         |

Commit messages describe **intent**, not mechanics.

## Definition of done

A change is not done until all of the following hold:

1. Code implements the behaviour.
2. Tests cover it (unit, and integration/workflow where it crosses a boundary).
3. Documentation is updated.
4. `scripts/check.sh` passes.

## Architectural rules

These are enforced by review and by `tests/unit/test_architecture.py`:

- **Core is domain independent.** `medagentos.core` must not import MRI logic,
  diagnosis rules, model code, or any medical specialty.
- **Dependency direction is one-way:** `api -> services -> core interfaces -> infra`.
  Core never imports from `api`, `services`, or `infra`.
- **Medical logic lives in plugins.** Adding a specialty must not require a Core change.
- **Models are reached through adapters**, never called directly from a workflow.
- **Every execution emits trace events.** An untraced side effect is a bug.

## Safety rules

Outputs that describe medical content must carry provenance, uncertainty, and
stated limitations, and workflows that produce a report must include a human
review node. See `docs/design/.../SAFETY_MODEL.md`.

## Running checks

```bash
scripts/check.sh          # lint (if available) + type check (if available) + tests
python -m pytest          # tests only, when pytest is installed
python -m unittest discover -s tests -t .   # tests with the standard library alone
```
