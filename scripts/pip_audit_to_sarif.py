"""Convert pip-audit JSON output to SARIF 2.1.0 for GitHub Security tab upload.

Reads pip-audit JSON from the path given as argv[1], writes SARIF 2.1.0 to
stdout.  Called from the scan-deps job in .github/workflows/publish.yml.

Usage (in CI):
    python3 scripts/pip_audit_to_sarif.py pip-audit.json > pip-audit.sarif
"""

from __future__ import annotations

import json
import sys
from typing import Any

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master"
    "/Documents/CommitteeSpecifications/2.1.0/sarif-schema-2.1.0.json"
)
PIP_AUDIT_VERSION = "2.10.1"
TOOL_NAME = "pip-audit"
SOURCE_URI = "pyproject.toml"


def convert(audit_path: str) -> None:
    """Read pip-audit JSON at audit_path and write SARIF 2.1.0 to stdout."""
    with open(audit_path) as fh:
        audit: dict[str, Any] = json.load(fh)

    results: list[dict[str, Any]] = []
    for pkg in audit.get("packages", []):
        pkg_name: str = pkg["name"]
        pkg_version: str = pkg["version"]
        for vuln in pkg.get("vulns", []):
            vuln_id: str = vuln["id"]
            fix_versions: list[str] = vuln.get("fix_versions", [])
            fix_vers = ", ".join(fix_versions) or "none"
            results.append(
                {
                    "ruleId": vuln_id,
                    "message": {
                        "text": (
                            f"{pkg_name}=={pkg_version} — {vuln_id} (fix: {fix_vers})"
                        )
                    },
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {
                                    "uri": SOURCE_URI,
                                    "uriBaseId": "%SRCROOT%",
                                }
                            }
                        }
                    ],
                }
            )

    sarif: dict[str, Any] = {
        "version": SARIF_VERSION,
        "$schema": SARIF_SCHEMA,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "version": PIP_AUDIT_VERSION,
                        "rules": [],
                    }
                },
                "results": results,
            }
        ],
    }
    json.dump(sarif, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    convert(sys.argv[1])
