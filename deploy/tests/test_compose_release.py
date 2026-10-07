"""Publication guards for the standalone, unprivileged OCI Compose artifact."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compose_release as oci
import container_release as containers

ROOT = Path(__file__).resolve().parents[2]
CTX = containers.context("v0.3.7", "guoweiyi/treasure-up", "a" * 40, "42", "yunyunjuan")
IMAGES = {"images": {name: {"image": containers.image_name(CTX, name), "digest": "sha256:" + "a" * 64,
                            "reference": containers.image_name(CTX, name) + "@sha256:" + "a" * 64,
                            "platforms": ["linux/amd64", "linux/arm64"]}
                     for name in containers.COMPONENTS}}


def test_compose_retains_consumer_variables_without_host_credentials(monkeypatch):
    monkeypatch.setenv("TREASURE_SECRET_KEY", "sentinel-never-publish")
    monkeypatch.setenv("TREASURE_PUBLIC_ORIGIN", "https://sentinel.invalid")
    content = oci.release_compose(ROOT, CTX, IMAGES)
    assert b"sentinel" not in content
    assert b"${TREASURE_PUBLIC_ORIGIN:-}" in content
    config = yaml.safe_load(content)
    assert config["x-treasure-release"]["revision"] == CTX["revision"]
    assert config["services"]["setup"]["image"] == IMAGES["images"]["setup"]["reference"]
    assert all("build" not in service and "env_file" not in service for service in config["services"].values())


@pytest.mark.parametrize("mutation", [
    lambda value: value["services"]["api"].update(env_file=".env"),
    lambda value: value["services"]["api"].update(use_api_socket=True),
    lambda value: value["services"]["api"].update(privileged=True),
    lambda value: value["services"]["api"].update(network_mode="host"),
    lambda value: value["services"]["api"].update(volumes=["/var/run/docker.sock:/var/run/docker.sock"]),
    lambda value: value["services"]["api"].update(environment={"API_TOKEN": "private-value"}),
    lambda value: value["services"]["api"].update(environment={"FROM_HOST": None}),
    lambda value: value["volumes"].update(config={"driver_opts": {"device": "/"}}),
    lambda value: value.update(include="https://untrusted.invalid/compose.yaml"),
])
def test_compose_rejects_host_access_and_secret_publication(mutation):
    config = yaml.safe_load(oci.release_compose(ROOT, CTX, IMAGES))
    mutation(config)
    with pytest.raises(ValueError):
        oci.validate_compose(config)


def test_embedded_compose_content_must_match_manifest_digest():
    content = oci.release_compose(ROOT, CTX, IMAGES)
    manifest = {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"mediaType": "application/vnd.docker.compose.config.empty.v1+json"},
                "layers": [{"mediaType": "application/vnd.docker.compose.file+yaml", "size": len(content),
                            "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                            "data": base64.b64encode(content).decode(),
                            "annotations": {"com.docker.compose.file": "compose.yaml"}}]}
    assert oci.read_payload(manifest) == content
    manifest["layers"][0]["digest"] = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="OCI digest"):
        oci.read_payload(manifest)


def test_standard_external_layer_uses_only_digest_and_checks_downloaded_bytes():
    content = oci.release_compose(ROOT, CTX, IMAGES)
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    manifest = {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"mediaType": "application/vnd.docker.compose.config.empty.v1+json"},
                "layers": [{"mediaType": "application/vnd.docker.compose.file+yaml", "size": len(content),
                            "digest": digest, "urls": ["http://127.0.0.1/private"],
                            "annotations": {"com.docker.compose.file": "compose.yaml", "com.docker.compose.version": "2.38.2"}}]}
    calls = []
    def download(value, size):
        calls.append((value, size))
        return content
    assert oci.read_payload(manifest, download) == content
    assert calls == [(digest, len(content))]
    with pytest.raises(ValueError, match="OCI digest"):
        oci.read_payload(manifest, lambda *_: content[:-1] + b"x")
    with pytest.raises(ValueError, match="OCI digest"):
        oci.read_payload(manifest, lambda *_: content[:-1])
    manifest["layers"][0]["size"] = oci.MAX_COMPOSE_BYTES + 1
    with pytest.raises(ValueError, match="descriptor"):
        oci.read_payload(manifest, lambda *_: pytest.fail("Oversized blob must not be requested"))


def test_registry_redirect_removes_bearer_and_limits_hops():
    request = urllib.request.Request("https://registry-1.docker.io/v2/test/blob",
                                     headers={"Authorization": "Bearer private-sentinel"})
    handler = oci.RegistryRedirect()
    for index in range(3):
        redirected = handler.redirect_request(request, None, 302, "Found", {}, f"https://cdn.example/{index}")
        assert not any(key.lower() == "authorization" for key in redirected.headers)
        assert not any(key.lower() == "authorization" for key in redirected.unredirected_hdrs)
    with pytest.raises(ValueError, match="redirect"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://cdn.example/fourth")


@pytest.mark.parametrize("url", ["http://cdn.example/blob", "https://user:password@cdn.example/blob", "https://cdn.example:8443/blob"])
def test_registry_redirect_rejects_insecure_or_credentialed_destinations(url):
    request = urllib.request.Request("https://registry-1.docker.io/v2/test/blob")
    with pytest.raises(ValueError, match="redirect"):
        oci.RegistryRedirect().redirect_request(request, None, 302, "Found", {}, url)


def test_auth_endpoint_cannot_redirect_or_fetch_another_repository():
    request = urllib.request.Request("https://auth.docker.io/token")
    with pytest.raises(ValueError, match="redirect"):
        oci.RegistryRedirect(allow=False).redirect_request(request, None, 302, "Found", {}, "https://cdn.example/token")
    with pytest.raises(ValueError, match="outside"):
        oci.public_compose_blob(CTX, "docker.io/other/treasure-up@sha256:" + "a" * 64, "sha256:" + "a" * 64, 10)


def test_artifact_digest_uses_buildx_descriptor_output():
    digest = "sha256:" + "b" * 64
    result = subprocess.CompletedProcess([], 0, f"Name: test\nMediaType: application/vnd.oci.image.manifest.v1+json\nDigest:    {digest}\n", "")
    with patch.object(containers.subprocess, "run", return_value=result):
        assert containers.inspect_digest("test", artifact=True) == digest


def test_existing_compose_version_reuses_original_created_timestamp(tmp_path):
    path = tmp_path / "compose.yaml"
    payload = oci.release_compose(ROOT, CTX, IMAGES)
    path.write_bytes(payload)
    digest = "sha256:" + "c" * 64
    with patch.object(containers, "inspect_digest", return_value=digest), \
            patch.object(oci, "inspect_compose", return_value=(payload, {})), \
            patch.object(containers, "verify_public_images"), patch.object(oci.subprocess, "run") as command:
        assert oci.prepare_compose(CTX, path, IMAGES)["digest"] == digest
        command.assert_not_called()


def test_conflicting_version_stops_before_any_publication(tmp_path):
    path = tmp_path / "images.json"
    path.write_text(json.dumps({**CTX, **deepcopy(IMAGES)}))
    with patch.object(containers, "verify_public_images"), \
            patch.object(containers, "inspect_digest", return_value="sha256:" + "f" * 64), \
            patch.object(containers, "command") as command, patch.object(oci, "prepare_compose") as prepare:
        with pytest.raises(ValueError, match="different digest"):
            containers.promote(CTX, path, "stable", tmp_path / "compose.yaml")
        command.assert_not_called()
        prepare.assert_not_called()
