"""Developer checkout helper; end users can install a release with Docker Compose alone."""
import argparse
from pathlib import Path
import subprocess
import sys

from setup import configure_site, create_environment


def compose_command(root, env_file, *, light=False, prebuilt=False):
    base = ["docker", "compose", "--env-file", str(env_file.resolve()), "-f", str(root / "compose.yaml")]
    if light:
        base += ["-f", str(root / "compose.light.yaml")]
    if prebuilt:
        base += ["-f", str(root / "compose.registry.yaml")]
        if light:
            # extends in compose.light.yaml resolves against compose.yaml, not its
            # merged collector service, so the combined worker needs its own override.
            base += ["-f", str(root / "compose.registry.light.yaml")]
    return base


def start_services(root, env_file, *, light=False, build=True, prebuilt=False, run=subprocess.run):
    selected = compose_command(root, env_file, light=light, prebuilt=prebuilt)
    if prebuilt:
        # Complete all downloads before touching running containers. Never fall back
        # to a local build if a release is missing or a registry is unavailable.
        run(selected + ["pull", "--policy", "always"], cwd=root, check=True)
    command = selected + ["up", "-d", "--wait", "--wait-timeout", "180"]
    if build and not prebuilt:
        command.append("--build")
    else:
        command += ["--no-build", "--pull", "never"]
    run(command, cwd=root, check=True)
    # Keep the same compose project/environment; only retire workers from the other mode.
    # Compose stop respects stop_grace_period. Expired task leases recover unfinished work.
    stop = compose_command(root, env_file, light=True, prebuilt=prebuilt) + ["--profile", "full-workers", "stop"]
    stop += ["download-worker", "media-worker"] if light else ["worker"]
    run(stop, cwd=root, check=True)


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--light", action="store_true", help="Combine download/media workers; metadata and backup remain separate")
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--origin", help="Set the external HTTPS origin, including on an existing deployment; other settings stay intact")
    parser.add_argument("--bind-address", help="Set the web listener IPv4 interface; defaults to 127.0.0.1 on first install")
    parser.add_argument("--allow-http", action="store_true", help="Explicitly allow an HTTP tunnel without passkeys; HTTPS remains recommended")
    parser.add_argument("--prebuilt", action="store_true", help="Pull published registry images, then start without a local build (also works with --light)")
    parser.add_argument("--no-build", action="store_true", help="Use only already available local images; with --prebuilt, the registry pull still runs")
    args = parser.parse_args()
    try:
        created = create_environment(args.env_file, origin=args.origin or f"http://localhost:{args.port}", port=args.port,
                                     bind_address=args.bind_address or "127.0.0.1", allow_http=args.allow_http)
        if not created and (args.origin or args.bind_address):
            configure_site(args.env_file, origin=args.origin, bind_address=args.bind_address, allow_http=args.allow_http)
    except ValueError as error:
        parser.error(str(error))
    try:
        start_services(root, args.env_file, light=args.light, build=not args.no_build, prebuilt=args.prebuilt)
    except (OSError, subprocess.CalledProcessError):
        print("Startup did not complete. Check Docker/Compose (and registry access for --prebuilt); preserve the existing environment file. Old-mode workers were not stopped unless replacement startup succeeded.", file=sys.stderr)
        return 1
    print("Treasure Up started. Use the site origin configured in your environment file.")
    print("Previous-mode workers were stopped gracefully. Any interrupted work resumes through task leases; backup workers were not stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
