# renovate-docker-datasource

This repo lets Renovate wait until a new Docker image version is a set number
of days old, such as 14, before proposing it.

Renovate's `minimumReleaseAge` setting does that for other dependencies, but
it needs to know when each version came out, and container registries don't
say:

- Docker Hub gives the date of a tag's last push. Images that are rebuilt on
  a schedule, such as linuxserver's weekly builds or Docker's official
  images, push their version tags again with each rebuild, so the wait keeps
  starting over and the update arrives late or never.
- GHCR and other registries give no date, so their updates arrive at once.

This repo keeps its own dates. A daily job gives each tag a date the first
time it sees it and keeps that date while the tag stays in its image's file.
A shared Renovate preset points Renovate at those dates.

## How the dates are set

Every day, the job finds the Docker images used in the repositories listed in
this repo's `REPOSITORIES` Actions variable, using Renovate's own dependency
extraction, and lists every tag of each image. Each tag it has not seen
before gets a date:

- On Docker Hub, the tag's push date at the time the job first sees it.
- Everywhere else, the time the job first saw the tag.

Each tag's digest is recorded too. A tag that is rebuilt keeps its date, so
Renovate offers the new digest without waiting.

The first time the job sees an image, it dates the tags in use and, on Docker
Hub, the tags with push dates. Every other existing tag goes straight to the
seen list and is not offered unless a repository starts using it, which dates
it as new. A repository that is already behind on such an image is offered
nothing for it until a newer tag appears.

## The files

- `images/<image>.json` holds the recent tags of one image with their dates
  and digests, in the format Renovate's custom datasources read. A tag stays
  here while it is newer than the retention window in `config.toml`, is one
  of the few most recent (`count` in `config.toml`, currently 5), or is in use
  in any of the repositories. Renovate sees only the tags in this file.
- `images/<image>.txt`, beside it, lists every other tag the job has seen, one
  per line, so that an old tag is not mistaken for a new one. Keeping the two
  files together puts a tag leaving the JSON file next to its arrival in the
  list in a diff.

When no repository uses an image any more, the job removes both files.

Image names are written in full, so `nginx` is
`images/docker.io/library/nginx.json` and `ghcr.io/foo/bar` is
`images/ghcr.io/foo/bar.json`.

## Using the preset

Add the preset to a repository's Renovate config and set the wait:

```json
{
  "extends": ["github>claytono/renovate-docker-datasource"],
  "minimumReleaseAge": "14 days"
}
```

The preset sends each Docker dependency whose tag looks like a version, such
as `1.2.3`, `v2.0` or `1.31-alpine`, to the `custom.docker-first-seen`
datasource. Renovate fetches that image's file from this repo through
GitHub's contents API.

Other tags stay on the Docker datasource:

- Floating tags such as `latest` or `stable`. Renovate offers only new digests
  for these, which cannot be dated, so they arrive without waiting, as before.
- Tags that don't start with a number, optionally after a `v`, such as
  `python3.14-trixie-slim`.

Rules in the repository that match `matchDatasources: ["docker"]` no longer
match the dependencies the preset moves. Add `custom.docker-first-seen` to
those rules.

The repository must also be in this repo's `REPOSITORIES` variable, or its
images never get files here. The variable holds `owner/name` entries separated
by spaces or newlines, and adding one needs no pull request:

```sh
gh variable set REPOSITORIES -R claytono/renovate-docker-datasource \
  --body "$(gh variable get REPOSITORIES -R claytono/renovate-docker-datasource)
claytono/new-repo"
```

## Running the job

The `Update image data` workflow runs daily. It opens a pull request with the
changes and enables auto-merge, so the pull request merges once CI passes. It
needs two secrets:

- `PAT`: a classic token that opens the pull request and turns on auto-merge.
  A pull request opened with `GITHUB_TOKEN` would not run CI.
- `READ_TOKEN`: a fine-grained token with read access to the contents of the
  private repositories in `REPOSITORIES`.

Both are set from 1Password by OpenTofu in claytono/infra.

To run it locally, with Docker running and in the Nix shell, set
`REPOSITORIES` to the same list, and `READ_TOKEN` too unless git already has
credentials for the private repositories:

```sh
export REPOSITORIES="$(gh variable get REPOSITORIES -R claytono/renovate-docker-datasource)"
uv run python -m renovate_docker_datasource
```

`--image NAME` (repeatable) updates only the named images and removes
nothing; the repositories are still cloned to find the tags in use.

`--checkout DIR` reads a local checkout instead of cloning. It stands in for
the whole repository list, so give one `--checkout` per repository in
`REPOSITORIES`. With fewer, tags used only in the missing repositories move to
the seen list, and without `--image` the files of images only they use are
removed.

`python -m renovate_docker_datasource.check` checks that the files are in the
shape the job writes. It runs in pre-commit and CI.

CI also runs the whole job on each pull request that changes more than the
data files, and throws the results away. A change that breaks the job, such
as a new Renovate image, fails that check, which is what lets Renovate merge
its own image updates. `--allow-registry-errors` keeps an outage at a
registry from failing CI; any other error still does.
