"""Check the produced APK, without rewriting its ZIP entries or debug signature."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zipfile
from pathlib import Path, PurePosixPath


RUST_LIBRARY = "lib/arm64-v8a/libtreasure_up_native_lib.so"
MAX_LIBRARY_BYTES = 512 * 1024 * 1024
PRIVATE_SUFFIXES = {".jks", ".keystore", ".p12", ".pfx", ".pem", ".key", ".mobileprovision"}


def elf_sections(data: bytes) -> list[str]:
    """Read section names from the ARM64 little-endian ELF shipped by this job."""
    if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
        raise ValueError("Expected a little-endian ELF64 library")
    if struct.unpack_from("<H", data, 18)[0] != 183:
        raise ValueError("Expected an AArch64 library")
    section_offset = struct.unpack_from("<Q", data, 40)[0]
    entry_size, count, string_index = struct.unpack_from("<HHH", data, 58)
    if entry_size != 64 or count == 0 or not 0 < string_index < count:
        raise ValueError("Missing or unsupported ELF section table")
    if section_offset < 64 or section_offset + entry_size * count > len(data):
        raise ValueError("Truncated ELF section table")
    string_header = section_offset + string_index * entry_size
    string_offset, string_size = struct.unpack_from("<QQ", data, string_header + 24)
    if string_offset + string_size > len(data):
        raise ValueError("Truncated ELF section names")
    names = data[string_offset:string_offset + string_size]
    sections = []
    for index in range(count):
        name_offset = struct.unpack_from("<I", data, section_offset + index * entry_size)[0]
        end = names.find(b"\0", name_offset)
        if name_offset >= len(names) or end < 0:
            raise ValueError("Invalid ELF section name")
        sections.append(names[name_offset:end].decode("ascii", errors="strict"))
    return sections


def check_entry_name(name: str) -> None:
    path = PurePosixPath(name)
    if "\\" in name or path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe APK entry path")
    lowered = path.name.lower()
    if (lowered == ".env" or lowered.startswith(".env.")
            or path.suffix.lower() in PRIVATE_SUFFIXES
            or lowered in {"id_rsa", "id_ed25519", "credentials.toml", "connection.json"}):
        raise ValueError(f"Private configuration or key file in APK: {name}")


def audit_apk(path: Path, baseline_bytes: int | None = None) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("APK must be a regular file")
    libraries = []
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate APK entry")
        for name in names:
            check_entry_name(name)
        if "AndroidManifest.xml" not in names or "classes.dex" not in names or RUST_LIBRARY not in names:
            raise ValueError("Missing Android manifest, classes or Treasure Up ARM64 library")
        for item in entries:
            if not item.filename.startswith("lib/") or not item.filename.endswith(".so"):
                continue
            if not item.filename.startswith("lib/arm64-v8a/"):
                raise ValueError("Unexpected ABI in the ARM64-only APK")
            if item.file_size > MAX_LIBRARY_BYTES:
                raise ValueError("APK library exceeds the inspection size limit")
            sections = elf_sections(archive.read(item))
            debug_sections = [name for name in sections if name.startswith((".debug_", ".zdebug_"))]
            if debug_sections:
                raise ValueError(f"Rust/native debug sections remain in {item.filename}: {', '.join(debug_sections)}")
            libraries.append({"name": item.filename, "bytes": item.file_size, "compressed_bytes": item.compress_size})
    size = path.stat().st_size
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {"file": path.name, "bytes": size, "sha256": checksum, "native_libraries": libraries, "debug_sections": False}
    if baseline_bytes is not None:
        if baseline_bytes <= 0:
            raise ValueError("Baseline size must be positive")
        report["baseline_bytes"] = baseline_bytes
        report["reduction_percent"] = round((1 - size / baseline_bytes) * 100, 2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--baseline-bytes", type=int)
    args = parser.parse_args()
    apks = sorted(args.directory.rglob("*.apk"))
    if not apks:
        raise SystemExit("No APK produced")
    for path in apks:
        print(json.dumps(audit_apk(path, args.baseline_bytes), ensure_ascii=False))


if __name__ == "__main__":
    main()
