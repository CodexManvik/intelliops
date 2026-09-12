"""Guards the dependency-isolation boundary: importing common.stores (which the
non-ML services do via make_stores) must NOT pull in numpy/river/sklearn. The
leak was services/correlation/adapters/__init__.py eagerly importing the
River/Robust correlators (numpy+river at module scope)."""

from __future__ import annotations

import subprocess
import sys


def test_common_stores_does_not_import_heavy_deps():
    # Run in a fresh subprocess: import common.stores, then assert none of the
    # heavy ML deps landed in sys.modules. A subprocess is required — the pytest
    # process itself has numpy/river already loaded from other tests.
    code = (
        "import importlib, sys; "
        "importlib.import_module('common.stores'); "
        "leaked = {'numpy', 'river', 'sklearn'} & set(sys.modules); "
        "assert not leaked, f'common.stores leaked heavy deps: {leaked}'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, (
        f"import-boundary violated:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
