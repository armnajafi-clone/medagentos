"""The architectural rules, enforced by static import analysis.

CONTRIBUTING.md states these rules; this module makes them fail the build rather
than depend on a reviewer noticing. It parses the source with ``ast`` instead of
importing it, so a violation is caught even in a module nothing imports yet.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import ROOT

SRC = ROOT / "src" / "medagentos"
PLUGINS = ROOT / "plugins"

#: Modules the standard library provides. Core may use these and nothing else.
_STDLIB = set(__import__("sys").stdlib_module_names)


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imports(path: Path) -> list[tuple[str, int]]:
    """Return ``(module, lineno)`` for every import in a file.

    Relative imports are resolved to their absolute dotted path so that
    ``from ..core import x`` inside ``medagentos.infra`` is recognised as
    ``medagentos.core``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = path.relative_to(ROOT / "src").with_suffix("").parts
    if package_parts[-1] == "__init__":
        package_parts = package_parts[:-1]

    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - node.level + 1]
                module = ".".join([*base, node.module] if node.module else list(base))
            else:
                module = node.module or ""
            found.append((module, node.lineno))
    return found


class TestCoreIsDomainIndependent(unittest.TestCase):
    """ADR-001: medical logic belongs in plugins."""

    #: Words that betray a specialty leaking into Core. ``modality`` and
    #: ``medical`` are permitted: a domain-independent runtime is still allowed
    #: to know it orchestrates medical cases without knowing any medicine.
    FORBIDDEN = (
        "mri", "ct_scan", "xray", "x_ray", "dicom", "nifti", "segmentation_model",
        "lesion", "tumor", "tumour", "diagnos", "monai", "nnunet", "medsam",
        "radiolog", "patholog",
    )

    def test_core_source_names_no_specialty(self) -> None:
        for path in _python_files(SRC / "core"):
            text = path.read_text(encoding="utf-8")
            # Docstrings may cite the design documents by name; only executable
            # code is checked, which is where a real leak would live.
            code = "\n".join(
                line for line in text.splitlines() if not line.lstrip().startswith("#")
            )
            module = ast.parse(code)
            for node in ast.walk(module):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    node.value = ""
            stripped = ast.unparse(module).lower()
            for word in self.FORBIDDEN:
                with self.subTest(file=path.name, word=word):
                    self.assertNotIn(
                        word, stripped, f"{path.relative_to(ROOT)} leaks medical domain knowledge"
                    )

    def test_core_imports_no_third_party_library(self) -> None:
        """D-02: Core runs on a bare interpreter, so ADR-001 stays mechanical."""
        for path in _python_files(SRC / "core"):
            for module, lineno in _imports(path):
                root = module.split(".", 1)[0]
                if not root or root == "medagentos":
                    continue
                with self.subTest(file=path.name, module=module):
                    self.assertIn(
                        root,
                        _STDLIB,
                        f"{path.relative_to(ROOT)}:{lineno} imports the third-party "
                        f"module {module!r}; Core must stay dependency-free",
                    )


class TestDependencyDirection(unittest.TestCase):
    """SERVICE_ARCHITECTURE.md: API -> Services -> Core Interfaces -> Infrastructure."""

    #: Layer -> the sibling layers it may not import.
    FORBIDDEN_IMPORTS = {
        "core": ("medagentos.api", "medagentos.services", "medagentos.infra",
                 "medagentos.agent", "medagentos.evaluation"),
        "services": ("medagentos.api",),
        "infra": ("medagentos.api", "medagentos.services", "medagentos.agent"),
        "agent": ("medagentos.api", "medagentos.infra"),
        "evaluation": ("medagentos.api", "medagentos.infra"),
    }

    def test_layers_only_depend_downward(self) -> None:
        for layer, forbidden in self.FORBIDDEN_IMPORTS.items():
            directory = SRC / layer
            if not directory.exists():
                continue
            for path in _python_files(directory):
                for module, lineno in _imports(path):
                    for banned in forbidden:
                        with self.subTest(file=str(path.relative_to(ROOT)), module=module):
                            self.assertFalse(
                                module == banned or module.startswith(banned + "."),
                                f"{path.relative_to(ROOT)}:{lineno} imports {module!r}; "
                                f"the {layer!r} layer must not depend on {banned!r}",
                            )

    def test_core_never_imports_a_plugin(self) -> None:
        plugin_packages = {p.name for p in PLUGINS.iterdir() if p.is_dir()} if PLUGINS.exists() else set()
        for path in _python_files(SRC):
            if "core" not in path.parts:
                continue
            for module, lineno in _imports(path):
                with self.subTest(file=path.name):
                    self.assertNotIn(
                        module.split(".", 1)[0],
                        plugin_packages,
                        f"{path.relative_to(ROOT)}:{lineno} imports plugin {module!r}",
                    )


class TestPluginsStayOutOfCore(unittest.TestCase):
    """A new specialty must not require a Core change."""

    def test_plugins_only_use_the_public_core_surface(self) -> None:
        if not PLUGINS.exists():
            self.skipTest("no plugins yet")
        allowed_prefixes = ("medagentos.core", "medagentos.plugin")
        for path in _python_files(PLUGINS):
            for module, lineno in _imports(path):
                if not module.startswith("medagentos"):
                    continue
                with self.subTest(file=str(path.relative_to(ROOT))):
                    self.assertTrue(
                        module.startswith(allowed_prefixes),
                        f"{path.relative_to(ROOT)}:{lineno} imports {module!r}; a plugin may "
                        f"only use the core and plugin SDK surface",
                    )


if __name__ == "__main__":
    unittest.main()
