from __future__ import annotations

import hashlib
import os
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .schemas import InventoryExtension, InventoryReport, SourceKind

DOCUMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".pdf",
    ".docx",
    ".epub",
    ".html",
    ".htm",
}
SUBTITLE_EXTENSIONS = {".srt", ".vtt"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp"}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
KNOWN_EXTENSIONS = (
    DOCUMENT_EXTENSIONS
    | SUBTITLE_EXTENSIONS
    | AUDIO_EXTENSIONS
    | VIDEO_EXTENSIONS
    | IMAGE_EXTENSIONS
    | ARCHIVE_EXTENSIONS
)
IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
IGNORED_SUFFIXES = {".tmp", ".temp", ".partial", ".swp"}


@dataclass(frozen=True)
class InventoryEntry:
    path: Path
    relative_path: str
    name: str
    extension: str
    kind: SourceKind
    size_bytes: int
    mtime_ns: int
    is_hidden: bool
    is_ignored: bool
    is_symlink: bool


@dataclass(frozen=True)
class InventorySnapshot:
    report: InventoryReport
    entries: tuple[InventoryEntry, ...]
    hashes: dict[str, str]


def source_kind(extension: str) -> SourceKind:
    if extension in DOCUMENT_EXTENSIONS:
        return SourceKind.DOCUMENT
    if extension in SUBTITLE_EXTENSIONS:
        return SourceKind.SUBTITLE
    if extension in AUDIO_EXTENSIONS:
        return SourceKind.AUDIO
    if extension in VIDEO_EXTENSIONS:
        return SourceKind.VIDEO
    if extension in IMAGE_EXTENSIONS:
        return SourceKind.IMAGE
    if extension in ARCHIVE_EXTENSIONS:
        return SourceKind.ARCHIVE
    return SourceKind.UNKNOWN


def sha256_file(path: Path, *, block_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def _problematic_name(path: Path) -> bool:
    name = path.name
    return (
        name != name.strip()
        or unicodedata.normalize("NFC", name) != name
        or any(ord(character) < 32 for character in name)
        or len(name.encode("utf-8")) > 240
    )


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _hidden(relative_path: str) -> bool:
    return any(part.startswith(".") for part in Path(relative_path).parts)


def collect_inventory(
    root: Path,
    *,
    confirm_duplicates: bool = True,
    hash_paths: set[str] | None = None,
) -> InventorySnapshot:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    entries: list[InventoryEntry] = []
    extension_files: Counter[str] = Counter()
    extension_bytes: Counter[str] = Counter()
    directories = 0
    total_bytes = 0
    hidden_files = 0
    empty_files = 0
    over_50_mb = 0
    over_250_mb = 0
    over_1_gb = 0
    without_extension = 0
    symlinks = 0
    inaccessible: list[str] = []
    problematic: list[str] = []
    long_paths: list[str] = []
    size_groups: dict[int, list[InventoryEntry]] = defaultdict(list)

    for current, dirnames, filenames in root.walk(top_down=True, follow_symlinks=False):
        dirnames.sort(key=str.casefold)
        filenames.sort(key=str.casefold)
        safe_directories: list[str] = []
        for dirname in dirnames:
            directory_path = current / dirname
            relative = _relative(directory_path, root)
            if directory_path.is_symlink():
                symlinks += 1
                entries.append(
                    InventoryEntry(
                        path=directory_path,
                        relative_path=relative,
                        name=dirname,
                        extension="",
                        kind=SourceKind.UNKNOWN,
                        size_bytes=0,
                        mtime_ns=0,
                        is_hidden=_hidden(relative),
                        is_ignored=True,
                        is_symlink=True,
                    )
                )
                continue
            directories += 1
            safe_directories.append(dirname)
        dirnames[:] = safe_directories

        for filename in filenames:
            path = current / filename
            relative = _relative(path, root)
            extension = path.suffix.lower()
            is_hidden = _hidden(relative)
            is_ignored = (
                filename in IGNORED_NAMES
                or extension in IGNORED_SUFFIXES
                or filename.endswith("~")
                or is_hidden
            )
            if path.is_symlink():
                symlinks += 1
                entries.append(
                    InventoryEntry(
                        path=path,
                        relative_path=relative,
                        name=filename,
                        extension=extension,
                        kind=source_kind(extension),
                        size_bytes=0,
                        mtime_ns=0,
                        is_hidden=is_hidden,
                        is_ignored=True,
                        is_symlink=True,
                    )
                )
                continue
            try:
                stat = path.stat(follow_symlinks=False)
            except OSError:
                inaccessible.append(relative)
                continue
            if not path.is_file():
                continue
            entry = InventoryEntry(
                path=path,
                relative_path=relative,
                name=filename,
                extension=extension,
                kind=source_kind(extension),
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                is_hidden=is_hidden,
                is_ignored=is_ignored,
                is_symlink=False,
            )
            entries.append(entry)
            extension_key = extension or "[sin extensión]"
            extension_files[extension_key] += 1
            extension_bytes[extension_key] += stat.st_size
            total_bytes += stat.st_size
            if not extension:
                without_extension += 1
            if is_hidden:
                hidden_files += 1
            if stat.st_size == 0:
                empty_files += 1
            if stat.st_size > 50 * 1024 * 1024:
                over_50_mb += 1
            if stat.st_size > 250 * 1024 * 1024:
                over_250_mb += 1
            if stat.st_size > 1024 * 1024 * 1024:
                over_1_gb += 1
            if _problematic_name(path):
                problematic.append(relative)
            if len(os.fsencode(str(path))) > 1_024:
                long_paths.append(relative)
            if stat.st_size > 0 and not is_ignored:
                size_groups[stat.st_size].append(entry)

    candidate_groups = [group for group in size_groups.values() if len(group) > 1]
    hashes: dict[str, str] = {}
    requested_hashes = hash_paths or set()
    paths_to_hash = {
        entry.relative_path for entry in entries if entry.relative_path in requested_hashes
    }
    if confirm_duplicates:
        paths_to_hash.update(entry.relative_path for group in candidate_groups for entry in group)
    for entry in entries:
        if entry.relative_path not in paths_to_hash or entry.is_symlink:
            continue
        try:
            hashes[entry.relative_path] = sha256_file(entry.path)
        except OSError:
            inaccessible.append(entry.relative_path)

    duplicates: dict[tuple[int, str], list[str]] = defaultdict(list)
    for group in candidate_groups:
        for entry in group:
            content_hash = hashes.get(entry.relative_path)
            if content_hash:
                duplicates[(entry.size_bytes, content_hash)].append(entry.relative_path)
    duplicate_groups = [sorted(paths) for paths in duplicates.values() if len(paths) > 1]
    duplicate_groups.sort(key=lambda paths: (paths[0].casefold(), len(paths)))

    kinds = Counter(entry.kind for entry in entries if not entry.is_symlink)
    report = InventoryReport(
        generated_at=datetime.now(UTC),
        root=str(root),
        files=sum(extension_files.values()),
        directories=directories,
        total_bytes=total_bytes,
        by_extension=[
            InventoryExtension(
                extension=extension,
                files=extension_files[extension],
                bytes=extension_bytes[extension],
            )
            for extension in sorted(extension_files, key=str.casefold)
        ],
        without_extension=without_extension,
        hidden_files=hidden_files,
        empty_files=empty_files,
        over_50_mb=over_50_mb,
        over_250_mb=over_250_mb,
        over_1_gb=over_1_gb,
        possible_duplicate_groups=len(candidate_groups),
        confirmed_duplicate_groups=len(duplicate_groups),
        confirmed_duplicate_files=sum(len(group) for group in duplicate_groups),
        symlinks=symlinks,
        unknown_formats=kinds[SourceKind.UNKNOWN],
        documents=kinds[SourceKind.DOCUMENT],
        subtitles=kinds[SourceKind.SUBTITLE],
        audio=kinds[SourceKind.AUDIO],
        video=kinds[SourceKind.VIDEO],
        images=kinds[SourceKind.IMAGE],
        archives=kinds[SourceKind.ARCHIVE],
        problematic_names=sorted(set(problematic), key=str.casefold)[:500],
        overly_long_paths=sorted(set(long_paths), key=str.casefold)[:500],
        inaccessible=sorted(set(inaccessible), key=str.casefold)[:500],
        duplicate_groups=duplicate_groups[:500],
    )
    entries.sort(key=lambda entry: entry.relative_path.casefold())
    return InventorySnapshot(report=report, entries=tuple(entries), hashes=hashes)
