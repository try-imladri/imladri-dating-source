import hashlib
import json
from pathlib import Path
import struct
import sys

import pytest

from scripts.prune_runtime_components import elf_needed, prune


def linked_elf(*dependencies):
    """Small, non-executable ELF metadata fixture with no machine-code payload."""
    strings = b"\0"
    offsets = []
    for name in dependencies:
        offsets.append(len(strings))
        strings += name.encode() + b"\0"
    entries = [(1, offset) for offset in offsets] + [(5, 0x200), (10, len(strings)), (0, 0)]
    data = bytearray(0x200 + len(strings))
    header = struct.pack("<16sHHIQQQIHHHHHH", b"\x7fELF\x02\x01\x01" + bytes(9),
                         3, 62, 1, 0, 64, 0, 0, 64, 56, 2, 0, 0, 0)
    data[:64] = header
    struct.pack_into("<IIQQQQQQ", data, 64, 1, 4, 0, 0, 0, len(data), len(data), 0x1000)
    struct.pack_into("<IIQQQQQQ", data, 120, 2, 4, 0x100, 0x100, 0x100,
                     len(entries) * 16, len(entries) * 16, 8)
    for index, entry in enumerate(entries):
        struct.pack_into("<qQ", data, 0x100 + index * 16, *entry)
    data[0x200:] = strings
    return bytes(data)


def staged(tmp_path, caller_dependencies=("libmpdec.so.4", "libc.so.6")):
    root, venv = tmp_path / "runtime", tmp_path / "opt/venv"
    library = root / "usr/lib"
    library.mkdir(parents=True)
    venv.mkdir(parents=True)
    cpp = library / "libstdc++.so.6"
    wrapper = library / "libmpdec++.so.4"
    decimal = library / "libmpdec.so.4"
    cpp.write_bytes(linked_elf("libc.so.6"))
    wrapper.write_bytes(linked_elf("libstdc++.so.6", "libmpdec.so.4"))
    decimal.write_bytes(linked_elf("libc.so.6"))
    (venv / "_decimal.so").write_bytes(linked_elf(*caller_dependencies))
    db = root / "usr/lib/apk/db/installed"
    db.parent.mkdir(parents=True)
    db.write_text("P:libstdc++\nV:16.2.0-r3\no:gcc\nF:usr/lib\nR:libstdc++.so.6\n\n"
                  "P:mpdecimal\nV:4.0.1-r6\no:mpdecimal\nF:usr/lib\nR:libmpdec++.so.4\nR:libmpdec.so.4\n\n")
    return root, venv, db, cpp, wrapper, decimal


def test_dependency_metadata_is_read_without_loading_the_library(tmp_path):
    path = tmp_path / "fixture.so"
    path.write_bytes(linked_elf("libmpdec.so.4", "libc.so.6"))
    assert elf_needed(path) == ["libmpdec.so.4", "libc.so.6"]


def test_only_explicit_cpp_files_are_removed_while_c_decimal_and_package_evidence_remain(tmp_path):
    root, venv, database, cpp, wrapper, decimal = staged(tmp_path)
    original_db, original_decimal = database.read_bytes(), decimal.read_bytes()
    manifest = root / "app/backend/runtime_pruning.json"
    result = prune(root, venv, manifest, apply=True)
    assert not cpp.exists() and not wrapper.exists()
    assert decimal.read_bytes() == original_decimal
    assert database.read_bytes() == original_db
    assert result["apk_database_sha256_before"] == hashlib.sha256(original_db).hexdigest()
    assert result["apk_database_sha256_after"] == result["apk_database_sha256_before"]
    assert {row["owner"]["P"] for row in result["removed_files"]} == {"libstdc++", "mpdecimal"}
    assert result["remaining_removed_library_dynamic_dependencies"] == []
    assert json.loads(manifest.read_text()) == result


@pytest.mark.parametrize("dependency", ["libstdc++.so.6", "libstdc++-vendored.so.6.0", "libmpdec++.so.5",
                                       "libform.so.6", "libformw.so.6"])
def test_any_remaining_cpp_caller_blocks_all_file_changes(tmp_path, dependency):
    root, venv, database, cpp, wrapper, decimal = staged(tmp_path, (dependency,))
    before = {path: path.read_bytes() for path in (database, cpp, wrapper, decimal)}
    manifest = root / "app/backend/runtime_pruning.json"
    with pytest.raises(ValueError, match="Remaining ELF"):
        prune(root, venv, manifest, apply=True)
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not manifest.exists()


def test_a_review_run_never_prunes_or_writes_a_manifest(tmp_path):
    root, venv, database, cpp, wrapper, decimal = staged(tmp_path)
    manifest = root / "app/backend/runtime_pruning.json"
    result = prune(root, venv, manifest)
    assert result["status"] == "reviewed_not_applied"
    assert cpp.exists() and wrapper.exists() and decimal.exists()
    assert not manifest.exists()


def test_form_libraries_are_removed_without_changing_curses_or_panel_libraries(tmp_path):
    root, venv, database, cpp, wrapper, decimal = staged(tmp_path)
    library = root / "usr/lib"
    forms = [library / name for name in ("libform.so.6", "libformw.so.6")]
    retained = [library / name for name in ("libncursesw.so.6", "libpanelw.so.6")]
    for path in forms:
        path.write_bytes(linked_elf("libncursesw.so.6", "libc.so.6"))
    for path in retained:
        path.write_bytes(linked_elf("libc.so.6"))
    database.write_text(database.read_text() + "P:ncurses\nV:6.6-r0\no:ncurses\nF:usr/lib\n"
                        + "".join("R:" + path.name + "\n" for path in forms + retained) + "\n")
    original = {path: path.read_bytes() for path in retained}
    result = prune(root, venv, root / "app/backend/runtime_pruning.json", apply=True)
    assert all(not path.exists() for path in forms)
    assert all(path.read_bytes() == data for path, data in original.items())
    assert {row["family"] for row in result["removed_files"]} == {"libstdc++", "libmpdec++", "libform", "libformw"}


def test_incorrect_package_ownership_blocks_component_removal(tmp_path):
    root, venv, database, cpp, wrapper, decimal = staged(tmp_path)
    database.write_text(database.read_text().replace("P:libstdc++", "P:unreviewed-package"))
    with pytest.raises(ValueError, match="ownership"):
        prune(root, venv, root / "app/backend/runtime_pruning.json", apply=True)
    assert cpp.exists() and wrapper.exists()


def test_manifest_cannot_overwrite_a_file_outside_the_staged_image(tmp_path):
    root, venv, database, cpp, wrapper, decimal = staged(tmp_path)
    with pytest.raises(ValueError, match="inside"):
        prune(root, venv, tmp_path / "outside.json", apply=True)
    assert cpp.exists() and wrapper.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux symlink boundary")
def test_component_link_to_outside_the_staging_root_is_rejected(tmp_path):
    root, venv, database, cpp, wrapper, decimal = staged(tmp_path)
    other = tmp_path / "outside.so"
    other.write_bytes(cpp.read_bytes())
    cpp.unlink()
    cpp.symlink_to(other)
    with pytest.raises(ValueError, match="outside"):
        prune(root, venv, root / "app/backend/runtime_pruning.json", apply=True)
    assert cpp.is_symlink() and other.exists() and wrapper.exists()
