"""Symlink-safe, atomic output helpers for Keen-generated artifacts.

Repositories and capture directories are untrusted input. A pre-created
``components/`` symlink or ``report.json`` symlink must never let a Keen run
write outside the caller-selected output root.
"""

from __future__ import annotations

import contextlib
import os
import stat
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path


class UnsafeOutputError(ValueError):
    """Raised when an output path is not a regular file below its trusted root."""


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def is_linklike(path: Path) -> bool:
    """Detect symlinks plus Windows junction/reparse-point directories."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            if is_junction():
                return True
        except OSError:
            return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except (FileNotFoundError, OSError):
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _allowed_system_alias(path: Path) -> bool:
    """Allow only immutable, root-owned macOS compatibility aliases."""
    if sys.platform != "darwin":
        return False
    expected_targets = {
        "/etc": "private/etc",
        "/home": "/System/Volumes/Data/home",
        "/tmp": "private/tmp",  # nosec B108
        "/var": "private/var",
    }
    expected = expected_targets.get(str(path))
    if expected is None or not path.is_symlink():
        return False
    try:
        return path.lstat().st_uid == 0 and os.readlink(path) == expected
    except OSError:
        return False


def _reject_linklike_ancestors(path: Path) -> None:
    # Walk from the filesystem anchor so an absolute path into another
    # project cannot hide a repo-controlled symlink above the selected run.
    # macOS's root-owned /tmp, /var, /etc, and /home compatibility aliases are
    # the only exceptions; arbitrary user-owned links remain fail-closed.
    anchor = Path(path.anchor)
    current = anchor
    walked: list[Path] = [current]
    for part in path.relative_to(anchor).parts:
        current = current / part
        walked.append(current)
    candidates = tuple(walked)
    for candidate in candidates:
        if is_linklike(candidate) and not _allowed_system_alias(candidate):
            raise UnsafeOutputError(f"refusing symlink or junction output ancestor: {candidate}")


def ensure_safe_input_path(path: Path, *, directory: bool | None = None) -> Path:
    """Validate an existing read target without creating or resolving it.

    Every non-system ancestor is part of the trust boundary. This prevents
    ``.keen`` (or another ancestor) from redirecting reads outside a project
    even when the final run directory and artifact are regular files.
    """
    candidate = _absolute(path)
    _reject_linklike_ancestors(candidate)
    if is_linklike(candidate):
        raise UnsafeOutputError(f"refusing symlink or junction input path: {candidate}")
    try:
        mode = candidate.stat(follow_symlinks=False).st_mode
    except OSError as exc:
        raise UnsafeOutputError(f"input path is unavailable: {candidate}") from exc
    if directory is True and not stat.S_ISDIR(mode):
        raise UnsafeOutputError(f"input path is not a directory: {candidate}")
    if directory is False and not stat.S_ISREG(mode):
        raise UnsafeOutputError(f"input path is not a regular file: {candidate}")
    return candidate


def _root(root: Path) -> Path:
    root_path = _absolute(root)
    _reject_linklike_ancestors(root_path)
    if root_path.exists() and not root_path.is_dir():
        raise UnsafeOutputError(f"output root is not a directory: {root_path}")
    root_path.mkdir(parents=True, exist_ok=True)
    _reject_linklike_ancestors(root_path)
    if not root_path.is_dir():
        raise UnsafeOutputError(f"unsafe output root: {root_path}")
    return root_path


def _relative_parts(root: Path, path: Path) -> tuple[Path, tuple[str, ...]]:
    raw = Path(path)
    if ".." in raw.parts:
        raise UnsafeOutputError(f"output path contains traversal: {path}")
    root_path = _root(root)
    candidate = _absolute(raw)
    try:
        relative = candidate.relative_to(root_path)
    except ValueError:
        raise UnsafeOutputError(f"output path escapes root {root_path}: {candidate}") from None
    if not relative.parts:
        raise UnsafeOutputError("output file path must not be the output root")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise UnsafeOutputError(f"unsafe output path: {path}")
    return root_path, relative.parts


def ensure_output_dir(root: Path, directory: Path) -> Path:
    """Create ``directory`` below ``root`` without following descendant symlinks."""
    root_path = _root(root)
    raw = Path(directory)
    if ".." in raw.parts:
        raise UnsafeOutputError(f"output directory contains traversal: {directory}")
    candidate = _absolute(raw)
    try:
        relative = candidate.relative_to(root_path)
    except ValueError:
        raise UnsafeOutputError(f"output directory escapes root {root_path}: {candidate}") from None

    current = root_path
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise UnsafeOutputError(f"unsafe output directory: {directory}")
        current = current / part
        if is_linklike(current):
            raise UnsafeOutputError(f"refusing symlink or junction output directory: {current}")
        if current.exists():
            if not current.is_dir():
                raise UnsafeOutputError(f"output ancestor is not a directory: {current}")
            continue
        current.mkdir(mode=0o700)
        if is_linklike(current) or not current.is_dir():
            raise UnsafeOutputError(f"unsafe output directory: {current}")
    return candidate


def _prepare_file(root: Path, path: Path) -> tuple[Path, Path]:
    root_path, parts = _relative_parts(root, path)
    parent = ensure_output_dir(root_path, root_path.joinpath(*parts[:-1]))
    destination = parent / parts[-1]
    if is_linklike(destination):
        raise UnsafeOutputError(f"refusing symlink or junction output file: {destination}")
    if destination.exists():
        try:
            mode = destination.stat(follow_symlinks=False).st_mode
        except OSError as exc:
            raise UnsafeOutputError(f"cannot inspect output file: {destination}") from exc
        if not stat.S_ISREG(mode):
            raise UnsafeOutputError(f"output destination is not a regular file: {destination}")
    return root_path, destination


def _replace(root: Path, temporary: Path, destination: Path, *, mode: int) -> None:
    # Re-check immediately before the atomic rename. os.replace replaces a
    # symlink itself rather than following its target, but rejecting it makes
    # the policy explicit and catches a concurrent path swap.
    _root(root)
    ensure_output_dir(root, destination.parent)
    if is_linklike(destination):
        raise UnsafeOutputError(f"refusing symlink or junction output file: {destination}")
    if destination.exists() and not destination.is_file():
        raise UnsafeOutputError(f"output destination is not a regular file: {destination}")
    if is_linklike(temporary) or not temporary.is_file():
        raise UnsafeOutputError(f"unsafe staged output file: {temporary}")
    os.replace(temporary, destination)


def atomic_write_bytes(root: Path, path: Path, data: bytes, *, mode: int = 0o600) -> Path:
    """Atomically write bytes without following output symlinks."""
    root_path, destination = _prepare_file(root, path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            fchmod = getattr(os, "fchmod", None)
            if callable(fchmod):
                fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _replace(root_path, temporary, destination, mode=mode)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    return destination


def atomic_write_text(
    root: Path,
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int = 0o600,
) -> Path:
    """Atomically write text without following output symlinks."""
    return atomic_write_bytes(root, path, text.encode(encoding), mode=mode)


def remove_output_file(root: Path, path: Path) -> None:
    """Remove one regular generated file without following links or ancestors."""
    root_path, destination = _prepare_file(root, path)
    _root(root_path)
    ensure_output_dir(root_path, destination.parent)
    if is_linklike(destination):
        raise UnsafeOutputError(f"refusing symlink or junction output file: {destination}")
    try:
        mode = destination.stat(follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise UnsafeOutputError(f"cannot inspect output file: {destination}") from exc
    if not stat.S_ISREG(mode):
        raise UnsafeOutputError(f"output destination is not a regular file: {destination}")
    destination.unlink()


@contextlib.contextmanager
def staged_output_path(
    root: Path,
    path: Path,
    *,
    mode: int = 0o600,
) -> Iterator[Path]:
    """Yield a private temporary path, then atomically install it at ``path``.

    Use this only for libraries that require a filesystem pathname. Prefer
    ``atomic_write_bytes`` when the library can serialize in memory.
    """
    root_path, destination = _prepare_file(root, path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        fchmod = getattr(os, "fchmod", None)
        if callable(fchmod):
            fchmod(descriptor, mode)
    finally:
        os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        yield temporary
        _replace(root_path, temporary, destination, mode=mode)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
