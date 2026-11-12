"""Find the Docker images a repository uses, as Renovate sees them."""

import json
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

# One line, so that the custom manager in renovate.json can update it.
RENOVATE_IMAGE = "ghcr.io/renovatebot/renovate:44.234.1@sha256:7f97a190186bbb994ace854b17afe29e3c37c1f672a2b2d28ebd433c17a612df"  # noqa: E501


def extract(checkout: Path, renovate_image: str = RENOVATE_IMAGE) -> list[dict]:
    """Run Renovate's extract-only mode on a checkout; return its dependencies.

    Renovate reads the repository's own config, so custom managers such as
    annotation regexes count, exactly as they do when Renovate runs for real.
    """
    with tempfile.TemporaryDirectory() as out:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{checkout.resolve()}:/usr/src/app:ro",
                "-v",
                f"{out}:/out",
                "-e",
                "LOG_LEVEL=warn",
                renovate_image,
                "--platform=local",
                "--dry-run=extract",
                "--report-type=file",
                "--report-path=/out/report.json",
            ],
            capture_output=True,
            text=True,
        )
        report_path = Path(out) / "report.json"
        if result.returncode != 0 or not report_path.exists():
            raise RuntimeError(
                f"Renovate extract failed for {checkout}:\n{result.stdout}{result.stderr}"
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


def docker_images(deps: list[dict]) -> dict[str, set[str]]:
    """Map each Docker package name to the tags in use.

    Includes OCI Helm charts, which Renovate also looks up through its
    Docker datasource. Dependencies Renovate skips at extract time, such
    as image names built from variables, are left out.
    """
    images: dict[str, set[str]] = defaultdict(set)
    for dep in deps:
        if dep.get("datasource") != "docker" or dep.get("skipReason"):
            continue
        name = dep.get("packageName") or dep["depName"]
        images[name]
        if dep.get("currentValue"):
            images[name].add(dep["currentValue"])
    return dict(images)
