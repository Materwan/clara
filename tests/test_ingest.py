"""Archives and Word documents: the size in an archive's header is written by its maker, so the bytes are counted."""

from __future__ import annotations

import io
import struct
import tracemalloc
import zipfile

import pytest

from clara.knowledge import ingest

BOMB_BYTES = 60_000_000  # 60 MB of one character: a 60 KB archive


def forged(entries: dict[str, bytes], claimed: int = 10) -> bytes:
    """A zip whose headers (central directory and local) claim every entry is `claimed` bytes long."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    data = bytearray(buffer.getvalue())
    for signature, offset in ((b"PK\x01\x02", 24), (b"PK\x03\x04", 22)):
        at = data.find(signature)
        while at != -1:
            struct.pack_into("<I", data, at + offset, claimed)
            at = data.find(signature, at + 4)
    return bytes(data)


def peak_memory(function, *args):
    tracemalloc.start()
    try:
        function(*args)
    finally:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return peak


def test_a_zip_whose_header_understates_its_size_does_not_unpack_in_memory():
    """CODE_REVIEW S2: a 60 KB archive that claims 10 bytes used to decompress 60 MB before zipfile looked at the size."""
    data = forged({"big.txt": b"0" * BOMB_BYTES})
    assert len(data) < 100_000
    results = []
    peak = peak_memory(lambda: results.append(ingest.unpack(data)))
    files, skipped = results[0]
    assert files == [] and [item.path for item in skipped] == ["big.txt"]
    assert peak < 20_000_000


def test_a_docx_whose_header_understates_its_size_does_not_unpack_in_memory():
    data = forged({"word/document.xml": b"0" * BOMB_BYTES})

    def read():
        with pytest.raises(ingest.IngestError):
            ingest.docx_text(data)

    assert peak_memory(read) < 20_000_000


def test_reading_stops_at_the_cap_whatever_the_header_says():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("a.txt", b"x" * 3_000_000)
    with zipfile.ZipFile(buffer) as archive:
        assert ingest.read_capped(archive, "a.txt", 2_999_999) is None
        assert ingest.read_capped(archive, "a.txt", 3_000_000) == b"x" * 3_000_000


def test_what_an_archive_really_unpacks_to_is_counted_against_the_total(monkeypatch):
    monkeypatch.setattr(ingest, "MAX_UNPACKED_BYTES", 1_500)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("a.txt", "b.txt", "c.txt"):
            archive.writestr(name, "x" * 1_000)
    files, skipped = ingest.unpack(buffer.getvalue())
    assert [file.path for file in files] == ["a.txt"]
    assert {item.path: item.reason for item in skipped} == {
        "b.txt": "the archive unpacks to too much",
        "c.txt": "the archive unpacks to too much",
    }


def test_an_ordinary_archive_and_document_still_read():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("notes/a.md", "# Hello\n")
        archive.writestr("word/document.xml", "")  # not a real docx, just an entry
    files, skipped = ingest.unpack(buffer.getvalue())
    assert [(file.path, file.text) for file in files if file.path.endswith(".md")] == [("notes/a.md", "# Hello\n")]
    assert all(item.path != "notes/a.md" for item in skipped)
