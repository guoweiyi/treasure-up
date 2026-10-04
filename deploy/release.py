"""Validate versioned releases and assemble only tracked source and expected CI assets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
import zipfile

from container_release import context, read_images


CLIENTS = (
    ("x86_64-pc-windows-msvc", ".exe", "windows-x64-unsigned.exe"),
    ("aarch64-apple-darwin", ".dmg", "macos-arm64-unnotarized.dmg"),
    ("x86_64-apple-darwin", ".dmg", "macos-x64-unnotarized.dmg"),
    ("android-debug", ".apk", "android-arm64-debug.apk"),
    ("ios-simulator-unsigned", ".zip", "ios-arm64-simulator.zip"),
)

DEPLOYMENT_FILES = (
    "compose.yaml", "compose.light.yaml", "compose.registry.yaml", "compose.registry.light.yaml",
    "deploy/start.py", "deploy/bootstrap.py",
)


def _replace_package_version(text, version):
    """Replace the top-level JSON value without reformatting package scripts."""
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        raise ValueError("Package version field missing")
    decoder = json.JSONDecoder()
    whitespace = re.compile(r"\s*")
    cursor = whitespace.match(text).end() + 1  # Opening object brace.
    locations = []
    while True:
        cursor = whitespace.match(text, cursor).end()
        if text[cursor] == "}":
            break
        key, cursor = decoder.raw_decode(text, cursor)
        cursor = whitespace.match(text, cursor).end() + 1  # Colon, already validated.
        start = whitespace.match(text, cursor).end()
        _, cursor = decoder.raw_decode(text, start)
        if key == "version":
            locations.append((start, cursor))
        cursor = whitespace.match(text, cursor).end()
        if text[cursor] == ",":
            cursor += 1
    if len(locations) != 1:
        raise ValueError("Package must have exactly one top-level version")
    start, end = locations[0]
    return text[:start] + json.dumps(version) + text[end:]


def prepare(root, version, channel):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) or channel not in {"preview", "stable"}:
        raise ValueError("Expected MAJOR.MINOR.PATCH and preview/stable channel")
    planned = {}
    for folder in ("frontend", "native"):
        for name in ("package.json", "package-lock.json"):
            path = root / folder / name
            source = path.read_text(encoding="utf-8")
            if name == "package.json":
                planned[path] = _replace_package_version(source, version)
                continue
            data = json.loads(source)
            data["version"] = version
            data["packages"][""]["version"] = version
            planned[path] = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    changes = {
        "native/src-tauri/Cargo.toml": (r'(?m)^version = "[^"]+"', f'version = "{version}"'),
        "native/src-tauri/Cargo.lock": (r'(name = "treasure-up-native"\nversion = ")[^"]+', rf'\g<1>{version}'),
        "native/src-tauri/tauri.conf.json": (r'"version": "[^"]+"', f'"version": "{version}"'),
        "backend/app/main.py": (r'(FastAPI\([^\n]*version=")[^"]+', rf'\g<1>{version}'),
        "compose.yaml": (r'(image: treasure-up-(?:backend|web):)[^\s]+', rf'\g<1>{version}'),
        "compose.registry.yaml": (r'(ghcr\.io/[^\s}]+:)[0-9]+\.[0-9]+\.[0-9]+', rf'\g<1>{version}'),
        "compose.registry.light.yaml": (r'(ghcr\.io/[^\s}]+:)[0-9]+\.[0-9]+\.[0-9]+', rf'\g<1>{version}'),
    }
    for name, (pattern, replacement) in changes.items():
        path = root / name
        text, count = re.subn(pattern, replacement, path.read_text(encoding="utf-8"))
        if not count:
            raise ValueError(f"Version field missing: {name}")
        planned[path] = text
    planned[root / "release.json"] = json.dumps({"version": version, "channel": channel}, indent=2) + "\n"
    notes = root / f"docs/releases/v{version}.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    if not notes.exists():
        planned[notes] = f"# Treasure Up v{version}\n\n<!-- RELEASE_NOTES_REQUIRED -->\n\n填写本版变更和升级说明，再推送版本标签。\n"
    for path, content in planned.items():
        path.write_text(content, encoding="utf-8")


def check_versions(root, tag):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Release tag must be vMAJOR.MINOR.PATCH")
    version = tag[1:]
    values = {}
    release = json.loads((root / "release.json").read_text(encoding="utf-8"))
    values["release.json"] = release["version"]
    if release.get("channel") not in {"preview", "stable"}:
        raise ValueError("Invalid release channel")
    for folder in ("frontend", "native"):
        for name in ("package.json", "package-lock.json"):
            data = json.loads((root / folder / name).read_text(encoding="utf-8"))
            values[f"{folder}/{name}"] = data["version"]
            if name == "package-lock.json":
                values[f"{folder}/lock root"] = data["packages"][""]["version"]
    tauri = root / "native/src-tauri"
    values["tauri.conf.json"] = json.loads((tauri / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    values["Cargo.toml"] = tomllib.loads((tauri / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"]
    lock = tomllib.loads((tauri / "Cargo.lock").read_text(encoding="utf-8"))
    values["Cargo.lock"] = next(p["version"] for p in lock["package"] if p["name"] == "treasure-up-native")
    api = (root / "backend/app/main.py").read_text(encoding="utf-8")
    values["FastAPI"] = re.search(r'FastAPI\([^\n]*version="([^"]+)"', api).group(1)
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    for image in ("backend", "web"):
        values[f"compose/{image}"] = re.search(rf"image: treasure-up-{image}:([^\s]+)", compose).group(1)
    for name in ("compose.registry.yaml", "compose.registry.light.yaml"):
        tags = re.findall(r"ghcr\.io/[^\s}]+:([0-9]+\.[0-9]+\.[0-9]+)", (root / name).read_text(encoding="utf-8"))
        if not tags:
            raise ValueError(f"Version field missing: {name}")
        for index, match in enumerate(tags):
            values[f"{name}/{index}"] = match
    mismatch = [name for name, actual in values.items() if actual != version]
    if mismatch:
        raise ValueError("Version mismatch: " + ", ".join(mismatch))
    notes = root / "docs/releases" / f"{tag}.md"
    if not notes.is_file():
        raise ValueError("Release notes missing")
    if "RELEASE_NOTES_REQUIRED" in notes.read_text(encoding="utf-8"):
        raise ValueError("Complete the release notes before publishing")
    return notes


def select_clients(artifacts):
    artifacts = artifacts.resolve()
    selected = []
    for target, suffix, filename in CLIENTS:
        folder = artifacts / f"treasure-up-{target}"
        candidates = [p for p in folder.rglob(f"*{suffix}") if p.is_file()]
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one {suffix} for {target}, found {len(candidates)}")
        candidate = candidates[0]
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(artifacts):
            raise ValueError("Client artifact escapes input directory")
        if candidate.stat().st_size == 0:
            raise ValueError("Client artifact is empty")
        selected.append((candidate, filename))
    return selected


def package(root, tag, artifacts, output, images_file=None, repository=None, run_id=None):
    notes = check_versions(root, tag)
    selected = select_clients(artifacts)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty; stale files must not be released")
    output.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    tag_commit = subprocess.check_output(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"], cwd=root, text=True).strip()
    if commit != tag_commit:
        raise ValueError("Checkout does not match the release tag")
    images = None
    if images_file is not None:
        images = read_images(images_file, context(tag, repository, commit, run_id))
    subprocess.run([
        "git", "-c", "core.autocrlf=false", "archive", "--format=zip", f"--prefix=treasure-up-{tag}/",
        f"--output={output.resolve() / f'treasure-up-{tag}-source.zip'}", commit,
    ], cwd=root, check=True)
    for source, filename in selected:
        shutil.copyfile(source, output / f"treasure-up-{tag}-{filename}")
    for source, filename in (
        ("frontend/public/userscripts/treasure-up.user.js", "treasure-up.user.js"),
        (notes.relative_to(root).as_posix(), "RELEASE-NOTES.md"),
    ):
        content = subprocess.check_output(["git", "show", f"{commit}:{source}"], cwd=root)
        if filename == "RELEASE-NOTES.md" and b"RELEASE_NOTES_REQUIRED" in content:
            raise ValueError("Tagged release notes are incomplete")
        (output / filename).write_bytes(content)
    if images is not None:
        (output / "release-images.json").write_text(json.dumps(images, indent=2) + "\n", encoding="utf-8")
        prefix = f"treasure-up-{tag}/"
        with zipfile.ZipFile(output / f"treasure-up-{tag}-docker.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
            for name in DEPLOYMENT_FILES:
                content = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=root).decode("utf-8")
                if name.startswith("compose.registry"):
                    for component, value in images["images"].items():
                        content = re.sub(rf"ghcr\.io/[^\s}}]+-{component}:[0-9]+\.[0-9]+\.[0-9]+", value["reference"], content)
                bundle.writestr(prefix + name, content)
            bundle.writestr(prefix + "release-images.json", json.dumps(images, indent=2) + "\n")
            bundle.writestr(prefix + "README.txt", f"Treasure Up {tag}\n\nPython 3.10+ / Docker Compose v2\n\nRun: python deploy/start.py --prebuilt\nLight mode: python deploy/start.py --prebuilt --light\n\nImages default to verified immutable digests. Preserve .env and Docker volumes when upgrading.\nInitial GHCR packages may be private: sign in using docker login ghcr.io, or ask the package owner to enable Public visibility.\nDo not run docker compose down -v during upgrades.\n")
    assets = []
    for path in sorted(output.iterdir()):
        assets.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest(path)})
    manifest = {"tag": tag, "commit": commit, "assets": assets}
    if images is not None:
        manifest["images"] = images["images"]
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sums = [f"{digest(p)}  {p.name}" for p in sorted(output.iterdir())]
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "package"))
    parser.add_argument("tag")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--repository")
    parser.add_argument("--run-id")
    parser.add_argument("--channel", choices=("preview", "stable"), default="preview")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.action == "prepare":
            prepare(root, args.tag.removeprefix("v"), args.channel)
        elif args.action == "check":
            check_versions(root, args.tag)
        else:
            if args.artifacts is None or args.output is None:
                parser.error("package requires --artifacts and --output")
            if args.images is None or not args.repository or not args.run_id:
                parser.error("package requires --images, --repository and --run-id")
            package(root, args.tag, args.artifacts, args.output, args.images, args.repository, args.run_id)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release validation failed: {error}\n")
    print(f"Release {args.action} complete: {args.tag}")


if __name__ == "__main__":
    main()
