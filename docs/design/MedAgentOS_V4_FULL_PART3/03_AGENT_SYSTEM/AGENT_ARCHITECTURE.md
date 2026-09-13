# Agent Architecture

Agent is an orchestration layer.

Responsibilities:

- understand user intent
- select workflow
- choose available capabilities
- request missing information


Agent does not:

- diagnose
- replace clinicians
- bypass safety rules


Architecture:

Planner Agent

↓

Workflow Runtime

↓

Medical Tools
