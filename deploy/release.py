"""Validate versioned releases and assemble only tracked source and expected CI assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import zipfile

from container_release import context, read_images


CLIENTS = (
    ("ios-unsigned", ".ipa", "ios-unsigned.ipa"),
)

DEPLOYMENT_FILES = (
    "compose.yaml", "compose.light.yaml", "compose.setup.yaml", ".env.example",
)
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
REGISTRY_VERSION = r"(treasure-up-(?:backend|web):)[0-9]+\.[0-9]+\.[0-9]+"


def release_changes(subjects):
    """Use user-facing commit subjects; leave CI/version bookkeeping out of release notes."""
    changes = []
    for subject in subjects.splitlines():
        match = re.fullmatch(r"(?:feat|fix|perf|refactor)(?:\([^)]*\))?!?:\s*(.+)", subject.strip())
        if match and match[1] not in changes:
            changes.append(match[1])
    return "\n".join(f"- {change}" for change in changes) or "- 更新安装与使用体验。"


def resolve_version(root, mode, requested=""):
    """Choose a monotonic version; retry only names an already-existing tag."""
    requested = requested.removeprefix("v")
    tags = subprocess.check_output(["git", "tag", "--list", "v*"], cwd=root, text=True).splitlines()
    known = [tag[1:] for tag in tags if VERSION.fullmatch(tag[1:])]
    current = json.loads((root / "release.json").read_text(encoding="utf-8"))["version"]
    if not VERSION.fullmatch(current):
        raise ValueError("Invalid current version")
    greatest = max(tuple(map(int, version.split("."))) for version in [current, *known])
    if mode == "retry":
        if not VERSION.fullmatch(requested) or requested not in known:
            raise ValueError("Retry requires an existing vMAJOR.MINOR.PATCH tag")
        return "v" + requested
    if mode == "custom":
        if not VERSION.fullmatch(requested) or tuple(map(int, requested.split("."))) <= greatest:
            raise ValueError("New version must be greater than every existing version")
        return "v" + requested
    if mode not in {"patch", "minor", "major"} or requested:
        raise ValueError("Choose patch/minor/major, or custom/retry with a version")
    index = {"major": 0, "minor": 1, "patch": 2}[mode]
    values = list(greatest)
    values[index] += 1
    for position in range(index + 1, 3):
        values[position] = 0
    return "v" + ".".join(map(str, values))


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


def prepare(root, version, channel, *, automatic_notes=False):
    """Prepare a service release; the native iOS app is versioned independently."""
    if not VERSION.fullmatch(version) or channel not in {"preview", "stable"}:
        raise ValueError("Expected MAJOR.MINOR.PATCH and preview/stable channel")
    planned = {}
    for name in ("package.json", "package-lock.json"):
        path = root / "frontend" / name
        source = path.read_text(encoding="utf-8")
        if name == "package.json":
            planned[path] = _replace_package_version(source, version)
            continue
        data = json.loads(source)
        data["version"] = version
        data["packages"][""]["version"] = version
        planned[path] = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    changes = {
        "backend/app/main.py": (r'(FastAPI\([^\n]*version=")[^"]+', rf'\g<1>{version}'),
        "compose.yaml": (r'(image: treasure-up-(?:backend|web):)[^\s]+', rf'\g<1>{version}'),
        "compose.registry.yaml": (REGISTRY_VERSION, rf'\g<1>{version}'),
        "compose.registry.light.yaml": (REGISTRY_VERSION, rf'\g<1>{version}'),
        "compose.setup.yaml": (REGISTRY_VERSION, rf'\g<1>{version}'),
    }
    for name, (pattern, replacement) in changes.items():
        path = root / name
        text, count = re.subn(pattern, replacement, path.read_text(encoding="utf-8"))
        if not count:
            raise ValueError(f"Version field missing: {name}")
        planned[path] = text
    planned[root / "release.json"] = json.dumps({"version": version, "channel": channel, "distribution": "dockerhub"}, indent=2) + "\n"
    notes = root / f"docs/releases/v{version}.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    if not notes.exists():
        if automatic_notes:
            previous = subprocess.run(["git", "describe", "--tags", "--match", "v*", "--abbrev=0"],
                                      cwd=root, capture_output=True, text=True)
            revision_range = [f"{previous.stdout.strip()}..HEAD"] if previous.returncode == 0 else ["HEAD", "-20"]
            subjects = subprocess.check_output(["git", "log", "--no-merges", "--format=%s",
                                                *revision_range], cwd=root, text=True, encoding="utf-8")
            namespace = os.environ.get("DOCKERHUB_NAMESPACE", "yunyunjuan")
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,38}", namespace):
                raise ValueError("Invalid Docker Hub namespace")
            planned[notes] = (f"# Treasure Up v{version}\n\n{release_changes(subjects)}\n\n"
                              "Docker 安装，无需下载源码或 ZIP：\n\n```sh\n"
                              "docker run --rm -it --pull always --user 0 -v /var/run/docker.sock:/var/run/docker.sock "
                              f"-v treasure-up-config:/config docker.io/{namespace}/treasure-up-backend:{version} python /app/install.py\n```\n\n"
                              f"iPhone / iPad 请下载 `treasure-up-v{version}-ios-unsigned.ipa`，自行签名后安装；步骤见附件 `IOS-INSTALL.md`。\n\n"
                              "升级前请备份数据库和配置，保留原数据卷，不要执行 `docker compose down -v`。\n")
        else:
            planned[notes] = f"# Treasure Up v{version}\n\n<!-- RELEASE_NOTES_REQUIRED -->\n\n填写本版变更和升级说明，再推送版本标签。\n"
    for path, content in planned.items():
        path.write_text(content, encoding="utf-8")


def check_versions(root, tag):
    """Validate the service tag without coupling it to the iOS app version."""
    if not tag.startswith("v") or not VERSION.fullmatch(tag[1:]):
        raise ValueError("Release tag must be vMAJOR.MINOR.PATCH")
    version = tag[1:]
    values = {}
    release = json.loads((root / "release.json").read_text(encoding="utf-8"))
    values["release.json"] = release["version"]
    if release.get("channel") not in {"preview", "stable"}:
        raise ValueError("Invalid release channel")
    for name in ("package.json", "package-lock.json"):
        data = json.loads((root / "frontend" / name).read_text(encoding="utf-8"))
        values[f"frontend/{name}"] = data["version"]
        if name == "package-lock.json":
            values["frontend/lock root"] = data["packages"][""]["version"]
    api = (root / "backend/app/main.py").read_text(encoding="utf-8")
    values["FastAPI"] = re.search(r'FastAPI\([^\n]*version="([^"]+)"', api).group(1)
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    for image in ("backend", "web"):
        values[f"compose/{image}"] = re.search(rf"image: treasure-up-{image}:([^\s]+)", compose).group(1)
    for name in ("compose.registry.yaml", "compose.registry.light.yaml", "compose.setup.yaml"):
        tags = re.findall(r"treasure-up-(?:backend|web):([0-9]+\.[0-9]+\.[0-9]+)", (root / name).read_text(encoding="utf-8"))
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


def validate_device_archive(path):
    """Check the mandatory device payload before publishing an unsigned IPA."""
    try:
        with zipfile.ZipFile(path) as archive:
            prefix = "Payload/TreasureUp.app/"
            names = archive.namelist()
            if names.count(prefix + "Info.plist") != 1:
                raise ValueError("Device IPA must contain one Payload/TreasureUp.app/Info.plist")
            if any(name.startswith("/") or ".." in Path(name).parts or "\\" in name for name in names):
                raise ValueError("Device IPA contains unsafe paths")
            info = plistlib.loads(archive.read(prefix + "Info.plist"))
            if not isinstance(info, dict) or info.get("CFBundleSupportedPlatforms") != ["iPhoneOS"]:
                raise ValueError("Device IPA must target iPhoneOS")
            executable = info.get("CFBundleExecutable")
            if not isinstance(executable, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", executable):
                raise ValueError("Device app executable missing")
            if names.count(prefix + executable) != 1:
                raise ValueError("Device app executable missing or duplicated")
            with archive.open(prefix + executable) as binary:
                header = binary.read(8)
            # MH_MAGIC_64 and CPU_TYPE_ARM64. The native packager also verifies
            # LC_BUILD_VERSION=iOS, which distinguishes arm64 simulators.
            if header != b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01":
                raise ValueError("Device app must contain an arm64 Mach-O executable")
            if any(name.endswith("embedded.mobileprovision") for name in names):
                raise ValueError("Unsigned IPA must not contain a provisioning profile")
    except (zipfile.BadZipFile, plistlib.InvalidFileException) as error:
        raise ValueError("Invalid device IPA archive") from error


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
        validate_device_archive(candidate)
        selected.append((candidate, filename))
    return selected


def deployment_content(name, source, images):
    """Resolve YAML anchors, remove build recipes, and pin runtime images by digest."""
    if name not in {"compose.yaml", "compose.setup.yaml"}:
        return source
    import yaml
    config = yaml.safe_load(source)
    if not isinstance(config, dict) or not isinstance(config.get("services"), dict):
        raise ValueError(f"Invalid deployment compose: {name}")
    config = {key: value for key, value in config.items() if not key.startswith("x-")}
    for service in config["services"].values():
        service.pop("build", None)
        value = service.get("image", "")
        for component in ("backend", "web"):
            if f"treasure-up-{component}:" in value:
                service["image"] = images["images"][component]["reference"]
                break
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def validate_device_metadata(artifacts, commit, ipa):
    metadata_path = artifacts / "treasure-up-ios-unsigned/ios-device-build.json"
    if metadata_path.is_symlink() or not metadata_path.resolve().is_relative_to(artifacts.resolve()):
        raise ValueError("Device build metadata escapes the artifact directory")
    device = json.loads(metadata_path.read_text(encoding="utf-8"))
    if (device.get("revision") != commit or device.get("platform") != "iOS"
            or device.get("architectures") != ["arm64"] or device.get("signing") != "unsigned"
            or device.get("requires_resigning") is not True):
        raise ValueError("Device IPA build metadata does not match this release")
    if device.get("sha256") != digest(ipa):
        raise ValueError("Device IPA differs from the validated native build")
    return device


def package(root, tag, artifacts, output, images_file=None, repository=None, run_id=None, namespace=None):
    notes = check_versions(root, tag)
    selected = select_clients(artifacts)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty; stale files must not be released")
    output.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    tag_commit = subprocess.check_output(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"], cwd=root, text=True).strip()
    if commit != tag_commit:
        raise ValueError("Checkout does not match the release tag")
    device = validate_device_metadata(artifacts, commit, selected[0][0])
    images = None
    if images_file is not None:
        images = read_images(images_file, context(tag, repository, commit, run_id, namespace))
    subprocess.run([
        "git", "-c", "core.autocrlf=false", "archive", "--format=zip", f"--prefix=treasure-up-{tag}/",
        f"--output={output.resolve() / f'treasure-up-{tag}-source.zip'}", commit,
    ], cwd=root, check=True)
    for source, filename in selected:
        shutil.copyfile(source, output / f"treasure-up-{tag}-{filename}")
    for source, filename in (
        ("frontend/public/userscripts/treasure-up.user.js", "treasure-up.user.js"),
        (notes.relative_to(root).as_posix(), "RELEASE-NOTES.md"),
        ("native/apple/INSTALL.md", "IOS-INSTALL.md"),
    ):
        content = subprocess.check_output(["git", "show", f"{commit}:{source}"], cwd=root)
        if filename == "RELEASE-NOTES.md" and b"RELEASE_NOTES_REQUIRED" in content:
            raise ValueError("Tagged release notes are incomplete")
        (output / filename).write_bytes(content)
    (output / "ios-device-build.json").write_text(json.dumps(device, indent=2) + "\n", encoding="utf-8")
    if images is not None:
        (output / "release-images.json").write_text(json.dumps(images, indent=2) + "\n", encoding="utf-8")
        prefix = f"treasure-up-{tag}/"
        with zipfile.ZipFile(output / f"treasure-up-{tag}-docker.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
            for name in DEPLOYMENT_FILES:
                content = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=root).decode("utf-8")
                bundle.writestr(prefix + name, deployment_content(name, content, images))
            bundle.writestr(prefix + "release-images.json", json.dumps(images, indent=2) + "\n")
            bundle.writestr(prefix + "README.txt", f"Treasure Up {tag}\n\n需要 Docker Engine / Docker Desktop 和 Docker Compose v2。无需 Python、Git 或编译。\n\n"
                            "首次安装（Linux）:\n  docker compose -f compose.setup.yaml run --rm --user \"$(id -u):$(id -g)\" setup\n"
                            "首次安装（Windows / macOS Docker Desktop）:\n  docker compose -f compose.setup.yaml run --rm setup\n"
                            "启动:\n  docker compose pull\n  docker compose up -d --wait\n"
                            "访问 http://localhost:8788，使用初始化时显示的管理员账密。\n"
                            "设置 HTTPS 地址：在 setup 命令后加 --origin https://video.example.com\n"
                            "局域网 / NAS：在 setup 命令后加 --origin http://192.168.1.20:8788 --bind-address 0.0.0.0（替换为实际地址）。\n"
                            "轻量模式：docker compose -f compose.yaml -f compose.light.yaml up -d --wait\n\n"
                            "升级先备份并保留 .env 和所有数据卷。覆盖此包的配置文件后重新 pull / up。\n"
                            "镜像已固定为经测试的不可变摘要。不要执行 docker compose down -v。\n")
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
    parser.add_argument("action", choices=("prepare", "check", "package", "resolve"))
    parser.add_argument("tag", nargs="?", default="")
    parser.add_argument("--mode", choices=("patch", "minor", "major", "custom", "retry"), default="patch")
    parser.add_argument("--automatic-notes", action="store_true")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--repository")
    parser.add_argument("--run-id")
    parser.add_argument("--namespace", default=os.environ.get("DOCKERHUB_NAMESPACE", ""))
    parser.add_argument("--channel", choices=("preview", "stable"), default="preview")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.action == "resolve":
            tag = resolve_version(root, args.mode, args.tag)
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                    output.write(f"tag={tag}\n")
            print(tag)
        elif args.action == "prepare":
            prepare(root, args.tag.removeprefix("v"), args.channel, automatic_notes=args.automatic_notes)
        elif args.action == "check":
            check_versions(root, args.tag)
        else:
            if args.artifacts is None or args.output is None:
                parser.error("package requires --artifacts and --output")
            if args.images is None or not args.repository or not args.run_id:
                parser.error("package requires --images, --repository and --run-id")
            package(root, args.tag, args.artifacts, args.output, args.images, args.repository, args.run_id, args.namespace)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release validation failed: {error}\n")
    print(f"Release {args.action} complete: {args.tag}")


if __name__ == "__main__":
    main()
