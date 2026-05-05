from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

from astrbot.api import logger

from .models import Collection, DownloadedFile, SEARCH_TYPE_ALBUM, SEARCH_TYPE_PLAYLIST, SEARCH_TYPE_SONG, Song
from .extra_providers import (
    ALL_SOURCE_NAMES,
    DEFAULT_SOURCE_NAMES,
    BilibiliProvider,
    FivesingProvider,
    JamendoProvider,
    JooxProvider,
    NeteaseProvider,
    QianqianProvider,
    SodaProvider,
    source_description,
)

UA_PC = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
UA_MOBILE = "Mozilla/5.0 (iPhone; CPU iPhone OS 9_1 like Mac OS X) AppleWebKit/601.1.46 (KHTML, like Gecko) Version/9.0 Mobile/13B143 Safari/601.1"
MIGU_MAGIC_USER_ID = "15548614588710179085069"


class ProviderError(RuntimeError):
    pass


@dataclass
class SourceResponse:
    url: str
    headers: dict[str, str] | None = None
    extension: str = ""
    post_process: Any = None


class MusicProvider:
    source = ""

    def __init__(self, cookie: str = "", timeout: float = 30) -> None:
        self.cookie = cookie or ""
        self.timeout = timeout

    async def search(self, keyword: str, limit: int) -> list[Song]:
        return await asyncio.to_thread(self.search_sync, keyword, limit)

    async def parse(self, link: str) -> Song | None:
        return await asyncio.to_thread(self.parse_sync, link)

    async def get_download_url(self, song: Song) -> SourceResponse:
        return await asyncio.to_thread(self.get_download_url_sync, song)

    async def search_playlist(self, keyword: str, limit: int) -> list[Collection]:
        return await asyncio.to_thread(self.search_playlist_sync, keyword, limit)

    async def search_album(self, keyword: str, limit: int) -> list[Collection]:
        return await asyncio.to_thread(self.search_album_sync, keyword, limit)

    async def get_playlist_songs(self, collection: Collection) -> list[Song]:
        return await asyncio.to_thread(self.get_playlist_songs_sync, collection)

    async def get_album_songs(self, collection: Collection) -> list[Song]:
        return await asyncio.to_thread(self.get_album_songs_sync, collection)

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        raise NotImplementedError

    def parse_sync(self, link: str) -> Song | None:
        return None

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        raise NotImplementedError

    def search_playlist_sync(self, keyword: str, limit: int) -> list[Collection]:
        raise NotImplementedError

    def search_album_sync(self, keyword: str, limit: int) -> list[Collection]:
        raise NotImplementedError

    def get_playlist_songs_sync(self, collection: Collection) -> list[Song]:
        raise NotImplementedError

    def get_album_songs_sync(self, collection: Collection) -> list[Song]:
        raise NotImplementedError

    def get_json(self, url: str, headers: dict[str, str] | None = None, *, no_redirect: bool = False) -> Any:
        data, _ = self.get_bytes(url, headers=headers, no_redirect=no_redirect)
        text = data.decode("utf-8", errors="replace")
        return json.loads(text)

    def post_json(self, url: str, payload: Any, headers: dict[str, str] | None = None) -> Any:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req_headers = {"User-Agent": UA_PC, "Content-Type": "application/json"}
        if self.cookie:
            req_headers["Cookie"] = self.cookie
        if headers:
            req_headers.update(headers)
        req = Request(url, data=raw, headers=req_headers, method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except (HTTPError, URLError) as exc:
            raise ProviderError(str(exc)) from exc

    def get_bytes(self, url: str, headers: dict[str, str] | None = None, *, no_redirect: bool = False) -> tuple[bytes, dict[str, str]]:
        req_headers = {"User-Agent": UA_PC}
        if self.cookie:
            req_headers["Cookie"] = self.cookie
        if headers:
            req_headers.update(headers)
        req = Request(url, headers=req_headers)
        opener = None
        if no_redirect:
            from urllib.request import HTTPRedirectHandler, build_opener

            class NoRedirect(HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None

            opener = build_opener(NoRedirect)
        try:
            open_func = opener.open if opener else urlopen
            with open_func(req, timeout=self.timeout) as resp:
                return resp.read(), dict(resp.headers.items())
        except HTTPError as exc:
            if no_redirect and exc.code in (301, 302, 303, 307, 308):
                return b"", dict(exc.headers.items())
            body = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(body.strip() or f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise ProviderError(str(exc.reason)) from exc


class QQProvider(MusicProvider):
    source = "qq"

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        params = urlencode({"w": keyword, "format": "json", "p": 1, "n": limit})
        payload = self.get_json(
            f"http://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp?{params}",
            {"User-Agent": UA_MOBILE, "Referer": "http://m.y.qq.com"},
        )
        items = (((payload or {}).get("data") or {}).get("song") or {}).get("list") or []
        songs: list[Song] = []
        for item in items[:limit]:
            songmid = str(item.get("songmid") or "").strip()
            if not songmid:
                continue
            artists = "、".join(str(s.get("name") or "").strip() for s in item.get("singer") or [] if s.get("name"))
            albummid = str(item.get("albummid") or "").strip()
            cover = f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{albummid}.jpg" if albummid else ""
            extra = {"songmid": songmid, "song_id": str(item.get("songid") or "")}
            songs.append(Song(
                id=songmid,
                source=self.source,
                name=str(item.get("songname") or "Unknown"),
                artist=artists or "Unknown",
                album=str(item.get("albumname") or ""),
                cover=cover,
                duration=int(item.get("interval") or 0),
                extra=json.dumps(extra, ensure_ascii=False),
            ))
        return songs

    def parse_sync(self, link: str) -> Song | None:
        match = re.search(r"songDetail/([A-Za-z0-9]+)", link)
        if not match:
            return None
        songmid = match.group(1)
        params = urlencode({"songmid": songmid, "format": "json"})
        payload = self.get_json(
            f"https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg?{params}",
            {"User-Agent": UA_MOBILE, "Referer": "http://m.y.qq.com"},
        )
        rows = (payload or {}).get("data") or []
        if not rows:
            return None
        item = rows[0]
        artists = "、".join(str(s.get("name") or "").strip() for s in item.get("singer") or [] if s.get("name"))
        album = item.get("album") or {}
        albummid = str(album.get("mid") or "").strip()
        cover = f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{albummid}.jpg" if albummid else ""
        extra = {"songmid": str(item.get("mid") or songmid), "song_id": str(item.get("id") or "")}
        return Song(str(item.get("mid") or songmid), self.source, str(item.get("name") or "Unknown"), artists or "Unknown", str(album.get("name") or ""), cover, int(item.get("interval") or 0), json.dumps(extra, ensure_ascii=False))

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        songmid = _extra(song).get("songmid") or song.id
        guid = str(random.randint(1000000000, 9999999999))
        prefixes = ["M800", "M500"]
        exts = ["mp3", "mp3"]
        filenames = [f"{prefix}{songmid}{songmid}.{ext}" for prefix, ext in zip(prefixes, exts)]
        payload = {
            "comm": {"cv": 4747474, "ct": 24, "format": "json", "inCharset": "utf-8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 1, "uin": 0},
            "req_1": {"module": "music.vkey.GetVkey", "method": "UrlGetVkey", "param": {"guid": guid, "songmid": [songmid] * len(filenames), "songtype": [0] * len(filenames), "uin": "0", "loginflag": 1, "platform": "20", "filename": filenames}},
        }
        result = self.post_json("https://u.y.qq.com/cgi-bin/musicu.fcg", payload, {"User-Agent": UA_MOBILE, "Referer": "http://y.qq.com"})
        infos = (((result or {}).get("req_1") or {}).get("data") or {}).get("midurlinfo") or []
        for filename in filenames:
            for info in infos:
                if info.get("filename") == filename and info.get("purl"):
                    return SourceResponse("https://ws.stream.qqmusic.qq.com/" + info["purl"], {"Referer": "http://y.qq.com", "User-Agent": UA_MOBILE})
        raise ProviderError("QQ 音乐未返回可用下载地址，可能需要会员或 Cookie")


class KugouProvider(MusicProvider):
    source = "kugou"

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        params = urlencode({"keyword": keyword, "platform": "WebFilter", "format": "json", "page": 1, "pagesize": limit})
        payload = self.get_json(
            f"http://songsearch.kugou.com/song_search_v2?{params}",
            {"User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/80.0 Mobile Safari/537.36"},
        )
        rows = (((payload or {}).get("data") or {}).get("lists") or [])
        songs: list[Song] = []
        for item in rows[:limit]:
            file_hash = _first(item.get("SQFileHash"), item.get("HQFileHash"), item.get("FileHash"))
            if not file_hash:
                continue
            image = str(item.get("Image") or "").replace("{size}", "240")
            extra = {
                "hash": file_hash,
                "file_hash": str(item.get("FileHash") or ""),
                "sq_hash": str(item.get("SQFileHash") or ""),
                "hq_hash": str(item.get("HQFileHash") or ""),
            }
            songs.append(Song(file_hash, self.source, str(item.get("SongName") or "Unknown"), str(item.get("SingerName") or "Unknown"), str(item.get("AlbumName") or ""), image, int(item.get("Duration") or 0), json.dumps(extra, ensure_ascii=False)))
        return songs

    def parse_sync(self, link: str) -> Song | None:
        match = re.search(r"(?i)hash=([a-f0-9]{32})", link)
        if not match:
            return None
        hash_value = match.group(1)
        return Song(hash_value, self.source, f"Kugou_{hash_value[:8]}", "Unknown", extra=json.dumps({"hash": hash_value}, ensure_ascii=False))

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        hash_value = _extra(song).get("hash") or song.id
        params = urlencode({"cmd": "playInfo", "hash": hash_value})
        payload = self.get_json(
            f"http://m.kugou.com/app/i/getSongInfo.php?{params}",
            {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 Version/13.0.3 Mobile/15E148 Safari/604.1", "Referer": "http://m.kugou.com"},
        )
        url = str((payload or {}).get("url") or "").strip()
        if not url:
            raise ProviderError("酷狗未返回可用下载地址，可能触发风控或歌曲受限")
        return SourceResponse(url, {"Referer": "http://m.kugou.com", "User-Agent": UA_MOBILE})


class KuwoProvider(MusicProvider):
    source = "kuwo"

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        params = urlencode({"vipver": 1, "client": "kt", "ft": "music", "cluster": 0, "strategy": 2012, "encoding": "utf8", "rformat": "json", "mobi": 1, "issubtitle": 1, "show_copyright_off": 1, "pn": 0, "rn": limit, "all": keyword})
        payload = self.get_json(f"http://www.kuwo.cn/search/searchMusicBykeyWord?{params}", {"User-Agent": UA_PC})
        rows = (payload or {}).get("abslist") or []
        songs: list[Song] = []
        for item in rows[:limit]:
            rid = str(item.get("MUSICRID") or "").replace("MUSIC_", "").strip()
            if not rid:
                continue
            songs.append(Song(rid, self.source, str(item.get("SONGNAME") or "Unknown"), str(item.get("ARTIST") or "Unknown"), str(item.get("ALBUM") or ""), str(item.get("hts_MVPIC") or ""), _safe_int(item.get("DURATION")), json.dumps({"rid": rid}, ensure_ascii=False)))
        return songs

    def parse_sync(self, link: str) -> Song | None:
        match = re.search(r"play_detail/(\d+)", link)
        if not match:
            return None
        rid = match.group(1)
        return Song(rid, self.source, f"Kuwo_{rid}", "Unknown", extra=json.dumps({"rid": rid}, ensure_ascii=False))

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        rid = _extra(song).get("rid") or song.id
        random_id = f"C_APK_guanwang_{time.time_ns()}{random.randint(0, 999999)}"
        last_error = ""
        for br in ("320kmp3", "128kmp3", "flac", "2000kflac"):
            params = urlencode({"f": "web", "source": "kwplayercar_ar_6.0.0.9_B_jiakong_vh.apk", "from": "PC", "type": "convert_url_with_sign", "br": br, "rid": rid, "user": random_id})
            try:
                payload = self.get_json(f"https://mobi.kuwo.cn/mobi.s?{params}", {"User-Agent": UA_PC})
                url = str((payload or {}).get("data", {}).get("url") or (payload or {}).get("url") or "").strip()
                if url:
                    return SourceResponse(url, {"User-Agent": UA_PC})
            except Exception as exc:
                last_error = str(exc)
        raise ProviderError(last_error or "酷我未返回可用下载地址")


class MiguProvider(MusicProvider):
    source = "migu"

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        params = urlencode({"ua": "Android_migu", "version": "5.0.1", "text": keyword, "pageNo": 1, "pageSize": limit, "searchSwitch": json.dumps({"song": 1, "album": 0, "singer": 0, "tagSong": 0, "mvSong": 0, "songlist": 0, "bestShow": 1}, separators=(",", ":"))})
        payload = self.get_json(
            f"http://pd.musicapp.migu.cn/MIGUM2.0/v1.0/content/search_all.do?{params}",
            {"User-Agent": UA_MOBILE, "Referer": "http://music.migu.cn/"},
        )
        rows = (((payload or {}).get("songResultData") or {}).get("result") or [])
        songs: list[Song] = []
        for item in rows[:limit]:
            song = self._song_from_item(item)
            if song:
                songs.append(song)
        return songs

    def parse_sync(self, link: str) -> Song | None:
        match = re.search(r"music\.migu\.cn/v3/music/song/(\d+)", link)
        if not match:
            return None
        content_id = match.group(1)
        params = urlencode({"resourceType": 2, "contentId": content_id})
        payload = self.get_json(f"http://c.musicapp.migu.cn/MIGUM2.0/v1.0/content/queryById.do?{params}", {"User-Agent": UA_MOBILE, "Referer": "http://music.migu.cn/"})
        item = ((payload or {}).get("resource") or [{}])[0] if isinstance((payload or {}).get("resource"), list) else (payload or {}).get("resource")
        return self._song_from_item(item or {"contentId": content_id, "name": f"Migu_{content_id}"})

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        extra = _extra(song)
        content_id = extra.get("content_id") or song.id.split("|")[0]
        resource_type = extra.get("resource_type") or (song.id.split("|")[1] if "|" in song.id else "2")
        format_type = extra.get("format_type") or (song.id.split("|")[2] if song.id.count("|") >= 2 else "PQ")
        params = urlencode({"toneFlag": format_type, "netType": "00", "userId": MIGU_MAGIC_USER_ID, "ua": "Android_migu", "version": "5.1", "copyrightId": "0", "contentId": content_id, "resourceType": resource_type, "channel": "0"})
        url = f"http://app.pd.nf.migu.cn/MIGUM2.0/v1.0/content/sub/listenSong.do?{params}"
        _, headers = self.get_bytes(url, {"User-Agent": UA_MOBILE, "Referer": "http://music.migu.cn/"}, no_redirect=True)
        return SourceResponse(headers.get("Location") or url, {"User-Agent": UA_MOBILE, "Referer": "http://music.migu.cn/"})

    def _song_from_item(self, item: dict[str, Any]) -> Song | None:
        content_id = str(item.get("contentId") or item.get("id") or "").strip()
        if not content_id:
            return None
        formats = item.get("rateFormats") or []
        picked = formats[0] if formats else {}
        for candidate in formats:
            if str(candidate.get("formatType") or "").upper() in {"SQ", "HQ", "PQ"}:
                picked = candidate
                break
        resource_type = str(picked.get("resourceType") or "2")
        format_type = str(picked.get("formatType") or "PQ")
        singers = item.get("singers") or item.get("artists") or []
        artist = "、".join(str(s.get("name") or "").strip() for s in singers if s.get("name")) or str(item.get("singer") or "Unknown")
        albums = item.get("albums") or []
        album = str((albums[0] or {}).get("name") if albums else item.get("album") or "")
        images = item.get("imgItems") or item.get("albumImgs") or []
        cover = str((images[0] or {}).get("img") if images else "")
        extra = {"content_id": content_id, "resource_type": resource_type, "format_type": format_type}
        return Song(f"{content_id}|{resource_type}|{format_type}", self.source, str(item.get("songName") or item.get("name") or "Unknown"), artist, album, cover, 0, json.dumps(extra, ensure_ascii=False))


class MusicAggregator:
    def __init__(self, config: dict, download_dir: Path) -> None:
        self.config = config or {}
        self.download_dir = download_dir
        self.timeout = 30.0
        self.page_size = _safe_int(self.config.get("cliPageSize")) or 50
        self.max_download_bytes = 0
        cookies = _normalize_cookies(self.config.get("cookies"))
        self.providers: dict[str, MusicProvider] = {
            "netease": NeteaseProvider(str(cookies.get("netease", "")), self.timeout),
            "qq": QQProvider(str(cookies.get("qq", "")), self.timeout),
            "kugou": KugouProvider(str(cookies.get("kugou", "")), self.timeout),
            "kuwo": KuwoProvider(str(cookies.get("kuwo", "")), self.timeout),
            "migu": MiguProvider(str(cookies.get("migu", "")), self.timeout),
            "fivesing": FivesingProvider(str(cookies.get("fivesing", "")), self.timeout),
            "jamendo": JamendoProvider(str(cookies.get("jamendo", "")), self.timeout),
            "joox": JooxProvider(str(cookies.get("joox", "")), self.timeout),
            "qianqian": QianqianProvider(str(cookies.get("qianqian", "")), self.timeout),
            "soda": SodaProvider(str(cookies.get("soda", "")), self.timeout),
            "bilibili": BilibiliProvider(str(cookies.get("bilibili", "")), self.timeout),
        }

    async def search(self, keyword: str, search_type: str = SEARCH_TYPE_SONG, sources: list[str] | None = None) -> list[Song] | list[Collection]:
        if search_type == SEARCH_TYPE_SONG:
            return await self.search_songs(keyword, sources)
        if search_type in {SEARCH_TYPE_PLAYLIST, SEARCH_TYPE_ALBUM}:
            return await self.search_collections(keyword, search_type, sources)
        raise ProviderError(f"unsupported search type: {search_type}")

    async def search_songs(self, keyword: str, sources: list[str] | None = None) -> list[Song]:
        if keyword.startswith("http://") or keyword.startswith("https://"):
            parsed = await self.parse_link(keyword)
            return [parsed] if parsed else []
        selected = self._selected_sources(sources)
        tasks = [self._safe_search(provider, keyword) for provider in selected]
        groups = await asyncio.gather(*tasks)
        merged: list[Song] = []
        for group in groups:
            merged.extend(group)
        return merged[: self.page_size]

    async def search_collections(self, keyword: str, search_type: str, sources: list[str] | None = None) -> list[Collection]:
        selected = self._selected_sources(sources)
        tasks = [self._safe_search_collections(provider, keyword, search_type) for provider in selected]
        groups = await asyncio.gather(*tasks)
        merged: list[Collection] = []
        for group in groups:
            merged.extend(group)
        return merged[: self.page_size]

    async def parse_link(self, link: str) -> Song | None:
        provider = self._provider_for_link(link)
        if not provider:
            return None
        return await provider.parse(link)

    async def get_collection_songs(self, collection: Collection) -> list[Song]:
        provider = self.providers.get(collection.source)
        if not provider:
            raise ProviderError(f"unsupported source: {collection.source}")
        method = getattr(provider, "get_album_songs" if collection.kind == SEARCH_TYPE_ALBUM else "get_playlist_songs", None)
        if not callable(method):
            raise ProviderError(f"{collection.source} does not support expanding {collection.label}")
        songs = await method(collection)
        return songs[: self.page_size]

    async def switch_source(self, song: Song) -> Song:
        keyword = f"{song.name} {song.artist}".strip()
        candidates = await self.search(keyword, SEARCH_TYPE_SONG, [name for name in self.providers if name != song.source])
        if not candidates:
            raise ProviderError("未找到可换源结果")
        candidates.sort(key=lambda cand: _similarity(song.name, song.artist, cand.name, cand.artist), reverse=True)
        return candidates[0]

    async def download_song(self, song: Song) -> DownloadedFile:
        provider = self.providers.get(song.source)
        if not provider:
            raise ProviderError(f"不支持的来源：{song.source}")
        source = await provider.get_download_url(song)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        path, data = await asyncio.to_thread(self._download_sync, song, source)
        await asyncio.to_thread(path.write_bytes, data)
        return DownloadedFile(path=path, filename=path.name, song=song)

    async def _safe_search(self, provider: MusicProvider, keyword: str) -> list[Song]:
        try:
            return await provider.search(keyword, self.page_size)
        except Exception as exc:
            logger.warning(f"[MusicDL] {provider.source} search failed: {exc}")
            return []

    async def _safe_search_collections(self, provider: MusicProvider, keyword: str, search_type: str) -> list[Collection]:
        method = getattr(provider, "search_album" if search_type == SEARCH_TYPE_ALBUM else "search_playlist", None)
        if not callable(method):
            return []
        try:
            return await method(keyword, self.page_size)
        except NotImplementedError:
            return []
        except Exception as exc:
            logger.warning(f"[MusicDL] {provider.source} {search_type} search failed: {exc}")
            return []

    def source_capabilities(self) -> dict[str, dict[str, bool]]:
        result: dict[str, dict[str, bool]] = {}
        for name, provider in self.providers.items():
            result[name] = {
                "song": callable(getattr(provider, "search", None)),
                "playlist": _provider_supports(provider, "search_playlist_sync", "search_playlist"),
                "album": _provider_supports(provider, "search_album_sync", "search_album"),
                "default": name in DEFAULT_SOURCE_NAMES,
            }
        return result

    def _selected_sources(self, sources: list[str] | None) -> list[MusicProvider]:
        names = sources or DEFAULT_SOURCE_NAMES[:]
        providers = [self.providers[name] for name in names if name in self.providers]
        return providers or [self.providers[name] for name in DEFAULT_SOURCE_NAMES if name in self.providers]

    def _provider_for_link(self, link: str) -> MusicProvider | None:
        lower = link.lower()
        if "163.com" in lower:
            return self.providers["netease"]
        if "qq.com" in lower:
            return self.providers["qq"]
        if "5sing" in lower:
            return self.providers["fivesing"]
        if "kugou.com" in lower:
            return self.providers["kugou"]
        if "kuwo.cn" in lower:
            return self.providers["kuwo"]
        if "migu.cn" in lower:
            return self.providers["migu"]
        if "joox.com" in lower:
            return self.providers["joox"]
        if "bilibili.com" in lower or "b23.tv" in lower:
            return self.providers["bilibili"]
        if "douyin.com" in lower or "qishui" in lower:
            return self.providers["soda"]
        if "91q.com" in lower:
            return self.providers["qianqian"]
        if "jamendo.com" in lower:
            return self.providers["jamendo"]
        return None

    def _download_sync(self, song: Song, source: SourceResponse) -> tuple[Path, bytes]:
        headers = dict(source.headers or {})
        data, response_headers = _http_bytes(source.url, headers, self.timeout)
        if getattr(source, "post_process", None):
            data = source.post_process(data)
        if self.max_download_bytes > 0 and len(data) > self.max_download_bytes:
            raise ProviderError(f"音频文件过大：{len(data) / 1024 / 1024:.1f} MB")
        content_type = response_headers.get("Content-Type", "").split(";", 1)[0].strip()
        ext = getattr(source, "extension", "") or _ext_from_url_or_type(source.url, content_type)
        filename = _safe_filename(f"{song.name} - {song.artist}.{ext}")
        path = self.download_dir / filename
        if path.exists():
            path = self.download_dir / _safe_filename(f"{song.name} - {song.artist}-{int(time.time())}.{ext}")
        return path, data




def _json_extra(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _join_names(items: list[dict[str, Any]], key: str = "name") -> str:
    return " / ".join(str(item.get(key) or "").strip() for item in items if item.get(key))


def _provider_supports(provider: object, sync_name: str, async_name: str) -> bool:
    cls_method = getattr(type(provider), sync_name, None)
    base_method = getattr(MusicProvider, sync_name, None)
    if cls_method is not None and cls_method is not base_method:
        return True
    method = getattr(provider, async_name, None)
    return callable(method) and not isinstance(provider, MusicProvider)


def _qq_search_album_sync(self: QQProvider, keyword: str, limit: int) -> list[Collection]:
    params = urlencode({"format": "json", "p": 1, "n": limit, "w": keyword, "t": 8})
    payload = self.get_json(f"http://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp?{params}", {"User-Agent": UA_PC, "Referer": "https://y.qq.com/portal/search.html"})
    rows = ((((payload or {}).get("data") or {}).get("album") or {}).get("list") or [])
    albums: list[Collection] = []
    for item in rows[:limit]:
        album_mid = str(item.get("albumMID") or "").strip()
        if not album_mid:
            continue
        albums.append(Collection(
            id=album_mid,
            source="qq",
            name=str(item.get("albumName") or ""),
            creator=str(item.get("singerName") or ""),
            cover=f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_mid}.jpg",
            kind=SEARCH_TYPE_ALBUM,
            link=f"https://y.qq.com/n/ryqq/albumDetail/{album_mid}",
            extra=_json_extra({"type": "album", "album_id": str(item.get("albumID") or ""), "album_mid": album_mid, "publish_time": str(item.get("publicTime") or "")}),
        ))
    return albums


def _qq_search_playlist_sync(self: QQProvider, keyword: str, limit: int) -> list[Collection]:
    params = urlencode({"query": keyword, "page_no": 0, "num_per_page": limit, "format": "json", "remoteplace": "txt.yqq.playlist", "flag_qc": 0})
    data, _ = self.get_bytes(f"http://c.y.qq.com/soso/fcgi-bin/client_music_search_songlist?{params}", {"User-Agent": UA_PC, "Referer": "https://y.qq.com/portal/search.html"})
    text = data.decode("utf-8", errors="replace")
    if "(" in text and text.rstrip().endswith(")"):
        text = text[text.index("(") + 1 : text.rindex(")")]
    payload = json.loads(text)
    rows = (((payload or {}).get("data") or {}).get("list") or [])
    playlists: list[Collection] = []
    for item in rows[:limit]:
        dissid = str(item.get("dissid") or "").strip()
        if not dissid:
            continue
        cover = str(item.get("imgurl") or "")
        if cover.startswith("http://"):
            cover = cover.replace("http://", "https://", 1)
        creator = ((item.get("creator") or {}).get("name") or "")
        playlists.append(Collection(dissid, "qq", str(item.get("dissname") or ""), str(creator), cover, _safe_int(item.get("song_count")), SEARCH_TYPE_PLAYLIST, _safe_int(item.get("listennum")), "", f"https://y.qq.com/n/ryqq/playlist/{dissid}"))
    return playlists


def _qq_get_album_songs_sync(self: QQProvider, collection: Collection) -> list[Song]:
    album_mid = _extra_collection(collection).get("album_mid") or collection.id
    payload = {
        "comm": {"ct": 24, "cv": 0},
        "album": {"module": "music.musichallAlbum.AlbumSongList", "method": "GetAlbumSongList", "param": {"albumMid": album_mid, "begin": 0, "num": 100, "order": 2}},
    }
    result = self.post_json("https://u.y.qq.com/cgi-bin/musicu.fcg", payload, {"User-Agent": UA_PC, "Referer": "https://y.qq.com/"})
    rows = ((((result or {}).get("album") or {}).get("data") or {}).get("songList") or [])
    return [_qq_song_from_info((item or {}).get("songInfo") or {}) for item in rows if (item or {}).get("songInfo")]


def _qq_get_playlist_songs_sync(self: QQProvider, collection: Collection) -> list[Song]:
    params = urlencode({"type": 1, "json": 1, "utf8": 1, "onlysong": 0, "disstid": collection.id, "format": "json", "g_tk": 5381, "loginUin": 0, "hostUin": 0, "inCharset": "utf8", "outCharset": "utf-8", "notice": 0, "platform": "yqq", "needNewCode": 0})
    data, _ = self.get_bytes(f"http://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg?{params}", {"User-Agent": UA_PC, "Referer": "https://y.qq.com/"})
    text = data.decode("utf-8", errors="replace")
    if "(" in text and text.rstrip().endswith(")"):
        text = text[text.index("(") + 1 : text.rindex(")")]
    payload = json.loads(text)
    cdlist = (payload or {}).get("cdlist") or []
    rows = (cdlist[0].get("songlist") if cdlist else []) or []
    songs: list[Song] = []
    for item in rows:
        songmid = str(item.get("songmid") or "").strip()
        if not songmid:
            continue
        album_mid = str(item.get("albummid") or "")
        songs.append(Song(
            id=songmid,
            source="qq",
            name=str(item.get("songname") or "Unknown"),
            artist=_join_names(item.get("singer") or []) or "Unknown",
            album=str(item.get("albumname") or ""),
            cover=f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_mid}.jpg" if album_mid else "",
            duration=_safe_int(item.get("interval")),
            extra=_json_extra({"songmid": songmid, "album_mid": album_mid}),
            album_id=album_mid,
            size=_safe_int(item.get("sizeflac") or item.get("size320") or item.get("size128")),
            bitrate=320 if _safe_int(item.get("size320")) else 128,
            link=f"https://y.qq.com/n/ryqq/songDetail/{songmid}",
        ))
    return songs


def _qq_song_from_info(info: dict[str, Any]) -> Song:
    songmid = str(info.get("mid") or "").strip()
    album = info.get("album") or {}
    album_mid = str(album.get("mid") or "")
    file_info = info.get("file") or {}
    return Song(
        id=songmid,
        source="qq",
        name=str(info.get("name") or "Unknown"),
        artist=_join_names(info.get("singer") or []) or "Unknown",
        album=str(album.get("name") or ""),
        cover=f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_mid}.jpg" if album_mid else "",
        duration=_safe_int(info.get("interval")),
        extra=_json_extra({"songmid": songmid, "album_mid": album_mid, "album_id": str(album.get("id") or "")}),
        album_id=album_mid,
        size=_safe_int(file_info.get("size_flac") or file_info.get("size_320mp3") or file_info.get("size_128mp3")),
        bitrate=320 if _safe_int(file_info.get("size_320mp3")) else 128,
        link=f"https://y.qq.com/n/ryqq/songDetail/{songmid}",
    )


def _kuwo_search_collection(self: KuwoProvider, keyword: str, limit: int, kind: str) -> list[Collection]:
    params = urlencode({"all": keyword, "ft": "album" if kind == SEARCH_TYPE_ALBUM else "playlist", "itemset": "web_2013", "client": "kt", "pcmp4": 1, "geo": "c", "vipver": 1, "pn": 0, "rn": limit, "rformat": "json", "encoding": "utf8"})
    payload = self.get_json(f"http://search.kuwo.cn/r.s?{params}", {"User-Agent": UA_PC})
    rows = (payload or {}).get("albumlist" if kind == SEARCH_TYPE_ALBUM else "abslist") or []
    result: list[Collection] = []
    for item in rows[:limit]:
        if kind == SEARCH_TYPE_ALBUM:
            cid = str(item.get("albumid") or item.get("id") or "").strip()
            name = str(item.get("name") or "")
            creator = str(item.get("aartist") or item.get("artist") or "")
            cover = _normalize_url(str(item.get("hts_img") or item.get("img") or ""))
            count = _safe_int(item.get("musiccnt"))
            desc = str(item.get("info") or "")
            link = f"http://www.kuwo.cn/album_detail/{cid}"
        else:
            cid = str(item.get("playlistid") or "").strip()
            name = str(item.get("name") or "")
            creator = str(item.get("nickname") or "")
            cover = _normalize_url(str(item.get("pic") or "").replace("_150.", "_700."))
            count = _safe_int(item.get("songnum"))
            desc = str(item.get("intro") or "")
            link = f"http://www.kuwo.cn/playlist_detail/{cid}"
        if cid:
            result.append(Collection(cid, "kuwo", name, creator, cover, count, kind, 0, desc, link, _json_extra({"type": kind, "id": cid})))
    return result


def _kuwo_search_album_sync(self: KuwoProvider, keyword: str, limit: int) -> list[Collection]:
    return _kuwo_search_collection(self, keyword, limit, SEARCH_TYPE_ALBUM)


def _kuwo_search_playlist_sync(self: KuwoProvider, keyword: str, limit: int) -> list[Collection]:
    return _kuwo_search_collection(self, keyword, limit, SEARCH_TYPE_PLAYLIST)


def _kuwo_get_playlist_songs_sync(self: KuwoProvider, collection: Collection) -> list[Song]:
    params = urlencode({"op": "getlistinfo", "pid": collection.id, "pn": 0, "rn": 100, "encode": "utf8", "keyset": "pl2012", "identity": "kuwo", "pcmp4": 1, "vipver": 1, "newver": 1})
    payload = self.get_json(f"http://nplserver.kuwo.cn/pl.svc?{params}", {"User-Agent": UA_PC})
    rows = (payload or {}).get("musiclist") or []
    return [_kuwo_song_from_item(item) for item in rows if item.get("id")]


def _kuwo_get_album_songs_sync(self: KuwoProvider, collection: Collection) -> list[Song]:
    params = urlencode({"pn": 0, "rn": 100, "stype": "albuminfo", "albumid": collection.id, "sortby": 0, "alflac": 1, "show_copyright_off": 1, "pcmp4": 1, "encoding": "utf8", "vipver": 1, "rformat": "json"})
    payload = self.get_json(f"http://search.kuwo.cn/r.s?{params}", {"User-Agent": UA_PC})
    rows = (payload or {}).get("musiclist") or (payload or {}).get("abslist") or []
    songs = [_kuwo_song_from_item(item, collection) for item in rows if item.get("id") or item.get("MUSICRID")]
    if songs:
        return songs
    return _kuwo_get_playlist_songs_sync(self, collection)


def _kuwo_song_from_item(item: dict[str, Any], collection: Collection | None = None) -> Song:
    rid = str(item.get("id") or item.get("MUSICRID") or "").replace("MUSIC_", "")
    cover = _normalize_url(str(item.get("albumpic") or item.get("hts_MVPIC") or item.get("pic") or (collection.cover if collection else "")))
    return Song(
        id=rid,
        source="kuwo",
        name=str(item.get("name") or item.get("song_name") or item.get("SONGNAME") or "Unknown"),
        artist=str(item.get("artist") or item.get("artist_name") or item.get("ARTIST") or "Unknown"),
        album=str(item.get("album") or item.get("ALBUM") or (collection.name if collection and collection.kind == SEARCH_TYPE_ALBUM else "")),
        cover=cover,
        duration=_safe_int(item.get("duration") or item.get("DURATION")),
        extra=_json_extra({"rid": rid, "album_id": collection.id if collection else ""}),
        album_id=collection.id if collection and collection.kind == SEARCH_TYPE_ALBUM else "",
        link=f"http://www.kuwo.cn/play_detail/{rid}",
    )


def _migu_search_album_sync(self: MiguProvider, keyword: str, limit: int) -> list[Collection]:
    params = urlencode({"ua": "Android_migu", "version": "5.0.1", "text": keyword, "pageNo": 1, "pageSize": limit, "searchSwitch": json.dumps({"song": 0, "album": 1, "singer": 0, "tagSong": 0, "mvSong": 0, "songlist": 0, "bestShow": 1}, separators=(",", ":"))})
    payload = self.get_json(f"http://pd.musicapp.migu.cn/MIGUM2.0/v1.0/content/search_all.do?{params}", {"User-Agent": UA_MOBILE, "Referer": "http://music.migu.cn/"})
    rows = (((payload or {}).get("albumResultData") or {}).get("result") or [])
    result: list[Collection] = []
    for item in rows[:limit]:
        cid = str(item.get("id") or "").strip()
        if not cid:
            continue
        result.append(Collection(cid, "migu", str(item.get("name") or ""), str(item.get("singer") or ""), _migu_cover(item), 0, SEARCH_TYPE_ALBUM, 0, str(item.get("desc") or item.get("publishDate") or ""), f"https://music.migu.cn/v3/music/album/{cid}", _json_extra({"type": "album", "album_id": cid, "resource_type": str(item.get("resourceType") or "2003")})))
    return result


def _migu_search_playlist_sync(self: MiguProvider, keyword: str, limit: int) -> list[Collection]:
    params = urlencode({"ua": "Android_migu", "version": "5.0.1", "text": keyword, "pageNo": 1, "pageSize": limit, "searchSwitch": json.dumps({"song": 0, "album": 0, "singer": 0, "tagSong": 0, "mvSong": 0, "songlist": 1, "bestShow": 1}, separators=(",", ":"))})
    payload = self.get_json(f"http://pd.musicapp.migu.cn/MIGUM2.0/v1.0/content/search_all.do?{params}", {"User-Agent": UA_MOBILE, "Referer": "http://music.migu.cn/"})
    rows = (((payload or {}).get("songListResultData") or {}).get("result") or [])
    result: list[Collection] = []
    for item in rows[:limit]:
        cid = str(item.get("id") or "").strip()
        if not cid:
            continue
        result.append(Collection(cid, "migu", str(item.get("name") or ""), str(item.get("userName") or ""), _migu_cover(item), _safe_int(item.get("musicNum")), SEARCH_TYPE_PLAYLIST, 0, "", "", _json_extra({"type": "playlist", "playlist_id": cid})))
    return result


def _migu_get_playlist_songs_sync(self: MiguProvider, collection: Collection) -> list[Song]:
    params = urlencode({"musicListId": collection.id, "pageNo": 1, "pageSize": 100})
    payload = self.get_json(f"http://c.musicapp.migu.cn/MIGUM2.0/v1.0/content/musicListContent.do?{params}", {"User-Agent": UA_MOBILE, "Referer": "http://music.migu.cn/"})
    rows = (payload or {}).get("contentList") or []
    songs: list[Song] = []
    for item in rows:
        content_id = str(item.get("contentId") or item.get("songId") or "").strip()
        if not content_id:
            continue
        cover = _normalize_url(str(item.get("picL") or item.get("picM") or ""))
        songs.append(Song(content_id, "migu", str(item.get("songName") or "Unknown"), str(item.get("singerName") or "Unknown"), str(item.get("albumName") or ""), cover, 0, _json_extra({"content_id": content_id, "resource_type": "2", "format_type": "PQ", "copyright_id": str(item.get("copyrightId") or "")})))
    return songs


def _migu_get_album_songs_sync(self: MiguProvider, collection: Collection) -> list[Song]:
    params = urlencode({"albumId": collection.id, "pageNo": 1, "pageSize": 100})
    payload = self.get_json(f"https://app.c.nf.migu.cn/MIGUM2.0/v1.0/content/queryAlbumSong?{params}", {"User-Agent": UA_MOBILE, "Referer": "http://music.migu.cn/"})
    rows = (((payload or {}).get("data") or {}).get("songList") or [])
    songs = []
    for item in rows:
        song = self._song_from_item(item)
        if song:
            songs.append(song)
    return songs


def _migu_cover(item: dict[str, Any]) -> str:
    images = item.get("imgItems") or item.get("albumImgs") or []
    if images:
        return _normalize_url(str((images[0] or {}).get("img") or ""))
    return ""


def _normalize_url(value: str) -> str:
    value = value.strip()
    if value.startswith("//"):
        return "https:" + value
    if value and not value.startswith("http"):
        return "http://" + value
    return value


def _extra_collection(collection: Collection) -> dict[str, str]:
    try:
        parsed = json.loads(collection.extra or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if v is not None}




def _kugou_search_album_sync(self: KugouProvider, keyword: str, limit: int) -> list[Collection]:
    params = urlencode({"keyword": keyword, "format": "json", "page": 1, "pagesize": limit})
    payload = self.get_json(f"http://mobilecdn.kugou.com/api/v3/search/album?{params}", {"User-Agent": UA_MOBILE})
    rows = (((payload or {}).get("data") or {}).get("info") or [])
    albums: list[Collection] = []
    for item in rows[:limit]:
        album_id = str(item.get("albumid") or "").strip()
        if not album_id:
            continue
        albums.append(Collection(album_id, "kugou", str(item.get("albumname") or ""), str(item.get("singername") or ""), str(item.get("imgurl") or "").replace("{size}", "240"), _safe_int(item.get("songcount")), SEARCH_TYPE_ALBUM, 0, str(item.get("intro") or ""), f"https://www.kugou.com/album/{album_id}.html", _json_extra({"type": "album", "album_id": album_id, "publish_time": str(item.get("publishtime") or "")})))
    return albums


def _kugou_search_playlist_sync(self: KugouProvider, keyword: str, limit: int) -> list[Collection]:
    params = urlencode({"keyword": keyword, "platform": "WebFilter", "format": "json", "page": 1, "pagesize": limit, "filter": 0})
    payload = self.get_json(f"http://mobilecdn.kugou.com/api/v3/search/special?{params}", {"User-Agent": UA_MOBILE})
    rows = (((payload or {}).get("data") or {}).get("info") or [])
    playlists: list[Collection] = []
    for item in rows[:limit]:
        playlist_id = str(item.get("specialid") or "").strip()
        if not playlist_id:
            continue
        playlists.append(Collection(playlist_id, "kugou", str(item.get("specialname") or ""), str(item.get("nickname") or ""), str(item.get("imgurl") or "").replace("{size}", "240"), _safe_int(item.get("songcount")), SEARCH_TYPE_PLAYLIST, _safe_int(item.get("playcount")), str(item.get("intro") or ""), f"https://www.kugou.com/yy/special/single/{playlist_id}.html", _json_extra({"type": "playlist", "playlist_id": playlist_id, "publish_time": str(item.get("publishtime") or "")})))
    return playlists


def _kugou_get_album_songs_sync(self: KugouProvider, collection: Collection) -> list[Song]:
    info_payload = self.get_json(f"http://mobilecdn.kugou.com/api/v3/album/info?albumid={collection.id}&version=9108&area_code=1", {"User-Agent": UA_MOBILE})
    info = ((info_payload or {}).get("data") or {})
    params = urlencode({"albumid": collection.id, "page": 1, "pagesize": 300, "version": 9108, "area_code": 1})
    payload = self.get_json(f"http://mobilecdn.kugou.com/api/v3/album/song?{params}", {"User-Agent": UA_MOBILE})
    rows = (((payload or {}).get("data") or {}).get("info") or [])
    fallback_cover = str(info.get("imgurl") or collection.cover or "").replace("{size}", "240")
    fallback_album = str(info.get("albumname") or collection.name or "")
    return [_kugou_song_from_item(item, collection, fallback_cover, fallback_album) for item in rows if _kugou_song_hash(item)]


def _kugou_get_playlist_songs_sync(self: KugouProvider, collection: Collection) -> list[Song]:
    params = urlencode({"specialid": collection.id, "page": 1, "pagesize": 300, "version": 9108, "area_code": 1})
    payload = self.get_json(f"http://mobilecdn.kugou.com/api/v3/special/song?{params}", {"User-Agent": UA_MOBILE})
    rows = (((payload or {}).get("data") or {}).get("info") or [])
    return [_kugou_song_from_item(item, collection, collection.cover, "") for item in rows if _kugou_song_hash(item)]


def _kugou_song_hash(item: dict[str, Any]) -> str:
    trans = item.get("trans_param") or {}
    return _first(item.get("hash"), item.get("FileHash"), item.get("origin_hash"), item.get("SQFileHash"), item.get("sqhash"), item.get("HQFileHash"), item.get("320hash"), item.get("ResFileHash"), item.get("res_hash"), trans.get("ogg_320_hash"), trans.get("ogg_128_hash"))


def _kugou_song_from_item(item: dict[str, Any], collection: Collection | None = None, fallback_cover: str = "", fallback_album: str = "") -> Song:
    trans = item.get("trans_param") or {}
    song_hash = _kugou_song_hash(item)
    file_name = str(item.get("filename") or item.get("FileName") or "")
    name = str(item.get("songname") or item.get("SongName") or "")
    artist = str(item.get("singername") or item.get("SingerName") or "")
    if (not name or not artist) and " - " in file_name:
        artist, name = [part.strip() for part in file_name.split(" - ", 1)]
    name = name or file_name or "Unknown"
    artist = artist or "Unknown"
    album = str(item.get("album_name") or item.get("AlbumName") or item.get("remark") or fallback_album or "")
    cover = str(trans.get("union_cover") or item.get("Image") or fallback_cover or "").replace("{size}", "240")
    duration = _safe_int(item.get("duration") or item.get("Duration"))
    size = _safe_int(item.get("sqfilesize") or item.get("SQFileSize") or item.get("320filesize") or item.get("HQFileSize") or item.get("filesize") or item.get("FileSize"))
    bitrate = int(size * 8 / 1000 / duration) if size and duration else 0
    album_id = str(item.get("album_id") or item.get("AlbumID") or (collection.id if collection and collection.kind == SEARCH_TYPE_ALBUM else ""))
    extra = {
        "hash": song_hash,
        "ogg_320_hash": str(trans.get("ogg_320_hash") or ""),
        "ogg_128_hash": str(trans.get("ogg_128_hash") or ""),
        "sq_hash": str(item.get("SQFileHash") or item.get("sqhash") or ""),
        "file_hash": str(item.get("FileHash") or item.get("origin_hash") or ""),
        "res_hash": str(item.get("ResFileHash") or item.get("res_hash") or ""),
        "mv_hash": str(item.get("MvHash") or item.get("mvhash") or ""),
        "hq_hash": str(item.get("HQFileHash") or item.get("320hash") or ""),
        "album_id": album_id,
        "privilege": str(item.get("Privilege") or item.get("privilege") or ""),
    }
    return Song(song_hash, "kugou", name, artist, album, cover, duration, _json_extra(extra), album_id, size, bitrate, link=f"https://www.kugou.com/song/#hash={song_hash}")

KugouProvider.search_album_sync = _kugou_search_album_sync
KugouProvider.search_playlist_sync = _kugou_search_playlist_sync
KugouProvider.get_album_songs_sync = _kugou_get_album_songs_sync
KugouProvider.get_playlist_songs_sync = _kugou_get_playlist_songs_sync
QQProvider.search_album_sync = _qq_search_album_sync
QQProvider.search_playlist_sync = _qq_search_playlist_sync
QQProvider.get_album_songs_sync = _qq_get_album_songs_sync
QQProvider.get_playlist_songs_sync = _qq_get_playlist_songs_sync
KuwoProvider.search_album_sync = _kuwo_search_album_sync
KuwoProvider.search_playlist_sync = _kuwo_search_playlist_sync
KuwoProvider.get_album_songs_sync = _kuwo_get_album_songs_sync
KuwoProvider.get_playlist_songs_sync = _kuwo_get_playlist_songs_sync
MiguProvider.search_album_sync = _migu_search_album_sync
MiguProvider.search_playlist_sync = _migu_search_playlist_sync
MiguProvider.get_album_songs_sync = _migu_get_album_songs_sync
MiguProvider.get_playlist_songs_sync = _migu_get_playlist_songs_sync

def _normalize_cookies(value: object) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if v is not None}
    raw = str(value or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if v is not None}

def parse_sources(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        parts = [str(item).strip().lower() for item in value if str(item).strip()]
    else:
        raw = str(value or "").replace("，", ",").strip()
        if not raw:
            return []
        parts = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if any(part in {"all", "全部"} for part in parts):
        return ALL_SOURCE_NAMES[:]
    if any(part in {"default", "默认"} for part in parts):
        return DEFAULT_SOURCE_NAMES[:]
    return [part for part in parts if part in ALL_SOURCE_NAMES]


def _http_bytes(url: str, headers: dict[str, str], timeout: float) -> tuple[bytes, dict[str, str]]:
    req_headers = {"User-Agent": UA_PC}
    req_headers.update(headers)
    req = Request(url, headers=req_headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read(), dict(resp.headers.items())
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ProviderError(body.strip() or f"HTTP {exc.code}") from exc
    except URLError as exc:
        raise ProviderError(str(exc.reason)) from exc


def _extra(song: Song) -> dict[str, str]:
    try:
        parsed = json.loads(song.extra or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if v is not None}


def _first(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _safe_int(value: object) -> int:
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0


def _safe_filename(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", value).strip(" ._")
    return value or "music.mp3"


def _ext_from_url_or_type(url: str, content_type: str) -> str:
    path = urlparse(url).path.lower()
    for ext in ("flac", "m4a", "mp3", "ogg", "wav", "wma", "aac", "mp4"):
        if path.endswith("." + ext):
            return ext
    guessed = mimetypes.guess_extension(content_type or "") or ""
    guessed = guessed.lstrip(".").lower()
    if guessed in {"mpeg", "mpga"}:
        return "mp3"
    return guessed or "mp3"


def _similarity(name: str, artist: str, cand_name: str, cand_artist: str) -> float:
    left = set(re.sub(r"\s+", "", (name + artist).lower()))
    right = set(re.sub(r"\s+", "", (cand_name + cand_artist).lower()))
    if not left or not right:
        return 0
    return len(left & right) / len(left | right)
