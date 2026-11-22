"""Read a project-owned behavior specification from its exact Git version."""

import hashlib
import os
from pathlib import PurePosixPath

from pack_evidence import git


def spec_path(value):
    if not isinstance(value, str) or not value or not all(ch.isprintable() for ch in value):
        raise ValueError("specification must have a printable project-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or value == "." or ".." in path.parts or ".git" in path.parts:
        raise ValueError("specification must have a normalized project-relative path")
    return value


def committed_spec(project, commit, path):
    """Return the bytes a reviewer and the acceptance entry point will both use."""
    path = spec_path(path)
    entry = git(project, "ls-tree", "-z", commit, "--", f":(literal){path}").stdout
    attrs, _, name = entry.partition(b"\t")
    fields = attrs.split()
    if (name != os.fsencode(path) + b"\0" or len(fields) != 3
            or fields[0] not in (b"100644", b"100755") or fields[1] != b"blob"):
        raise ValueError(f"specification must be a tracked regular file at {commit}: {path}")
    data = git(project, "cat-file", "blob", fields[2].decode("ascii")).stdout
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("the specification presented for approval must be UTF-8 text") from exc
    if not content.strip() or any(not char.isprintable() and char not in "\n\r\t" for char in content):
        raise ValueError("the specification must contain readable examples")
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "body": content, "commit": commit}


def checked_out_spec(project, commit, path):
    spec = committed_spec(project, commit, path)
    local = project / path
    if local.resolve() != local or not local.is_file() or local.read_bytes() != spec["body"].encode("utf-8"):
        raise ValueError("the checked-out examples must match the committed specification bytes")
    return spec
