"""Read and write the per-image files.

`images/<image>.json` holds the releases in the retention window, in the
shape Renovate's custom datasources expect. `seen/<image>.txt` lists the
names of every other tag the job has seen, one per line.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from .refresh import Release, State
from .registry import Image, parse_image


class Store:
    def __init__(self, root: Path) -> None:
        self.images = root / "images"
        self.seen = root / "seen"

    def _paths(self, image: Image) -> tuple[Path, Path]:
        return self.images / f"{image.name}.json", self.seen / f"{image.name}.txt"

    def load(self, image: Image) -> State | None:
        releases_path, seen_path = self._paths(image)
        if not releases_path.exists():
            return None
        state = State()
        for entry in json.loads(releases_path.read_text())["releases"]:
            first_seen = datetime.fromisoformat(entry["releaseTimestamp"])
            state.releases[entry["version"]] = Release(
                entry["version"], first_seen, entry["digest"]
            )
        if seen_path.exists():
            state.retired = set(seen_path.read_text().split())
        return state

    def save(self, image: Image, state: State) -> None:
        releases_path, seen_path = self._paths(image)
        releases = sorted(state.releases.values(), key=lambda r: (r.first_seen, r.version))
        body = {
            "releases": [
                {
                    "version": r.version,
                    "releaseTimestamp": r.first_seen.astimezone(UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "digest": r.digest,
                }
                for r in releases
            ]
        }
        releases_path.parent.mkdir(parents=True, exist_ok=True)
        releases_path.write_text(json.dumps(body, indent=2) + "\n")
        if state.retired:
            seen_path.parent.mkdir(parents=True, exist_ok=True)
            seen_path.write_text("".join(f"{tag}\n" for tag in sorted(state.retired)))
        else:
            seen_path.unlink(missing_ok=True)

    def stored(self) -> set[Image]:
        return {
            parse_image(str(path.relative_to(self.images).with_suffix("")))
            for path in self.images.rglob("*.json")
        }

    def remove(self, image: Image) -> None:
        for path in self._paths(image):
            path.unlink(missing_ok=True)
        _prune_empty_dirs(self.images)
        _prune_empty_dirs(self.seen)


def _prune_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
