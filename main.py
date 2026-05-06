from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import File, Image, Node, Nodes, Plain, Record
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
    "0.3.0",
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
        self.probe_tasks: set[asyncio.Task] = set()

    async def initialize(self):
        logger.info("[MusicDL] 插件已加载，纯 Python Provider 模式启用")

    async def terminate(self):
        for task in list(self.probe_tasks):
            task.cancel()
        self.probe_tasks.clear()
        self.sessions.clear()

    @filter.command("music", alias={"musicdl", "点歌", "搜歌"})
    async def music_search(self, event: AstrMessageEvent):
        text = self._strip_command(event.message_str, {"music", "musicdl", "点歌", "搜歌"})
        if not text:
            yield event.plain_result(self._help_text())
            return

        keyword, sources, search_type, page, page_size = self._parse_search_options(text)
        if not keyword:
            yield event.plain_result(self._help_text())
            return

        limit = self._loaded_limit(page, page_size)
        await event.send(MessageChain([Plain(f"正在搜索{self._search_type_label(search_type)}: {keyword}")]))
        try:
            search_started_at = time.perf_counter()
            results = await self.music.search(keyword, search_type, sources, limit=limit, probe=False)
            search_elapsed = time.perf_counter() - search_started_at
        except Exception as exc:
            logger.warning(f"[MusicDL] 搜索失败: {exc}")
            yield event.plain_result(f"搜索失败: {exc}")
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
            state = SelectionState(keyword=keyword, search_type=search_type, sources=sources, songs=songs, page=page, page_size=page_size, reloadable=True, created_at=time.time())
            self.sessions[event.unified_msg_origin] = state
            prefix = f"搜索完成，用时 {search_elapsed:.1f}s。已先返回搜索结果，后台正在补充大小/码率/可用性；现在可以直接回复编号下载。"
            await self._send_search_list_forward(event, self._format_song_list(keyword, songs, page=page, page_size=page_size, prefix=prefix, sources=sources, search_type=search_type))
            self._schedule_probe_update(event, event.unified_msg_origin, state)
            return

        collections = [item for item in results if isinstance(item, Collection)]
        if not collections:
            yield event.plain_result(f"没有找到可用{self._search_type_label(search_type)}。")
            return
        self.sessions[event.unified_msg_origin] = SelectionState(keyword=keyword, search_type=search_type, sources=sources, collections=collections, page=page, page_size=page_size, reloadable=True, created_at=time.time())
        prefix = f"搜索完成，用时 {search_elapsed:.1f}s。可回复编号展开，歌曲展开后会先返回结果再后台探测。"
        await self._send_search_list_forward(event, self._format_collection_list(keyword, collections, page=page, page_size=page_size, prefix=prefix, sources=sources, search_type=search_type))

    @filter.regex(r".*https?://[^\s]+.*")
    async def music_direct_link(self, event: AstrMessageEvent):
        raw = event.message_str or ""
        if self._strip_command(raw, {"music", "musicdl", "点歌", "搜歌"}) != raw.strip():
            return
        link = self._extract_supported_music_link(raw)
        if not link:
            return
        event.stop_event()
        await event.send(MessageChain([Plain(f"正在解析链接点歌: {link}")]))
        try:
            results = await self.music.search(link, SEARCH_TYPE_SONG, None, limit=1, probe=False)
        except Exception as exc:
            logger.warning(f"[MusicDL] 链接解析失败: {exc}")
            yield event.plain_result(f"链接解析失败: {exc}")
            return
        songs = [item for item in results if isinstance(item, Song)]
        if not songs:
            yield event.plain_result("暂不支持该链接的点歌解析。")
            return
        async for result in self._download_and_send(event, [songs[0]]):
            yield result

    @filter.command("music_help", alias={"点歌帮助", "搜歌帮助"})
    async def music_help(self, event: AstrMessageEvent):
        yield event.plain_result(self._help_text())

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

    @filter.regex(r"^\s*(?:(?:\d+(?:\s*[-~～—–至到]\s*\d+)?)(?:[\s,，;；]+(?:\d+(?:\s*[-~～—–至到]\s*\d+)?))*|a|all|全部|取消|q|r\s*\d+|换源\s*\d+|n|next|下一页|下页|p|prev|previous|上一页|上页|page\s*\d+|第\s*\d+\s*页)\s*$")
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

        page = self._parse_page_command(text, state)
        if page is not None:
            if page < 1:
                yield event.plain_result("已经是第一页。")
                return
            try:
                old_song_count = len(state.songs)
                message = await self._format_state_page(state, page)
            except Exception as exc:
                logger.warning(f"[MusicDL] 翻页失败: {exc}")
                yield event.plain_result(f"翻页失败: {exc}")
                return
            if state.search_type == SEARCH_TYPE_SONG and len(state.songs) > old_song_count:
                self._schedule_probe_update(event, event.unified_msg_origin, state)
            yield event.plain_result(message)
            return

        if state.collections:
            indices = self._parse_indices(text, len(state.collections))
            if not indices:
                yield event.plain_result("请选择有效编号，例如：1、1-3、1,3,5 或 1 2。")
                return
            selected = [state.collections[i - 1] for i in indices]
            await event.send(MessageChain([Plain(f"正在展开 {len(selected)} 个{selected[0].label}...")]))
            songs: list[Song] = []
            errors: list[str] = []
            for collection in selected:
                try:
                    songs.extend(await self.music.get_collection_songs(collection, probe=False))
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
            expanded_state = SelectionState(keyword=keyword, search_type=SEARCH_TYPE_SONG, sources=state.sources, songs=songs, page=1, page_size=state.page_size, reloadable=False, created_at=time.time())
            self.sessions[event.unified_msg_origin] = expanded_state
            prefix = f"已展开 {len(songs)} 首歌。后台正在补充大小/码率/可用性；现在可以直接回复编号下载。"
            if errors:
                prefix += f"部分失败 {len(errors)} 个。"
            await self._send_search_list_forward(event, self._format_song_list(keyword, songs, page=1, page_size=state.page_size, prefix=prefix, sources=state.sources, search_type=SEARCH_TYPE_SONG))
            self._schedule_probe_update(event, event.unified_msg_origin, expanded_state)
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
                yield event.plain_result(f"换源失败: {exc}")
                return
            yield event.plain_result(self._format_song_list(state.keyword, state.songs, page=state.page, page_size=state.page_size, prefix="换源完成。", sources=state.sources, search_type=state.search_type))
            return

        indices = self._parse_indices(text, len(state.songs))
        if not indices:
            yield event.plain_result("请选择有效编号，例如：1、1-3、1,3,5 或 1 2。")
            return

        selected = [state.songs[i - 1] for i in indices]
        self.sessions.pop(event.unified_msg_origin, None)
        async for result in self._download_and_send(event, selected):
            yield result

    async def _send_search_list_forward(self, event: AstrMessageEvent, text: str) -> None:
        get_self_id = getattr(event, "get_self_id", None)
        self_id = str(get_self_id() if callable(get_self_id) else "0")
        try:
            await event.send(MessageChain([Nodes([Node(uin=self_id, name="MusicDL", content=[Plain(text)])])]))
        except Exception as exc:
            logger.warning(f"[MusicDL] 合并转发搜索结果失败: {exc}")
            await event.send(MessageChain([Plain(text)]))

    def _schedule_probe_update(self, event: AstrMessageEvent, origin: str, state: SelectionState) -> None:
        task = asyncio.create_task(self._probe_and_send_update(event, origin, state))
        self.probe_tasks.add(task)
        task.add_done_callback(self.probe_tasks.discard)

    async def _probe_and_send_update(self, event: AstrMessageEvent, origin: str, state: SelectionState) -> None:
        if not state.songs:
            return
        started_at = time.perf_counter()
        try:
            await self.music.probe_songs(state.songs)
            if self.sessions.get(origin) is not state:
                return
            state.created_at = time.time()
            prefix = f"探针完成，用时 {time.perf_counter() - started_at:.1f}s。已补充歌曲可用性、大小和码率；编号与上一条列表保持一致。"
            await self._send_search_list_forward(event, self._format_song_list(state.keyword, state.songs, page=state.page, page_size=state.page_size, prefix=prefix, sources=state.sources, search_type=state.search_type))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[MusicDL] 后台探针失败: {exc}")

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
        sent = False
        if self.send_mode in {"record", "both"}:
            try:
                await event.send(MessageChain([Record.fromFileSystem(str(downloaded.path))]))
                sent = True
            except Exception as exc:
                logger.warning(f"[MusicDL] 语音发送失败: {song.title}: {exc}")
        if self.send_mode in {"file", "both"} or (self.send_mode == "record" and not sent):
            sent = await self._send_downloaded_file(event, song, downloaded) or sent
        return sent

    async def _send_downloaded_file(self, event: AstrMessageEvent, song: Song, downloaded) -> bool:
        file_url = str(getattr(downloaded, "url", "") or getattr(song, "url", "") or "").strip()
        use_file_url = str(getattr(song, "source", "") or "").strip().lower() != "soda"
        if use_file_url and file_url.startswith(("http://", "https://")):
            try:
                await event.send(MessageChain([File(name=downloaded.filename, url=file_url)]))
                return True
            except Exception as exc:
                logger.warning(f"[MusicDL] 文件 URL 发送失败，尝试本地文件: {song.title}: {exc}")
        try:
            await event.send(MessageChain([File(name=downloaded.filename, file=str(downloaded.path))]))
            return True
        except Exception as exc:
            logger.warning(f"[MusicDL] 文件发送失败: {song.title}: {exc}")
            return False

    async def _send_song_info_forward(self, event: AstrMessageEvent, results: list[dict]) -> None:
        if not results:
            return
        get_self_id = getattr(event, "get_self_id", None)
        self_id = str(get_self_id() if callable(get_self_id) else "0")
        try:
            nodes = [Node(uin=self_id, name="MusicDL", content=self._song_detail_components(item["song"], item.get("downloaded"), item.get("error"))) for item in results]
            await event.send(MessageChain([Nodes(nodes)]))
        except Exception as exc:
            logger.warning(f"[MusicDL] 合并转发歌曲信息失败: {exc}")
            details = [self._format_song_detail(item["song"], item.get("downloaded"), item.get("error"), include_cover_link=True) for item in results]
            await event.send(MessageChain([Plain("歌曲信息:\n\n" + "\n\n".join(details))]))

    def _song_detail_components(self, song: Song, downloaded=None, error: Exception | None = None) -> list:
        content = [Plain(self._format_song_detail(song, downloaded, error))]
        cover = str(getattr(song, "cover", "") or "").strip()
        if cover.startswith(("http://", "https://")):
            content.append(Image.fromURL(cover))
        return content

    def _song_send_caption(self, song: Song) -> str:
        return "\n".join([
            song.title,
            f"来源: {song.source or '-'}",
            f"大小: {self._format_size(song.size)}    码率: {song.bitrate} kbps" if song.bitrate else f"大小: {self._format_size(song.size)}",
        ])

    def _format_song_detail(self, song: Song, downloaded=None, error: Exception | None = None, include_cover_link: bool = False) -> str:
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
        if include_cover_link and song.cover:
            lines.append(f"封面: {song.cover}")
        if downloaded:
            lines.append(f"文件: {downloaded.filename}")
        return "\n".join(lines)

    def _format_song_list(self, keyword: str, songs: list[Song], page: int = 1, page_size: int | None = None, prefix: str = "", sources: list[str] | None = None, search_type: str = SEARCH_TYPE_SONG) -> str:
        page_size = page_size or self.music.page_size
        start, end = self._page_bounds(page, page_size, len(songs))
        visible = songs[start:end]
        total_page = self._page_total(len(songs), page_size)
        lines = []
        if prefix:
            lines.extend([prefix, ""])
        lines.append(f'## 🎵 搜歌结果：{keyword}')
        lines.append(f'> 本页 `{len(visible)}` 首 · 已加载 `{len(songs)}` 首 · 第 `{page}/{total_page}` 页 · 每页 `{page_size}` 条')
        display_sources = self._source_display_sources(sources, search_type, visible, songs)
        if display_sources:
            lines.append('> 渠道：' + " ".join(f"`{source}`" for source in display_sources))
        pending_count = sum(1 for item in visible if not getattr(item, "probed", False))
        invalid_count = sum(1 for item in visible if getattr(item, "probed", False) and item.is_invalid)
        valid_count = sum(1 for item in visible if getattr(item, "probed", False) and not item.is_invalid)
        lines.append(f'> 状态：✅ `{valid_count}` · ❌ `{invalid_count}` · ⏳ `{pending_count}`')
        lines.extend(["", "---", ""])
        if not visible:
            lines.append('没有更多结果。')
        last_offset = start + len(visible)
        for offset, song in enumerate(visible, start + 1):
            title = self._truncate_text(song.name or "Unknown", 36)
            artist = self._truncate_text(song.artist or '未知歌手', 24)
            album = self._truncate_text(song.album or "-", 28)
            duration = self._format_duration(song.duration) or "-"
            size = self._format_size(song.size)
            bitrate = f"{song.bitrate} kbps" if song.bitrate else "-"
            lines.append(f"- `{offset}` {self._song_status(song)} · `{song.source or '-'}` · **{title}**")
            lines.append(f'  - 歌手：{artist} · 专辑：{album} · 时长：{duration}')
            lines.append(f'  - 大小：`{size}` · 码率：`{bitrate}`')
            if offset != last_offset:
                lines.append("")
        lines.extend([
            "",
            "---",
            "",
            '**操作**',
            '- `1` 下载第 1 首；`1-3`、`1,3,5` 或 `1 2` 批量下载。',
            '- `a` / `all` / `全部` 下载当前已加载全部歌曲。',
            '- `n` / `下一页`、`p` / `上一页`、`page 2` / `第 2 页` 翻页。',
            '- `r1` / `换源1` 给第 1 首歌换源。',
            '- `取消` 或 `/music_cancel` 结束。',
        ])
        return "\n".join(lines)
    def _format_collection_list(self, keyword: str, collections: list[Collection], page: int = 1, page_size: int | None = None, prefix: str = "", sources: list[str] | None = None, search_type: str = SEARCH_TYPE_PLAYLIST) -> str:
        page_size = page_size or self.music.page_size
        start, end = self._page_bounds(page, page_size, len(collections))
        visible = collections[start:end]
        label = collections[0].label if collections else "集合"
        kind = collections[0].kind if collections else SEARCH_TYPE_PLAYLIST
        total_page = self._page_total(len(collections), page_size)
        lines = []
        if prefix:
            lines.extend([prefix, ""])
        lines.append(f"**找到 {len(visible)} 个{label}**：{keyword}")
        lines.append(f"**分页**：第 `{page}/{total_page}` 页 · 每页 `{page_size}` 条 · 已加载 `{len(collections)}` 条")
        display_sources = self._source_display_sources(sources, search_type, visible, collections)
        if display_sources:
            lines.append("**搜索渠道**：" + ", ".join(f"`{source}`" for source in display_sources))
        rows = []
        for offset, collection in enumerate(visible, start + 1):
            rows.append([
                str(offset),
                self._truncate_text(collection.name or collection.id, 40),
                str(collection.track_count) if collection.track_count else "-",
                self._truncate_text(collection.creator or "-", 20),
                collection.source or "-",
            ])
        headers = ["ID", f"{label}名称", self._collection_count_label(kind), self._collection_creator_label(kind), "渠道"]
        lines.append("")
        lines.extend(self._format_markdown_table(headers, rows))
        lines.extend([
            "",
            "**操作**",
            "- 回复 `1` 展开第 1 个结果；回复 `1-3`、`1,3,5` 批量展开。",
            "- 回复 `n` / `下一页`、`p` / `上一页`、`page 2` / `第 2 页` 翻页。",
            "- 回复 `取消` 或 `/music_cancel` 结束。",
        ])
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
            '# MusicDL 点歌',
            "",
            '> 先快速返回搜索结果，后台再补充大小 / 码率 / 可用性。',
            "",
            '## 搜索',
            '- `/music 周杰伦`：搜索单曲。',
            '- `/music -s qq,kuwo 稻香`：指定渠道搜索。',
            '- `/music -t playlist 周杰伦`：搜索歌单。',
            '- `/music -t album 范特西`：搜索专辑。',
            '- `/music -p 2 -ps 20 周杰伦`：直接打开第 2 页，每页 20 条。',
            '- `/music https://y.qq.com/n/ryqq/songDetail/xxxx`：链接点歌。',
            "",
            '## 回复操作',
            '- `1`：下载第 1 首 / 展开第 1 个歌单或专辑。',
            '- `1-3`、`1,3,5`、`1 2`：批量下载或展开。',
            '- `a` / `all` / `全部`：下载已加载的全部歌曲。',
            '- `n` / `p` / `page 2`：下一页 / 上一页 / 跳页。',
            '- `r1` / `换源1`：给第 1 首歌换源。',
            '- `取消`：结束当前选择会话。',
            "",
            '## 性能与状态',
            '- 搜索和翻页会优先返回已完成且够当前页展示的渠道，慢源不会继续阻塞。',
            '- `⏳ 待探测`：首屏先返回，后台探针后会补发更新列表。',
            '- `probeConcurrency`：后台探针并发数。',
            "",
            '## 其他',
            '- `/music_sources`：查看渠道能力。',
            '- `sendMode=record|file|both`：语音 / 文件 / 两者都发。',
            '- `forwardSongInfo`：下载后是否发送歌曲详情合并转发。',
            '- 项目：https://github.com/guohuiyuan/go-music-dl',
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

    def _parse_search_options(self, text: str) -> tuple[str, list[str] | None, str, int, int]:
        text = text.strip()
        sources = None
        search_type = SEARCH_TYPE_SONG
        page = 1
        page_size = self.music.page_size
        page_size_match = re.search(r"(?:^|\s)(?:-ps|--page-size|--pagesize|pagesize|size|每页)\s*=?\s*(\d+)", text, re.IGNORECASE)
        if page_size_match:
            page_size = self._normalize_int(page_size_match.group(1), self.music.page_size, 1, 100)
            text = (text[: page_size_match.start()] + " " + text[page_size_match.end() :]).strip()
        page_match = re.search(r"(?:^|\s)(?:-p|--page|page|页)\s*=?\s*(\d+)", text, re.IGNORECASE)
        if page_match:
            page = self._normalize_int(page_match.group(1), 1, 1, 999)
            text = (text[: page_match.start()] + " " + text[page_match.end() :]).strip()
        source_match = re.search(r"(?:^|\s)-s\s+([a-zA-Z0-9_,，-]+)", text)
        if source_match:
            sources = parse_sources(source_match.group(1).replace("，", ","))
            text = (text[: source_match.start()] + " " + text[source_match.end() :]).strip()
        type_match = re.search(r"(?:^|\s)-(?:t|type)\s+([^\s]+)", text)
        if type_match:
            search_type = self._normalize_search_type(type_match.group(1))
            text = (text[: type_match.start()] + " " + text[type_match.end() :]).strip()
        return text, sources, search_type, page, page_size

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
        raw = text.strip().lower()
        if raw in {"a", "all", "全部"}:
            return list(range(1, total + 1))
        normalized = re.sub(r"(\d+)\s*[-~～—–至到]\s*(\d+)", r"\1-\2", raw)
        result = []
        for part in re.split(r"[\s,，;；]+", normalized):
            match = re.fullmatch(r"(\d+)(?:-(\d+))?", part.strip())
            if not match:
                continue
            start = int(match.group(1))
            end = int(match.group(2) or start)
            step = 1 if start <= end else -1
            for idx in range(start, end + step, step):
                if 1 <= idx <= total and idx not in result:
                    result.append(idx)
        return result

    def _extract_first_index(self, text: str) -> int | None:
        match = re.search(r"\d+", text)
        return int(match.group(0)) if match else None

    def _parse_page_command(self, text: str, state: SelectionState) -> int | None:
        raw = text.strip().lower()
        if raw in {"n", "next", "下一页", "下页"}:
            return state.page + 1
        if raw in {"p", "prev", "previous", "上一页", "上页"}:
            return state.page - 1
        match = re.fullmatch(r"(?:page\s*|第\s*)(\d+)\s*页?", raw)
        return int(match.group(1)) if match else None

    async def _format_state_page(self, state: SelectionState, page: int) -> str:
        page_size = state.page_size or self.music.page_size
        loaded = len(state.collections) if state.collections else len(state.songs)
        if page < 1:
            page = 1
        if page > self._page_total(loaded, page_size) and state.reloadable:
            results = await self.music.search(state.keyword, state.search_type, state.sources, limit=self._loaded_limit(page, page_size), probe=False)
            if state.search_type == SEARCH_TYPE_SONG:
                state.songs = [item for item in results if isinstance(item, Song)]
                state.collections = []
            else:
                state.collections = [item for item in results if isinstance(item, Collection)]
                state.songs = []
            loaded = len(state.collections) if state.collections else len(state.songs)
        if (page - 1) * page_size >= loaded:
            return "没有更多结果。"
        state.page = page
        state.created_at = time.time()
        if state.collections:
            return self._format_collection_list(state.keyword, state.collections, page=page, page_size=page_size, sources=state.sources, search_type=state.search_type)
        return self._format_song_list(state.keyword, state.songs, page=page, page_size=page_size, sources=state.sources, search_type=state.search_type)

    def _loaded_limit(self, page: int, page_size: int) -> int:
        return max(1, page) * max(1, page_size)

    def _page_bounds(self, page: int, page_size: int, total: int) -> tuple[int, int]:
        start = max(0, (max(1, page) - 1) * max(1, page_size))
        end = min(total, start + max(1, page_size))
        return start, end

    def _page_total(self, total: int, page_size: int) -> int:
        if total <= 0:
            return 1
        return max(1, (total + max(1, page_size) - 1) // max(1, page_size))

    def _source_display_sources(self, sources: list[str] | None, search_type: str, visible: list[Song] | list[Collection], all_items: list[Song] | list[Collection]) -> list[str]:
        if sources:
            return sources
        if search_type == SEARCH_TYPE_SONG:
            return DEFAULT_SOURCE_NAMES[:]
        return self._page_sources(visible) or self._page_sources(all_items)

    def _page_sources(self, items: list[Song] | list[Collection]) -> list[str]:
        return sorted({item.source for item in items if getattr(item, "source", "")})

    def _song_status(self, song: Song) -> str:
        if not getattr(song, "probed", False):
            return "⏳ 待探测"
        if not song.is_invalid:
            return "✅ 有效"
        labels = {
            "restricted": "❌ 受限",
            "network": "❌ 网络",
            "http": "❌ HTTP",
            "unsupported": "❌ 不支持",
            "download_url": "❌ 下载失败",
        }
        label = labels.get(getattr(song, "invalid_type", ""), "❌ 无效")
        reason = getattr(song, "invalid_reason", "") or ""
        if reason:
            return f"{label}：{self._truncate_text(reason, 16)}"
        return label

    def _extract_supported_music_link(self, text: str) -> str:
        for match in re.finditer(r"https?://[^\s]+", text or ""):
            link = match.group(0).rstrip(',.!?)]"\'' + '，。！？）】》')
            if self.music.supports_link(link):
                return link
        return ""

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
