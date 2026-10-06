import importlib.util
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import zipfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

spec = importlib.util.spec_from_file_location("release", Path(__file__).parents[1] / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
TAG = "v" + json.loads((Path(__file__).resolve().parents[2] / "release.json").read_text())["version"]


def simulator_archive(path, *, platform="iPhoneSimulator", executable=True):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("TreasureUp.app/Info.plist", plistlib.dumps({
            "CFBundleSupportedPlatforms": [platform], "CFBundleExecutable": "TreasureUp",
            "CFBundleShortVersionString": "0.4.2", "CFBundleVersion": "3",
        }))
        if executable:
            archive.writestr("TreasureUp.app/TreasureUp", b"fixture-binary")


def artifacts(tmp_path):
    for target, suffix, _ in release.CLIENTS:
        path = tmp_path / f"treasure-up-{target}" / f"client{suffix}"
        path.parent.mkdir()
        simulator_archive(path)
    return tmp_path


def test_requires_ios_archive_and_ignores_obsolete_platforms(tmp_path):
    root = artifacts(tmp_path)
    (root / "treasure-up-ios-simulator/client.zip").unlink()
    legacy = root / "treasure-up-android-debug"
    legacy.mkdir()
    (legacy / "client.apk").write_bytes(b"must-not-ship")
    with pytest.raises(ValueError, match="exactly one .zip for ios-simulator"):
        release.select_clients(root)


def test_rejects_ambiguous_or_empty_client(tmp_path):
    root = artifacts(tmp_path)
    extra = root / "treasure-up-ios-simulator/extra.zip"
    extra.write_bytes(b"stale")
    with pytest.raises(ValueError, match="found 2"):
        release.select_clients(root)
    extra.unlink()
    (extra.parent / "client.zip").write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        release.select_clients(root)


def test_only_ios_simulator_is_selected_with_explicit_output_label(tmp_path):
    root = artifacts(tmp_path)
    legacy = root / "treasure-up-x86_64-pc-windows-msvc"
    legacy.mkdir()
    (legacy / "client.exe").write_bytes(b"must-not-ship")
    selected = release.select_clients(root)
    assert selected == [(root / "treasure-up-ios-simulator/client.zip", "ios-simulator-ad-hoc.zip")]


@pytest.mark.parametrize("invalid", ["not-zip", "empty-zip", "device", "missing-executable"])
def test_rejects_placeholder_or_device_archives(tmp_path, invalid):
    root = artifacts(tmp_path)
    path = root / "treasure-up-ios-simulator/client.zip"
    if invalid == "not-zip":
        path.write_bytes(b"not-an-app")
    elif invalid == "empty-zip":
        with zipfile.ZipFile(path, "w"):
            pass
    else:
        simulator_archive(path, platform="iPhoneOS" if invalid == "device" else "iPhoneSimulator",
                          executable=invalid != "missing-executable")
    with pytest.raises(ValueError, match="[Ss]imulator"):
        release.select_clients(root)


def test_rejects_symlinked_client_outside_artifact_directory(tmp_path):
    inputs = tmp_path / "artifacts"
    inputs.mkdir()
    artifacts(inputs)
    archive = inputs / "treasure-up-ios-simulator/client.zip"
    archive.unlink()
    external = tmp_path / "outside.zip"
    simulator_archive(external)
    try:
        archive.symlink_to(external)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows does not grant symlink creation; this security case also runs in Linux CI")
        raise
    with pytest.raises(ValueError, match="escapes input directory"):
        release.select_clients(inputs)


@pytest.mark.parametrize("tag", ["main", "v1.2", "v1.2.3/../../other", "v1.2.3\n", "v1.2.3$(id)"])
def test_rejects_non_release_tags_before_reading_files(tmp_path, tag):
    with pytest.raises(ValueError, match="Release tag"):
        release.check_versions(tmp_path, tag)


def versioned_project(root):
    """A small complete release tree, independent of the working copy version."""
    package = ('{\n  "metadata": {"version": "1.2.3"},\n  "version" : "1.2.3",\n'
               '  "scripts": {"format:check":\n    "prettier --check src package.json"}\n}\n')
    lock = json.dumps({"version": "1.2.3", "packages": {
        "": {"version": "1.2.3"}, "node_modules/dependency": {"version": "1.2.3"},
    }})
    files = {
        "frontend/package.json": package,
        "frontend/package-lock.json": lock,
        "native/apple/project.yml": 'settings:\n  base:\n    MARKETING_VERSION: "0.4.2"\n'
                                    '    CURRENT_PROJECT_VERSION: "3"\n',
        "native/apple/TreasureUp.xcodeproj/project.pbxproj": 'MARKETING_VERSION = 0.4.2;\n',
        "backend/app/main.py": 'app = FastAPI(title="Fixture", version="1.2.3")\n',
        "compose.yaml": 'services:\n  api:\n    image: treasure-up-backend:1.2.3\n'
                        '  web:\n    image: treasure-up-web:1.2.3\n',
        "compose.registry.yaml": 'services:\n  api:\n    image: ghcr.io/fixture/treasure-up-backend:1.2.3\n',
        "compose.registry.light.yaml": 'services:\n  web:\n    image: ghcr.io/fixture/treasure-up-web:1.2.3\n',
        "release.json": '{"version": "1.2.3", "channel": "preview"}\n',
        "docs/releases/v1.2.3.md": '# Fixture release\n\nExisting reviewed notes.\n',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


@pytest.mark.parametrize("missing", ["backend/app/main.py", "compose.registry.light.yaml"])
def test_prepare_missing_version_does_not_partially_update_files(tmp_path, missing):
    root = versioned_project(tmp_path)
    (root / missing).write_text("# Version accidentally removed\n", encoding="utf-8")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="Version field missing") as error:
        release.prepare(root, "2.4.0", "stable")
    assert missing in str(error.value)
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert not (root / "docs/releases/v2.4.0.md").exists()


def test_prepare_preserves_dependencies_format_notes_and_is_idempotent(tmp_path):
    root = versioned_project(tmp_path)
    notes = root / "docs/releases/v2.4.0.md"
    notes.write_text("# Reviewed release\n\nKeep this hand-written upgrade guidance.\n", encoding="utf-8")
    notes_before = notes.read_bytes()
    package_before = (root / "frontend/package.json").read_text(encoding="utf-8")
    ios_before = {p.relative_to(root): p.read_bytes() for p in (root / "native").rglob("*") if p.is_file()}

    release.prepare(root, "2.4.0", "stable")

    package = (root / "frontend/package.json").read_text(encoding="utf-8")
    assert package == package_before.replace('"version" : "1.2.3"', '"version" : "2.4.0"')
    lock = json.loads((root / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == lock["packages"][""]["version"] == "2.4.0"
    assert lock["packages"]["node_modules/dependency"]["version"] == "1.2.3"
    assert {p.relative_to(root): p.read_bytes() for p in (root / "native").rglob("*") if p.is_file()} == ios_before
    assert notes.read_bytes() == notes_before
    assert release.check_versions(root, "v2.4.0") == notes
    prepared = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    release.prepare(root, "2.4.0", "stable")
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == prepared


@pytest.mark.parametrize("name", ["compose.registry.yaml", "compose.registry.light.yaml"])
@pytest.mark.parametrize("reference", ["", "ghcr.io/fixture/treasure-up-web:latest", "ghcr.io/fixture/treasure-up-web@sha256:" + "1" * 64])
def test_check_rejects_each_registry_without_version_tags(tmp_path, name, reference):
    root = versioned_project(tmp_path)
    assert release.check_versions(root, "v1.2.3").is_file()
    (root / name).write_text(f"services:\n  web:\n    image: {reference}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Version field missing") as error:
        release.check_versions(root, "v1.2.3")
    assert name in str(error.value)


@pytest.mark.parametrize("with_images", [False, True])
def test_packages_tagged_source_without_untracked_secrets(tmp_path, with_images):
    repo = tmp_path / "repo"
    repo.mkdir()
    project = Path(__file__).resolve().parents[2]
    files = [
        "frontend/package.json", "frontend/package-lock.json", "native/apple/project.yml",
        "native/apple/TreasureUp.xcodeproj/project.pbxproj",
        "release.json", "backend/app/main.py", "compose.yaml", "compose.light.yaml", "compose.registry.yaml", "compose.registry.light.yaml", "deploy/start.py", "deploy/bootstrap.py", f"docs/releases/{TAG}.md", "frontend/public/userscripts/treasure-up.user.js",
    ]
    for name in files:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / name, target)

    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=Release Test", "-c", "user.email=test@example.invalid", *args], cwd=repo)

    git("init")
    git("add", "--", *files)
    git("commit", "-qm", "fixture")
    git("tag", TAG)
    script = git("show", f"HEAD:{files[-1]}")
    notes = git("show", f"HEAD:{files[-2]}")
    (repo / ".env").write_text("SECRET=must-not-ship")
    (repo / ".private").mkdir()
    (repo / ".private/credentials.txt").write_text("must-not-ship")
    (repo / files[-1]).write_text("uncommitted script")
    (repo / files[-2]).write_text("uncommitted notes")
    inputs = tmp_path / "artifacts"
    inputs.mkdir()
    output = tmp_path / "output"
    image_args = []
    images = None
    if with_images:
        ctx = release.context(TAG, "guoweiyi/treasure-up", git("rev-parse", "HEAD").decode().strip(), "123")
        images = {**ctx, "images": {component: {
            "image": f"ghcr.io/guoweiyi/treasure-up-{component}", "digest": "sha256:" + "1" * 64,
            "reference": f"ghcr.io/guoweiyi/treasure-up-{component}@sha256:" + "1" * 64,
            "platforms": ["linux/amd64", "linux/arm64"],
        } for component in ("backend", "web")}}
        image_file = tmp_path / "images.json"
        image_file.write_text(json.dumps(images))
        image_args = [image_file, "guoweiyi/treasure-up", "123"]
    release.package(repo, TAG, artifacts(inputs), output, *image_args)
    assert (output / "treasure-up.user.js").read_bytes() == script
    assert (output / "RELEASE-NOTES.md").read_bytes() == notes
    with zipfile.ZipFile(output / f"treasure-up-{TAG}-source.zip") as archive:
        assert not any(".env" in name or ".private" in name for name in archive.namelist())
        assert archive.read(f"treasure-up-{TAG}/frontend/public/userscripts/treasure-up.user.js") == script
        assert archive.read(f"treasure-up-{TAG}/native/apple/project.yml") == git("show", "HEAD:native/apple/project.yml")
    client = output / f"treasure-up-{TAG}-ios-simulator-ad-hoc.zip"
    release.validate_simulator_archive(client)
    assert client.read_bytes() == (inputs / "treasure-up-ios-simulator/client.zip").read_bytes()
    assert not any(path.suffix in {".exe", ".dmg", ".apk"} for path in output.iterdir())
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["commit"] == git("rev-parse", "HEAD").decode().strip()
    for asset in manifest["assets"]:
        data = (output / asset["name"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == asset["sha256"]
        assert len(data) == asset["bytes"]
    if with_images:
        assert manifest["images"] == images["images"]
        with zipfile.ZipFile(output / f"treasure-up-{TAG}-docker.zip") as bundle:
            names = bundle.namelist()
            assert len(names) == 8
            assert not any("/backend/" in name or name.endswith("/.env") for name in names)
            for file in ("compose.registry.yaml", "compose.registry.light.yaml"):
                content = bundle.read(f"treasure-up-{TAG}/{file}").decode()
                assert "@sha256:" + "1" * 64 in content
                assert ":" + TAG[1:] not in content
    with pytest.raises(ValueError, match="must be empty"):
        release.package(repo, TAG, inputs, output)
