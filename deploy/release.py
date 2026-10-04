"""Validate versioned releases and assemble only tracked source and expected CI assets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib


CLIENTS = (
    ("x86_64-pc-windows-msvc", ".exe", "windows-x64-unsigned.exe"),
    ("aarch64-apple-darwin", ".dmg", "macos-arm64-unnotarized.dmg"),
    ("x86_64-apple-darwin", ".dmg", "macos-x64-unnotarized.dmg"),
    ("android-debug", ".apk", "android-arm64-debug.apk"),
    ("ios-simulator-unsigned", ".zip", "ios-arm64-simulator.zip"),
)


def check_versions(root, tag):
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Release tag must be vMAJOR.MINOR.PATCH")
    version = tag[1:]
    values = {}
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
    mismatch = [name for name, actual in values.items() if actual != version]
    if mismatch:
        raise ValueError("Version mismatch: " + ", ".join(mismatch))
    notes = root / "docs/releases" / f"{tag}.md"
    if not notes.is_file():
        raise ValueError("Release notes missing")
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


def package(root, tag, artifacts, output):
    notes = check_versions(root, tag)
    selected = select_clients(artifacts)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty; stale files must not be released")
    output.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    tag_commit = subprocess.check_output(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"], cwd=root, text=True).strip()
    if commit != tag_commit:
        raise ValueError("Checkout does not match the release tag")
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
        (output / filename).write_bytes(subprocess.check_output(["git", "show", f"{commit}:{source}"], cwd=root))
    assets = []
    for path in sorted(output.iterdir()):
        assets.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest(path)})
    manifest = {"tag": tag, "commit": commit, "assets": assets}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sums = [f"{digest(p)}  {p.name}" for p in sorted(output.iterdir())]
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "package"))
    parser.add_argument("tag")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.action == "check":
            check_versions(root, args.tag)
        else:
            if args.artifacts is None or args.output is None:
                parser.error("package requires --artifacts and --output")
            package(root, args.tag, args.artifacts, args.output)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release validation failed: {error}\n")
    print(f"Release {args.action} complete: {args.tag}")


if __name__ == "__main__":
    main()
