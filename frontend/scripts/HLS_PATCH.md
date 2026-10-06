# hls.js progressive fMP4 timeline patch

This project pins the official `hls.js` npm package to **1.7.3**. Its original
source, browser bundles and worker are Apache-2.0 licensed; the patch preserves
the package's license and copyright notices. Upstream source:
[passthrough-remuxer.ts](https://github.com/video-dev/hls.js/blob/v1.7.3/src/remux/passthrough-remuxer.ts).

## Defect and scope

For VOD progressive loading, `TransmuxerInterface.push` passes the same
`frag.start` (or `part.start`) for every chunk. `PassThroughRemuxer.remux` compares
each chunk's decoded start against that value and resets `initPTS` when the
difference exceeds `max(chunk duration, 1)`. With several short `moof` boxes
inside one HLS segment, later chunks can be mapped back to the beginning of the
segment. Interleaved single-track moofs also switch the timescale, triggering
the same reset. MSE appends overlapping timelines and can stall despite ongoing
downloads. This is reproducible with the package's actual source and parsed
sample timing; it is not specific to AV1.

The patch validates the timestamp origin on the first valid media chunk of each
`[playlist type, level, sequence number, part]`, preserving it for subsequent
progressive chunks (including the final flush). A decreasing chunk ID is treated
as a retry boundary. Empty chunks do not consume the boundary. I-frame remuxing
keeps the original behavior. `resetTimeStamp`, `resetNextTimestamp` and
`resetInitSegment` clear the identity before any early return, so seek,
discontinuity and track/initialization changes still revalidate normally.

The patch does not alter codecs, sample bytes, quality, audio format, buffering
targets or playlist timestamps. Native Apple/Atmos playback is unchanged.

## Reproducible installation and workers

`npm ci` runs `scripts/patch-hls-progressive.mjs` through `postinstall`. It verifies
the exact upstream version and SHA-256 for each original file, uses the installed
TypeScript parser to locate the same remux/reset methods, and verifies the
resulting SHA-256 before writing anything. Re-running it is idempotent. Unknown
versions or content fail closed rather than applying an approximate replacement.
`pretest` and `prebuild` verify the patched hashes again. The Docker build copies
the scripts before installing dependencies.

Patched artifacts are the original TypeScript source, full ESM bundle, full UMD
bundle (including its inline worker), and official standalone worker. The app
imports the ESM bundle and explicitly supplies the matching standalone worker
as a Vite asset, because ESM does not embed the UMD inline-worker factory. Worker
failure can use hls.js's existing main-thread fallback, which is patched too.
Unused light/minified distribution entry points are not used by this app and
are intentionally outside this manifest. Distribution source maps remain
upstream maps and should not be used to interpret the inserted lines.

## Verification and retirement

`src/player/hlsProgressive.test.mjs` executes the installed patched remuxer with
parsed sample timing. The unpatched baseline failed eight of the original nine
cases. Tests cover progressive blocks, audio/video timebases, fragment/part/level
boundaries, empty chunks, and all reset entry points. Player integration also
checks that the worker URL is passed to hls.js. Manual playback must cross
multiple segment boundaries and continue after seeking; unit tests do not
claim to validate browser MSE decoding.

On 2026-10-06 the official master source still contained the same timestamp
validation, and the v1.7.3 release notes did not describe a corresponding fix.
Focused official issue/PR searches did not identify a matching fix; this is not
a claim that no upstream report exists. No issue or PR was posted. This is a
local workaround, not an upstream release.

Before upgrading hls.js, review the upstream remuxer and run the same regression
suite against the new unpatched package. Remove the patch, install hooks,
manifest and Docker scripts-copy requirement when upstream passes the cases.
Keep the explicit worker asset and media integration checks as appropriate.
Do not merely regenerate the hashes for a new version without that review.
