"""The .json file saved next to every auto-recorded video: versioned, with automatic upgrades.

Schema history
  v1  Phase 2 - League only. No "schema" or "game_id" keys.
  v2  + "schema" and "game_id".
Add a step to MIGRATIONS whenever the format changes; old files are upgraded in memory
on read and written back in the new format the next time they're saved.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("gamecapture.sidecar")


class Sidecar:
    SCHEMA_VERSION = 2

    @staticmethod
    def _v1_to_v2(data: dict) -> dict:
        data.setdefault("game_id", "league")  # v1 files were only ever made for League
        return data

    MIGRATIONS = {1: _v1_to_v2}  # from-version -> upgrade step

    @staticmethod
    def path_for(video: Path) -> Path:
        return video.with_suffix(".json")

    @classmethod
    def build(cls, video_name: str, game_id: str, game: dict, markers: list[dict]) -> dict:
        return {"schema": cls.SCHEMA_VERSION, "game_id": game_id, "video": video_name,
                "game": game, "markers": markers}

    @classmethod
    def migrate(cls, data: dict) -> dict:
        version = int(data.get("schema", 1))
        while version < cls.SCHEMA_VERSION:
            data = cls.MIGRATIONS[version].__func__(data)
            version += 1
            data["schema"] = version
        return data

    @classmethod
    def is_writable(cls, data: dict | None) -> bool:
        """False for files from a NEWER GameCapture - saving would drop fields we don't know."""
        return data is not None and int(data.get("schema", 1)) <= cls.SCHEMA_VERSION

    @classmethod
    def read(cls, video: Path) -> dict | None:
        try:
            data = json.loads(cls.path_for(video).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        if int(data.get("schema", 1)) > cls.SCHEMA_VERSION:
            log.warning("%s was made by a newer GameCapture (schema %s) - opened read-only",
                        cls.path_for(video).name, data.get("schema"))
            return data
        return cls.migrate(data)

    @classmethod
    def write(cls, video: Path, data: dict) -> None:
        if not cls.is_writable(data):
            raise PermissionError(f"{cls.path_for(video).name} uses a newer schema - not overwriting it")
        target = cls.path_for(video)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, target)  # atomic: a crash never leaves half a file
