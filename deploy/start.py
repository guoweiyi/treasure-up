"""One entry point for a new local/full or light deployment. Existing secrets stay intact."""
import argparse
from pathlib import Path
import subprocess
import sys

from bootstrap import create_environment


def start_services(root, env_file, *, light=False, build=True, run=subprocess.run):
    base = ["docker", "compose", "--env-file", str(env_file.resolve()), "-f", str(root / "compose.yaml")]
    selected = base + (["-f", str(root / "compose.light.yaml")] if light else [])
    command = selected + ["up", "-d", "--wait", "--wait-timeout", "180"]
    if build:
        command.append("--build")
    run(command, cwd=root, check=True)
    # Keep the same compose project/environment; only retire workers from the other mode.
    # Compose stop respects stop_grace_period. Expired task leases recover unfinished work.
    stop = base + ["-f", str(root / "compose.light.yaml"), "--profile", "full-workers", "stop"]
    stop += ["download-worker", "media-worker"] if light else ["worker"]
    run(stop, cwd=root, check=True)


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--light", action="store_true", help="Combine download/media workers; metadata and backup remain separate")
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--origin", help="External HTTPS origin, or local http://localhost:<port>")
    parser.add_argument("--no-build", action="store_true", help="Use already built images")
    args = parser.parse_args()
    try:
        create_environment(args.env_file, origin=args.origin or f"http://localhost:{args.port}", port=args.port)
    except ValueError as error:
        parser.error(str(error))
    try:
        start_services(root, args.env_file, light=args.light, build=not args.no_build)
    except (OSError, subprocess.CalledProcessError):
        print("Startup did not complete. Check Docker/Compose and preserve the existing environment file.", file=sys.stderr)
        return 1
    print("Treasure Up started. Use the site origin configured in your environment file.")
    print("Previous-mode workers were stopped gracefully. Any interrupted work resumes through task leases; backup workers were not stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
