"""Infrastructure adapters.

Every module here implements a port from ``medagentos.core.ports``. Core never
imports this package; services choose an adapter and hand it to Core.

Adapters that need a third-party library import it lazily inside the adapter, so
that a runtime without the optional extra installed still starts.
"""
