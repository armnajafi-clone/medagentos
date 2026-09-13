"""Plugin SDK.

PLUGIN_SDK.md: a plugin carries metadata, capabilities, workflow definitions,
tools, model adapters and tests, and extends the system without modifying Core.

This package is the contract a plugin implements and the discovery mechanism the
runtime uses to find one. It sits outside ``core`` deliberately: Core must not
know that plugins exist, only that capabilities and workflows can be registered.
"""

from .base import MedicalPlugin, PluginMetadata
from .discovery import PluginLoader, discover_plugins
from .model_adapter import AdapterInfo, ModelAdapter

__all__ = [
    "AdapterInfo",
    "MedicalPlugin",
    "ModelAdapter",
    "PluginLoader",
    "PluginMetadata",
    "discover_plugins",
]
