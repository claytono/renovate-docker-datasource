"""Anonymous access to container registries: tag lists and tag digests."""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
USER_AGENT = "renovate-docker-datasource"


@dataclass(frozen=True)
class Image:
    registry: str
    repository: str

    @property
    def name(self) -> str:
        return f"{self.registry}/{self.repository}"


def parse_image(name: str) -> Image:
    """Split an image name into registry and repository, as Docker does.

    `nginx` is `docker.io/library/nginx`, `foo/bar` is `docker.io/foo/bar`,
    and a first component containing a dot or colon, or `localhost`, is a
    registry host.
    """
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, rest
    else:
        registry, repository = "docker.io", name
    if registry in ("index.docker.io", "registry-1.docker.io"):
        registry = "docker.io"
    if registry == "docker.io" and "/" not in repository:
        repository = f"library/{repository}"
    return Image(registry, repository)


@dataclass
class DockerHubTag:
    name: str
    digest: str | None
    pushed: datetime | None


class Registry:
    """Talks to one registry, reusing a bearer token per repository."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def _api_host(self, image: Image) -> str:
        return "registry-1.docker.io" if image.registry == "docker.io" else image.registry

    def _request(self, image: Image, method: str, path: str, accept: str | None = None):
        url = f"https://{self._api_host(image)}{path}"
        for attempt in range(2):
            headers = {"User-Agent": USER_AGENT}
            if accept:
                headers["Accept"] = accept
            if image.name in self._tokens:
                headers["Authorization"] = f"Bearer {self._tokens[image.name]}"
            req = urllib.request.Request(url, method=method, headers=headers)
            try:
                return _open(req)
            except urllib.error.HTTPError as err:
                if err.code != 401 or attempt:
                    raise
                self._tokens[image.name] = self._token(image, err.headers["WWW-Authenticate"])
        raise AssertionError("unreachable")

    def _token(self, image: Image, challenge: str | None) -> str:
        if not challenge or not challenge.lower().startswith("bearer "):
            raise RuntimeError(f"{image.name}: unsupported auth challenge {challenge!r}")
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = params.pop("realm")
        params.setdefault("scope", f"repository:{image.repository}:pull")
        with _open(urllib.request.Request(f"{realm}?{urllib.parse.urlencode(params)}")) as resp:
            body = json.load(resp)
        return body.get("token") or body["access_token"]

    def list_tags(self, image: Image) -> list[str]:
        """Every tag in the repository, following pagination."""
        tags: list[str] = []
        path = f"/v2/{image.repository}/tags/list?n=1000"
        while path:
            with self._request(image, "GET", path) as resp:
                tags.extend(json.load(resp).get("tags") or [])
                link = resp.headers.get("Link", "")
            match = re.search(r"<([^>]+)>;\s*rel=\"next\"", link)
            path = match.group(1) if match else ""
            if path.startswith("http"):
                path = urllib.parse.urlsplit(path)._replace(scheme="", netloc="").geturl()
        return tags

    def digest(self, image: Image, tag: str) -> str:
        """The manifest digest of a tag; an index digest for multi-arch images.

        Uses HEAD, which Docker Hub does not count toward its pull limit.
        """
        path = f"/v2/{image.repository}/manifests/{urllib.parse.quote(tag)}"
        with self._request(image, "HEAD", path, accept=MANIFEST_TYPES) as resp:
            return resp.headers["Docker-Content-Digest"]


def docker_hub_tags(image: Image, stop_before: datetime | None = None) -> list[DockerHubTag]:
    """Docker Hub tags with digests and push dates, most recently pushed first.

    Anonymous callers can page through only the newest 1,000 tags. Paging
    stops early at the first tag pushed before `stop_before`.
    """
    url = (
        f"https://hub.docker.com/v2/namespaces/{image.repository.replace('/', '/repositories/', 1)}"
        "/tags?page_size=100&ordering=last_updated"
    )
    tags: list[DockerHubTag] = []
    while url:
        try:
            with _open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT})) as resp:
                page = json.load(resp)
        except urllib.error.HTTPError as err:
            if err.code == 403 and tags:  # past the anonymous paging limit
                break
            raise
        for result in page["results"]:
            pushed = result.get("tag_last_pushed")
            tag = DockerHubTag(
                result["name"],
                result.get("digest"),
                datetime.fromisoformat(pushed) if pushed else None,
            )
            if stop_before and tag.pushed and tag.pushed < stop_before:
                return tags
            tags.append(tag)
        url = page.get("next")
    return tags


def _open(req: urllib.request.Request):
    """urlopen, retrying rate-limited and transient failures."""
    for attempt in range(5):
        try:
            return urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as err:
            if err.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            time.sleep(int(err.headers.get("Retry-After") or 2**attempt * 5))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(2**attempt * 5)
    raise AssertionError("unreachable")
