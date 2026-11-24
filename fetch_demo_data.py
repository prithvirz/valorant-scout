"""Download the pinned GRID demo feeds from Cloud9's starter repository."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen


REPOSITORY = "cloud9esports/stl-hackathon-2026-grid-starter"
COMMIT = "af2355877299e99b27e247951415e6fb5a40fc82"
RAW_BASE_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/val"
FETCH_COMMAND = "python fetch_demo_data.py"
G2_FETCH_COMMAND = "python fetch_demo_data.py --g2"
CHUNK_SIZE = 1024 * 1024
URL_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class DemoFeed:
    filename: str
    size: int
    git_blob_sha: str
    url: str


FEEDS = (
    DemoFeed(
        "events_2748743_grid.jsonl.zst",
        975909,
        "1417f518bcc68187a3e8070d1860e79485dcfdf5",
        f"{RAW_BASE_URL}/events_2748743_grid.jsonl.zst",
    ),
    DemoFeed(
        "events_2748747_grid.jsonl.zst",
        758665,
        "b351fc27aaf2f0386d1b17f704f15118d7ca3976",
        f"{RAW_BASE_URL}/events_2748747_grid.jsonl.zst",
    ),
    DemoFeed(
        "events_2748757_grid.jsonl.zst",
        1449868,
        "4c5a34d5f3113071ae02fac700da366646f9ff45",
        f"{RAW_BASE_URL}/events_2748757_grid.jsonl.zst",
    ),
)


G2_FEEDS = tuple(
    DemoFeed(
        f"events_{series_id}_grid.jsonl.zst",
        size,
        digest,
        f"{RAW_BASE_URL}/events_{series_id}_grid.jsonl.zst",
    )
    for series_id, size, digest in (
        (2775988, 1402728, "7d83755476777a97e5d39b0802f2582279f647c4"),
        (2775990, 1624892, "c51de5b6cd96aa2766d8509ca27b3977677f6c78"),
        (2775992, 1443953, "49f9360c5c6d9aa45b78fe30ba6c34e09f51602e"),
        (2775993, 1950364, "0c696f9a4bf7925151d7b555ec704d644c4caef6"),
        (2775994, 1919547, "4f75d74efafc922c5c472552fedf3c2ec72eefd7"),
    )
)


class FetchError(Exception):
    """Raised when a downloaded feed does not match the pinned repository object."""


def _git_blob_digest(path: Path, expected_size: int) -> str | None:
    try:
        if path.stat().st_size != expected_size:
            return None
        digest = hashlib.sha1(f"blob {expected_size}\0".encode("ascii"))
        with path.open("rb") as source:
            while chunk := source.read(CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _is_valid_copy(path: Path, feed: DemoFeed) -> bool:
    return _git_blob_digest(path, feed.size) == feed.git_blob_sha


def _download_feed(feed: DemoFeed, destination: Path) -> None:
    output_path = destination / feed.filename
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{feed.filename}.", suffix=".tmp", dir=destination
    )
    temporary_path = Path(temporary_name)
    try:
        digest = hashlib.sha1(f"blob {feed.size}\0".encode("ascii"))
        downloaded_size = 0
        with os.fdopen(descriptor, "wb") as output:
            with urlopen(feed.url, timeout=URL_TIMEOUT_SECONDS) as response:
                while chunk := response.read(CHUNK_SIZE):
                    output.write(chunk)
                    digest.update(chunk)
                    downloaded_size += len(chunk)
            output.flush()
            os.fsync(output.fileno())

        if downloaded_size != feed.size or digest.hexdigest() != feed.git_blob_sha:
            raise FetchError(f"Downloaded {feed.filename} failed its pinned size or Git blob SHA-1 check.")
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def fetch_demo_data(destination: Path | None = None, *, g2: bool = False) -> None:
    output_directory = Path.cwd() if destination is None else Path(destination)
    if not output_directory.is_dir():
        raise FileNotFoundError(f"Destination folder does not exist: {output_directory}")

    for feed in G2_FEEDS if g2 else FEEDS:
        output_path = output_directory / feed.filename
        if _is_valid_copy(output_path, feed):
            print(f"Reusing valid {feed.filename}")
            continue
        print(f"Downloading {feed.filename}")
        _download_feed(feed, output_directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--g2", action="store_true", help="fetch the five matches in the G2 Split study")
    args = parser.parse_args(argv)
    try:
        fetch_demo_data(g2=args.g2)
    except (OSError, FetchError) as exc:
        print(f"Unable to fetch GRID demo data: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
