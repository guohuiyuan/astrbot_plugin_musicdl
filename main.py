from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import File, Node, Nodes, Plain, Record
from astrbot.api.star import Context, Star, register

from .musicdl.models import Collection, SEARCH_TYPE_ALBUM, SEARCH_TYPE_PLAYLIST, SEARCH_TYPE_SONG, SelectionState, Song
from .musicdl.providers import DEFAULT_SOURCE_NAMES, MusicAggregator, parse_sources, source_description


SEARCH_TYPE_ALIASES = {
    "song": SEARCH_TYPE_SONG,
    "songs": SEARCH_TYPE_SONG,
    "music": SEARCH_TYPE_SONG,
    "track": SEARCH_TYPE_SONG,
    "歌曲": SEARCH_TYPE_SONG,
    "单曲": SEARCH_TYPE_SONG,
    "playlist": SEARCH_TYPE_PLAYLIST,
    "playlists": SEARCH_TYPE_PLAYLIST,
    "list": SEARCH_TYPE_PLAYLIST,
    "歌单": SEARCH_TYPE_PLAYLIST,
    "album": SEARCH_TYPE_ALBUM,
    "albums": SEARCH_TYPE_ALBUM,
    "专辑": SEARCH_TYPE_ALBUM,
}


@register(
    "astrbot_plugin_musicdl",
    "guohuiyuan",
    "纯 Python 聚合音乐搜索/下载/点歌插件。",
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
        self.send_mode = self._normalize_send_mode(self.config.get("sendMode", "record"))
        self.forward_song_info = self._as_bool(self.config.get("forwardSongInfo"), True)

    async def initialize(self):
        logger.info("[MusicDL] 插件已加载，纯 Python Provider 模式启用")

    async def terminate(self):
        self.sessions.clear()

    @filter.command("music", alias={"musicdl", "点歌", "搜歌"})
    async def music_search(self, event: AstrMessageEvent):
        text = self._strip_command(event.message_str, {"music", "musicdl", "点歌", "搜歌"})
        if not text:
            yield event.plain_result(self._help_text())
            return

        keyword, sources, search_type = self._parse_search_options(text)
        if not keyword:
            yield event.plain_result(self._help_text())
            return

        await event.send(MessageChain([Plain(f"正在搜索{self._search_type_label(search_type)}：{keyword}")]))
        try:
            results = await self.music.search(keyword, search_type, sources)
        except Exception as exc:
            logger.warning(f"[MusicDL] 搜索失败: {exc}")
            yield event.plain_result(f"搜索失败：{exc}")
            return

        if search_type == SEARCH_TYPE_SONG:
            songs = [item for item in results if isinstance(item, Song)]
            if not songs:
                yield event.plain_result("没有找到可用歌曲。")
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
            yield event.plain_result(f"没有找到可用{self._search_type_label(search_type)}。")
            return
        self.sessions[event.unified_msg_origin] = SelectionState(keyword=keyword, search_type=search_type, collections=collections, created_at=time.time())
        yield event.plain_result(self._format_collection_list(keyword, collections))

    @filter.command("music_sources", alias={"点歌源"})
    async def music_sources(self, event: AstrMessageEvent):
        lines = ["支持来源："]
        capabilities = self.music.source_capabilities()
        for name in self.music.providers.keys():
            caps = capabilities.get(name, {})
            tags = []
            if caps.get("default"):
                tags.append("默认")
            if caps.get("song"):
                tags.append("单曲")
            if caps.get("playlist"):
                tags.append("歌单")
            if caps.get("album"):
                tags.append("专辑")
            lines.append(f"- {name}({source_description(name)})：{', '.join(tags) if tags else '-'}")
        lines.append("\n默认来源：" + ", ".join(DEFAULT_SOURCE_NAMES))
        yield event.plain_result("\n".join(lines))

    @filter.command("music_cancel", alias={"取消点歌"})
    async def music_cancel(self, event: AstrMessageEvent):
        self.sessions.pop(event.unified_msg_origin, None)
        yield event.plain_result("已取消当前点歌选择。")

    @filter.regex(r"^\s*(?:\d+(?:[\s,，]+\d+)*|a|all|全部|取消|q|r\s*\d+|换源\s*\d+)\s*$")
    async def music_selection(self, event: AstrMessageEvent):
        self._cleanup_sessions()
        state = self.sessions.get(event.unified_msg_origin)
        if not state:
            return

        event.stop_event()
        text = event.message_str.strip().lower()
        if text in {"取消", "q"}:
            self.sessions.pop(event.unified_msg_origin, None)
            yield event.plain_result("已取消当前点歌选择。")
            return

        if state.collections:
            indices = self._parse_indices(text, len(state.collections))
            if not indices:
                yield event.plain_result("请选择有效编号，例如：1 或 1 2。")
                return
            selected = [state.collections[i - 1] for i in indices]
            await event.send(MessageChain([Plain(f"正在展开 {len(selected)} 个{selected[0].label}...")]))
            songs: list[Song] = []
            errors: list[str] = []
            for collection in selected:
                try:
                    songs.extend(await self.music.get_collection_songs(collection))
                except Exception as exc:
                    logger.warning(f"[MusicDL] 展开失败: {collection.source}/{collection.id}: {exc}")
                    errors.append(f"{collection.name}: {exc}")
            if not songs:
                self.sessions.pop(event.unified_msg_origin, None)
                message = "展开失败，未获取到可用歌曲。"
                if errors:
                    message += "\n" + "\n".join(errors[:5])
                yield event.plain_result(message)
                return
            keyword = "，".join(collection.name for collection in selected if collection.name) or state.keyword
            self.sessions[event.unified_msg_origin] = SelectionState(keyword=keyword, search_type=SEARCH_TYPE_SONG, songs=songs, created_at=time.time())
            prefix = f"已展开 {len(songs)} 首歌。"
            if errors:
                prefix += f"部分失败 {len(errors)} 个。"
            yield event.plain_result(self._format_song_list(keyword, songs, prefix=prefix))
            return

        if text.startswith("r") or text.startswith("换源"):
            idx = self._extract_first_index(text)
            if idx is None or idx < 1 or idx > len(state.songs):
                yield event.plain_result("换源编号无效。")
                return
            await event.send(MessageChain([Plain("正在换源...")]))
            try:
                new_song = await self.music.switch_source(state.songs[idx - 1])
                state.songs[idx - 1] = new_song
            except Exception as exc:
                yield event.plain_result(f"换源失败：{exc}")
                return
            yield event.plain_result(self._format_song_list(state.keyword, state.songs, prefix="换源完成。"))
            return

        indices = self._parse_indices(text, len(state.songs))
        if not indices:
            yield event.plain_result("请选择有效编号，例如：1 或 1 2。")
            return

        selected = [state.songs[i - 1] for i in indices]
        self.sessions.pop(event.unified_msg_origin, None)
        async for result in self._download_and_send(event, selected):
            yield result

    async def _download_and_send(self, event: AstrMessageEvent, songs: list[Song]):
        await event.send(MessageChain([Plain(f"开始下载 {len(songs)} 首歌曲...")]))
        semaphore = asyncio.Semaphore(self.download_concurrency)

        async def download_one(song: Song):
            async with semaphore:
                try:
                    downloaded = await self.music.download_song(song)
                    return {"song": song, "downloaded": downloaded, "error": None}
                except Exception as exc:
                    return {"song": song, "downloaded": None, "error": exc}

        results = []
        tasks = [asyncio.create_task(download_one(song)) for song in songs]
        for task in asyncio.as_completed(tasks):
            item = await task
            results.append(item)
            song = item["song"]
            downloaded = item["downloaded"]
            exc = item["error"]
            if exc is not None:
                logger.warning(f"[MusicDL] 下载失败: {song.title}: {exc}")
                yield event.plain_result(f"下载失败: {song.title}\n原因: {exc}")
                continue
            sent = await self._send_downloaded_song(event, song, downloaded)
            if not sent:
                yield event.plain_result(f"发送失败: {song.title}\n文件已保存: {downloaded.path}")
            if sent and not self.download_to_local:
                self._cleanup_downloaded_file(downloaded.path)
        if self.forward_song_info:
            await self._send_song_info_forward(event, results)

    async def _send_downloaded_song(self, event: AstrMessageEvent, song: Song, downloaded) -> bool:
        caption = self._song_send_caption(song)
        sent = False
        if self.send_mode in {"record", "both"}:
            try:
                await event.send(MessageChain([Plain(caption), Record.fromFileSystem(str(downloaded.path))]))
                sent = True
            except Exception as exc:
                logger.warning(f"[MusicDL] 语音发送失败: {song.title}: {exc}")
        if self.send_mode in {"file", "both"} or (self.send_mode == "record" and not sent):
            try:
                await event.send(MessageChain([Plain(caption), File(name=downloaded.filename, file=str(downloaded.path))]))
                sent = True
            except Exception as exc:
                logger.warning(f"[MusicDL] 文件发送失败: {song.title}: {exc}")
        return sent

    async def _send_song_info_forward(self, event: AstrMessageEvent, results: list[dict]) -> None:
        if not results:
            return
        details = [self._format_song_detail(item["song"], item.get("downloaded"), item.get("error")) for item in results]
        get_self_id = getattr(event, "get_self_id", None)
        self_id = str(get_self_id() if callable(get_self_id) else "0")
        try:
            nodes = [Node(uin=self_id, name="MusicDL", content=[Plain(detail)]) for detail in details]
            await event.send(MessageChain([Nodes(nodes)]))
        except Exception as exc:
            logger.warning(f"[MusicDL] 合并转发歌曲信息失败: {exc}")
            await event.send(MessageChain([Plain("歌曲信息:\n\n" + "\n\n".join(details))]))

    def _song_send_caption(self, song: Song) -> str:
        return "\n".join([
            song.title,
            f"来源: {song.source or '-'}",
            f"大小: {self._format_size(song.size)}    码率: {song.bitrate} kbps" if song.bitrate else f"大小: {self._format_size(song.size)}",
        ])

    def _format_song_detail(self, song: Song, downloaded=None, error: Exception | None = None) -> str:
        ext = song.ext or (downloaded.path.suffix.lower().lstrip(".") if downloaded else "")
        status = "下载成功" if downloaded else (f"下载失败: {error}" if error else "-")
        source_desc = source_description(song.source) if song.source else "-"
        lines = [
            f"歌名: {song.name or 'Unknown'}",
            f"歌手: {song.artist or '未知歌手'}",
            f"专辑: {song.album or '-'}",
            f"来源: {song.source or '-'} ({source_desc})",
            f"ID: {song.id or '-'}",
            f"时长: {self._format_duration(song.duration) or '-'}",
            f"大小: {self._format_size(song.size)}",
            f"码率: {song.bitrate} kbps" if song.bitrate else "码率: -",
            f"格式: {ext or '-'}",
            f"状态: {status}",
        ]
        if song.link:
            lines.append(f"链接: {song.link}")
        if song.cover:
            lines.append(f"封面: {song.cover}")
        if downloaded:
            lines.append(f"文件: {downloaded.filename}")
        return "\n".join(lines)

    def _format_song_list(self, keyword: str, songs: list[Song], prefix: str = "") -> str:
        lines = []
        if prefix:
            lines.append(prefix)
        lines.append(f"找到 {len(songs)} 首歌曲: {keyword}")
        lines.append(f"1/1 页, 每页 {self.music.page_size} 条")
        sources = sorted({song.source for song in songs if song.source})
        if sources:
            lines.append("来源: " + ", ".join(sources))
        rows = []
        for i, song in enumerate(songs, 1):
            rows.append([
                "[ ]",
                str(i),
                self._truncate_text(song.name or "Unknown", 25),
                self._truncate_text(song.artist or "未知歌手", 15),
                self._truncate_text(song.album or "-", 15),
                self._format_duration(song.duration) or "-",
                "!无效" if song.is_invalid else self._format_size(song.size),
                f"{song.bitrate} kbps" if song.bitrate else "-",
                song.source or "-",
            ])
        lines.extend(self._format_markdown_table(["[选]", "ID", "歌名", "歌手", "专辑", "时长", "大小", "码率", "来源"], rows))
        lines.append("\n回复编号下载, 例如: 1. 回复 1 2 可批量下载. 回复 r1 可给第 1 首换源. 回复取消结束.")
        return "\n".join(lines)

    def _format_collection_list(self, keyword: str, collections: list[Collection]) -> str:
        label = collections[0].label if collections else "集合"
        kind = collections[0].kind if collections else SEARCH_TYPE_PLAYLIST
        lines = [f"找到 {len(collections)} {label}: {keyword}"]
        lines.append(f"1/1 页, 每页 {self.music.page_size} 条")
        sources = sorted({collection.source for collection in collections if collection.source})
        if sources:
            lines.append("来源: " + ", ".join(sources))
        rows = []
        for i, collection in enumerate(collections, 1):
            rows.append([
                str(i),
                self._truncate_text(collection.name or collection.id, 40),
                str(collection.track_count) if collection.track_count else "-",
                self._truncate_text(collection.creator or "-", 20),
                collection.source or "-",
            ])
        headers = ["ID", f"{label}名称", self._collection_count_label(kind), self._collection_creator_label(kind), "来源"]
        lines.extend(self._format_markdown_table(headers, rows))
        lines.append(f"\n回复编号展开{label}歌曲, 例如: 1. 回复取消结束.")
        return "\n".join(lines)

    def _truncate_text(self, value: object, limit: int) -> str:
        text = str(value if value is not None else "").strip()
        if len(text) <= limit:
            return text or "-"
        return text[: max(1, limit - 3)] + "..."

    def _format_markdown_table(self, headers: list[str], rows: list[list[object]]) -> list[str]:
        table = ["| " + " | ".join(self._safe_markdown_cell(item) for item in headers) + " |"]
        table.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            table.append("| " + " | ".join(self._safe_markdown_cell(item) for item in row) + " |")
        return table

    def _safe_markdown_cell(self, value: object) -> str:
        text = str(value if value is not None else "-").replace("\n", " " ).replace("|", "\\|").strip()
        return text or "-"

    def _format_size(self, size: int) -> str:
        if size <= 0:
            return "-"
        units = ["B", "KB", "MB", "GB"]
        value = float(size)
        index = 0
        while value >= 1024 and index < len(units) - 1:
            value /= 1024
            index += 1
        if index == 0:
            return f"{int(value)} {units[index]}"
        return f"{value:.1f} {units[index]}"

    def _collection_count_label(self, search_type: str) -> str:
        return '曲目数' if search_type == SEARCH_TYPE_ALBUM else '歌曲数'

    def _collection_creator_label(self, search_type: str) -> str:
        return '歌手' if search_type == SEARCH_TYPE_ALBUM else '创建者'

    def _yes_no(self, value: object) -> str:
        return '是' if bool(value) else '否'

    def _help_text(self) -> str:
        return "\n".join([
            "MusicDL 点歌用法：",
            "/music 歌名或歌手",
            "/music -s qq,kuwo 歌名",
            "/music -t song 歌名",
            "/music -t playlist 歌单名",
            "/music -t album 专辑名",
            "/music -s all -t album 周杰伦",
            "/music https://y.qq.com/n/ryqq/songDetail/xxxx",
            "/music_sources 查看来源能力",
            "搜索后回复编号发送音频消息。",
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
        source_match = re.search(r"(?:^|\s)-s\s+([a-zA-Z0-9_,，-]+)", text)
        if source_match:
            sources = parse_sources(source_match.group(1).replace("，", ","))
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
            return "歌单"
        if search_type == SEARCH_TYPE_ALBUM:
            return "专辑"
        return "歌曲"

    def _parse_indices(self, text: str, total: int) -> list[int]:
        if text in {"a", "all", "全部"}:
            return list(range(1, total + 1))
        result = []
        for part in re.split(r"[\s,，]+", text.strip()):
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
            logger.warning(f"[MusicDL] 清理临时音频失败: {path}: {exc}")

    def _normalize_send_mode(self, value: object) -> str:
        raw = str(value or "record").strip().lower()
        aliases = {"voice": "record", "audio": "record", "file": "file", "both": "both"}
        raw = aliases.get(raw, raw)
        return raw if raw in {"record", "file", "both"} else "record"

    def _as_bool(self, value: object, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on", "是", "开启"}

    def _normalize_int(self, value: object, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(str(value or default))
        except ValueError:
            number = default
        return max(minimum, min(maximum, number))
