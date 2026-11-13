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

Every day, the job runs Renovate's lookup on the repositories listed in this
repo's `REPOSITORIES` Actions variable to find the Docker images they use, and
lists every tag of each image. Each new tag that Renovate could offer in place
of a tag in use gets a date:

- On Docker Hub, the tag's push date at the time the job first sees it.
- Everywhere else, the time the job first saw the tag.

Each tag's digest is recorded too. A tag that is rebuilt keeps its date, so
Renovate offers the new digest without waiting.

Renovate offers a tag only if the dependency's versioning reads it as a version
compatible with the tag in use and newer than it: with `1.31-alpine` in use,
`1.32-alpine` qualifies, and `1.32-windowsservercore` and `1.30-alpine` do not.
The job asks Renovate's own versioning code, with the versioning each
repository's config chooses, so it never repeats Renovate's rules. Tags that
can never be offered are not recorded; if a repository starts using one, it is
dated then.

The first time the job sees an image, every existing tag that qualifies gets a
date the same way, so a tag released shortly before is still held for the full
wait. The usual retention applies straight away: an older Docker Hub tag
outside the 30 days and the 5 newest is retired at once unless it is in use.

## The files

- `images/<image>.json` holds the recent tags of one image with their dates
  and digests, in the format Renovate's custom datasources read. A tag stays
  here while it is newer than the retention window in `config.toml`, is one
  of the few most recent (`count` in `config.toml`, currently 5), or is in use
  in any of the repositories. Renovate sees only the tags in this file.
- `images/<image>.txt`, beside it, lists the tags that have left the JSON
  file, one per line, so that an old tag is not mistaken for a new one.
  Keeping the two files together puts a tag leaving the JSON file next to its
  arrival in the list in a diff.

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

The preset sends each Docker dependency whose tag has a number, optionally
after a word, such as `1.2.3`, `v2.0`, `1.31-alpine`,
`python3.14-trixie-slim` or `web-v2.14.18`, to the `custom.docker-first-seen`
datasource. Renovate fetches that image's file from this repo through
GitHub's contents API. Which tags count as versions is still up to the
repository's versioning rules, as with the Docker datasource; a rule that
reads `python3.14-trixie-slim` as Python 3.14 works unchanged.

Floating tags such as `latest` or `stable` stay on the Docker datasource.
Renovate offers only new digests for these, which cannot be dated, so they
arrive without waiting, as before. A custom datasource can't look up the
digest of a tag that isn't a version, so they couldn't move anyway.

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
  private repositories in `REPOSITORIES`. Renovate's lookup also uses it for
  GitHub, which allows far more requests than an anonymous caller.

Both are set from 1Password by OpenTofu in claytono/infra.

To run it locally, with Docker running and in the Nix shell, set
`REPOSITORIES` to the same list, and `READ_TOKEN` too unless git already has
credentials for the private repositories:

```sh
export REPOSITORIES="$(gh variable get REPOSITORIES -R claytono/renovate-docker-datasource)"
uv run python -m renovate_docker_datasource
```

`--image NAME` (repeatable) updates only the named images and removes
nothing; the repositories are still cloned to find the tags in use, and an
image none of them uses is skipped.

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
