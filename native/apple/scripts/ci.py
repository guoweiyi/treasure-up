"""Small macOS-only helpers shared by the iPhone, iPad and device CI jobs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import zipfile


def read(*command: str) -> str:
    return subprocess.check_output(command, text=True).strip()


def prepare() -> None:
    expected = os.environ["SOURCE_SHA"]
    if not re.fullmatch(r"[0-9a-fA-F]{40}", expected) or read("git", "rev-parse", "HEAD").lower() != expected.lower():
        raise SystemExit("The iOS workflow requires the exact checked-out commit SHA.")
    candidates = []
    for app in Path("/Applications").glob("Xcode*.app"):
        sdk = app / "Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs"
        versions = [tuple(map(int, match.group(1).split("."))) for path in sdk.glob("iPhoneOS*.sdk")
                    if (match := re.fullmatch(r"iPhoneOS(\d+(?:\.\d+)*)\.sdk", path.name))]
        if versions and max(versions) >= (26, 1):
            candidates.append((max(versions), str(app / "Contents/Developer")))
    if not candidates:
        raise SystemExit("The app needs an installed iOS 26.1+ SDK; update the runner image.")
    subprocess.run(["sudo", "xcode-select", "--switch", max(candidates)[1]], check=True)
    print(read("xcodebuild", "-version"))


def simulator(family: str) -> None:
    devices = json.loads(read("xcrun", "simctl", "list", "devices", "available", "--json"))["devices"]
    candidates = [(tuple(map(int, match.group(1).split("-"))), device, runtime)
                  for runtime, rows in devices.items()
                  if (match := re.search(r"iOS-([0-9-]+)$", runtime)) and int(match.group(1).split("-")[0]) >= 18
                  for device in rows if device.get("isAvailable") and device["name"].startswith(family)]
    if not candidates:
        raise SystemExit(f"No available {family} simulator with iOS 18 or newer.")
    _, device, runtime = max(candidates, key=lambda entry: (entry[0], entry[1]["name"]))
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
        output.write("SIMULATOR_UDID=" + device["udid"] + "\n")
    Path("build").mkdir(exist_ok=True)
    Path("build/ios-simulator-build.json").write_text(json.dumps({
        "revision": read("git", "rev-parse", "HEAD"), "xcode": read("xcodebuild", "-version"),
        "sdk": read("xcrun", "--sdk", "iphonesimulator", "--show-sdk-version"),
        "device": device["name"], "runtime": runtime, "signing": "ad-hoc", "platform": "iOS Simulator",
    }, indent=2) + "\n", encoding="utf-8")
    if device["state"] != "Booted":
        subprocess.run(["xcrun", "simctl", "boot", device["udid"]], check=True)
    subprocess.run(["xcrun", "simctl", "bootstatus", device["udid"], "-b"], check=True)


def package_simulator() -> None:
    bundle = Path("build/Build/Products/Debug-iphonesimulator/TreasureUp.app")
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
    app = plistlib.loads((bundle / "Info.plist").read_bytes())
    if app.get("CFBundleSupportedPlatforms") != ["iPhoneSimulator"]:
        raise SystemExit("Expected an iOS Simulator app.")
    path = Path("build/ios-simulator-build.json")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata.update(app_version=app["CFBundleShortVersionString"], build_number=app["CFBundleVersion"],
                    architectures=read("lipo", "-archs", str(bundle / "TreasureUp")).split())
    path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    archive = Path("build/ios-simulator.zip")
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle), str(archive)], check=True)
    with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.write(path, path.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "simulator", "package-simulator"])
    parser.add_argument("--family", choices=["iPhone", "iPad"], default="iPhone")
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        prepare()
    elif arguments.command == "simulator":
        simulator(arguments.family)
    else:
        package_simulator()
