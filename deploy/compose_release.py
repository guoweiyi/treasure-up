"""Publish a self-contained Compose artifact without reading host secrets or running it."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import yaml

from compose_config import production_compose

MAX_COMPOSE_BYTES = 192 * 1024


class RegistryRedirect(urllib.request.HTTPRedirectHandler):
    """Registry blobs may redirect to HTTPS CDNs, without registry authorization."""
    max_redirections = 3
    max_repeats = 1

    def __init__(self, *, allow=True):
        self.allow = allow
        self.count = 0

    def redirect_request(self, request, response, code, message, headers, new_url):
        self.count += 1
        target = urllib.parse.urlsplit(new_url)
        if (not self.allow or self.count > 3 or target.scheme != "https" or not target.hostname
                or target.username is not None or target.password is not None or target.port not in (None, 443)):
            raise ValueError("Unexpected registry redirect")
        redirected = super().redirect_request(request, response, code, message, headers, new_url)
        if redirected is not None:
            for values in (redirected.headers, redirected.unredirected_hdrs):
                for key in tuple(values):
                    if key.lower() == "authorization":
                        del values[key]
        return redirected


def public_compose_blob(ctx, reference, digest, size):
    """Fetch only this public Docker Hub repository's digest-addressed blob."""
    namespace = ctx.get("namespace", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,38}", namespace):
        raise ValueError("Invalid Compose repository namespace")
    repository = f"{namespace}/treasure-up"
    if not re.fullmatch(rf"docker\.io/{re.escape(repository)}@sha256:[a-f0-9]{{64}}", reference):
        raise ValueError("Compose blob reference is outside the release repository")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest) or type(size) is not int or not 0 < size <= MAX_COMPOSE_BYTES:
        raise ValueError("Invalid Compose blob descriptor")
    query = urllib.parse.urlencode({"service": "registry.docker.io", "scope": f"repository:{repository}:pull"})
    try:
        auth = urllib.request.build_opener(RegistryRedirect(allow=False))
        with auth.open("https://auth.docker.io/token?" + query, timeout=15) as response:
            encoded = response.read(16385)
        if len(encoded) > 16384:
            raise ValueError("Registry token response exceeded the size limit")
        credentials = json.loads(encoded)
        token = credentials.get("token") if isinstance(credentials, dict) else None
        if not isinstance(token, str) or not token or re.search(r"[\s\x00-\x1f]", token):
            raise ValueError("Invalid anonymous registry token")
        request = urllib.request.Request(f"https://registry-1.docker.io/v2/{repository}/blobs/{digest}",
                                         headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"})
        registry = urllib.request.build_opener(RegistryRedirect())
        with registry.open(request, timeout=15) as response:
            payload = response.read(size + 1)
        if len(payload) != size:
            raise ValueError("Registry Compose blob size does not match its descriptor")
        return payload
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        # Never put the short-lived bearer token or CDN signed URL into logs.
        raise ValueError("Unable to fetch the public Compose blob from Docker Hub") from None


def validate_compose(config):
    if not isinstance(config, dict) or set(config) != {"name", "services", "volumes", "x-treasure-release"}:
        raise ValueError("Published Compose must be self-contained")
    volumes = config["volumes"]
    if not isinstance(volumes, dict) or any(value is not None and
            (not isinstance(value, dict) or set(value) - {"name"}) for value in volumes.values()):
        raise ValueError("Published volumes may not use host paths or custom drivers")
    if not isinstance(config["services"], dict) or not config["services"]:
        raise ValueError("Published Compose must contain services")
    forbidden = {"build", "env_file", "extends", "include", "secrets", "configs", "devices",
                 "device_cgroup_rules", "volumes_from", "use_api_socket", "provider"}
    for service in config["services"].values():
        if not isinstance(service, dict) or not isinstance(service.get("environment", {}), dict):
            raise ValueError("Published services and environment must be mappings")
        if forbidden.intersection(service) or service.get("privileged"):
            raise ValueError("Published service requires unavailable or privileged host resources")
        if any(service.get(key) == "host" for key in ("network_mode", "pid", "ipc", "uts", "userns_mode")):
            raise ValueError("Published service must not share host namespaces")
        if set(service.get("cap_add", [])) - {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}:
            raise ValueError("Published service requests unsupported capabilities")
        if not re.fullmatch(r"[^\s@]+@sha256:[a-f0-9]{64}", service.get("image", "")):
            raise ValueError("Every published image must be pinned by digest")
        for key, value in service.get("environment", {}).items():
            if not isinstance(key, str) or value is None or (re.search(r"PASSWORD|SECRET|TOKEN|(?:^|_)KEY$|DATABASE_URL", key) and not key.endswith("_FILE")):
                raise ValueError("Published Compose must not embed or inherit secret environment values")
        for volume in service.get("volumes", []):
            if isinstance(volume, str):
                source = volume.split(":", 1)[0]
            elif isinstance(volume, dict) and volume.get("type") == "volume":
                source = volume.get("source")
            else:
                raise ValueError("Published services may mount only named volumes")
            if source not in volumes:
                raise ValueError("Published mount is not a declared named volume")
    if "docker.sock" in json.dumps(config):
        raise ValueError("Published Compose must not grant access to the Docker socket")


def release_compose(root, ctx, images):
    config = production_compose(root, {name: value["reference"] for name, value in images["images"].items()})
    config["x-treasure-release"] = {"version": ctx["tag"][1:], "revision": ctx["revision"],
                                    "source": f"https://github.com/{ctx['repository']}"}
    validate_compose(config)
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True).encode("utf-8")


def read_payload(manifest, fetch_blob=None):
    """Read inline or digest-addressed content without evaluating remote Compose."""
    layers = manifest.get("layers", [])
    if (manifest.get("mediaType") != "application/vnd.oci.image.manifest.v1+json"
            or manifest.get("config", {}).get("mediaType") != "application/vnd.docker.compose.config.empty.v1+json"
            or len(layers) != 1):
        raise ValueError("Expected a single-file Compose OCI artifact")
    layer = layers[0]
    if (layer.get("mediaType") != "application/vnd.docker.compose.file+yaml"
            or layer.get("annotations", {}).get("com.docker.compose.file") != "compose.yaml"
            or type(layer.get("size")) is not int or not 0 < layer["size"] <= MAX_COMPOSE_BYTES
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(layer.get("digest", "")))):
        raise ValueError("Unexpected Compose layer or invalid content descriptor")
    if "data" in layer:
        if not isinstance(layer["data"], str) or len(layer["data"]) > 256 * 1024:
            raise ValueError("Invalid embedded Compose content")
        try:
            payload = base64.b64decode(layer["data"], validate=True)
        except ValueError as error:
            raise ValueError("Invalid embedded Compose content") from error
    elif fetch_blob is not None:
        # Ignore descriptor urls: a manifest cannot choose the download host.
        payload = fetch_blob(layer["digest"], layer["size"])
    else:
        raise ValueError("Compose content requires a registry blob reader")
    if layer.get("size") != len(payload) or layer.get("digest") != "sha256:" + hashlib.sha256(payload).hexdigest():
        raise ValueError("Compose content differs from its OCI digest")
    return payload


def inspect_compose(ctx, reference):
    from container_release import command
    payload = read_payload(json.loads(command("docker", "buildx", "imagetools", "inspect", reference, "--raw")),
                           lambda digest, size: public_compose_blob(ctx, reference, digest, size))
    config = yaml.safe_load(payload)
    validate_compose(config)
    metadata = config["x-treasure-release"]
    if (not isinstance(metadata, dict) or metadata.get("source") != f"https://github.com/{ctx['repository']}"
            or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", str(metadata.get("version", "")))
            or not re.fullmatch(r"[a-f0-9]{40}", str(metadata.get("revision", "")))):
        raise ValueError("Compose release metadata is invalid or belongs to another source")
    return payload, metadata


def prepare_compose(ctx, path, images):
    from container_release import inspect_digest, verify_public_images
    expected = release_compose(Path(__file__).resolve().parents[1], ctx, images)
    if path.read_bytes() != expected:
        raise ValueError("Compose attachment differs from the exact tested release source")
    image = f"docker.io/{ctx['namespace']}/treasure-up"
    existing = inspect_digest(f"{image}:{ctx['tag'][1:]}", missing=True, artifact=True)
    if existing:
        payload, _ = inspect_compose(ctx, f"{image}@{existing}")
        if payload != expected:
            raise ValueError("Compose version already contains different content; publish a new version")
        digest = existing
    else:
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
        if not attempt.isdecimal():
            raise ValueError("Invalid run attempt")
        candidate = f"{image}:candidate-compose-{ctx['revision'][:12]}-{ctx['run_id']}-{attempt}"
        # An empty directory and explicit empty env file prevent discovery of
        # any runner .env. Never publish with --with-env or --app.
        with tempfile.TemporaryDirectory(prefix="treasure-compose-publish-") as temporary:
            directory = Path(temporary)
            compose = directory / "compose.yaml"
            compose.write_bytes(expected)
            empty_env = directory / "publish.env"
            empty_env.write_bytes(b"")
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith(("COMPOSE_", "TREASURE_", "POSTGRES_"))}
            result = subprocess.run(["docker", "compose", "--env-file", str(empty_env), "-f", str(compose),
                                     "publish", "-y", "--oci-version", "1.0", candidate], cwd=directory,
                                    env=environment, capture_output=True, text=True)
            if result.returncode:
                raise ValueError("Compose candidate publication failed; no release tags were changed")
        digest = inspect_digest(candidate, artifact=True)
        payload, _ = inspect_compose(ctx, f"{image}@{digest}")
        if payload != expected:
            raise ValueError("Published Compose differs from the verified attachment")
    artifact = {"image": image, "digest": digest, "reference": f"{image}@{digest}"}
    verify_public_images({"compose": artifact})
    return artifact


def channel_may_advance(ctx, artifact, channel):
    from container_release import inspect_digest
    target = f"{artifact['image']}:{'latest' if channel == 'stable' else 'preview'}"
    old = inspect_digest(target, missing=True, artifact=True)
    if old:
        _, metadata = inspect_compose(ctx, f"{artifact['image']}@{old}")
        return tuple(map(int, metadata["version"].split("."))) <= tuple(map(int, ctx["tag"][1:].split(".")))
    return True


def copy_compose(artifact, tag, *, immutable=False):
    from container_release import command, inspect_digest
    target = f"{artifact['image']}:{tag}"
    if immutable:
        existing = inspect_digest(target, missing=True, artifact=True)
        if existing:
            if existing != artifact["digest"]:
                raise ValueError("Compose version tag changed; refusing to replace it")
            return
    command("docker", "buildx", "imagetools", "create", "--prefer-index=false", "--tag", target, artifact["reference"])
    if inspect_digest(target, artifact=True) != artifact["digest"]:
        raise ValueError("Compose artifact digest changed during publication")
