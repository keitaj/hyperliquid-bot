"""Tests for the hyperliquid-python-sdk minimum-version startup gate.

The gate lives at module scope in ``bot.py`` and runs at import time.
Importing ``bot`` directly therefore has side effects (it tries to read
the installed SDK version) and pulls in the rest of the bot. Here we
import only the gate's helpers via a lazy importer that loads bot.py's
source and extracts the two functions, avoiding the heavy import graph
while still exercising the real implementation.
"""

import importlib.util
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — load the two gate functions from bot.py without executing
# the module body (which would import hyperliquid and call _assert_sdk_version).
# ---------------------------------------------------------------------------

_BOT_PATH = Path(__file__).resolve().parent.parent / "bot.py"


def _extract_gate_module():
    """Compile bot.py's gate block as a standalone module.

    We slice the file up to (but excluding) the ``_assert_sdk_version()``
    invocation at module scope, so the helpers are defined but not run.
    """
    source = _BOT_PATH.read_text()
    marker = "\n_assert_sdk_version()\n"
    idx = source.index(marker)
    head = source[:idx]
    spec = importlib.util.spec_from_loader("bot_gate_under_test", loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(head, str(_BOT_PATH), "exec"), module.__dict__)
    return module


@pytest.fixture(scope="module")
def gate():
    return _extract_gate_module()


def _parse(gate, v: str):
    return gate._parse_version_tuple(v)


def _run_assert(gate, version_or_exc):
    """Return a patch context-manager for the gate's ``_pkg_version``.

    Uses ``patch.object`` because the gate module is dynamically built and
    not registered in ``sys.modules``, so the string-target form of
    ``patch`` cannot locate it.
    """
    if isinstance(version_or_exc, Exception):
        return patch.object(gate, "_pkg_version", side_effect=version_or_exc)
    return patch.object(gate, "_pkg_version", return_value=version_or_exc)


# ---------------------------------------------------------------------------
# _parse_version_tuple
# ---------------------------------------------------------------------------

class TestParseVersionTuple:
    def test_standard_three_components(self, gate):
        assert _parse(gate, "0.23.0") == (0, 23, 0)
        assert _parse(gate, "1.2.3") == (1, 2, 3)
        assert _parse(gate, "10.20.30") == (10, 20, 30)

    def test_pre_release_suffix_on_patch(self, gate):
        # "0.23.0rc1" → digits before suffix: 0, 23, 0
        assert _parse(gate, "0.23.0rc1") == (0, 23, 0)
        assert _parse(gate, "0.23.0a2") == (0, 23, 0)
        assert _parse(gate, "0.23.0.dev5") == (0, 23, 0)

    def test_pre_release_suffix_on_minor(self, gate):
        # "0.23a1.0" → minor digits "23"; patch "0"
        assert _parse(gate, "0.23a1.0") == (0, 23, 0)

    def test_two_components_pads_to_three(self, gate):
        assert _parse(gate, "1.0") == (1, 0, 0)
        assert _parse(gate, "2.5") == (2, 5, 0)

    def test_one_component_pads_to_three(self, gate):
        assert _parse(gate, "1") == (1, 0, 0)

    def test_extra_components_ignored(self, gate):
        # Anything beyond the first 3 is dropped.
        assert _parse(gate, "0.23.0.1") == (0, 23, 0)
        assert _parse(gate, "1.2.3.4.5") == (1, 2, 3)

    def test_non_numeric_component_yields_zero(self, gate):
        # A component with no leading digits → 0.
        assert _parse(gate, "0.foo.0") == (0, 0, 0)
        assert _parse(gate, "0.23.bar") == (0, 23, 0)

    def test_ordering_consistent_with_semver(self, gate):
        # Sanity: ordering used by the gate must match human intuition.
        assert _parse(gate, "0.22.0") < _parse(gate, "0.23.0")
        assert _parse(gate, "0.23.0") < _parse(gate, "0.23.1")
        assert _parse(gate, "0.23.0") < _parse(gate, "1.0.0")
        assert _parse(gate, "0.23.0") == _parse(gate, "0.23.0")


# ---------------------------------------------------------------------------
# _assert_sdk_version
# ---------------------------------------------------------------------------

class TestAssertSdkVersion:
    def test_minimum_version_passes(self, gate, capsys):
        """SDK == MINIMUM_HYPERLIQUID_SDK_VERSION → no exit."""
        min_str = ".".join(str(n) for n in gate.MINIMUM_HYPERLIQUID_SDK_VERSION)
        with _run_assert(gate, min_str):
            # Must NOT raise SystemExit.
            gate._assert_sdk_version()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_above_minimum_passes(self, gate, capsys):
        """SDK > minimum → no exit."""
        major, minor, patch_ = gate.MINIMUM_HYPERLIQUID_SDK_VERSION
        higher = f"{major}.{minor}.{patch_ + 1}"
        with _run_assert(gate, higher):
            gate._assert_sdk_version()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_much_above_minimum_passes(self, gate, capsys):
        """SDK far above minimum (e.g. major bump) → no exit."""
        major, _, _ = gate.MINIMUM_HYPERLIQUID_SDK_VERSION
        higher = f"{major + 1}.0.0"
        with _run_assert(gate, higher):
            gate._assert_sdk_version()
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_below_minimum_exits_with_status_2(self, gate, capsys):
        """SDK < minimum → SystemExit(2) with remediation message."""
        major, minor, patch_ = gate.MINIMUM_HYPERLIQUID_SDK_VERSION
        # Construct a version strictly less than the minimum.
        if patch_ > 0:
            lower = f"{major}.{minor}.{patch_ - 1}"
        elif minor > 0:
            lower = f"{major}.{minor - 1}.0"
        else:
            lower = f"{major - 1}.0.0" if major > 0 else "0.0.0rc1"  # pragma: no cover

        with _run_assert(gate, lower):
            with pytest.raises(SystemExit) as exc_info:
                gate._assert_sdk_version()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "hyperliquid-python-sdk" in captured.err
        assert lower in captured.err
        # Remediation hint must be present.
        assert "pip install" in captured.err

    def test_package_not_installed_exits_with_status_2(self, gate, capsys):
        """Missing SDK package → SystemExit(2)."""
        with _run_assert(gate, PackageNotFoundError("hyperliquid-python-sdk")):
            with pytest.raises(SystemExit) as exc_info:
                gate._assert_sdk_version()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "not installed" in captured.err
        assert "pip install" in captured.err

    def test_pre_release_of_minimum_passes(self, gate, capsys):
        """0.23.0rc1 → parses as (0, 23, 0); passes minimum (0, 23, 0)."""
        min_str = ".".join(str(n) for n in gate.MINIMUM_HYPERLIQUID_SDK_VERSION)
        with _run_assert(gate, f"{min_str}rc1"):
            gate._assert_sdk_version()
        captured = capsys.readouterr()
        assert captured.err == ""


# ---------------------------------------------------------------------------
# Constant invariant
# ---------------------------------------------------------------------------

class TestMinimumConstant:
    def test_constant_is_three_tuple_of_ints(self, gate):
        v = gate.MINIMUM_HYPERLIQUID_SDK_VERSION
        assert isinstance(v, tuple)
        assert len(v) == 3
        assert all(isinstance(n, int) and n >= 0 for n in v)

    def test_constant_matches_pyproject(self, gate):
        """The gate's minimum must match the pin in pyproject.toml so that
        a `pip install .` deployment satisfies the gate by construction."""
        pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        # Find a line like:    "hyperliquid-python-sdk==X.Y.Z",
        pinned = None
        for raw in pyproject.splitlines():
            line = raw.strip().strip(",").strip('"').strip("'")
            if line.startswith("hyperliquid-python-sdk"):
                # Strip env-marker suffix (e.g. "; python_version>=3.11") just in case.
                line = line.split(";", 1)[0].strip()
                for sep in ("==", ">=", "~="):
                    if sep in line:
                        _, pinned_str = line.split(sep, 1)
                        pinned = pinned_str.strip()
                        break
                if pinned is not None:
                    break
        assert pinned is not None, "could not find hyperliquid-python-sdk pin in pyproject.toml"
        pinned_tuple = gate._parse_version_tuple(pinned)
        assert pinned_tuple >= gate.MINIMUM_HYPERLIQUID_SDK_VERSION, (
            f"pyproject.toml pins {pinned} but bot.py requires "
            f">= {gate.MINIMUM_HYPERLIQUID_SDK_VERSION}"
        )
