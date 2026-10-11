#!/usr/bin/env python3
"""Helpers for the scheduled scan of the images deployed by compose.yml.

Two subcommands, both used by .github/workflows/scan-deployed-images.yml:

  scan_images.py matrix COMPOSE EXCEPTIONS
      Print the GitHub Actions matrix (JSON) with one entry per image in the compose
      file. An image with a current entry in the exceptions file is scanned in
      "report" mode (findings are shown but never fail the run); every other image,
      and any image whose exception has expired, is scanned in "enforce" mode.

  scan_images.py summary TRIVY_JSON
      Print a Markdown summary of a Trivy JSON report and exit 0. The number of
      findings is written to stdout's last line as ``findings=<n>``.

Exceptions file (JSON): a list of {"image": "<repository without tag>", "reason": "...",
"expires": "YYYY-MM-DD"}. An exception must say why and when it ends.
"""

import datetime
import json
import re
import sys

IMAGE_LINE = re.compile(r"^\s*image:\s*(?P<image>[^\s#]+)\s*(?:#.*)?$")
MAX_ROWS = 40


def list_images(compose_text):
    """Image references in a compose file, in order, without duplicates."""
    images = []
    for line in compose_text.splitlines():
        match = IMAGE_LINE.match(line)
        if not match:
            continue
        image = match["image"].strip("\"'")
        if "${" in image:
            raise ValueError(
                f"image '{image}' uses a variable; the scan needs literal references"
            )
        if image not in images:
            images.append(image)
    return images


def repository(image):
    """The image without its tag or digest: 'redis:8.10-alpine' -> 'redis'."""
    image = image.split("@", 1)[0]
    last = image.rsplit("/", 1)[-1]
    if ":" in last:
        image = image.rsplit(":", 1)[0]
    return image


def parse_exceptions(text):
    exceptions = json.loads(text) if text.strip() else []
    for entry in exceptions:
        for key in ("image", "reason", "expires"):
            if not entry.get(key):
                raise ValueError(f"exception {entry!r} needs a non-empty '{key}'")
        datetime.date.fromisoformat(entry["expires"])
    return exceptions


def build_matrix(images, exceptions, today):
    """One entry per image: its reference, a short name and the scan mode."""
    entries = []
    for image in images:
        mode, note = "enforce", ""
        for exception in exceptions:
            if exception["image"] != repository(image):
                continue
            if datetime.date.fromisoformat(exception["expires"]) >= today:
                mode = "report"
                note = f"exception until {exception['expires']}: {exception['reason']}"
            else:
                note = f"exception expired on {exception['expires']}"
        entries.append(
            {
                "image": image,
                "name": repository(image).rsplit("/", 1)[-1],
                "mode": mode,
                "note": note,
            }
        )
    return {"include": entries}


def collect_findings(report):
    """(severity, id, package, installed, fixed) for each vulnerability in a Trivy report."""
    findings = []
    for result in report.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            findings.append(
                (
                    vuln.get("Severity", "?"),
                    vuln.get("VulnerabilityID", "?"),
                    vuln.get("PkgName", "?"),
                    vuln.get("InstalledVersion", "?"),
                    vuln.get("FixedVersion", "") or "-",
                )
            )
    order = {"CRITICAL": 0, "HIGH": 1}
    return sorted(findings, key=lambda f: (order.get(f[0], 2), f[2], f[1]))


def summarize(report):
    findings = collect_findings(report)
    if not findings:
        return "No fixable HIGH or CRITICAL findings.\n", 0
    lines = [
        f"**{len(findings)} fixable HIGH/CRITICAL findings**",
        "",
        "| Severity | Vulnerability | Package | Installed | Fixed in |",
        "|---|---|---|---|---|",
    ]
    for severity, vuln_id, package, installed, fixed in findings[:MAX_ROWS]:
        lines.append(f"| {severity} | {vuln_id} | {package} | {installed} | {fixed} |")
    if len(findings) > MAX_ROWS:
        lines.append(f"\n... and {len(findings) - MAX_ROWS} more.")
    return "\n".join(lines) + "\n", len(findings)


def main(argv):
    if len(argv) == 4 and argv[1] == "matrix":
        with open(argv[2], encoding="utf-8") as handle:
            images = list_images(handle.read())
        with open(argv[3], encoding="utf-8") as handle:
            exceptions = parse_exceptions(handle.read())
        matrix = build_matrix(images, exceptions, datetime.date.today())
        print(json.dumps(matrix, separators=(",", ":")))
        return 0
    if len(argv) == 3 and argv[1] == "summary":
        with open(argv[2], encoding="utf-8") as handle:
            text, count = summarize(json.load(handle))
        print(text)
        print(f"findings={count}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
