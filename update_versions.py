"""Find newer image tags for every image pinned in the compose files and apply them.

Replaces Renovate for this repo (see .github/workflows/update-versions.yml):
- images under ghcr.io/unis-svalbard-weather-information/ are "internal" - the
  workflow commits their bumps straight onto the active staging/X.Y.Z branch;
- everything else (redis, traefik, ...) is "external" - the workflow opens one PR per
  image with the upstream release notes, and never commits those directly.

Tags are read from the registry itself (v2 tags/list, anonymous token), not from
GitHub releases: a release without a pushed image (seen for real with
swi-metobs-backend 3.0.5 and swi-mapcache-seaice 0.0.14) must never be picked.
"""

import argparse
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

CONFIG_PATH = ".github/version-updater.yml"
ORG_PREFIX = "ghcr.io/unis-svalbard-weather-information/"
ORG_URL = "https://github.com/UNIS-Svalbard-Weather-Information"
USER_AGENT = "swi-version-updater"
MAX_NOTES_RELEASES = 15
MAX_NOTES_BODY = 2000
MAX_NOTES_TOTAL = 60000

TAG_RE = re.compile(r"^(?P<prefix>\D*)(?P<num>\d+(?:\.\d+)*)(?P<suffix>.*)$")


# --------------------------------------------------------------------------- tags


def parse_tag(tag):
    """Split a tag into (prefix, numeric tuple, suffix), or None if it has no number.

    'v3.6' -> ('v', (3, 6), ''), '7.4-alpine' -> ('', (7, 4), '-alpine'),
    'latest' -> None."""
    m = TAG_RE.match(tag)
    if not m:
        return None
    return m["prefix"], tuple(int(x) for x in m["num"].split(".")), m["suffix"]


def tag_version(tag, pattern=None):
    """Comparable version tuple for a tag, or None if the tag isn't a candidate.

    With a custom pattern (named group 'version'), only that group is compared - used
    for swi-mapproxy's 'trixie-p3.13-mp6.0.1-0.0.12' scheme."""
    if pattern:
        m = re.match(pattern, tag)
        if not m:
            return None
        return tuple(int(x) for x in m["version"].split("."))
    parsed = parse_tag(tag)
    return parsed[1] if parsed else None


def is_same_shape(current, candidate):
    """Default rule: same prefix, same suffix and same count of numeric parts. This
    alone rejects floating tags ('latest', '3', '3.1' next to '3.0.6'), '-DEV' /
    '-devNN' builds and variant changes ('7.4-alpine' vs '7.4-bookworm')."""
    a, b = parse_tag(current), parse_tag(candidate)
    if a is None or b is None:
        return False
    return a[0] == b[0] and a[2] == b[2] and len(a[1]) == len(b[1])


def pick_latest(current, tags, pattern=None):
    """Newest tag strictly above `current`, or None if `current` is already newest
    (or isn't itself a recognisable version)."""
    current_version = tag_version(current, pattern)
    if current_version is None:
        return None

    best, best_version = None, current_version
    for tag in tags:
        if not pattern and not is_same_shape(current, tag):
            continue
        version = tag_version(tag, pattern)
        if version is None:
            continue
        # tag string as tie-break keeps the result deterministic when two tags share
        # a version (e.g. mapproxy rebuilt on a new base image).
        if version > best_version or (
            best is not None and version == best_version and tag > best
        ):
            best, best_version = tag, version
    return best


# ------------------------------------------------------------------------- images


def split_image(image):
    """'redis:7.4-alpine' -> ('registry-1.docker.io', 'library/redis', 'redis', '7.4-alpine').

    Returns (registry host, repository path, name as written without tag, tag), or
    None for references this script doesn't handle (no tag, digest-pinned)."""
    if "@" in image:
        return None
    name, sep, tag = image.rpartition(":")
    if not sep or "/" in tag:
        return None  # no tag at all, or the ':' belonged to a registry port

    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, rest
    else:
        registry = "registry-1.docker.io"
        repository = name if "/" in name else f"library/{name}"
    return registry, repository, name, tag


def find_images():
    """Every (compose file, image) pair, using the same glob as update-info.py."""
    found = []
    for compose_file in sorted(glob.glob("**/[cd]ompose*.yml", recursive=True)):
        with open(compose_file, encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                print(f"Error parsing {compose_file}: {e}", file=sys.stderr)
                continue
        for service in (data or {}).get("services", {}).values():
            image = (service or {}).get("image")
            if isinstance(image, str) and (compose_file, image) not in found:
                found.append((compose_file, image))
    return found


def is_internal(name):
    return name.startswith(ORG_PREFIX)


# ----------------------------------------------------------------------- registry


def _get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp), resp.headers


def _bearer_token(www_authenticate):
    """Anonymous pull token from a 'Bearer realm=...,service=...,scope=...' challenge."""
    params = dict(re.findall(r'(\w+)="([^"]*)"', www_authenticate))
    query = {k: params[k] for k in ("service", "scope") if k in params}
    data, _ = _get(f"{params['realm']}?{urllib.parse.urlencode(query)}")
    return data.get("token") or data.get("access_token")


def list_tags(registry, repository):
    """All tags of an image, following the registry's Link pagination."""
    base = f"https://{registry}"
    url = f"{base}/v2/{repository}/tags/list?n=1000"
    headers, tags = {}, []
    while url:
        try:
            data, resp_headers = _get(url, headers)
        except urllib.error.HTTPError as e:
            if e.code != 401 or headers:
                raise
            headers = {"Authorization": f"Bearer {_bearer_token(e.headers['WWW-Authenticate'])}"}
            continue
        tags.extend(data.get("tags") or [])
        m = re.search(r'<([^>]+)>;\s*rel="next"', resp_headers.get("Link", ""))
        url = urllib.parse.urljoin(base, m.group(1)) if m else None
    return tags


# -------------------------------------------------------------------------- notes


def github_releases(repo):
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    releases = []
    for page in (1, 2, 3):
        data, _ = _get(f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}", headers)
        releases.extend(data)
        if len(data) < 100:
            break
    return releases


def releases_between(releases, old_version, new_version):
    """Stable upstream releases in (old, new], newest first. Release versions are
    truncated to the pinned tag's precision, so pinning '7.4' and moving to '7.6'
    lists 7.6.x but not the 7.4.x patches we were already floating on."""
    width = len(old_version)
    picked = []
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        parsed = parse_tag(release["tag_name"])
        if parsed is None or parsed[2]:  # skip '-rc1' style suffixes
            continue
        truncated = parsed[1][:width]
        if old_version < truncated <= new_version:
            picked.append((parsed[1], release))
    picked.sort(key=lambda p: p[0], reverse=True)
    return [r for _, r in picked]


def render_notes(change, config):
    """Markdown 'What changed' block for one change (PR body / commit summary)."""
    lines = [f"### `{change['name']}` `{change['old']}` → `{change['new']}`", ""]
    if change["kind"] == "internal":
        lines.append(f"Release notes: {change['notes_url']}")
        return "\n".join(lines)

    repo = config.get("release_notes_repo")
    if not repo:
        lines.append("_No `release_notes_repo` configured in `.github/version-updater.yml` for this image._")
        return "\n".join(lines)

    try:
        releases = releases_between(
            github_releases(repo),
            tag_version(change["old"], config.get("tag_pattern")),
            tag_version(change["new"], config.get("tag_pattern")),
        )
    except (OSError, KeyError, ValueError) as e:
        lines.append(f"_Could not fetch release notes from {repo}: {e}_")
        return "\n".join(lines)

    if not releases:
        lines.append(f"No matching releases found in https://github.com/{repo}/releases.")
        return "\n".join(lines)

    lines.append(f"Upstream releases from https://github.com/{repo}/releases:")
    for release in releases[:MAX_NOTES_RELEASES]:
        body = (release.get("body") or "").strip()
        if len(body) > MAX_NOTES_BODY:
            body = body[:MAX_NOTES_BODY].rstrip() + "\n\n_… truncated, see the release page._"
        lines += [
            "",
            f"<details><summary><b>{release['tag_name']}</b> ({release.get('published_at', '')[:10]})</summary>",
            "",
            f"{release['html_url']}",
            "",
            body or "_No release notes._",
            "",
            "</details>",
        ]
    if len(releases) > MAX_NOTES_RELEASES:
        rest = ", ".join(f"[{r['tag_name']}]({r['html_url']})" for r in releases[MAX_NOTES_RELEASES:])
        lines += ["", f"Older releases in this range: {rest}"]
    return "\n".join(lines)


# -------------------------------------------------------------------------- apply


def replace_image(text, old_image, new_image):
    """Swap one image reference in compose text, keeping quotes and comments intact.
    Never round-trips the YAML, which would lose every comment in compose.yml."""
    pattern = re.compile(
        r"^(\s*image:\s*[\"']?)" + re.escape(old_image) + r"([\"']?\s*(?:#.*)?)$",
        re.MULTILINE,
    )
    return pattern.subn(lambda m: f"{m[1]}{new_image}{m[2]}", text)


def apply_change(change):
    with open(change["file"], newline="", encoding="utf-8") as f:
        text = f.read()
    text, count = replace_image(
        text, f"{change['name']}:{change['old']}", f"{change['name']}:{change['new']}"
    )
    if not count:
        raise RuntimeError(f"{change['file']}: could not find image line for {change['name']}:{change['old']}")
    with open(change["file"], "w", newline="", encoding="utf-8") as f:
        f.write(text)


# --------------------------------------------------------------------------- main


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {"ignore": [], "images": {}}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {"ignore": data.get("ignore") or [], "images": data.get("images") or {}}


def warn(message):
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "WARNING: "
    print(f"{prefix}{message}", file=sys.stderr)


def check(images, config, only=None, image_filter=None):
    """Return (changes, rows): the bumps to make, and one summary row per image."""
    tag_cache, changes, rows = {}, [], []
    for compose_file, image in images:
        ref = split_image(image)
        if ref is None:
            rows.append((compose_file, image, "-", "skipped: no plain tag"))
            continue
        registry, repository, name, tag = ref
        kind = "internal" if is_internal(name) else "external"
        if only and kind != only:
            continue
        if image_filter and image_filter not in (name, name.rsplit("/", 1)[-1]):
            continue
        if name in config["ignore"]:
            rows.append((compose_file, name, tag, "ignored (config)"))
            continue

        image_config = config["images"].get(name) or {}
        try:
            if (registry, repository) not in tag_cache:
                tag_cache[(registry, repository)] = list_tags(registry, repository)
        except (OSError, KeyError, ValueError) as e:
            warn(f"{name}: could not list tags: {e}")
            rows.append((compose_file, name, tag, "error listing tags"))
            continue

        pattern = image_config.get("tag_pattern")
        if tag_version(tag, pattern) is None:
            warn(f"{name}:{tag} is not a recognisable version - add a tag_pattern in {CONFIG_PATH}")
            rows.append((compose_file, name, tag, "skipped: unrecognised tag"))
            continue

        new = pick_latest(tag, tag_cache[(registry, repository)], pattern)
        if new is None:
            rows.append((compose_file, name, tag, "up to date"))
            continue

        repo_name = name.rsplit("/", 1)[-1]
        changes.append({
            "file": compose_file,
            "name": name,
            "repo": repo_name,
            "old": tag,
            "new": new,
            "kind": kind,
            "notes_url": f"{ORG_URL}/{repo_name}/releases/tag/{new}" if kind == "internal" else None,
        })
        rows.append((compose_file, name, tag, f"→ {new} ({'commit' if kind == 'internal' else 'PR'})"))
    return changes, rows


def write_summary(rows, dry_run):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"## Component versions{' (dry run)' if dry_run else ''}\n\n")
        f.write("| File | Image | Current | Result |\n|---|---|---|---|\n")
        for compose_file, name, tag, result in rows:
            f.write(f"| `{compose_file}` | `{name}` | `{tag}` | {result} |\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report only, don't edit compose files")
    parser.add_argument("--only", choices=("internal", "external"), help="limit to one kind of image")
    parser.add_argument("--image", help="limit to one image (e.g. 'redis' or 'swi-titiller')")
    parser.add_argument("--json", help="write the list of changes to this file")
    parser.add_argument("--notes", help="write the markdown 'What changed' for the changes to this file")
    parser.add_argument("--summary", action="store_true", help="append a table to $GITHUB_STEP_SUMMARY")
    args = parser.parse_args(argv)

    config = load_config()
    changes, rows = check(find_images(), config, args.only, args.image)

    for compose_file, name, tag, result in rows:
        print(f"{compose_file}: {name}:{tag} {result}")

    if not args.dry_run:
        for change in changes:
            apply_change(change)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(changes, f, indent=2)
    if args.notes:
        notes = "\n\n".join(render_notes(c, config["images"].get(c["name"]) or {}) for c in changes)
        if len(notes) > MAX_NOTES_TOTAL:  # GitHub rejects PR bodies over 65536 chars
            notes = notes[:MAX_NOTES_TOTAL] + "\n\n_… truncated._"
        with open(args.notes, "w", encoding="utf-8") as f:
            f.write(notes + "\n")
    if args.summary:
        write_summary(rows, args.dry_run)

    if not changes:
        print("Up-to-date - Nothing to change")


if __name__ == "__main__":
    main()
