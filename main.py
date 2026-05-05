from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.star import Context, Star, register

from .musicdl.models import Collection, SEARCH_TYPE_ALBUM, SEARCH_TYPE_PLAYLIST, SEARCH_TYPE_SONG, SelectionState, Song
from .musicdl.providers import DEFAULT_SOURCE_NAMES, MusicAggregator, parse_sources, source_description


SEARCH_TYPE_ALIASES = {
    "song": SEARCH_TYPE_SONG,
    "songs": SEARCH_TYPE_SONG,
    "music": SEARCH_TYPE_SONG,
    "track": SEARCH_TYPE_SONG,
    "\u6b4c\u66f2": SEARCH_TYPE_SONG,
    "\u5355\u66f2": SEARCH_TYPE_SONG,
    "playlist": SEARCH_TYPE_PLAYLIST,
    "playlists": SEARCH_TYPE_PLAYLIST,
    "list": SEARCH_TYPE_PLAYLIST,
    "\u6b4c\u5355": SEARCH_TYPE_PLAYLIST,
    "album": SEARCH_TYPE_ALBUM,
    "albums": SEARCH_TYPE_ALBUM,
    "\u4e13\u8f91": SEARCH_TYPE_ALBUM,
}


@register(
    "astrbot_plugin_musicdl",
    "guohuiyuan",
    "\u7eaf Python \u805a\u5408\u97f3\u4e50\u641c\u7d22/\u4e0b\u8f7d/\u70b9\u6b4c\u63d2\u4ef6\u3002",
    "0.2.0",
)
class MusicDLPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.context = context
        self.config = config or {}
        download_dir = self._resolve_download_dir(self.config.get("downloadDir", "data/downloads"))
        self.music = MusicAggregator(self.config, download_dir)
        self.sessions: dict[str, SelectionState] = {}
        self.session_timeout = 120
        self.download_to_local = self._as_bool(self.config.get("downloadToLocal"), False)
        self.download_concurrency = self._normalize_int(self.config.get("downloadConcurrency"), 3, 1, 5)

    async def initialize(self):
        logger.info("[MusicDL] \u63d2\u4ef6\u5df2\u52a0\u8f7d\uff0c\u7eaf Python Provider \u6a21\u5f0f\u542f\u7528")

    async def terminate(self):
        self.sessions.clear()

    @filter.command("music", alias={"musicdl", "\u70b9\u6b4c", "\u641c\u6b4c"})
    async def music_search(self, event: AstrMessageEvent):
        text = self._strip_command(event.message_str, {"music", "musicdl", "\u70b9\u6b4c", "\u641c\u6b4c"})
        if not text:
            yield event.plain_result(self._help_text())
            return

        keyword, sources, search_type = self._parse_search_options(text)
        if not keyword:
            yield event.plain_result(self._help_text())
            return

        await event.send(MessageChain([Plain(f"\u6b63\u5728\u641c\u7d22{self._search_type_label(search_type)}\uff1a{keyword}")]))
        try:
            results = await self.music.search(keyword, search_type, sources)
        except Exception as exc:
            logger.warning(f"[MusicDL] \u641c\u7d22\u5931\u8d25: {exc}")
            yield event.plain_result(f"\u641c\u7d22\u5931\u8d25\uff1a{exc}")
            return

        if search_type == SEARCH_TYPE_SONG:
            songs = [item for item in results if isinstance(item, Song)]
            if not songs:
                yield event.plain_result("\u6ca1\u6709\u627e\u5230\u53ef\u7528\u6b4c\u66f2\u3002")
                return
            if keyword.startswith(("http://", "https://")) and len(songs) == 1:
                async for result in self._download_and_send(event, [songs[0]]):
                    yield result
                return
            self.sessions[event.unified_msg_origin] = SelectionState(keyword=keyword, search_type=search_type, songs=songs, created_at=time.time())
            yield event.plain_result(self._format_song_list(keyword, songs))
            return

        collections = [item for item in results if isinstance(item, Collection)]
        if not collections:
            yield event.plain_result(f"\u6ca1\u6709\u627e\u5230\u53ef\u7528{self._search_type_label(search_type)}\u3002")
            return
        self.sessions[event.unified_msg_origin] = SelectionState(keyword=keyword, search_type=search_type, collections=collections, created_at=time.time())
        yield event.plain_result(self._format_collection_list(keyword, collections))

    @filter.command("music_sources", alias={"\u70b9\u6b4c\u6e90"})
    async def music_sources(self, event: AstrMessageEvent):
        lines = ["\u652f\u6301\u6765\u6e90\uff1a"]
        capabilities = self.music.source_capabilities()
        for name in self.music.providers.keys():
            caps = capabilities.get(name, {})
            tags = []
            if caps.get("default"):
                tags.append("\u9ed8\u8ba4")
            if caps.get("song"):
                tags.append("\u5355\u66f2")
            if caps.get("playlist"):
                tags.append("\u6b4c\u5355")
            if caps.get("album"):
                tags.append("\u4e13\u8f91")
            lines.append(f"- {name}({source_description(name)})\uff1a{', '.join(tags) if tags else '-'}")
        lines.append("\n\u9ed8\u8ba4\u6765\u6e90\uff1a" + ", ".join(DEFAULT_SOURCE_NAMES))
        yield event.plain_result("\n".join(lines))

    @filter.command("music_cancel", alias={"\u53d6\u6d88\u70b9\u6b4c"})
    async def music_cancel(self, event: AstrMessageEvent):
        self.sessions.pop(event.unified_msg_origin, None)
        yield event.plain_result("\u5df2\u53d6\u6d88\u5f53\u524d\u70b9\u6b4c\u9009\u62e9\u3002")

    @filter.regex(r"^\s*(?:\d+(?:[\s,\uff0c]+\d+)*|a|all|\u5168\u90e8|\u53d6\u6d88|q|r\s*\d+|\u6362\u6e90\s*\d+)\s*$")
    async def music_selection(self, event: AstrMessageEvent):
        self._cleanup_sessions()
        state = self.sessions.get(event.unified_msg_origin)
        if not state:
            return

        event.stop_event()
        text = event.message_str.strip().lower()
        if text in {"\u53d6\u6d88", "q"}:
            self.sessions.pop(event.unified_msg_origin, None)
            yield event.plain_result("\u5df2\u53d6\u6d88\u5f53\u524d\u70b9\u6b4c\u9009\u62e9\u3002")
            return

        if state.collections:
            indices = self._parse_indices(text, len(state.collections))
            if not indices:
                yield event.plain_result("\u8bf7\u9009\u62e9\u6709\u6548\u7f16\u53f7\uff0c\u4f8b\u5982\uff1a1 \u6216 1 2\u3002")
                return
            selected = [state.collections[i - 1] for i in indices]
            await event.send(MessageChain([Plain(f"\u6b63\u5728\u5c55\u5f00 {len(selected)} \u4e2a{selected[0].label}...")]))
            songs: list[Song] = []
            errors: list[str] = []
            for collection in selected:
                try:
                    songs.extend(await self.music.get_collection_songs(collection))
                except Exception as exc:
                    logger.warning(f"[MusicDL] \u5c55\u5f00\u5931\u8d25: {collection.source}/{collection.id}: {exc}")
                    errors.append(f"{collection.name}: {exc}")
            if not songs:
                self.sessions.pop(event.unified_msg_origin, None)
                message = "\u5c55\u5f00\u5931\u8d25\uff0c\u672a\u83b7\u53d6\u5230\u53ef\u7528\u6b4c\u66f2\u3002"
                if errors:
                    message += "\n" + "\n".join(errors[:5])
                yield event.plain_result(message)
                return
            keyword = "\uff0c".join(collection.name for collection in selected if collection.name) or state.keyword
            self.sessions[event.unified_msg_origin] = SelectionState(keyword=keyword, search_type=SEARCH_TYPE_SONG, songs=songs, created_at=time.time())
            prefix = f"\u5df2\u5c55\u5f00 {len(songs)} \u9996\u6b4c\u3002"
            if errors:
                prefix += f"\u90e8\u5206\u5931\u8d25 {len(errors)} \u4e2a\u3002"
            yield event.plain_result(self._format_song_list(keyword, songs, prefix=prefix))
            return

        if text.startswith("r") or text.startswith("\u6362\u6e90"):
            idx = self._extract_first_index(text)
            if idx is None or idx < 1 or idx > len(state.songs):
                yield event.plain_result("\u6362\u6e90\u7f16\u53f7\u65e0\u6548\u3002")
                return
            await event.send(MessageChain([Plain("\u6b63\u5728\u6362\u6e90...")]))
            try:
                new_song = await self.music.switch_source(state.songs[idx - 1])
                state.songs[idx - 1] = new_song
            except Exception as exc:
                yield event.plain_result(f"\u6362\u6e90\u5931\u8d25\uff1a{exc}")
                return
            yield event.plain_result(self._format_song_list(state.keyword, state.songs, prefix="\u6362\u6e90\u5b8c\u6210\u3002"))
            return

        indices = self._parse_indices(text, len(state.songs))
        if not indices:
            yield event.plain_result("\u8bf7\u9009\u62e9\u6709\u6548\u7f16\u53f7\uff0c\u4f8b\u5982\uff1a1 \u6216 1 2\u3002")
            return

        selected = [state.songs[i - 1] for i in indices]
        self.sessions.pop(event.unified_msg_origin, None)
        async for result in self._download_and_send(event, selected):
            yield result

    async def _download_and_send(self, event: AstrMessageEvent, songs: list[Song]):
        await event.send(MessageChain([Plain(f"\u5f00\u59cb\u4e0b\u8f7d {len(songs)} \u9996\u6b4c\u66f2...")]))
        semaphore = asyncio.Semaphore(self.download_concurrency)

        async def download_one(song: Song):
            async with semaphore:
                try:
                    downloaded = await self.music.download_song(song)
                    return song, downloaded, None
                except Exception as exc:
                    return song, None, exc

        tasks = [asyncio.create_task(download_one(song)) for song in songs]
        for task in asyncio.as_completed(tasks):
            song, downloaded, exc = await task
            if exc is not None:
                logger.warning(f"[MusicDL] \u4e0b\u8f7d\u5931\u8d25: {song.title}: {exc}")
                yield event.plain_result(f"\u4e0b\u8f7d\u5931\u8d25\uff1a{song.title}\n\u539f\u56e0\uff1a{exc}")
                continue
            yield event.chain_result([Plain(f"\U0001f3b5 {song.title}\n\u6765\u6e90\uff1a{song.source}"), Record.fromFileSystem(str(downloaded.path))])
            if not self.download_to_local:
                self._cleanup_downloaded_file(downloaded.path)

    def _format_song_list(self, keyword: str, songs: list[Song], prefix: str = "") -> str:
        lines = []
        if prefix:
            lines.append(prefix)
        lines.append(f"\u627e\u5230 {len(songs)} \u9996\uff1a{keyword}")
        for i, song in enumerate(songs, 1):
            parts = [f"[{song.source}] {song.title}"]
            if song.album:
                parts.append(song.album)
            duration = self._format_duration(song.duration)
            if duration:
                parts.append(duration)
            if song.bitrate:
                parts.append(f"{song.bitrate}kbps")
            if song.ext:
                parts.append(song.ext)
            lines.append(f"{i}. " + " \u00b7 ".join(parts))
        lines.append("\n\u56de\u590d\u7f16\u53f7\u4e0b\u8f7d\uff0c\u4f8b\u5982\uff1a1\u3002\u56de\u590d 1 2 \u53ef\u6279\u91cf\u4e0b\u8f7d\u3002\u56de\u590d r1 \u53ef\u7ed9\u7b2c 1 \u9996\u6362\u6e90\u3002\u56de\u590d \u53d6\u6d88 \u7ed3\u675f\u3002")
        return "\n".join(lines)

    def _format_collection_list(self, keyword: str, collections: list[Collection]) -> str:
        label = collections[0].label if collections else "\u96c6\u5408"
        lines = [f"\u627e\u5230 {len(collections)} \u4e2a{label}\uff1a{keyword}"]
        for i, collection in enumerate(collections, 1):
            parts = [f"[{collection.source}] {collection.name or collection.id}"]
            if collection.creator:
                parts.append(collection.creator)
            if collection.track_count:
                parts.append(f"{collection.track_count} \u9996")
            if collection.play_count:
                parts.append(f"\u64ad\u653e {collection.play_count}")
            lines.append(f"{i}. " + " \u00b7 ".join(parts))
        lines.append(f"\n\u56de\u590d\u7f16\u53f7\u5c55\u5f00{label}\u6b4c\u66f2\uff0c\u4f8b\u5982\uff1a1\u3002\u56de\u590d \u53d6\u6d88 \u7ed3\u675f\u3002")
        return "\n".join(lines)

    def _help_text(self) -> str:
        return "\n".join([
            "MusicDL \u70b9\u6b4c\u7528\u6cd5\uff1a",
            "/music \u6b4c\u540d\u6216\u6b4c\u624b",
            "/music -s qq,kuwo \u6b4c\u540d",
            "/music -t song \u6b4c\u540d",
            "/music -t playlist \u6b4c\u5355\u540d",
            "/music -t album \u4e13\u8f91\u540d",
            "/music -s all -t album \u5468\u6770\u4f26",
            "/music https://y.qq.com/n/ryqq/songDetail/xxxx",
            "/music_sources \u67e5\u770b\u6765\u6e90\u80fd\u529b",
            "\u641c\u7d22\u540e\u56de\u590d\u7f16\u53f7\u53d1\u9001\u97f3\u9891\u6d88\u606f\u3002",
        ])

    def _strip_command(self, text: str, names: set[str]) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        parts = text.split(maxsplit=1)
        head = parts[0].lstrip("/").lower()
        if head in names:
            return parts[1].strip() if len(parts) > 1 else ""
        return text

    def _parse_search_options(self, text: str) -> tuple[str, list[str] | None, str]:
        text = text.strip()
        sources = None
        search_type = SEARCH_TYPE_SONG
        source_match = re.search(r"(?:^|\s)-s\s+([a-zA-Z0-9_,\uff0c-]+)", text)
        if source_match:
            sources = parse_sources(source_match.group(1).replace("\uff0c", ","))
            text = (text[: source_match.start()] + " " + text[source_match.end() :]).strip()
        type_match = re.search(r"(?:^|\s)-(?:t|type)\s+([^\s]+)", text)
        if type_match:
            search_type = self._normalize_search_type(type_match.group(1))
            text = (text[: type_match.start()] + " " + text[type_match.end() :]).strip()
        return text, sources, search_type

    def _normalize_search_type(self, value: object) -> str:
        raw = str(value or "").strip().lower()
        return SEARCH_TYPE_ALIASES.get(raw, SEARCH_TYPE_SONG)

    def _search_type_label(self, search_type: str) -> str:
        if search_type == SEARCH_TYPE_PLAYLIST:
            return "\u6b4c\u5355"
        if search_type == SEARCH_TYPE_ALBUM:
            return "\u4e13\u8f91"
        return "\u6b4c\u66f2"

    def _parse_indices(self, text: str, total: int) -> list[int]:
        if text in {"a", "all", "\u5168\u90e8"}:
            return list(range(1, total + 1))
        result = []
        for part in re.split(r"[\s,\uff0c]+", text.strip()):
            if not part.isdigit():
                continue
            idx = int(part)
            if 1 <= idx <= total and idx not in result:
                result.append(idx)
        return result

    def _extract_first_index(self, text: str) -> int | None:
        match = re.search(r"\d+", text)
        return int(match.group(0)) if match else None

    def _cleanup_sessions(self):
        now = time.time()
        expired = [key for key, value in self.sessions.items() if now - value.created_at > self.session_timeout]
        for key in expired:
            self.sessions.pop(key, None)

    def _format_duration(self, seconds: int) -> str:
        if seconds <= 0:
            return ""
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _resolve_download_dir(self, value: object) -> Path:
        raw = str(value or "data/downloads").strip() or "data/downloads"
        return Path(raw)

    def _cleanup_downloaded_file(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"[MusicDL] \u6e05\u7406\u4e34\u65f6\u97f3\u9891\u5931\u8d25: {path}: {exc}")

    def _as_bool(self, value: object, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on", "\u662f", "\u5f00\u542f"}

    def _normalize_int(self, value: object, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(str(value or default))
        except ValueError:
            number = default
        return max(minimum, min(maximum, number))
