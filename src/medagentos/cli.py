"""Command line interface.

Exists so the runtime can be exercised without Docker, a database or a web
server. Uses ``argparse`` from the standard library: the CLI must work on a
fresh clone with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .core.entities import RunStatus, TraceEvent
from .core.errors import MedAgentError
from .runtime import Runtime, build_runtime


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2

    runtime = build_runtime()
    try:
        return args.handler(runtime, args)
    except MedAgentError as exc:
        payload = exc.to_dict()
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
            if exc.details:
                print(f"  details: {json.dumps(exc.details)}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="medagentos",
        description="MedAgentOS: an open-source medical AI workflow runtime.",
    )
    subparsers = parser.add_subparsers(dest="command")

    workflows = subparsers.add_parser("workflows", help="list registered workflows")
    workflows.add_argument("--json", action="store_true")
    workflows.set_defaults(handler=_cmd_workflows)

    capabilities = subparsers.add_parser("capabilities", help="list registered capabilities")
    capabilities.add_argument("--json", action="store_true")
    capabilities.set_defaults(handler=_cmd_capabilities)

    plugins = subparsers.add_parser("plugins", help="list loaded plugins")
    plugins.add_argument("--json", action="store_true")
    plugins.set_defaults(handler=_cmd_plugins)

    run = subparsers.add_parser("run", help="execute a workflow")
    run.add_argument("workflow", help="workflow name")
    run.add_argument("--input", type=Path, help="path to a JSON file of inputs")
    run.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                     help="override a single input; repeatable")
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--approve", metavar="REVIEWER",
                     help="approve the human review gate as REVIEWER and resume")
    run.add_argument("--trace", action="store_true", help="print the execution trace")
    run.add_argument("--report", action="store_true", help="print the generated report")
    run.add_argument("--json", action="store_true")
    run.set_defaults(handler=_cmd_run)

    return parser


def _cmd_workflows(runtime: Runtime, args: argparse.Namespace) -> int:
    described = runtime.workflows.describe()
    if args.json:
        print(json.dumps(described, indent=2))
        return 0
    if not described:
        print("No workflows are registered.")
        return 0
    for workflow in described:
        print(f"{workflow['name']}@{workflow['version']}  [{workflow['plugin']}]")
        print(f"  {workflow['description']}")
        print(f"  required inputs: {', '.join(workflow['required_inputs']) or 'none'}")
        for step in workflow["steps"]:
            gate = "  <- human review gate" if step["requires_approval"] else ""
            print(f"    - {step['name']}{gate}")
    return 0


def _cmd_capabilities(runtime: Runtime, args: argparse.Namespace) -> int:
    described = runtime.capabilities.describe()
    if args.json:
        print(json.dumps(described, indent=2))
        return 0
    for capability in described:
        adapter = f"  via {capability['model_adapter']}" if capability["model_adapter"] else ""
        print(f"{capability['name']}@{capability['version']}  [{capability['plugin']}]{adapter}")
        print(f"  {capability['description']}")
    return 0


def _cmd_plugins(runtime: Runtime, args: argparse.Namespace) -> int:
    described = runtime.plugins.describe()
    if args.json:
        print(json.dumps(described, indent=2))
        return 0
    for plugin in described:
        print(f"{plugin['name']}@{plugin['version']}  ({plugin['specialty']})")
        print(f"  {plugin['description']}")
        print(f"  capabilities: {len(plugin['capabilities'])}, workflows: {len(plugin['workflows'])}")
        for limitation in plugin["limitations"]:
            print(f"  ! {limitation}")
    return 0


def _cmd_run(runtime: Runtime, args: argparse.Namespace) -> int:
    inputs, case_id, seed = _collect_inputs(runtime, args)

    result = runtime.engine.start(args.workflow, inputs, case_id=case_id, seed=seed)

    if result.is_paused and args.approve:
        state = dict(result.state)
        state["__approvals__"] = {result.run.paused_at_step: args.approve}
        result = runtime.engine.resume(result.run.id, state=state)

    events = runtime.uow.traces.list_events(result.run.id)

    if args.json:
        print(json.dumps(_as_json(result, events), indent=2, default=str))
    else:
        _print_summary(result, events, runtime)
        if args.trace:
            _print_trace(events)
        if args.report and "report" in result.state:
            print()
            print(result.state["report"].body)

    # Distinct exit codes: a run that paused for review has not failed, and a
    # script driving the CLI needs to tell the two apart.
    return _EXIT_CODES.get(result.status, 1)


#: Process exit code per terminal run status.
_EXIT_CODES = {
    RunStatus.SUCCEEDED: 0,
    RunStatus.FAILED: 1,
    RunStatus.AWAITING_APPROVAL: 2,
}


def _collect_inputs(runtime: Runtime, args: argparse.Namespace) -> tuple[dict, str | None, int]:
    inputs: dict[str, Any] = {}
    case_id: str | None = None
    seed = args.seed

    if args.input:
        document = json.loads(Path(args.input).read_text(encoding="utf-8"))
        inputs.update(document.get("inputs", {}))
        seed = args.seed or document.get("seed", 0)
        if "case" in document:
            from .core.entities import MedicalCase, Study

            case = runtime.uow.cases.save_case(MedicalCase(**document["case"]))
            case_id = case.id
            if "study" in document:
                runtime.uow.cases.save_study(Study(case_id=case.id, **document["study"]))

    for override in args.set:
        key, _, raw = override.partition("=")
        try:
            inputs[key] = json.loads(raw)
        except json.JSONDecodeError:
            inputs[key] = raw  # a bare string is the common case

    return inputs, case_id, seed


def _print_summary(result: Any, events: list[TraceEvent], runtime: Runtime) -> None:
    run = result.run
    print(f"run       {run.id}")
    print(f"workflow  {run.workflow_name}@{run.workflow_version}")
    print(f"status    {run.status.value}")
    print(f"seed      {run.seed}")
    if run.duration_ms is not None:
        print(f"duration  {run.duration_ms:.1f} ms")
    print(f"steps     {', '.join(run.completed_steps) or 'none'}")
    print(f"events    {len(events)}")

    artifacts = runtime.uow.artifacts.list_artifacts(run_id=run.id)
    if artifacts:
        print("artifacts")
        for artifact in artifacts:
            print(f"  {artifact.type.value:<10} {artifact.id}  {artifact.digest[:19]}…")

    if run.status is RunStatus.AWAITING_APPROVAL:
        print()
        print(f"Paused at {run.paused_at_step!r} for human review.")
        print("Re-run with --approve NAME to continue.")
    elif run.status is RunStatus.FAILED:
        print()
        print(f"Failed: [{run.error_code}] {run.error_message}")


def _print_trace(events: list[TraceEvent]) -> None:
    print()
    print("trace")
    for event in events:
        duration = f"{event.duration_ms:7.2f}ms" if event.duration_ms is not None else " " * 9
        marker = "!" if event.error_code else " "
        print(f"  {event.sequence:>3} {marker} {event.type.value:<20} {duration}  {event.name}")
        if event.error_code:
            print(f"        {event.error_code}: {event.error_message}")


def _as_json(result: Any, events: list[TraceEvent]) -> dict:
    run = result.run
    return {
        "run": {
            "id": run.id,
            "workflow": f"{run.workflow_name}@{run.workflow_version}",
            "status": run.status.value,
            "seed": run.seed,
            "duration_ms": run.duration_ms,
            "completed_steps": list(run.completed_steps),
            "paused_at_step": run.paused_at_step,
            "error": {"code": run.error_code, "message": run.error_message}
            if run.error_code
            else None,
            "outputs": run.outputs,
        },
        "trace": [
            {
                "sequence": e.sequence,
                "type": e.type.value,
                "name": e.name,
                "duration_ms": e.duration_ms,
                "error_code": e.error_code,
            }
            for e in events
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
