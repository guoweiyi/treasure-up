"""Produce the source-free Compose deployment shared by Releases and OCI publishing."""
from copy import deepcopy
import json
from pathlib import Path

import yaml


def production_compose(root: Path, images: dict[str, str] | None = None) -> dict:
    root = Path(root)
    version = json.loads((root / "release.json").read_text(encoding="utf-8"))["version"]
    images = images or {name: f"docker.io/yunyunjuan/treasure-up-{name}:{version}"
                        for name in ("backend", "web", "setup")}
    if set(images) != {"backend", "web", "setup"} or not all(images.values()):
        raise ValueError("Backend, web and setup image references are required")
    source = yaml.safe_load((root / "compose.yaml").read_text(encoding="utf-8"))
    services = {name: deepcopy(service) for name, service in source["services"].items()}
    # Collection remains independent; download and media preparation share one
    # worker by default, as in the existing light deployment.
    media_resources = services.pop("media-worker")
    services.pop("download-worker")
    services["worker"] = deepcopy(services["collector"])
    for key in ("cpus", "mem_limit", "mem_reservation", "memswap_limit", "pids_limit"):
        services["worker"][key] = media_resources[key]
    services["worker"]["command"] = ["celery", "-A", "app.worker:celery", "worker", "-Q",
                                      "download,media", "--concurrency=1", "--loglevel=WARNING"]
    services["worker"]["healthcheck"]["test"] = ["CMD", "python", "/app/worker_healthcheck.py", "download", "media"]
    file_settings = {"TREASURE_DATABASE_URL", "TREASURE_SECRET_KEY", "TREASURE_ADMIN_USERNAME",
                     "TREASURE_ADMIN_PASSWORD", "TREASURE_BACKUP_KEY", "TREASURE_COOKIE_SECURE",
                     "TREASURE_ALLOWED_HOSTS", "TREASURE_TRUSTED_ORIGINS", "TREASURE_PASSKEY_RP_ID", "TREASURE_PASSKEY_ORIGIN",
                     "TREASURE_PASSKEY_RP_NAME", "TREASURE_PASSKEYS_ENABLED"}
    for name, service in services.items():
        service.pop("build", None)
        # Host media paths are a source-checkout option. Published Compose must
        # remain self-contained and mount only declared named volumes.
        for variable, volume_name in (("TREASURE_MEDIA_PATH", "media"), ("TREASURE_SCRATCH_PATH", "scratch")):
            prefix = "${" + variable + ":-" + volume_name + "}:"
            service["volumes"] = [
                volume.replace(prefix, volume_name + ":", 1)
                if isinstance(volume, str) and volume.startswith(prefix) else volume
                for volume in service.get("volumes", [])
            ]
        if name in {"postgres", "redis", "web"}:
            continue
        service["image"] = images["backend"]
        service["environment"] = {key: value for key, value in service["environment"].items()
                                  if key not in file_settings}
        service["environment"]["TREASURE_CONFIG_FILE"] = "/run/treasure/runtime.json"
        service["volumes"].append("runtime-config:/run/treasure:ro")
        if name in {"init", "backup-worker"}:
            role = "init" if name == "init" else "backup"
            service["environment"]["TREASURE_ROLE_CONFIG_FILE"] = f"/run/treasure-{role}/role.json"
            service["volumes"].append(f"{role}-config:/run/treasure-{role}:ro")
        if name == "init":
            service["depends_on"]["setup"] = {"condition": "service_completed_successfully"}
        check = service.get("healthcheck", {}).get("test", [])
        if "/app/worker_healthcheck.py" in check:
            service["healthcheck"]["test"] = ["CMD", "python", "/app/runtime_entrypoint.py", *check[1:]]
    postgres = services["postgres"]
    postgres["environment"].pop("POSTGRES_PASSWORD", None)
    postgres["environment"]["POSTGRES_PASSWORD_FILE"] = "/run/treasure/password"
    postgres["volumes"].append("postgres-config:/run/treasure:ro")
    postgres["depends_on"] = {"setup": {"condition": "service_completed_successfully"}}
    services["web"]["image"] = images["web"]
    services["web"]["ports"] = [{"target": 80, "published": "${TREASURE_PORT:-8788}",
                                    "host_ip": "${TREASURE_BIND_ADDRESS:-127.0.0.1}", "protocol": "tcp"}]
    setup = {
        "image": images["setup"], "restart": "no", "network_mode": "none", "read_only": True,
        "security_opt": ["no-new-privileges:true"], "cap_drop": ["ALL"],
        "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER"], "logging": {"driver": "none"},
        "environment": {"TREASURE_PUBLIC_ORIGIN": "${TREASURE_PUBLIC_ORIGIN:-}"},
        "volumes": ["config:/config", "database:/existing-database:ro",
                    "runtime-config:/exports/runtime", "init-config:/exports/init",
                    "backup-config:/exports/backup", "postgres-config:/exports/postgres"],
    }
    volumes = deepcopy(source["volumes"])
    volumes.update({name + "-config": None for name in ("runtime", "init", "backup", "postgres")})
    volumes["config"] = {"name": "${TREASURE_CONFIG_VOLUME:-treasure-up-config}"}
    return {"name": "treasure-up", "services": {"setup": setup, **services}, "volumes": volumes}


def compose_content(root: Path, images: dict[str, str] | None = None) -> str:
    class DeploymentDumper(yaml.SafeDumper):
        def ignore_aliases(self, _data):
            return True
    return yaml.dump(production_compose(root, images), Dumper=DeploymentDumper, sort_keys=False, allow_unicode=True)
