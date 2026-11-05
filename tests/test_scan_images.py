import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scan_images import (  # noqa: E402
    build_matrix,
    list_images,
    parse_exceptions,
    repository,
    summarize,
)

COMPOSE = """\
services:
  redis:
    image: redis:8.10-alpine
  api:
    image: "ghcr.io/org/swi-api:1.2.3"   # pinned
    command: ["serve"]
  worker:
    image: ghcr.io/org/swi-api:1.2.3
  elevation:
    image: ghcr.io/org/swi-elevationapi:1.0.0
  # image: ghcr.io/org/not-used:9
"""

TODAY = datetime.date(2026, 10, 12)


class ListImagesTest(unittest.TestCase):
    def test_lists_each_image_once_in_order_and_ignores_comments(self):
        self.assertEqual(
            list_images(COMPOSE),
            [
                "redis:8.10-alpine",
                "ghcr.io/org/swi-api:1.2.3",
                "ghcr.io/org/swi-elevationapi:1.0.0",
            ],
        )

    def test_a_variable_in_an_image_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            list_images("    image: ghcr.io/org/x:${TAG}\n")

    def test_repository_strips_tag_and_digest_but_keeps_registry_port(self):
        self.assertEqual(repository("redis:8.10-alpine"), "redis")
        self.assertEqual(repository("ghcr.io/org/a:1.0"), "ghcr.io/org/a")
        self.assertEqual(repository("localhost:5000/a:1.0"), "localhost:5000/a")
        self.assertEqual(repository("localhost:5000/a"), "localhost:5000/a")
        self.assertEqual(repository("ghcr.io/org/a:1@sha256:abc"), "ghcr.io/org/a")


class MatrixTest(unittest.TestCase):
    def exceptions(self, expires):
        return parse_exceptions(
            '[{"image": "ghcr.io/org/swi-elevationapi", "reason": "replaced soon",'
            f' "expires": "{expires}"}}]'
        )

    def modes(self, exceptions):
        images = list_images(COMPOSE)
        return {e["name"]: e["mode"] for e in build_matrix(images, exceptions, TODAY)["include"]}

    def test_images_without_an_exception_are_enforced(self):
        self.assertEqual(self.modes([]), {"redis": "enforce", "swi-api": "enforce", "swi-elevationapi": "enforce"})

    def test_a_current_exception_switches_only_that_image_to_report(self):
        modes = self.modes(self.exceptions("2026-12-31"))
        self.assertEqual(modes["swi-elevationapi"], "report")
        self.assertEqual(modes["swi-api"], "enforce")

    def test_the_last_day_of_an_exception_still_counts(self):
        self.assertEqual(self.modes(self.exceptions("2026-10-12"))["swi-elevationapi"], "report")

    def test_an_expired_exception_is_enforced_again_and_says_so(self):
        entries = build_matrix(list_images(COMPOSE), self.exceptions("2026-10-11"), TODAY)["include"]
        elevation = next(e for e in entries if e["name"] == "swi-elevationapi")
        self.assertEqual(elevation["mode"], "enforce")
        self.assertIn("expired", elevation["note"])

    def test_an_exception_needs_reason_and_expiry(self):
        with self.assertRaises(ValueError):
            parse_exceptions('[{"image": "a", "reason": "", "expires": "2026-12-31"}]')
        with self.assertRaises(ValueError):
            parse_exceptions('[{"image": "a", "reason": "x", "expires": "soon"}]')

    def test_the_committed_exceptions_file_is_valid(self):
        path = Path(__file__).resolve().parent.parent / ".github" / "image-scan-exceptions.json"
        parse_exceptions(path.read_text(encoding="utf-8"))


class SummaryTest(unittest.TestCase):
    REPORT = {
        "Results": [
            {
                "Vulnerabilities": [
                    {"Severity": "HIGH", "VulnerabilityID": "CVE-2", "PkgName": "b", "InstalledVersion": "1", "FixedVersion": "2"},
                    {"Severity": "CRITICAL", "VulnerabilityID": "CVE-1", "PkgName": "a", "InstalledVersion": "1", "FixedVersion": "1.1"},
                ]
            },
            {"Target": "no vulnerabilities key"},
        ]
    }

    def test_counts_findings_and_lists_critical_first(self):
        text, count = summarize(self.REPORT)
        self.assertEqual(count, 2)
        self.assertLess(text.index("CVE-1"), text.index("CVE-2"))

    def test_a_clean_report_has_zero_findings(self):
        self.assertEqual(summarize({"Results": None})[1], 0)
        self.assertEqual(summarize({})[1], 0)


if __name__ == "__main__":
    unittest.main()
