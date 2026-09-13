"""MedAgentOS: an open-source medical AI workflow runtime.

The package is layered, and the layering is load-bearing:

``medagentos.core``
    Domain-independent runtime. Entities, ports, and the execution engine.
    Imports nothing from the layers below and no third-party library.
``medagentos.services``
    Application use cases that wire core ports to infrastructure adapters.
``medagentos.api``
    HTTP transport. Contains no medical and no application logic.
``medagentos.infra``
    Adapters for databases, object storage and vector search.
``medagentos.agent``
    Orchestration layer that selects workflows. Never executes tools itself.
``medagentos.evaluation``
    Metrics over completed runs.

Medical knowledge lives in ``plugins/``, never here.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
