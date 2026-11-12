"""Check the per-image files are in the shape the job writes.

Usage: python -m renovate_docker_datasource.check [--root DIR]

Renovate reads `images/` directly, so a hand edit or a bad merge that breaks
a file breaks lookups for that image. Each problem is printed, and the exit
code is 1 if there were any.
"""

import argparse
import json
import re
import sys
from pathlib import Path

TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def check_releases(path: Path) -> list[str]:
    try:
        releases = json.loads(path.read_text())["releases"]
    except (ValueError, KeyError, TypeError) as err:
        return [f"{path}: not a releases file ({err})"]
    for release in releases:
        if set(release) != {"version", "releaseTimestamp", "digest"}:
            return [f"{path}: {release} has the wrong keys"]
        if not isinstance(release["version"], str) or not release["version"]:
            return [f"{path}: {release} has no version"]
        if not TIMESTAMP.fullmatch(str(release["releaseTimestamp"])):
            return [f"{path}: {release['version']} has a bad releaseTimestamp"]
        if not DIGEST.fullmatch(str(release["digest"])):
            return [f"{path}: {release['version']} has a bad digest"]
    problems = []
    versions = [r["version"] for r in releases]
    if len(set(versions)) != len(versions):
        problems.append(f"{path}: a version is listed twice")
    order = [(r["releaseTimestamp"], r["version"]) for r in releases]
    if order != sorted(order):
        problems.append(f"{path}: releases are not sorted by date")
    return problems


def check_seen(images: Path, seen: Path) -> list[str]:
    problems = []
    for path in sorted(seen.rglob("*.txt")):
        releases_path = images / path.relative_to(seen).with_suffix(".json")
        if not releases_path.exists():
            problems.append(f"{path}: no {releases_path}")
            continue
        tags = path.read_text().splitlines()
        if not tags or "" in tags or tags != sorted(set(tags)):
            problems.append(f"{path}: tags must be sorted, unique and non-empty")
        try:
            versions = {r["version"] for r in json.loads(releases_path.read_text())["releases"]}
        except (ValueError, KeyError, TypeError):
            continue  # reported by check_releases
        if both := versions.intersection(tags):
            problems.append(f"{path}: also in {releases_path}: {', '.join(sorted(both))}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path())
    args = parser.parse_args()
    images, seen = args.root / "images", args.root / "seen"
    problems = [p for path in sorted(images.rglob("*.json")) for p in check_releases(path)]
    problems += check_seen(images, seen)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
