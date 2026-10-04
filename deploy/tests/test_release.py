import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

spec = importlib.util.spec_from_file_location("release", Path(__file__).parents[1] / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def artifacts(tmp_path):
    for target, suffix, _ in release.CLIENTS:
        path = tmp_path / f"treasure-up-{target}" / f"client{suffix}"
        path.parent.mkdir()
        path.write_bytes(b"fixture-client")
    return tmp_path


def test_requires_real_installer_not_just_cargo_lock(tmp_path):
    root = artifacts(tmp_path)
    (root / "treasure-up-x86_64-pc-windows-msvc/client.exe").unlink()
    (root / "treasure-up-x86_64-pc-windows-msvc/Cargo.lock").write_text("fixture")
    with pytest.raises(ValueError, match="exactly one .exe"):
        release.select_clients(root)


def test_rejects_ambiguous_or_empty_client(tmp_path):
    root = artifacts(tmp_path)
    extra = root / "treasure-up-android-debug/extra.apk"
    extra.write_bytes(b"stale")
    with pytest.raises(ValueError, match="found 2"):
        release.select_clients(root)
    extra.unlink()
    (extra.parent / "client.apk").write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        release.select_clients(root)


def test_all_platforms_have_explicit_output_labels(tmp_path):
    selected = release.select_clients(artifacts(tmp_path))
    assert len(selected) == 5
    assert len({name for _, name in selected}) == 5
    assert any("simulator" in name for _, name in selected)
    assert any("debug" in name for _, name in selected)


@pytest.mark.parametrize("tag", ["main", "v1.2", "v1.2.3/../../other", "v1.2.3\n", "v1.2.3$(id)"])
def test_rejects_non_release_tags_before_reading_files(tmp_path, tag):
    with pytest.raises(ValueError, match="Release tag"):
        release.check_versions(tmp_path, tag)


def test_packages_tagged_source_without_untracked_secrets(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    project = Path(__file__).resolve().parents[2]
    files = [
        "frontend/package.json", "frontend/package-lock.json", "native/package.json", "native/package-lock.json",
        "native/src-tauri/tauri.conf.json", "native/src-tauri/Cargo.toml", "native/src-tauri/Cargo.lock",
        "backend/app/main.py", "compose.yaml", "docs/releases/v0.3.1.md", "frontend/public/userscripts/treasure-up.user.js",
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
    git("tag", "v0.3.1")
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
    release.package(repo, "v0.3.1", artifacts(inputs), output)
    assert (output / "treasure-up.user.js").read_bytes() == script
    assert (output / "RELEASE-NOTES.md").read_bytes() == notes
    with zipfile.ZipFile(output / "treasure-up-v0.3.1-source.zip") as archive:
        assert not any(".env" in name or ".private" in name for name in archive.namelist())
        assert archive.read("treasure-up-v0.3.1/frontend/public/userscripts/treasure-up.user.js") == script
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["commit"] == git("rev-parse", "HEAD").decode().strip()
    for asset in manifest["assets"]:
        data = (output / asset["name"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == asset["sha256"]
        assert len(data) == asset["bytes"]
    with pytest.raises(ValueError, match="must be empty"):
        release.package(repo, "v0.3.1", inputs, output)
