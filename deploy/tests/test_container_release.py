import importlib.util
import json
from copy import deepcopy
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


def platform_index():
    # Shape returned by --raw for the public v0.3.2 Docker schema2 index.
    return {"schemaVersion": 2, "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
        "manifests": [{"mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "platform": {"os": "linux", "architecture": arch}, "digest": digest}
            for arch, digest in DIGESTS.items()]}


def platform_configs(version="0.3.2", revision=CTX["revision"]):
    # --format '{{json .Image}}' is a platform-keyed map, with capital-L Labels.
    return {f"linux/{arch}": {"os": "linux", "architecture": arch,
        "config": {"Labels": {"org.opencontainers.image.version": version,
            "org.opencontainers.image.revision": revision,
            "org.opencontainers.image.source": "https://github.com/guoweiyi/treasure-up"}}}
        for arch in container.ARCHES}


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
    writes, reads = [], []
    def command(*args):
        if "--raw" in args:
            reads.append(args[4])
            return json.dumps(platform_index())
        if "--format" in args:
            reads.append(args[4])
            return json.dumps(platform_configs("0.4.0"))
        writes.append(args)
        return ""
    monkeypatch.setattr(container, "command", command)
    monkeypatch.setattr(container, "inspect_digest", lambda *args, **kwargs: DIGESTS["amd64"])
    container.promote(CTX, path, "preview")
    assert len(writes) == 2
    assert all(any(value.endswith(":0.3.2") for value in args) for args in writes)
    assert len(reads) == 4
    assert all(reference.endswith("@" + DIGESTS["amd64"]) for reference in reads)


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


@pytest.mark.parametrize("oci", [False, True])
def test_reads_actual_multiarch_config_shape_using_only_immutable_digest(monkeypatch, oci):
    index = platform_index()
    revision = "45d7b81cf4343706bbbdff920fc648a9835dd929"
    configs = platform_configs(revision=revision)
    configs["linux/amd64"]["config"]["Labels"]["org.opencontainers.image.source"] = "https://github.com/Guoweiyi/Treasure-Up"
    if oci:
        index["mediaType"] = "application/vnd.oci.image.index.v1+json"
        index["annotations"] = deepcopy(configs["linux/amd64"]["config"]["Labels"])
    calls = []
    def command(*args):
        calls.append(args)
        return json.dumps(index if args[-1] == "--raw" else configs)
    monkeypatch.setattr(container, "command", command)
    image = container.image_name(CTX, "backend")
    metadata = container.inspect_release_metadata(CTX, image, DIGESTS["amd64"])
    assert metadata == {"version": "0.3.2", "revision": revision,
        "source": "https://github.com/guoweiyi/treasure-up"}
    assert [args[4:] for args in calls] == [
        (f"{image}@{DIGESTS['amd64']}", "--raw"),
        (f"{image}@{DIGESTS['amd64']}", "--format", "{{json .Image}}")]


@pytest.mark.parametrize("field,value", [
    ("version", None), ("version", "0.03.2"), ("version", "0.3.2-rc.1"),
    ("version", "0.3.2\n"), ("version", "0.3.3"),
    ("revision", None), ("revision", "a" * 39), ("revision", "b" * 40),
    ("source", "https://github.com/other/project"), ("source", None),
    ("source", "https://github.com/guoweiyi/treasure-up?other"),
    ("source", "https://github.com/guoweiyi/treasure-up/other"),
])
def test_rejects_missing_untrusted_or_disagreeing_platform_labels(monkeypatch, field, value):
    configs = platform_configs()
    configs["linux/arm64"]["config"]["Labels"][f"org.opencontainers.image.{field}"] = value
    monkeypatch.setattr(container, "command", lambda *args: json.dumps(configs))
    with pytest.raises(ValueError, match="labels|metadata"):
        container.inspect_release_metadata(CTX, container.image_name(CTX, "web"), DIGESTS["amd64"], index=platform_index())


@pytest.mark.parametrize("corruption", ["missing_platform", "extra_platform", "wrong_arch", "wrong_os", "missing_labels"])
def test_rejects_incomplete_or_misidentified_config_platforms(monkeypatch, corruption):
    configs = platform_configs()
    if corruption == "missing_platform":
        configs.pop("linux/arm64")
    elif corruption == "extra_platform":
        configs["linux/386"] = deepcopy(configs["linux/amd64"])
    elif corruption == "wrong_arch":
        configs["linux/arm64"]["architecture"] = "amd64"
    elif corruption == "wrong_os":
        configs["linux/arm64"]["os"] = "windows"
    else:
        configs["linux/arm64"]["config"].pop("Labels")
    monkeypatch.setattr(container, "command", lambda *args: json.dumps(configs))
    with pytest.raises(ValueError):
        container.inspect_release_metadata(CTX, container.image_name(CTX, "web"), DIGESTS["amd64"], index=platform_index())


def test_oci_annotations_do_not_bypass_platform_validation(monkeypatch):
    index = platform_index()
    index["manifests"].pop()
    index["annotations"] = platform_configs()["linux/amd64"]["config"]["Labels"]
    def unexpected(*args):
        pytest.fail("Invalid index must fail before reading configs")
    monkeypatch.setattr(container, "command", unexpected)
    with pytest.raises(ValueError, match="both tested"):
        container.inspect_release_metadata(CTX, container.image_name(CTX, "web"), DIGESTS["amd64"], index=index)


def test_oci_annotations_cannot_contradict_platform_labels(monkeypatch):
    index = platform_index()
    index["annotations"] = {"org.opencontainers.image.version": "0.3.3"}
    monkeypatch.setattr(container, "command", lambda *args: json.dumps(platform_configs()))
    with pytest.raises(ValueError, match="contradict"):
        container.inspect_release_metadata(CTX, container.image_name(CTX, "web"), DIGESTS["amd64"], index=index)


@pytest.mark.parametrize("version,revision,valid", [
    ("0.3.2", CTX["revision"], True),
    ("0.3.3", CTX["revision"], False),
    ("0.3.2", "b" * 40, False),
])
def test_merge_verifies_schema2_platform_metadata_before_publishing_record(tmp_path, monkeypatch, version, revision, valid):
    inputs = candidates(tmp_path / "inputs")
    output = tmp_path / "release-images.json"
    reads = []
    def command(*args):
        if "--raw" in args:
            return json.dumps(platform_index())
        if "--format" in args:
            reads.append(args[4])
            return json.dumps(platform_configs(version, revision))
        return ""
    monkeypatch.setattr(container, "command", command)
    monkeypatch.setattr(container, "inspect_digest", lambda *args: "sha256:" + "f" * 64)
    if valid:
        container.merge(CTX, inputs, output)
        assert len(reads) == 2
        assert set(json.loads(output.read_text())["images"]) == set(container.COMPONENTS)
    else:
        with pytest.raises(ValueError, match="does not match the tested release"):
            container.merge(CTX, inputs, output)
        assert not output.exists()
    assert all(reference.endswith("@sha256:" + "f" * 64) for reference in reads)
