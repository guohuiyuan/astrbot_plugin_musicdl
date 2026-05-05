from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from .models import Collection, SEARCH_TYPE_ALBUM, SEARCH_TYPE_PLAYLIST, Song


class MusicDLPageParser(HTMLParser):
    def __init__(self, fallback_collection_kind: str = SEARCH_TYPE_PLAYLIST) -> None:
        super().__init__(convert_charrefs=True)
        self.fallback_collection_kind = fallback_collection_kind
        self.songs: list[Song] = []
        self.collections: list[Collection] = []
        self._collection: Collection | None = None
        self._collection_depth = 0
        self._text_target = ""
        self._text_buffer: list[str] = []
        self._creator_seen = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        classes = set(attrs_dict.get("class", "").split())

        if tag == "li" and "song-card" in classes:
            self.songs.append(self._song_from_attrs(attrs_dict))
            return

        if tag == "div" and "playlist-card" in classes:
            collection = self._collection_from_onclick(attrs_dict.get("onclick", ""))
            self._collection = collection
            self._collection_depth = 1
            self._creator_seen = False
            return

        if self._collection is None:
            return

        if tag == "div":
            self._collection_depth += 1
            if "playlist-title" in classes:
                self._begin_text("title")
            elif "playlist-author" in classes and not self._creator_seen:
                self._begin_text("creator")
                self._creator_seen = True
            elif "playlist-count" in classes:
                self._begin_text("count")
        elif tag == "img" and not self._collection.cover:
            src = attrs_dict.get("src", "")
            if src and "placeholder" not in src:
                self._collection.cover = src

    def handle_endtag(self, tag: str) -> None:
        if self._collection is None:
            return

        if tag == "div" and self._text_target:
            text = " ".join("".join(self._text_buffer).split())
            if self._text_target == "title":
                self._collection.name = text
            elif self._text_target == "creator":
                self._collection.creator = re.sub(r"^\s*\S*\s*", "", text).strip() or text
            elif self._text_target == "count":
                match = re.search(r"(\d+)", text)
                if match:
                    self._collection.track_count = int(match.group(1))
            self._text_target = ""
            self._text_buffer = []

        if tag == "div":
            self._collection_depth -= 1
            if self._collection_depth <= 0:
                if self._collection.id and self._collection.source:
                    self.collections.append(self._collection)
                self._collection = None
                self._collection_depth = 0
                self._text_target = ""
                self._text_buffer = []

    def handle_data(self, data: str) -> None:
        if self._text_target:
            self._text_buffer.append(data)

    def _begin_text(self, target: str) -> None:
        self._text_target = target
        self._text_buffer = []

    def _song_from_attrs(self, attrs: dict[str, str]) -> Song:
        return Song(
            id=attrs.get("data-id", "").strip(),
            source=attrs.get("data-source", "").strip(),
            name=attrs.get("data-name", "").strip() or "Unknown",
            artist=attrs.get("data-artist", "").strip() or "Unknown",
            album=attrs.get("data-album", "").strip(),
            cover=attrs.get("data-cover", "").strip(),
            duration=_safe_int(attrs.get("data-duration", "0")),
            extra=_normalize_extra(attrs.get("data-extra", "")),
        )

    def _collection_from_onclick(self, onclick: str) -> Collection:
        detail_url = ""
        match = re.search(r"navigateTo\('([^']+)'\)", onclick)
        if match:
            detail_url = match.group(1)
        parsed = urlparse(detail_url)
        query = parse_qs(parsed.query)
        kind = SEARCH_TYPE_ALBUM if parsed.path.endswith("/album") else self.fallback_collection_kind
        if kind not in (SEARCH_TYPE_ALBUM, SEARCH_TYPE_PLAYLIST):
            kind = SEARCH_TYPE_PLAYLIST
        return Collection(
            id=(query.get("id") or [""])[0].strip(),
            source=(query.get("source") or [""])[0].strip(),
            kind=kind,
        )


def parse_musicdl_page(html: str, collection_kind: str = SEARCH_TYPE_PLAYLIST) -> tuple[list[Song], list[Collection]]:
    parser = MusicDLPageParser(collection_kind)
    parser.feed(html)
    parser.close()
    return parser.songs, parser.collections


def _safe_int(value: str) -> int:
    try:
        return int(str(value).strip() or "0")
    except ValueError:
        return 0


def _normalize_extra(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
