#!/usr/bin/env python3
"""Atomic, strictly shaped metadata support for managed installations."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

FORMAT_VERSION = 1


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".metadata.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    release = subparsers.add_parser("release")
    release.add_argument("path", type=Path)
    release.add_argument("release_id")
    release.add_argument("version")
    release.add_argument("commit")
    release.add_argument("timestamp")
    release.add_argument("wheel_sha256")
    root = subparsers.add_parser("root")
    root.add_argument("path", type=Path)
    root.add_argument("version")
    root.add_argument("commit")
    root.add_argument("timestamp")
    root.add_argument("active")
    root.add_argument("previous")
    root.add_argument("udev_path")
    root.add_argument("udev_sha256")
    read = subparsers.add_parser("get")
    read.add_argument("path", type=Path)
    read.add_argument("field")
    arguments = parser.parse_args()
    if arguments.command == "release":
        write_json(arguments.path, {
            "format": "nextuxlinq-managed-release", "format_version": FORMAT_VERSION,
            "release_id": arguments.release_id, "package_version": arguments.version,
            "source_commit": arguments.commit, "installed_at": arguments.timestamp,
            "wheel_sha256": arguments.wheel_sha256,
        })
    elif arguments.command == "root":
        write_json(arguments.path, {
            "format": "nextuxlinq-managed-install", "format_version": FORMAT_VERSION,
            "package_version": arguments.version, "source_commit": arguments.commit,
            "installed_at": arguments.timestamp, "active_release": arguments.active,
            "previous_release": arguments.previous or None,
            "udev_rule": {"path": arguments.udev_path,
                          "sha256": arguments.udev_sha256 or None,
                          "installed": bool(arguments.udev_sha256)},
        })
    else:
        value: object = json.loads(arguments.path.read_text(encoding="utf-8"))
        for part in arguments.field.split("."):
            if not isinstance(value, dict) or part not in value:
                return 2
            value = value[part]
        print("" if value is None else value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
