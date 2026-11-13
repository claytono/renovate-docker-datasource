// Prints the tags Renovate could offer in place of a tag in use, as a JSON
// list. Reads {"inUse": [[versioning, tag, version], ...], "tags": [...]} on
// stdin, where version is the version Renovate resolved the tag to.
//
// Renovate's lookup keeps a release only if the dependency's versioning
// accepts it as a version, compatible with the tag in use and newer than
// the resolved version; this runs the same checks with Renovate's own
// versioning code. The module is internal to Renovate, so a Renovate update
// that moves it fails CI's dry run instead of merging.
import { text } from "node:stream/consumers";
import { get } from "/usr/local/renovate/dist/modules/versioning/index.js";

// Read as a stream: under `docker run -i` stdin is non-blocking, so a
// synchronous read can fail with EAGAIN.
const { inUse, tags } = JSON.parse(await text(process.stdin));
const current = inUse.map(([versioning, tag, version]) => [get(versioning), tag, version]);
const offerable = tags.filter((tag) =>
  current.some(
    ([api, value, version]) =>
      api.isVersion(tag) &&
      api.isCompatible(tag, value) &&
      api.isVersion(version) &&
      api.isGreaterThan(tag, version),
  ),
);
console.log(JSON.stringify(offerable));
