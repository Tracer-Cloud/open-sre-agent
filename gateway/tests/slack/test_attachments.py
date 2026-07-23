from __future__ import annotations

from gateway.slack.attachments import (
    build_files_context,
    extract_text,
    is_text_file,
)
from gateway.slack.events import SlackInboundFile


def _file(name: str = "checkout.log", mimetype: str = "text/plain") -> SlackInboundFile:
    return SlackInboundFile(
        id="F1",
        name=name,
        mimetype=mimetype,
        size=10,
        url_private="https://files.slack.com/x/F1",
    )


def test_is_text_file_accepts_text_like_and_rejects_binary() -> None:
    assert is_text_file(_file(mimetype="text/plain")) is True
    assert is_text_file(_file(mimetype="text/csv")) is True
    assert is_text_file(_file(mimetype="application/json")) is True
    assert is_text_file(_file(mimetype="text/plain; charset=utf-8")) is True
    assert is_text_file(_file(mimetype="image/png")) is False
    assert is_text_file(_file(mimetype="application/octet-stream")) is False


def test_extract_text_decodes_text_and_skips_binary() -> None:
    # Arrange / Act / Assert
    assert extract_text(_file(), b"pool exhausted\n500s") == "pool exhausted\n500s"
    assert extract_text(_file(mimetype="image/png"), b"\x89PNG\r\n") is None
    # latin-1 fallback: undecodable-as-utf-8 bytes do not raise.
    assert extract_text(_file(), b"\xff\xfe caf\xe9") is not None


def test_build_files_context_inlines_text_and_names_binaries() -> None:
    # Arrange: one text file (downloaded via the injected downloader) and one
    # opaque binary that is neither text nor a supported image.
    files = (
        _file(name="checkout.log", mimetype="text/plain"),
        _file(name="blob.bin", mimetype="application/octet-stream"),
    )

    def fake_download(file: SlackInboundFile, _token: str) -> bytes | None:
        return b"database connection timeout" if file.name == "checkout.log" else None

    # Act
    context = build_files_context(
        files, token="test-bot-token", downloader=fake_download, describer=lambda *_: None
    )

    # Assert: the log is inlined; the opaque binary is named but not inlined.
    assert "--- attached file: checkout.log ---" in context
    assert "database connection timeout" in context
    assert "blob.bin (application/octet-stream) — not readable" in context


def test_build_files_context_describes_images_via_vision() -> None:
    # Arrange: an image whose (injected) vision description gets inlined.
    files = (_file(name="graph.png", mimetype="image/png"),)

    # Act
    context = build_files_context(
        files,
        token="test-bot-token",
        downloader=lambda *_: b"\x89PNG\r\n",
        describer=lambda _data, _mime: "A dashboard showing 500 errors spiking at 14:05.",
    )

    # Assert
    assert "--- image: graph.png (vision description) ---" in context
    assert "500 errors spiking" in context


def test_build_files_context_scrubs_secrets_from_inlined_text() -> None:
    # Arrange: a distinctive secret marker that must never survive into the prompt.
    # Assembled at runtime so the source carries no token-shaped literal (which
    # would trip secret scanners); the runtime value still exercises the scrub.
    marker = "xoxb-" + "9" * 12 + "-SECRETLEAKMARKER"
    files = (_file(name="app.log", mimetype="text/plain"),)

    # Act
    context = build_files_context(
        files,
        token="test-bot-token",
        downloader=lambda *_: f"boot token={marker} ready".encode(),
        describer=lambda *_: None,
    )

    # Assert: the secret is gone, replaced by a redaction placeholder.
    assert "SECRETLEAKMARKER" not in context
    assert "[REDACTED]" in context


def test_build_files_context_reports_failed_download() -> None:
    # Arrange: a text file whose download fails.
    files = (_file(name="big.log"),)

    # Act
    context = build_files_context(files, token="test-bot-token", downloader=lambda _f, _t: None)

    # Assert
    assert "big.log — could not be downloaded" in context


def test_build_files_context_empty_when_no_files() -> None:
    assert build_files_context((), token="test-bot-token") == ""
