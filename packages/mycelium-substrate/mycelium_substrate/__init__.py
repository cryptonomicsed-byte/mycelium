"""mycelium_substrate -- compatibility shim (P2-01 merge of mycelium-tools).

mycelium-tools used to ship its own copy of the substrate core under the
``mycelium_substrate`` package name. That copy was dropped when the tools
moved into this repo: the real substrate is the ``mycelium`` package at the
root of this repository. This shim keeps every old import working by making
``mycelium_substrate.<name>`` resolve to the *same module object* as
``mycelium.<name>``::

    from mycelium_substrate import core          # -> mycelium.core
    from mycelium_substrate.cli import cmd_cycle # -> mycelium.cli.cmd_cycle
    import mycelium_substrate.miners.registry    # -> mycelium.miners.registry

Because the alias is the identical module (not a copy), module-level state
such as ``core.DB_PATH`` and ``unittest.mock.patch`` targets are shared
between the two names.

The only module that physically lives here is ``http_server`` -- the small
stdlib HTTP trace ingest/query server (``mycelium-http``) that mycelium-tools
had and the ``mycelium`` package has no Python equivalent of (the full
product surface is the Go ``gateway/``). It runs on top of ``mycelium.core``.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import os
import sys
from pathlib import Path

#: Submodules that physically live in this shim package (never aliased).
LOCAL_MODULES = frozenset({"http_server"})

_PREFIX = __name__ + "."


def _ensure_mycelium_importable() -> None:
    """Make the real ``mycelium`` package importable.

    Normally it already is (the repo root is on sys.path, or install.sh
    put it there). Otherwise fall back to MYCELIUM_REPO, then to the repo
    this shim lives in (packages/mycelium-substrate/mycelium_substrate/ ->
    three levels up), which is what an editable install gives you.
    """
    if importlib.util.find_spec("mycelium") is not None:
        return
    candidates = []
    if os.environ.get("MYCELIUM_REPO"):
        candidates.append(Path(os.environ["MYCELIUM_REPO"]).expanduser())
    candidates.append(Path(__file__).resolve().parents[3])
    for root in candidates:
        if (root / "mycelium" / "core.py").is_file():
            sys.path.insert(0, str(root))
            importlib.invalidate_caches()
            return
    raise ImportError(
        "mycelium_substrate is a compatibility shim for the 'mycelium' package, "
        "which is not importable. Run install.sh from the mycelium repo, add the "
        "repo root to PYTHONPATH, or set MYCELIUM_REPO=/path/to/mycelium."
    )


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target: str) -> None:
        self.target = target
        self._real_spec = None

    def create_module(self, spec):
        # Return the real module itself; the import system will register it
        # in sys.modules under the alias name and bind it on the parent.
        module = importlib.import_module(self.target)
        self._real_spec = module.__spec__
        return module

    def exec_module(self, module) -> None:
        # Already executed by import_module. The import machinery has just
        # overwritten module.__spec__ with the alias spec -- put the real one
        # back so mycelium.* modules keep their true identity (reload,
        # pickling, relative imports, subprocess `-m` all depend on it).
        if self._real_spec is not None:
            module.__spec__ = self._real_spec


class _AliasFinder(importlib.abc.MetaPathFinder):
    """Maps ``mycelium_substrate.X[.Y]`` -> ``mycelium.X[.Y]``."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_PREFIX):
            return None
        tail = fullname[len(_PREFIX):]
        if tail.split(".", 1)[0] in LOCAL_MODULES:
            return None  # real file in this package
        real = "mycelium." + tail
        if importlib.util.find_spec(real) is None:
            return None
        return importlib.util.spec_from_loader(fullname, _AliasLoader(real))


_ensure_mycelium_importable()

if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

import mycelium as _mycelium  # noqa: E402

__version__ = getattr(_mycelium, "__version__", "0.0.0")
__doc_upstream__ = _mycelium.__doc__


def __getattr__(name: str):
    """``mycelium_substrate.core`` etc. without an explicit submodule import."""
    if name.startswith("__"):
        raise AttributeError(name)
    try:
        return importlib.import_module(_PREFIX + name)
    except ModuleNotFoundError as e:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from e
