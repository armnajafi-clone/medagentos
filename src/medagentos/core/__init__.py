"""Domain-independent runtime core.

This package must never import from ``medagentos.api``, ``medagentos.services``,
``medagentos.infra``, or any plugin, and must never depend on a third-party
library. ``tests/unit/test_architecture.py`` enforces both rules.
"""
