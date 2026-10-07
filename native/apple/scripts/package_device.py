"""Package a real iPhoneOS build for user re-signing, never a simulator build.

This creates a transport ZIP, not an Apple-signed distribution archive. Run after
an unsigned Release build; the recipient must supply their own signing identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import struct
import subprocess
import tempfile
import zipfile


def validate_executable(path: Path) -> None:
    """Require a thin arm64 iOS executable; arm64 alone also permits Simulator."""
    with path.open("rb") as source:
        header = source.read(32)
        if len(header) != 32:
            raise ValueError("The device executable has a truncated Mach-O header.")
        magic, cpu, _, filetype, commands, size, _, _ = struct.unpack("<8I", header)
        if (magic, cpu, filetype) != (0xFEEDFACF, 0x0100000C, 2):
            raise ValueError("The device executable must be a thin arm64 Mach-O app.")
        if not 1 <= commands <= 4096 or not 8 <= size <= 16 * 1024 * 1024:
            raise ValueError("The device executable has invalid load commands.")
        data = source.read(size)
    if len(data) != size:
        raise ValueError("The device executable has truncated load commands.")
    offset = 0
    platforms = []
    for _ in range(commands):
        if offset + 8 > size:
            raise ValueError("The device executable has truncated load commands.")
        command, length = struct.unpack_from("<II", data, offset)
        if length < 8 or length % 8 or offset + length > size:
            raise ValueError("The device executable has malformed load commands.")
        if command == 0x32:  # LC_BUILD_VERSION, PLATFORM_IOS=2 (Simulator=7)
            if length < 24:
                raise ValueError("The device executable has no valid build platform.")
            platforms.append(struct.unpack_from("<I", data, offset + 8)[0])
        if command == 0x2C:  # LC_ENCRYPTION_INFO_64: no App Store-encrypted binary
            if length < 24 or struct.unpack_from("<I", data, offset + 16)[0]:
                raise ValueError("Encrypted executables cannot be re-signed.")
        if command == 0x1D:  # LC_CODE_SIGNATURE
            raise ValueError("Build the device app with CODE_SIGNING_ALLOWED=NO.")
        offset += length
    if offset != size or platforms != [2]:
        raise ValueError("The executable must target iPhoneOS, not iOS Simulator.")


def validate_app(bundle: Path) -> dict:
    if bundle.name != "TreasureUp.app" or not bundle.is_dir() or bundle.is_symlink():
        raise ValueError("Expected a regular TreasureUp.app build directory.")
    for path in bundle.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Device bundles cannot contain links or special files.")
        if path.name == "_CodeSignature" or path.suffix == ".mobileprovision":
            raise ValueError("The device bundle must not contain signing material.")
    info = plistlib.loads((bundle / "Info.plist").read_bytes())
    if info.get("CFBundleSupportedPlatforms") != ["iPhoneOS"]:
        raise ValueError("The app must be built for iPhoneOS, not iOS Simulator.")
    if info.get("CFBundlePackageType") != "APPL" or info.get("CFBundleExecutable") != "TreasureUp":
        raise ValueError("The device bundle must contain the TreasureUp app executable.")
    if info.get("CFBundleIdentifier") != "com.guoweiyi.treasureup":
        raise ValueError("The release bundle identifier must match the source project.")
    if sorted(info.get("UIDeviceFamily", [])) != [1, 2]:
        raise ValueError("The device app must support both iPhone and iPad.")
    for key in ("CFBundleShortVersionString", "CFBundleVersion", "MinimumOSVersion"):
        if not re.fullmatch(r"\d+(?:\.\d+){0,2}", str(info.get(key, ""))):
            raise ValueError(f"The device bundle has an invalid {key}.")
    validate_executable(bundle / "TreasureUp")
    return info


def package(bundle: Path, output: Path, *, revision: str, sdk: str, xcode: str) -> dict:
    if not re.fullmatch(r"[a-fA-F0-9]{40}", revision):
        raise ValueError("An exact source commit SHA is required.")
    bundle = bundle.absolute()
    output = output.resolve()
    if output.is_relative_to(bundle.resolve()):
        raise ValueError("Distribution output must be outside the app bundle.")
    info = validate_app(bundle)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "ios-unsigned.ipa"
    # ZIP stores each original Unix mode, including executable permissions.
    with tempfile.TemporaryDirectory(prefix="ipa-", dir=output) as temporary:
        archive = Path(temporary) / destination.name
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    zipped.write(path, "Payload/" + path.relative_to(bundle.parent).as_posix())
        os.replace(archive, destination)
    digest = hashlib.sha256()
    with destination.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    metadata = {
        "revision": revision.lower(), "platform": "iOS", "architectures": ["arm64"],
        "configuration": "Release", "signing": "unsigned", "requires_resigning": True,
        "app_version": info["CFBundleShortVersionString"], "build_number": info["CFBundleVersion"],
        "bundle_identifier": info["CFBundleIdentifier"], "minimum_os_version": info["MinimumOSVersion"],
        "device_families": ["iPhone", "iPad"], "sdk": sdk, "xcode": xcode,
        "sha256": digest.hexdigest(),
    }
    (output / "ios-device-build.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path("build/device/Build/Products/Release-iphoneos/TreasureUp.app"))
    parser.add_argument("--output-dir", type=Path, default=Path("build/distribution"))
    args = parser.parse_args()
    def read(*command: str) -> str:
        return subprocess.check_output(command, text=True).strip()
    metadata = package(args.app, args.output_dir, revision=read("git", "rev-parse", "HEAD"),
                       sdk=read("xcrun", "--sdk", "iphoneos", "--show-sdk-version"),
                       xcode=read("xcodebuild", "-version"))
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
