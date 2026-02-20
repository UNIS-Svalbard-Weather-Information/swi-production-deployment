import glob
import json
import os
import yaml
from pathlib import Path


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

    # Write back to version.json
    with open(version_json_path, "w") as f:
        json.dump(version_data, f, indent=2)


if __name__ == "__main__":
    versions = extract_versions_from_compose()
    update_version_json(versions)
