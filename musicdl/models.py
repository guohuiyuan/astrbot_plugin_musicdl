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
    album_id: str = ""
    size: int = 0
    bitrate: int = 0
    url: str = ""
    ext: str = ""
    link: str = ""
    is_invalid: bool = False
    invalid_reason: str = ""
    invalid_type: str = ""

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
    play_count: int = 0
    description: str = ""
    link: str = ""
    extra: str = ""

    @property
    def label(self) -> str:
        return "专辑" if self.kind == SEARCH_TYPE_ALBUM else "歌单"


@dataclass
class SelectionState:
    keyword: str = ""
    search_type: str = SEARCH_TYPE_SONG
    sources: list[str] | None = None
    songs: list[Song] = field(default_factory=list)
    collections: list[Collection] = field(default_factory=list)
    page: int = 1
    page_size: int = 50
    reloadable: bool = True
    created_at: float = 0


@dataclass
class DownloadedFile:
    path: Path
    filename: str
    song: Song
    url: str = ""
