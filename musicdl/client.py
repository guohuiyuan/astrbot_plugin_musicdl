from __future__ import annotations

from .models import SEARCH_TYPE_ALBUM, SEARCH_TYPE_PLAYLIST, SEARCH_TYPE_SONG
from .providers import MusicAggregator, ProviderError, parse_sources

MusicDLClient = MusicAggregator


def normalize_search_type(value: str) -> str:
    value = (value or "").strip().lower()
    if value in {"playlist", "pl", "歌单", "搜歌单"}:
        return SEARCH_TYPE_PLAYLIST
    if value in {"album", "al", "专辑", "搜专辑"}:
        return SEARCH_TYPE_ALBUM
    return SEARCH_TYPE_SONG
