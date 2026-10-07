"""Platform and packaging checks run on Linux/Windows before macOS CI builds."""
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import stat
import struct
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("package_device", ROOT / "native/apple/scripts/package_device.py")
device = importlib.util.module_from_spec(spec)
spec.loader.exec_module(device)
REVISION = "a" * 40


def executable(*, platform=2, cpu=0x0100000C, signed=False, encrypted=False):
    commands = [struct.pack("<6I", 0x32, 24, platform, 18 << 16, 26 << 16, 0)]
    if signed:
        commands.append(struct.pack("<4I", 0x1D, 16, 128, 16))
    if encrypted:
        commands.append(struct.pack("<6I", 0x2C, 24, 128, 16, 1, 0))
    payload = b"".join(commands)
    return struct.pack("<8I", 0xFEEDFACF, cpu, 0, 2, len(commands), len(payload), 0, 0) + payload + b"test-text-section"


@pytest.fixture
def app(tmp_path):
    bundle = tmp_path / "Release-iphoneos/TreasureUp.app"
    bundle.mkdir(parents=True)
    (bundle / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleSupportedPlatforms": ["iPhoneOS"], "CFBundlePackageType": "APPL",
        "CFBundleIdentifier": "com.guoweiyi.treasureup", "CFBundleExecutable": "TreasureUp",
        "UIDeviceFamily": [1, 2], "CFBundleShortVersionString": "0.4.3",
        "CFBundleVersion": "4", "MinimumOSVersion": "18.0",
    }))
    (bundle / "TreasureUp").write_bytes(executable())
    (bundle / "TreasureUp").chmod(0o755)
    (bundle / "Assets.car").write_bytes(b"test-assets")
    return bundle


def test_device_ipa_layout_metadata_hash_and_permissions(app, tmp_path):
    output = tmp_path / "distribution"
    metadata = device.package(app, output, revision=REVISION, sdk="26.5", xcode="Xcode 26.6")
    archive = output / "ios-unsigned.ipa"
    assert json.loads((output / "ios-device-build.json").read_text()) == metadata
    assert metadata["revision"] == REVISION
    assert metadata["platform"] == "iOS"
    assert metadata["architectures"] == ["arm64"]
    assert metadata["signing"] == "unsigned" and metadata["requires_resigning"] is True
    assert metadata["minimum_os_version"] == "18.0"
    assert metadata["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as ipa:
        assert set(ipa.namelist()) == {
            "Payload/TreasureUp.app/Info.plist", "Payload/TreasureUp.app/TreasureUp", "Payload/TreasureUp.app/Assets.car",
        }
        assert ipa.read("Payload/TreasureUp.app/TreasureUp") == executable()
        # Python preserves the source filesystem's mode; Windows does not expose chmod's Unix execute bit.
        assert stat.S_IMODE(ipa.getinfo("Payload/TreasureUp.app/TreasureUp").external_attr >> 16) == stat.S_IMODE((app / "TreasureUp").stat().st_mode)


@pytest.mark.parametrize("settings", [{"platform": 7}, {"platform": 1}, {"cpu": 0x01000007}])
def test_rejects_arm64_simulator_and_other_platform_binaries_even_with_device_plist(app, settings):
    (app / "TreasureUp").write_bytes(executable(**settings))
    with pytest.raises(ValueError, match="iPhoneOS|arm64"):
        device.validate_app(app)


@pytest.mark.parametrize("settings", [{"signed": True}, {"encrypted": True}])
def test_rejects_previously_signed_or_app_store_encrypted_binary(app, settings):
    (app / "TreasureUp").write_bytes(executable(**settings))
    with pytest.raises(ValueError, match="CODE_SIGNING_ALLOWED|Encrypted"):
        device.validate_app(app)


@pytest.mark.parametrize("content", [b"", b"not-an-app", executable()[:40],
    struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 1, 8, 0, 0) + struct.pack("<II", 0x32, 0)])
def test_rejects_truncated_or_malformed_macho(app, content):
    (app / "TreasureUp").write_bytes(content)
    with pytest.raises(ValueError, match="header|commands"):
        device.validate_app(app)


@pytest.mark.parametrize("key,value", [
    ("CFBundleSupportedPlatforms", ["iPhoneSimulator"]),
    ("CFBundleExecutable", "../unrelated"), ("UIDeviceFamily", [1]),
    ("CFBundleIdentifier", "some.other.app"), ("CFBundleShortVersionString", "$(MARKETING_VERSION)"),
])
def test_rejects_wrong_or_unexpanded_bundle_properties(app, key, value):
    path = app / "Info.plist"
    info = plistlib.loads(path.read_bytes())
    info[key] = value
    path.write_bytes(plistlib.dumps(info))
    with pytest.raises(ValueError):
        device.validate_app(app)


@pytest.mark.parametrize("relative", ["embedded.mobileprovision", "_CodeSignature/CodeResources"])
def test_signing_material_is_not_published(app, relative):
    path = app / relative
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"must-not-publish")
    with pytest.raises(ValueError, match="signing material"):
        device.validate_app(app)


def test_bundle_symlinks_cannot_exfiltrate_unrelated_runner_files(app, tmp_path):
    secret = tmp_path / "unrelated.txt"
    secret.write_text("must-not-publish")
    try:
        (app / "secret.txt").symlink_to(secret)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Symlink creation requires Windows developer mode; Linux CI runs this case")
        raise
    with pytest.raises(ValueError, match="links"):
        device.validate_app(app)


def test_bad_revision_and_recursive_output_are_rejected_before_creating_ipa(app, tmp_path):
    with pytest.raises(ValueError, match="exact source"):
        device.package(app, tmp_path, revision="main", sdk="26.5", xcode="Xcode 26.6")
    with pytest.raises(ValueError, match="outside"):
        device.package(app, app / "output", revision=REVISION, sdk="26.5", xcode="Xcode 26.6")
    assert not (app / "output").exists()
