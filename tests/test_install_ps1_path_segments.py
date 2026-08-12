"""Native-Windows regression coverage for the installer PATH migration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


INSTALL_PS1 = Path(__file__).resolve().parent.parent / "scripts" / "install.ps1"
POWERSHELL = next(
    (candidate for candidate in ("powershell", "pwsh") if shutil.which(candidate)),
    None,
)


def _function_body(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated function body for {name}")


@pytest.mark.windows_only
def test_path_update_uses_literal_case_insensitive_segments() -> None:
    install_dir = r"C:\Users\profile[qa]\hermes-agent"
    legacy_bin = install_dir + r"\venv\Scripts"
    hermes_bin = install_dir + r"\bin"
    sibling_legacy = legacy_bin + "-tools"
    sibling_hermes = hermes_bin + "-tools"
    test_path = ";".join((legacy_bin.upper(), sibling_legacy, sibling_hermes))

    source = INSTALL_PS1.read_text(encoding="ascii")
    function = _function_body(source, "Get-HermesUserPathUpdate")
    command = function + "\n" + r"""
$update = Get-HermesUserPathUpdate `
    -CurrentPath $env:HERMES_TEST_CURRENT_PATH `
    -HermesBin $env:HERMES_TEST_BIN `
    -LegacyBin $env:HERMES_TEST_LEGACY `
    -RemoveLegacy $true
$update | ConvertTo-Json -Compress
"""
    env = os.environ | {
        "HERMES_TEST_CURRENT_PATH": test_path,
        "HERMES_TEST_BIN": hermes_bin,
        "HERMES_TEST_LEGACY": legacy_bin,
    }
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    update = json.loads(result.stdout)
    entries = update["Path"].split(";")
    assert update["RemovedLegacy"] is True
    assert update["AddedHermes"] is True
    assert entries[0].casefold() == hermes_bin.casefold()
    assert all(entry.casefold() != legacy_bin.casefold() for entry in entries)
    assert sibling_legacy in entries
    assert sibling_hermes in entries
