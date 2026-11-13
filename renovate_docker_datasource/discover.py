"""Find the Docker images a repository uses, and the tags Renovate could offer for them.

Both questions are answered by Renovate itself, so the job follows each
repository's own config without repeating its rules.
"""

import json
import os
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

# One line, so that the custom manager in renovate.json can update it.
RENOVATE_IMAGE = "ghcr.io/renovatebot/renovate:44.234.1@sha256:7f97a190186bbb994ace854b17afe29e3c37c1f672a2b2d28ebd433c17a612df"  # noqa: E501

OFFERABLE_SCRIPT = Path(__file__).with_name("offerable.mjs")


class InUse(NamedTuple):
    """A tag in use, with the versioning Renovate applies to it there.

    `version` is the version Renovate resolves the tag to, such as 1.31 for
    1.31-alpine or a concrete version for a range; Renovate compares newer
    releases with it rather than with the tag.
    """

    tag: str
    versioning: str
    version: str


def lookup(checkout: Path, renovate_image: str = RENOVATE_IMAGE) -> list[dict]:
    """Run Renovate's lookup on a checkout without changing it; return its dependencies.

    Renovate reads the repository's own config, so custom managers such as
    annotation regexes count, and each dependency's versioning is the one
    its package rules choose, exactly as when Renovate runs for real.
    """
    with tempfile.TemporaryDirectory() as out:
        # Renovate's container runs as its own user, which cannot write to a
        # private temporary directory on Linux.
        os.chmod(out, 0o777)
        command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{checkout.resolve()}:/usr/src/app:ro",
            "-v",
            f"{out}:/out",
            "-e",
            "LOG_LEVEL=warn",
        ]
        env = os.environ.copy()
        # Renovate reads GitHub lookups' token from GITHUB_COM_TOKEN. Passed by
        # name so that the token does not appear in the process arguments.
        if token := os.environ.get("READ_TOKEN"):
            env["GITHUB_COM_TOKEN"] = token
            command += ["-e", "GITHUB_COM_TOKEN"]
        command += [
            renovate_image,
            "--platform=local",
            "--dry-run=lookup",
            "--report-type=file",
            "--report-path=/out/report.json",
        ]
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        report_path = Path(out) / "report.json"
        if result.returncode != 0 or not report_path.exists():
            raise RuntimeError(
                f"Renovate lookup failed for {checkout}:\n{result.stdout}{result.stderr}"
            )
        report = json.loads(report_path.read_text())
    deps = []
    for repo in report["repositories"].values():
        for manager, files in repo["packageFiles"].items():
            for package_file in files:
                for dep in package_file["deps"]:
                    deps.append(
                        {**dep, "manager": manager, "packageFile": package_file["packageFile"]}
                    )
    return deps


def docker_images(deps: list[dict]) -> dict[str, set[InUse]]:
    """Map each Docker package name to the tags in use.

    Includes OCI Helm charts, which Renovate also looks up through its
    Docker datasource. Dependencies Renovate skips, such as image names
    built from variables or images a package rule disables, are left out,
    since Renovate never looks them up. The preset moves versioned tags to
    its custom datasource at lookup time, but the report still gives the
    datasource they were extracted with.
    """
    images: dict[str, set[InUse]] = defaultdict(set)
    for dep in deps:
        if dep.get("datasource") != "docker" or dep.get("skipReason"):
            continue
        name = dep.get("packageName") or dep["depName"]
        images[name]
        if value := dep.get("currentValue"):
            versioning = dep.get("versioning") or "docker"
            # Floating tags such as latest resolve to no version.
            images[name].add(InUse(value, versioning, dep.get("currentVersion") or value))
    return dict(images)


def offerable(
    tags: Iterable[str], in_use: Iterable[InUse], renovate_image: str = RENOVATE_IMAGE
) -> set[str]:
    """The tags Renovate could offer in place of a tag in use.

    Renovate's lookup drops a release unless the dependency's versioning
    accepts it as a version, compatible with the current value and newer
    than it, so this asks Renovate's own versioning code those questions.
    Other tags, such as Windows builds of a Linux image or older releases,
    can never be offered.
    """
    request = {
        "inUse": [[u.versioning, u.tag, u.version] for u in in_use],
        "tags": sorted(tags),
    }
    if not request["tags"] or not request["inUse"]:
        return set()
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "-v",
            f"{OFFERABLE_SCRIPT}:/offerable.mjs:ro",
            "--entrypoint",
            "node",
            renovate_image,
            "/offerable.mjs",
        ],
        input=json.dumps(request),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Filtering tags with Renovate failed:\n{result.stderr}")
    return set(json.loads(result.stdout))
