import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from update_versions import (  # noqa: E402
    is_internal,
    parse_tag,
    pick_latest,
    releases_between,
    replace_image,
    split_image,
)

MAPPROXY_PATTERN = r"^trixie-p\d+\.\d+-mp\d+\.\d+\.\d+-(?P<version>\d+\.\d+\.\d+)$"


def test_parse_tag():
    assert parse_tag("v3.6") == ("v", (3, 6), "")
    assert parse_tag("7.4-alpine") == ("", (7, 4), "-alpine")
    assert parse_tag("latest") is None


def test_semver_ignores_floating_and_dev_tags():
    tags = ["3.0.5-DEV", "3.0.6", "3.1.0", "3", "3.1", "latest", "3.2.0-dev01", "v0.0-DEV"]
    assert pick_latest("3.0.6", tags) == "3.1.0"


def test_already_latest():
    assert pick_latest("3.1.0", ["3.0.6", "3.1.0", "3.1", "latest"]) is None


def test_major_bumps_are_taken():
    assert pick_latest("0.0.9", ["0.0.9", "0.0.10", "1.0.0"]) == "1.0.0"


def test_numeric_not_lexical_order():
    assert pick_latest("0.0.9", ["0.0.9", "0.0.10", "0.0.2"]) == "0.0.10"


def test_mapproxy_custom_pattern():
    tags = [
        "trixie-p3.13-mp6.0.1-0.0.12",
        "trixie-p3.13-mp6.0.1-0.0.13",
        "trixie-p3.14-mp6.0.2-0.0.14",
        "latest",
        "0.0",
    ]
    assert pick_latest("trixie-p3.13-mp6.0.1-0.0.12", tags, MAPPROXY_PATTERN) == "trixie-p3.14-mp6.0.2-0.0.14"


def test_custom_pattern_same_version_rebuild_is_not_a_bump():
    tags = ["trixie-p3.13-mp6.0.1-0.0.12", "trixie-p3.14-mp6.0.1-0.0.12"]
    assert pick_latest("trixie-p3.13-mp6.0.1-0.0.12", tags, MAPPROXY_PATTERN) is None


def test_redis_keeps_variant_and_precision():
    tags = ["7.4", "7.4-alpine", "7.6-alpine", "8.0-alpine", "7.4.2-alpine", "8.0-alpine3.21", "alpine"]
    assert pick_latest("7.4-alpine", tags) == "8.0-alpine"


def test_traefik_keeps_v_prefix_and_precision():
    tags = ["v3.6", "v3.7", "v3.7.1", "3.8", "latest", "v3"]
    assert pick_latest("v3.6", tags) == "v3.7"


def test_split_image():
    assert split_image("redis:7.4-alpine") == ("registry-1.docker.io", "library/redis", "redis", "7.4-alpine")
    assert split_image("traefik:v3.6")[1] == "library/traefik"
    assert split_image("ghcr.io/unis-svalbard-weather-information/swi-titiller:0.0.3") == (
        "ghcr.io",
        "unis-svalbard-weather-information/swi-titiller",
        "ghcr.io/unis-svalbard-weather-information/swi-titiller",
        "0.0.3",
    )
    assert split_image("localhost:5000/foo") is None
    assert split_image("redis@sha256:abc") is None


def test_is_internal():
    assert is_internal("ghcr.io/unis-svalbard-weather-information/swi-titiller")
    assert not is_internal("redis")
    assert not is_internal("ghcr.io/someone-else/swi-titiller")


def test_replace_image_keeps_comments_and_quotes():
    text = (
        "services:\n"
        "  api:\n"
        "    # keep me\n"
        "    image: ghcr.io/org/api:1.0.0  # pinned\n"
        "  other:\n"
        '    image: "ghcr.io/org/api:1.0.0"\n'
        "  untouched:\n"
        "    image: ghcr.io/org/api:1.0.01\n"
    )
    new, count = replace_image(text, "ghcr.io/org/api:1.0.0", "ghcr.io/org/api:1.1.0")
    assert count == 2
    assert "    # keep me\n    image: ghcr.io/org/api:1.1.0  # pinned\n" in new
    assert '    image: "ghcr.io/org/api:1.1.0"\n' in new
    assert "ghcr.io/org/api:1.0.01" in new


def test_releases_between_truncates_to_pinned_precision():
    releases = [
        {"tag_name": t, "draft": False, "prerelease": t.endswith("rc1")}
        for t in ["7.4.5", "7.6.0", "7.6.1", "8.0.0", "8.0.0-rc1", "8.2.0"]
    ]
    picked = [r["tag_name"] for r in releases_between(releases, (7, 4), (8, 0))]
    assert picked == ["8.0.0", "7.6.1", "7.6.0"]
