import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("container_release_tests", Path(__file__).parents[1] / "container_release.py")
container = importlib.util.module_from_spec(spec)
spec.loader.exec_module(container)
CTX = container.context("v0.3.2", "guoweiyi/treasure-up", "a" * 40, "123")
DIGESTS = {"amd64": "sha256:" + "1" * 64, "arm64": "sha256:" + "2" * 64}


def candidates(tmp_path):
    for arch in container.ARCHES:
        container.write_json(tmp_path / f"{arch}.json", {**CTX, "arch": arch, "images": {
            name: {"image": container.image_name(CTX, name), "digest": DIGESTS[arch]} for name in container.COMPONENTS
        }})
    return tmp_path


@pytest.mark.parametrize("field,value", [("run_id", "456"), ("revision", "b" * 40), ("tag", "v0.3.1"), ("repository", "other/project")])
def test_cross_run_and_cross_source_records_cannot_enter_release(tmp_path, field, value):
    root = candidates(tmp_path)
    path = root / "arm64.json"
    data = json.loads(path.read_text())
    data[field] = value
    container.write_json(path, data)
    with pytest.raises(ValueError, match="different source"):
        container.read_candidates(CTX, root)


def test_rejects_duplicate_platform_or_extra_digest_record(tmp_path):
    root = candidates(tmp_path)
    data = json.loads((root / "amd64.json").read_text())
    container.write_json(root / "arm64.json", data)
    with pytest.raises(ValueError, match="duplicate"):
        container.read_candidates(CTX, root)
    container.write_json(root / "extra.json", data)
    with pytest.raises(ValueError, match="exactly two"):
        container.read_candidates(CTX, root)


def test_rejects_foreign_images_and_invalid_digests(tmp_path):
    root = candidates(tmp_path)
    path = root / "arm64.json"
    data = json.loads(path.read_text())
    data["images"]["web"]["image"] = "ghcr.io/attacker/web"
    container.write_json(path, data)
    with pytest.raises(ValueError, match="outside"):
        container.read_candidates(CTX, root)
    data["images"]["web"]["image"] = container.image_name(CTX, "web")
    data["images"]["web"]["digest"] = "sha256:$(echo wrong)"
    container.write_json(path, data)
    with pytest.raises(ValueError, match="digest"):
        container.read_candidates(CTX, root)


def test_index_checks_tested_child_digests_and_all_platforms():
    manifests = [{"platform": {"os": "linux", "architecture": arch}, "digest": digest} for arch, digest in DIGESTS.items()]
    container.validate_index({"manifests": manifests}, DIGESTS)
    attestation = {"platform": {"os": "unknown", "architecture": "unknown"}, "annotations": {"vnd.docker.reference.type": "attestation-manifest"}}
    container.validate_index({"manifests": manifests + [attestation]}, DIGESTS)
    with pytest.raises(ValueError):
        container.validate_index({"manifests": manifests[:1]}, DIGESTS)
    with pytest.raises(ValueError):
        container.validate_index({"manifests": manifests + manifests[:1]}, DIGESTS)
    with pytest.raises(ValueError):
        container.validate_index({"manifests": manifests}, {**DIGESTS, "arm64": "sha256:" + "3" * 64})


def image_manifest(tmp_path):
    data = {**CTX, "images": {name: {"image": container.image_name(CTX, name), "digest": DIGESTS["amd64"],
        "reference": f"{container.image_name(CTX, name)}@{DIGESTS['amd64']}", "platforms": ["linux/amd64", "linux/arm64"]} for name in container.COMPONENTS}}
    path = tmp_path / "release-images.json"
    container.write_json(path, data)
    return path


def test_promotion_checks_both_immutable_tags_before_any_write(tmp_path, monkeypatch):
    path = image_manifest(tmp_path)
    writes = []
    monkeypatch.setattr(container, "command", lambda *args: writes.append(args))
    monkeypatch.setattr(container, "inspect_digest", lambda reference, **kwargs: DIGESTS["arm64"] if "-web:" in reference else None)
    with pytest.raises(ValueError, match="different digest"):
        container.promote(CTX, path, "preview")
    assert writes == []


def test_older_release_never_moves_channel_backwards(tmp_path, monkeypatch):
    path = image_manifest(tmp_path)
    writes = []
    def command(*args):
        if "--raw" in args:
            return json.dumps({"annotations": {"org.opencontainers.image.version": "0.4.0"}})
        writes.append(args)
        return ""
    monkeypatch.setattr(container, "command", command)
    monkeypatch.setattr(container, "inspect_digest", lambda *args, **kwargs: DIGESTS["amd64"])
    container.promote(CTX, path, "preview")
    assert len(writes) == 2
    assert all(any(value.endswith(":0.3.2") for value in args) for args in writes)


@pytest.mark.parametrize("error", [
    "unauthorized: access token not found",
    "ERROR: {reference}: failed to fetch anonymous token: token not found",
    "ERROR: {reference}: failed to get credentials: executable file not found in $PATH",
    "ERROR: {reference}: unexpected status from token endpoint: 404 Not Found",
    "ERROR: {reference}: not found; authentication required",
    "ERROR: other.example/image:0.3.2: not found",
])
def test_network_or_auth_failure_cannot_be_treated_as_unused_tag(monkeypatch, error):
    reference = "ghcr.io/guoweiyi/treasure-up-web:0.3.2"
    class Result:
        returncode = 1
        stdout = ""
        stderr = error.format(reference=reference)
    monkeypatch.setattr(container.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(ValueError, match="Unable to inspect"):
        container.inspect_digest(reference, missing=True)


@pytest.mark.parametrize("error", [
    "{reference}: not found",
    "ERROR: {reference}: not found\n",
    "manifest unknown",
    "ERROR: {reference}: manifest unknown",
    "ERROR: {reference}: manifest unknown: manifest unknown",
])
def test_only_explicit_missing_manifest_is_optional(monkeypatch, error):
    reference = "ghcr.io/guoweiyi/treasure-up-web:0.3.2"
    class Result:
        returncode = 1
        stdout = ""
        stderr = error.format(reference=reference)
    monkeypatch.setattr(container.subprocess, "run", lambda *args, **kwargs: Result())
    assert container.inspect_digest(reference, missing=True) is None
    with pytest.raises(ValueError, match="Unable to inspect"):
        container.inspect_digest(reference)


@pytest.mark.parametrize("channel,alias", [("preview", "preview"), ("stable", "latest")])
def test_promotion_reads_back_each_written_channel_digest(tmp_path, monkeypatch, channel, alias):
    path = image_manifest(tmp_path)
    registry, readbacks = {}, []
    def command(*args):
        target = args[args.index("--tag") + 1]
        registry[target] = args[-1].split("@", 1)[1]
        return ""
    def inspect(reference, *, missing=False):
        if not missing:
            readbacks.append(reference)
        return registry.get(reference)
    monkeypatch.setattr(container, "command", command)
    monkeypatch.setattr(container, "inspect_digest", inspect)
    container.promote(CTX, path, channel)
    for name in container.COMPONENTS:
        target = f"{container.image_name(CTX, name)}:{alias}"
        assert registry[target] == DIGESTS["amd64"]
        assert target in readbacks


@pytest.mark.parametrize("channel,alias", [("preview", "preview"), ("stable", "latest")])
@pytest.mark.parametrize("failure", ["mismatch", "unavailable"])
def test_promotion_stops_if_channel_write_cannot_be_verified(tmp_path, monkeypatch, channel, alias, failure):
    path = image_manifest(tmp_path)
    registry, writes = {}, []
    def command(*args):
        target = args[args.index("--tag") + 1]
        writes.append(target)
        registry[target] = args[-1].split("@", 1)[1]
        return ""
    def inspect(reference, *, missing=False):
        if not missing and reference.endswith(f":{alias}"):
            if failure == "unavailable":
                raise ValueError("Unable to inspect channel")
            return DIGESTS["arm64"]
        return registry.get(reference)
    monkeypatch.setattr(container, "command", command)
    monkeypatch.setattr(container, "inspect_digest", inspect)
    with pytest.raises(ValueError, match="Channel image digest|Unable to inspect channel"):
        container.promote(CTX, path, channel)
    assert f"{container.image_name(CTX, 'backend')}:{alias}" in writes
    assert f"{container.image_name(CTX, 'web')}:{alias}" not in writes
