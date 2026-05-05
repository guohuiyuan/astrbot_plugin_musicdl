from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


SEARCH_TYPE_SONG = "song"
SEARCH_TYPE_PLAYLIST = "playlist"
SEARCH_TYPE_ALBUM = "album"


@dataclass
class Song:
    id: str
    source: str
    name: str
    artist: str
    album: str = ""
    cover: str = ""
    duration: int = 0
    extra: str = ""

    @property
    def title(self) -> str:
        artist = self.artist or "未知歌手"
        return f"{self.name or 'Unknown'} - {artist}"


@dataclass
class Collection:
    id: str
    source: str
    name: str = ""
    creator: str = ""
    cover: str = ""
    track_count: int = 0
    kind: str = SEARCH_TYPE_PLAYLIST

    @property
    def label(self) -> str:
        return "专辑" if self.kind == SEARCH_TYPE_ALBUM else "歌单"


@dataclass
class SelectionState:
    search_type: str
    songs: list[Song] = field(default_factory=list)
    collections: list[Collection] = field(default_factory=list)


@dataclass
class DownloadedFile:
    path: Path
    filename: str
    song: Song
