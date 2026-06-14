"""Tests for the pip-audit JSON to SARIF 2.1.0 converter."""

from __future__ import annotations

import io
import json
import pathlib
import sys

import pytest

# Add scripts directory to path so we can import the converter module
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import pip_audit_to_sarif  # noqa: E402


FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "pip_audit_sample.json"
)


def test_convert_produces_valid_sarif_version_and_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convert with one vuln: SARIF version is 2.1.0, tool name is pip-audit, one result."""
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)

    pip_audit_to_sarif.convert(str(FIXTURE_PATH))

    sarif = json.loads(captured.getvalue())
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "pip-audit"
    assert len(sarif["runs"][0]["results"]) == 1
    assert sarif["runs"][0]["results"][0]["ruleId"] == "GHSA-j8r2-6x86-q33q"


def test_convert_result_message_contains_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Result message.text contains package name, version, vuln id, and fix version."""
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)

    pip_audit_to_sarif.convert(str(FIXTURE_PATH))

    sarif = json.loads(captured.getvalue())
    text = sarif["runs"][0]["results"][0]["message"]["text"]
    assert "requests" in text
    assert "2.28.0" in text
    assert "GHSA-j8r2-6x86-q33q" in text
    assert "2.31.0" in text


def test_convert_empty_packages_produces_empty_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Convert on JSON with empty packages list produces SARIF with empty results."""
    empty_audit = tmp_path / "empty_audit.json"
    empty_audit.write_text(json.dumps({"packages": []}))

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)

    pip_audit_to_sarif.convert(str(empty_audit))

    sarif = json.loads(captured.getvalue())
    assert sarif["runs"][0]["results"] == []


def test_convert_result_has_pyproject_artifact_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each result has physicalLocation.artifactLocation.uri == 'pyproject.toml'."""
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)

    pip_audit_to_sarif.convert(str(FIXTURE_PATH))

    sarif = json.loads(captured.getvalue())
    result = sarif["runs"][0]["results"][0]
    uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "pyproject.toml"
