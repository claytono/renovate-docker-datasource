"""Update the per-image files from the images used across the configured repos.

Usage: python -m renovate_docker_datasource [--checkout DIR ...] [--image NAME ...]

Without --checkout, each repository named in REPOSITORIES (owner/name,
separated by spaces or newlines) is shallow-cloned from GitHub, using the
token in READ_TOKEN when it is set. --image limits the run to the named
images and leaves every other image's files alone.

The exit code is 1 if any image failed. With --allow-registry-errors, an
image whose registry could not be reached or refused the request does not
count, so CI can run the job without failing on another site's outage;
any other error still does.
"""

import argparse
import base64
import os
import subprocess
import sys
import tempfile
import tomllib
import traceback
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .discover import docker_images, extract
from .refresh import Retention, refresh
from .registry import Image, Registry, parse_image
from .store import Store


def clone(repo: str, dest: Path) -> Path:
    command = ["git"]
    if token := os.environ.get("READ_TOKEN"):
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        command += ["-c", f"http.https://github.com/.extraheader=Authorization: Basic {basic}"]
    command += ["clone", "--quiet", "--depth=1", f"https://github.com/{repo}.git", str(dest)]
    subprocess.run(command, check=True)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", action="append", type=Path, default=[])
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--allow-registry-errors", action="store_true")
    args = parser.parse_args()
    # An empty list would look like no images in use and remove every file.
    repositories = os.environ.get("REPOSITORIES", "").split()
    if not args.checkout and not repositories:
        parser.error("set REPOSITORIES or pass --checkout")

    config = tomllib.loads((args.root / "config.toml").read_text())
    retention = Retention(**config["retention"])
    store = Store(args.root)
    now = datetime.now(UTC).replace(microsecond=0)

    in_use: dict[Image, set[str]] = defaultdict(set)
    with tempfile.TemporaryDirectory() as tmp:
        checkouts = args.checkout or [
            clone(repo, Path(tmp) / repo.replace("/", "__")) for repo in repositories
        ]
        for checkout in checkouts:
            for name, tags in docker_images(extract(checkout)).items():
                in_use[parse_image(name)] |= tags

    selected = {parse_image(name) for name in args.image}
    registry = Registry()
    failed, unreachable = [], []
    for image in sorted(selected or in_use, key=lambda i: i.name):
        try:
            # Read outside the OSError handler: a local file error is never the registry's.
            previous = store.load(image)
            try:
                state = refresh(image, in_use.get(image, set()), previous, registry, retention, now)
            except OSError as err:  # HTTP and network errors: the registry, not this code
                print(f"{image.name}: {err}", file=sys.stderr)
                unreachable.append(image.name)
                continue
        except Exception:  # one broken image must not stop the rest
            print(f"{image.name}: {traceback.format_exc()}", file=sys.stderr)
            failed.append(image.name)
            continue
        store.save(image, state)
        print(f"{image.name}: {len(state.releases)} releases, {len(state.retired)} retired")

    if not selected:
        for image in store.stored() - in_use.keys():
            print(f"{image.name}: no longer used, removed")
            store.remove(image)
    return 1 if failed or (unreachable and not args.allow_registry_errors) else 0


if __name__ == "__main__":
    sys.exit(main())
