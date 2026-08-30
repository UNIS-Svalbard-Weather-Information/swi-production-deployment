import glob
import json
import os
import re
import sys
from pathlib import Path

import yaml

BASELINE_PATH = "info/.release-baseline.json"
BUMP_ORDER = {"patch": 0, "minor": 1, "major": 2}


def extract_versions_from_compose():
    # Find all compose files
    compose_files = glob.glob("**/[cd]ompose*.yml", recursive=True)
    versions = {}

    for compose_file in compose_files:
        with open(compose_file, "r") as f:
            try:
                compose_data = yaml.safe_load(f)
                if not compose_data or "services" not in compose_data:
                    continue

                for service_name, service in compose_data["services"].items():
                    if "image" in service:
                        image = service["image"]
                        if isinstance(image, str) and image.startswith(
                            "ghcr.io/unis-svalbard-weather-information/"
                        ):
                            repo = image.split("/")[-1].split(":")[0]
                            version = image.split(":")[-1] if ":" in image else "latest"
                            versions[repo] = version
            except Exception as e:
                print(f"Error parsing {compose_file}: {e}")

    return versions


def compare_dicts(old, new):
    # New entries (in new but not in old)
    new_entries = {k: new[k] for k in new if k not in old}

    # Removed entries (in old but not in new)
    removed_entries = {k: old[k] for k in old if k not in new}

    # Updated entries (in both, but with different values)
    updated_entries = {
        k: (old[k], new[k]) for k in old if k in new and old[k] != new[k]
    }

    return new_entries, removed_entries, updated_entries


def display_changes(old, new):
    new_entries, removed_entries, updated_entries = compare_dicts(old, new)

    if not new_entries and not removed_entries and not updated_entries:
        print("Up-to-date - Nothing to change")
        return

    if new_entries:
        print("=== New entries (green) ===")
        for k, v in new_entries.items():
            print(f"\033[92m{k}: {v}\033[0m")  # Green

    if removed_entries:
        print("\n=== Removed entries (red) ===")
        for k, v in removed_entries.items():
            print(f"\033[91m{k}: {v}\033[0m")  # Red

    if updated_entries:
        print("\n=== Updated entries (blue) ===")
        for k, (old_v, new_v) in updated_entries.items():
            print(f"\033[94m{k}: {old_v} → {new_v}\033[0m")  # Blue

    print("info/version.json updated successfully.")


def parse_semver(version):
    """Return an (major, minor, patch) int tuple, or None if not parseable as semver
    (e.g. swi-mapproxy's 'trixie-p3.13-mp6.0.1-0.0.12' scheme)."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def classify_bump(old_version, new_version):
    """Classify a component's version change as 'major', 'minor', 'patch', or None
    (unchanged). Non-semver tags always classify as 'patch' if they changed at all -
    never force a major/minor release-version bump off an unparseable tag."""
    if old_version == new_version:
        return None
    old = parse_semver(old_version)
    new = parse_semver(new_version)
    if old is None or new is None:
        return "patch"
    if new[0] != old[0]:
        return "major"
    if new[1] != old[1]:
        return "minor"
    return "patch"


def bump_release_version(current_release, level):
    base = parse_semver(current_release) or (0, 0, 0)
    major, minor, patch = base
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def load_baseline():
    if not os.path.exists(BASELINE_PATH):
        return None
    with open(BASELINE_PATH) as f:
        return json.load(f)


def write_baseline(version_data):
    """Snapshot the current release version + component versions as the baseline for a
    new release cycle. Called once when a release/X.Y.Z branch is cut."""
    Path("info").mkdir(exist_ok=True)
    with open(BASELINE_PATH, "w") as f:
        json.dump(
            {
                "release": version_data["release"],
                "components": {
                    repo: data["version"]
                    for repo, data in version_data["components"].items()
                },
            },
            f,
            indent=2,
        )


def compute_release_bump(new_components, current_release):
    """Compare current component versions against the release-branch baseline and
    return the new aggregate release version, or None if nothing changed (or there's
    no baseline - e.g. running on main outside a release cycle)."""
    baseline = load_baseline()
    if baseline is None:
        return None

    highest = None
    for repo, data in new_components.items():
        old_version = baseline["components"].get(repo)
        if old_version is None:
            continue  # component wasn't tracked at cycle start, not part of the bump calc
        level = classify_bump(old_version, data["version"])
        if level and (highest is None or BUMP_ORDER[level] > BUMP_ORDER[highest]):
            highest = level

    if highest is None:
        return None
    return bump_release_version(baseline["release"], highest)


def update_version_json(versions):
    version_json_path = "info/version.json"
    if not os.path.exists("info"):
        os.makedirs("info")

    if os.path.exists(version_json_path):
        with open(version_json_path, "r") as f:
            version_data = json.load(f)
    else:
        version_data = {
            "release": "dev-alpha",
            "release_name": "TheBestNameEver",
            "release_date": "1970-01-01",
            "components": {},
        }

    new_comp = {}
    # Update or add components
    for repo, version in versions.items():
        new_comp[repo] = {
            "url": f"https://github.com/UNIS-Svalbard-Weather-Information/{repo}",
            "version": version,
        }

    display_changes(version_data["components"], new_comp)

    version_data["components"] = new_comp

    new_release = compute_release_bump(new_comp, version_data["release"])
    if new_release is not None:
        print(f"\033[94mrelease: {version_data['release']} → {new_release}\033[0m")
        version_data["release"] = new_release

    # Write back to version.json
    with open(version_json_path, "w") as f:
        json.dump(version_data, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--write-baseline":
        # Called once when cutting a new release/X.Y.Z branch, to snapshot the
        # starting point that later runs diff against for the automatic version bump.
        with open("info/version.json") as f:
            write_baseline(json.load(f))
        print(f"Baseline written to {BASELINE_PATH}.")
    else:
        versions = extract_versions_from_compose()
        update_version_json(versions)
