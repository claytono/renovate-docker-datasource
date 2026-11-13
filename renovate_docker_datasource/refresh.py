"""Bring one image's recorded tags up to date with its registry."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.error import HTTPError

from .registry import DockerHubTag, Image, Registry, docker_hub_tags


@dataclass
class Release:
    version: str
    first_seen: datetime
    digest: str | None = None


@dataclass
class State:
    """What the job knows about one image.

    `releases` are the tags inside the retention window, which Renovate
    reads. `retired` are the names of the tags that have left it, kept only
    so that they are not mistaken for new tags on the next run.
    """

    releases: dict[str, Release] = field(default_factory=dict)
    retired: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Retention:
    days: int
    count: int


def refresh(
    image: Image,
    in_use: set[str],
    offerable: Callable[[set[str]], set[str]],
    previous: State | None,
    registry: Registry,
    retention: Retention,
    now: datetime,
) -> State:
    """Return the image's new state.

    A tag's release date is its push date on Docker Hub, which reports one,
    and the first time the job saw it everywhere else. The date is recorded
    once: a new digest for a known tag keeps it, so a rebuild does not
    restart the wait.

    Only the tags in use and the tags `offerable` says Renovate could offer
    in their place are recorded. The rest, such as Windows builds of a
    Linux image, are left out and asked about again on the next run, so a
    repository that starts using one of them dates it then.
    """
    bootstrap = previous is None
    tags = set(registry.list_tags(image))
    tags = (tags & in_use) | offerable(tags - in_use)
    state = State(
        {v: r for v, r in previous.releases.items() if v in tags} if previous else {},
        (previous.retired & tags) if previous else set(),
    )
    cutoff = now - timedelta(days=retention.days)

    docker_hub: dict[str, DockerHubTag] = {}
    if image.registry == "docker.io":
        docker_hub = {
            t.name: t for t in docker_hub_tags(image, stop_before=None if bootstrap else cutoff)
        }

    for tag in sorted(tags - state.releases.keys() - state.retired):
        if tag in docker_hub and docker_hub[tag].pushed:
            state.releases[tag] = Release(tag, docker_hub[tag].pushed.replace(microsecond=0))
        else:
            state.releases[tag] = Release(tag, now)
    for tag in in_use & state.retired:
        state.retired.discard(tag)
        state.releases[tag] = Release(tag, now)

    newest = sorted(state.releases.values(), key=lambda r: (r.first_seen, r.version))[
        -retention.count :
    ]
    keep = {r.version for r in newest} | {
        v for v, r in state.releases.items() if r.first_seen >= cutoff or v in in_use
    }
    for tag in set(state.releases) - keep:
        state.retired.add(state.releases.pop(tag).version)

    _set_digests(image, state, docker_hub, registry)
    return state


def _set_digests(
    image: Image, state: State, docker_hub: dict[str, DockerHubTag], registry: Registry
) -> None:
    """Record each kept tag's current digest, dropping tags that vanished.

    Docker Hub's tag API already gave digests for recently pushed tags; every
    other tag is asked of the registry, so rebuilt tags pick up new digests.
    """
    lookups = []
    for tag, release in state.releases.items():
        if tag in docker_hub and docker_hub[tag].digest:
            release.digest = docker_hub[tag].digest
        else:
            lookups.append(tag)

    def lookup(tag: str) -> str | None:
        try:
            return registry.digest(image, tag)
        except HTTPError as err:
            if err.code == 404:  # deleted since the tag list was read
                return None
            raise

    with ThreadPoolExecutor(max_workers=8) as pool:
        for tag, digest in zip(lookups, pool.map(lookup, lookups), strict=True):
            if digest:
                state.releases[tag].digest = digest
            else:
                del state.releases[tag]
