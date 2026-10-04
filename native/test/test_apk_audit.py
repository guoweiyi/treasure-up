import importlib.util
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path


spec = importlib.util.spec_from_file_location("audit_apk", Path(__file__).parents[1] / "scripts" / "audit_apk.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def elf_fixture(extra_sections=()):
    section_names = ["", ".shstrtab", ".text", ".dynsym", ".eh_frame", *extra_sections]
    strings = bytearray()
    offsets = []
    for name in section_names:
        offsets.append(len(strings))
        strings.extend(name.encode("ascii") + b"\0")
    table = 64 + len(strings)
    data = bytearray(table + len(section_names) * 64)
    data[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<H", data, 18, 183)
    struct.pack_into("<Q", data, 40, table)
    struct.pack_into("<HHH", data, 58, 64, len(section_names), 1)
    data[64:table] = strings
    for index, offset in enumerate(offsets):
        struct.pack_into("<I", data, table + index * 64, offset)
    struct.pack_into("<QQ", data, table + 64 + 24, 64, len(strings))
    return bytes(data)


class ApkAuditTests(unittest.TestCase):
    def make_apk(self, directory, library=None, extra=()):
        path = Path(directory) / "treasure-up-debug.apk"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("AndroidManifest.xml", b"synthetic manifest")
            archive.writestr("classes.dex", b"synthetic classes")
            archive.writestr("META-INF/CERT.RSA", b"public debug signature is allowed")
            archive.writestr(audit.RUST_LIBRARY, library or elf_fixture())
            for name, data in extra:
                archive.writestr(name, data)
        return path

    def test_stripped_apk_reports_measured_bytes_without_changing_signed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_apk(directory)
            before = path.read_bytes()
            result = audit.audit_apk(path, baseline_bytes=100000)
            self.assertEqual(result["bytes"], len(before))
            self.assertGreater(result["reduction_percent"], 0)
            self.assertEqual(result["native_libraries"][0]["bytes"], len(elf_fixture()))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(result["sha256"]), 64)

    def test_dwarf_and_compressed_dwarf_fail_the_artifact_gate(self):
        for section in [".debug_info", ".debug_line", ".zdebug_info"]:
            with self.subTest(section=section), tempfile.TemporaryDirectory() as directory:
                path = self.make_apk(directory, elf_fixture([section]))
                with self.assertRaisesRegex(ValueError, "debug sections remain"):
                    audit.audit_apk(path)

    def test_private_files_and_path_traversal_are_not_publishable(self):
        for name in ["assets/.env", "assets/.env.production", "release.jks", "private.key", "signing.p12", "connection.json", "../outside"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = self.make_apk(directory, extra=[(name, b"synthetic test data")])
                with self.assertRaises(ValueError):
                    audit.audit_apk(path)
        # Python's ZIP writer normalizes backslashes on Windows; inspect this raw name directly.
        with self.assertRaises(ValueError):
            audit.check_entry_name("a\\b")

    def test_corrupt_or_wrong_architecture_libraries_are_rejected(self):
        invalid_arch = bytearray(elf_fixture())
        struct.pack_into("<H", invalid_arch, 18, 62)
        for binary in [b"not ELF", elf_fixture()[:70], bytes(invalid_arch)]:
            with self.subTest(size=len(binary)):
                with self.assertRaises(ValueError):
                    audit.elf_sections(binary)
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_apk(directory, extra=[("lib/x86_64/libunexpected.so", elf_fixture())])
            with self.assertRaisesRegex(ValueError, "Unexpected ABI"):
                audit.audit_apk(path)


if __name__ == "__main__":
    unittest.main()
