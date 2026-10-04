"""Publish tested GHCR images, validate platform manifests and protect released tags."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

COMPONENTS = ("backend", "web")
ARCHES = ("amd64", "arm64")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def command(*args):
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"{args[0]} {args[1]} failed; inspect the preceding workflow step")
    return result.stdout.strip()


def context(tag, repository, revision, run_id):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Invalid release tag")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository")
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(r"[0-9]+", run_id):
        raise ValueError("Invalid source revision or workflow run")
    return {"tag": tag, "repository": repository.lower(), "revision": revision, "run_id": run_id}


def image_name(ctx, component):
    if component not in COMPONENTS:
        raise ValueError("Unexpected image component")
    return f"ghcr.io/{ctx['repository']}-{component}"


def require_digest(value):
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ValueError("Invalid image digest")
    return value


def inspect_digest(reference, *, missing=False):
    result = subprocess.run(["docker", "buildx", "imagetools", "inspect", reference, "--format", "{{.Manifest.Digest}}"], capture_output=True, text=True)
    if result.returncode:
        # Only an explicit registry 404 can mean an unused version. Auth, network
        # and unknown errors must never authorize overwriting a tag.
        error = result.stderr.strip().lower()
        missing_reference = re.fullmatch(rf"(?:error:\s*)?{re.escape(reference.lower())}:\s*not found", error)
        missing_manifest = re.search(r"(?:^|:\s*)manifest unknown(?:\s*:|\s*$)", error)
        if missing and (missing_reference or missing_manifest):
            return None
        raise ValueError(f"Unable to inspect {reference}")
    return require_digest(result.stdout.strip())


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def push_candidates(ctx, arch, output):
    if arch not in ARCHES:
        raise ValueError("Unsupported platform")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not attempt.isdecimal():
        raise ValueError("Invalid run attempt")
    images = {}
    for component in COMPONENTS:
        image = image_name(ctx, component)
        candidate = f"{image}:candidate-{ctx['revision'][:12]}-{ctx['run_id']}-{attempt}-{arch}"
        command("docker", "tag", f"treasure-up-{component}:{ctx['tag'][1:]}", candidate)
        command("docker", "push", candidate)
        images[component] = {"image": image, "digest": inspect_digest(candidate)}
    write_json(output / f"{arch}.json", {**ctx, "arch": arch, "images": images})


def read_candidates(ctx, directory):
    records = {}
    files = list(directory.rglob("*.json"))
    if len(files) != len(ARCHES):
        raise ValueError("Expected exactly two platform records")
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        if any(record.get(key) != value for key, value in ctx.items()):
            raise ValueError("Candidate belongs to a different source or workflow run")
        arch = record.get("arch")
        if arch not in ARCHES or arch in records or set(record.get("images", {})) != set(COMPONENTS):
            raise ValueError("Unexpected or duplicate platform/component")
        for component, data in record["images"].items():
            if data.get("image") != image_name(ctx, component):
                raise ValueError("Candidate points outside the release repository")
            require_digest(data.get("digest"))
        records[arch] = record
    return records


def validate_index(index, expected=None):
    platforms = {}
    for manifest in index.get("manifests", []):
        platform = manifest.get("platform", {})
        os_name, arch = platform.get("os"), platform.get("architecture")
        if os_name == arch == "unknown" and manifest.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest":
            continue
        if os_name != "linux" or arch not in ARCHES or arch in platforms:
            raise ValueError("Unexpected image platform")
        platforms[arch] = require_digest(manifest.get("digest"))
    if set(platforms) != set(ARCHES) or (expected is not None and platforms != expected):
        raise ValueError("Image index does not contain both tested platform images")


def merge(ctx, directory, output):
    records = read_candidates(ctx, directory)
    images = {}
    for component in COMPONENTS:
        image = image_name(ctx, component)
        expected = {arch: records[arch]["images"][component]["digest"] for arch in ARCHES}
        candidate = f"{image}:candidate-{ctx['revision'][:12]}-{ctx['run_id']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
        command("docker", "buildx", "imagetools", "create", "--tag", candidate,
                "--annotation", f"index:org.opencontainers.image.version={ctx['tag'][1:]}",
                "--annotation", f"index:org.opencontainers.image.revision={ctx['revision']}",
                "--annotation", f"index:org.opencontainers.image.source=https://github.com/{ctx['repository']}",
                *(f"{image}@{expected[arch]}" for arch in ARCHES))
        digest = inspect_digest(candidate)
        index = json.loads(command("docker", "buildx", "imagetools", "inspect", f"{image}@{digest}", "--raw"))
        validate_index(index, expected)
        images[component] = {"image": image, "digest": digest, "reference": f"{image}@{digest}", "platforms": [f"linux/{arch}" for arch in ARCHES]}
    write_json(output, {**ctx, "images": images})


def read_images(path, ctx):
    data = json.loads(path.read_text(encoding="utf-8"))
    if any(data.get(key) != value for key, value in ctx.items()):
        raise ValueError("Image manifest belongs to a different release or workflow")
    if set(data.get("images", {})) != set(COMPONENTS):
        raise ValueError("Missing or unexpected release image")
    for component, value in data["images"].items():
        digest = require_digest(value.get("digest"))
        if value.get("image") != image_name(ctx, component) or value.get("reference") != f"{image_name(ctx, component)}@{digest}":
            raise ValueError("Image manifest points outside the release repository")
        if value.get("platforms") != [f"linux/{arch}" for arch in ARCHES]:
            raise ValueError("Missing release platforms")
    return data


def promote(ctx, path, channel):
    if channel not in {"preview", "stable"}:
        raise ValueError("Invalid release channel")
    data = read_images(path, ctx)
    version = ctx["tag"][1:]
    # Preflight every immutable version before changing either image.
    for value in data["images"].values():
        previous = inspect_digest(f"{value['image']}:{version}", missing=True)
        if previous is not None and previous != value["digest"]:
            raise ValueError("A version tag already points to a different digest; publish a new version")
    for value in data["images"].values():
        command("docker", "buildx", "imagetools", "create", "--tag", f"{value['image']}:{version}", value["reference"])
        if inspect_digest(f"{value['image']}:{version}") != value["digest"]:
            raise ValueError("Published image digest changed unexpectedly")
    alias = "latest" if channel == "stable" else "preview"
    for value in data["images"].values():
        target = f"{value['image']}:{alias}"
        old = inspect_digest(target, missing=True)
        if old:
            index = json.loads(command("docker", "buildx", "imagetools", "inspect", target, "--raw"))
            previous = index.get("annotations", {}).get("org.opencontainers.image.version", "")
            if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", previous):
                raise ValueError("Channel has no comparable version; refusing blind replacement")
            if tuple(map(int, previous.split("."))) > tuple(map(int, version.split("."))):
                continue
        command("docker", "buildx", "imagetools", "create", "--tag", target, value["reference"])
        if inspect_digest(target) != value["digest"]:
            raise ValueError("Channel image digest changed unexpectedly")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("push", "merge", "promote"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument("--arch", choices=ARCHES)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--channel", choices=("preview", "stable"), default="preview")
    args = parser.parse_args()
    try:
        ctx = context(args.tag, args.repository, args.revision, args.run_id)
        if args.action == "push" and args.arch and args.output:
            push_candidates(ctx, args.arch, args.output)
        elif args.action == "merge" and args.input and args.output:
            merge(ctx, args.input, args.output)
        elif args.action == "promote" and args.input:
            promote(ctx, args.input, args.channel)
        else:
            parser.error("Required action arguments missing")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Container release failed: {error}\n")
    print(f"Container {args.action} complete for {args.tag}")


if __name__ == "__main__":
    main()
