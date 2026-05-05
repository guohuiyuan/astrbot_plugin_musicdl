from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import random
import re
import struct
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from astrbot.api import logger

from .models import Song

UA_PC = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
UA_MOBILE = "Mozilla/5.0 (iPhone; CPU iPhone OS 9_1 like Mac OS X) AppleWebKit/601.1.46 (KHTML, like Gecko) Version/9.0 Mobile/13B143 Safari/601.1"


class ProviderError(RuntimeError):
    pass


@dataclass
class SourceResponse:
    url: str
    headers: dict[str, str] | None = None
    extension: str = ""
    post_process: object | None = None


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

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        raise NotImplementedError

    def parse_sync(self, link: str) -> Song | None:
        return None

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        raise NotImplementedError

    def get_json(self, url: str, headers: dict[str, str] | None = None, *, no_redirect: bool = False):
        data, _ = self.get_bytes(url, headers=headers, no_redirect=no_redirect)
        return json.loads(data.decode("utf-8", errors="replace"))

    def post_form_json(self, url: str, form: dict, headers: dict[str, str] | None = None):
        raw = urlencode(form).encode("utf-8")
        req_headers = {"User-Agent": UA_PC, "Content-Type": "application/x-www-form-urlencoded"}
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

    def get_bytes(self, url: str, headers: dict[str, str] | None = None, *, no_redirect: bool = False):
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
            raise ProviderError(f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise ProviderError(str(exc.reason)) from exc

MIGU_MAGIC_USER_ID = "15548614588710179085069"
QIANQIAN_APP_ID = "16073360"
QIANQIAN_SECRET = "0b50b02fd0d73a9c4c8c3a781c30845f"
JOOX_DEFAULT_COOKIE = "wmid=142420656; user_type=1; country=id; session_key=2a5d97d05dc8fe238150184eaf3519ad;"
JOOX_X_FORWARDED_FOR = "36.73.34.109"
JAMENDO_X_VERSION = "4gvfvv"

ALL_SOURCE_NAMES = ["netease", "qq", "kugou", "kuwo", "migu", "fivesing", "jamendo", "joox", "qianqian", "soda", "bilibili"]
DEFAULT_SOURCE_NAMES = ["netease", "qq", "kugou", "kuwo", "migu", "qianqian", "soda"]
SOURCE_DESCRIPTIONS = {
    "netease": "网易云音乐",
    "qq": "QQ音乐",
    "kugou": "酷狗音乐",
    "kuwo": "酷我音乐",
    "migu": "咪咕音乐",
    "fivesing": "5sing",
    "jamendo": "Jamendo (CC)",
    "joox": "JOOX",
    "qianqian": "千千音乐",
    "soda": "汽水音乐",
    "bilibili": "Bilibili",
}


def source_description(source: str) -> str:
    return SOURCE_DESCRIPTIONS.get(source, source)


class NeteaseProvider(MusicProvider):
    source = "netease"

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        payload = self.post_form_json(
            "https://music.163.com/api/search/get/web?csrf_token=",
            {"s": keyword, "type": 1, "offset": 0, "limit": limit},
            {"Referer": "https://music.163.com/"},
        )
        rows = (((payload or {}).get("result") or {}).get("songs") or [])
        songs: list[Song] = []
        for item in rows[:limit]:
            song_id = str(item.get("id") or "").strip()
            if not song_id:
                continue
            artists = item.get("artists") or item.get("ar") or []
            artist = "、".join(str(a.get("name") or "").strip() for a in artists if a.get("name"))
            album_obj = item.get("album") or item.get("al") or {}
            cover = str(album_obj.get("picUrl") or "")
            duration = int((item.get("duration") or item.get("dt") or 0) / 1000)
            songs.append(Song(song_id, self.source, str(item.get("name") or "Unknown"), artist or "Unknown", str(album_obj.get("name") or ""), cover, duration, _extra_json({"song_id": song_id})))
        return songs

    def parse_sync(self, link: str) -> Song | None:
        match = re.search(r"[?&]id=(\d+)|song\?id=(\d+)", link)
        song_id = _first(*(match.groups() if match else []))
        if not song_id:
            return None
        return Song(song_id, self.source, f"Netease_{song_id}", "Unknown", extra=_extra_json({"song_id": song_id}))

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        song_id = _extra(song).get("song_id") or song.id
        return SourceResponse(f"https://music.163.com/song/media/outer/url?id={song_id}.mp3", {"Referer": "https://music.163.com/", "User-Agent": UA_PC}, "mp3")


class FivesingProvider(MusicProvider):
    source = "fivesing"

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        params = urlencode({"keyword": keyword, "sort": 1, "page": 1, "filter": 0, "type": 0})
        payload = self.get_json(f"http://search.5sing.kugou.com/home/json?{params}", {"User-Agent": UA_PC})
        rows = (payload or {}).get("list") or []
        songs: list[Song] = []
        for item in rows[:limit]:
            song_id = str(item.get("songId") or "").strip()
            song_type = str(item.get("typeEname") or "").strip()
            if not song_id or not song_type:
                continue
            name = _clean_html(str(item.get("songName") or "Unknown"))
            artist = _clean_html(str(item.get("singer") or "Unknown"))
            songs.append(Song(f"{song_id}|{song_type}", self.source, name, artist, duration=0, extra=_extra_json({"songid": song_id, "songtype": song_type})))
        return songs

    def parse_sync(self, link: str) -> Song | None:
        match = re.search(r"5sing\.kugou\.com/(\w+)/(\d+)\.html", link)
        if not match:
            return None
        song_type, song_id = match.group(1), match.group(2)
        return Song(f"{song_id}|{song_type}", self.source, f"5sing_{song_type}_{song_id}", "Unknown", extra=_extra_json({"songid": song_id, "songtype": song_type}))

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        extra = _extra(song)
        song_id = extra.get("songid")
        song_type = extra.get("songtype")
        if not song_id or not song_type:
            parts = song.id.split("|")
            if len(parts) == 2:
                song_id, song_type = parts
        if not song_id or not song_type:
            raise ProviderError("5sing 歌曲 ID 无效")
        params = urlencode({"songid": song_id, "songtype": song_type})
        payload = self.get_json(f"http://mobileapi.5sing.kugou.com/song/getSongUrl?{params}", {"User-Agent": UA_PC})
        data = (payload or {}).get("data") or {}
        url = _first(data.get("squrl"), data.get("squrl_backup"), data.get("hqurl"), data.get("hqurl_backup"), data.get("lqurl"), data.get("lqurl_backup"))
        if not url:
            raise ProviderError("5sing 未返回可用下载地址")
        return SourceResponse(url, {"User-Agent": UA_PC})


class JamendoProvider(MusicProvider):
    source = "jamendo"

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        params = urlencode({"query": keyword, "type": "track", "limit": limit, "identities": "www"})
        rows = self._api_get(f"https://www.jamendo.com/api/search?{params}", "/api/search") or []
        songs: list[Song] = []
        for item in rows[:limit]:
            song = self._song_from_track(item)
            if song:
                songs.append(song)
        return songs

    def parse_sync(self, link: str) -> Song | None:
        match = re.search(r"jamendo\.com/track/(\d+)", link)
        if not match:
            return None
        return self._get_track(match.group(1))

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        extra = _extra(song)
        url = extra.get("url")
        ext = extra.get("ext") or "mp3"
        if not url:
            fetched = self._get_track(extra.get("track_id") or song.id)
            if fetched:
                fetched_extra = _extra(fetched)
                url = fetched_extra.get("url")
                ext = fetched_extra.get("ext") or ext
        if not url:
            raise ProviderError("Jamendo 未返回可用下载地址")
        return SourceResponse(url, {"User-Agent": UA_PC, "Referer": "https://www.jamendo.com/search?q=musicdl"}, ext)

    def _api_get(self, url: str, path: str) -> Any:
        headers = {"User-Agent": UA_PC, "Referer": "https://www.jamendo.com/search?q=musicdl", "x-jam-call": _jam_call(path), "x-jam-version": JAMENDO_X_VERSION, "x-requested-with": "XMLHttpRequest"}
        return self.get_json(url, headers)

    def _get_track(self, track_id: str) -> Song | None:
        params = urlencode({"id": track_id})
        rows = self._api_get(f"https://www.jamendo.com/api/tracks?{params}", "/api/tracks") or []
        return self._song_from_track(rows[0]) if rows else None

    def _song_from_track(self, item: dict[str, Any]) -> Song | None:
        track_id = str(item.get("id") or "").strip()
        if not track_id:
            return None
        streams = item.get("download") or item.get("stream") or {}
        url, ext = _pick_stream(streams)
        if not url:
            return None
        artist = ((item.get("artist") or {}).get("name") or "Unknown")
        album = ((item.get("album") or {}).get("name") or "")
        cover = ((((item.get("cover") or {}).get("big") or {}).get("size300")) or "")
        extra = {"track_id": track_id, "url": url, "ext": ext}
        return Song(track_id, self.source, str(item.get("name") or "Unknown"), str(artist), str(album), str(cover), int(item.get("duration") or 0), _extra_json(extra))


class JooxProvider(MusicProvider):
    source = "joox"

    def __init__(self, cookie: str = "", timeout: float = 30) -> None:
        super().__init__(cookie or JOOX_DEFAULT_COOKIE, timeout)

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        params = urlencode({"country": "sg", "lang": "zh_cn", "keyword": keyword})
        payload = self.get_json(f"https://cache.api.joox.com/openjoox/v3/search?{params}", self._headers())
        songs: list[Song] = []
        for section in (payload or {}).get("section_list") or []:
            for item in section.get("item_list") or []:
                for song_item in item.get("song") or []:
                    info = song_item.get("song_info") or {}
                    song_id = str(info.get("id") or "").strip()
                    if not song_id:
                        continue
                    artists = " / ".join(str(a.get("name") or "").strip() for a in info.get("artist_list") or [] if a.get("name"))
                    cover = _pick_image(info.get("images") or [])
                    songs.append(Song(song_id, self.source, str(info.get("name") or "Unknown"), artists or "Unknown", str(info.get("album_name") or ""), cover, int(info.get("play_duration") or 0), _extra_json({"songid": song_id})))
                    if len(songs) >= limit:
                        return songs
        return songs

    def parse_sync(self, link: str) -> Song | None:
        match = re.search(r"joox\.com/.*/single/([A-Za-z0-9+_-]+)", link)
        song_id = match.group(1) if match else (link.strip() if "/" not in link and len(link.strip()) > 8 else "")
        return self._fetch_song_info(song_id) if song_id else None

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        song_id = _extra(song).get("songid") or song.id
        info = self._fetch_song_info(song_id)
        if not info:
            raise ProviderError("JOOX 未返回可用下载地址")
        url = _extra(info).get("url")
        if not url:
            raise ProviderError("JOOX 未返回可用下载地址")
        return SourceResponse(url, self._headers())

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": UA_PC, "X-Forwarded-For": JOOX_X_FORWARDED_FOR}

    def _fetch_song_info(self, song_id: str) -> Song | None:
        params = urlencode({"songid": song_id, "lang": "zh_cn", "country": "sg"})
        data, _ = self.get_bytes(f"https://api.joox.com/web-fcgi-bin/web_get_songinfo?{params}", self._headers())
        text = data.decode("utf-8", errors="replace").strip()
        if text.startswith("MusicInfoCallback("):
            text = text[len("MusicInfoCallback(") :]
            if text.endswith(")"):
                text = text[:-1]
        payload = json.loads(text)
        kbps = payload.get("kbps_map") or {}
        if isinstance(kbps, str):
            try:
                kbps = json.loads(kbps)
            except json.JSONDecodeError:
                kbps = {}
        url = ""
        for key, candidate in (("320", payload.get("r320Url")), ("192", payload.get("r192Url")), ("128", payload.get("mp3Url")), ("96", payload.get("m4aUrl"))):
            value = kbps.get(key) if isinstance(kbps, dict) else None
            if candidate and str(value or "0") not in {"", "0"}:
                url = candidate
                break
        if not url:
            url = _first(payload.get("r320Url"), payload.get("r192Url"), payload.get("mp3Url"), payload.get("m4aUrl"))
        if not url:
            return None
        return Song(song_id, self.source, str(payload.get("msong") or "Unknown"), str(payload.get("msinger") or "Unknown"), str(payload.get("malbum") or ""), str(payload.get("img") or ""), int(payload.get("minterval") or 0), _extra_json({"songid": song_id, "url": url}))


class QianqianProvider(MusicProvider):
    source = "qianqian"

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        params = _qianqian_signed({"word": keyword, "type": "1", "pageNo": "1", "pageSize": str(limit), "appid": QIANQIAN_APP_ID})
        payload = self.get_json(f"https://music.91q.com/v1/search?{urlencode(params)}", {"User-Agent": UA_PC, "Referer": "https://music.91q.com/player"})
        rows = (((payload or {}).get("data") or {}).get("typeTrack") or [])
        songs: list[Song] = []
        for item in rows[:limit]:
            if int(item.get("isVip") or 0) != 0:
                continue
            tsid = str(item.get("TSID") or "").strip()
            if not tsid:
                continue
            artist = _join_qianqian_artists(item.get("artist") or [])
            songs.append(Song(tsid, self.source, str(item.get("title") or "Unknown"), artist or "Unknown", str(item.get("albumTitle") or ""), str(item.get("pic") or ""), int(item.get("duration") or 0), _extra_json({"tsid": tsid})))
        return songs

    def parse_sync(self, link: str) -> Song | None:
        match = re.search(r"music\.91q\.com/song/(\w+)", link)
        tsid = match.group(1) if match else ""
        if not tsid:
            return None
        return Song(tsid, self.source, f"Qianqian_{tsid}", "Unknown", extra=_extra_json({"tsid": tsid}))

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        tsid = _extra(song).get("tsid") or song.id
        for rate in ("3000", "320", "128", "64"):
            params = _qianqian_signed({"TSID": tsid, "appid": QIANQIAN_APP_ID, "rate": rate})
            try:
                payload = self.get_json(f"https://music.91q.com/v1/song/tracklink?{urlencode(params)}", {"User-Agent": UA_PC, "Referer": "https://music.91q.com/player"})
            except Exception:
                continue
            data = (payload or {}).get("data") or {}
            url = _first(data.get("path"), ((data.get("trail_audio_info") or {}).get("path")))
            if url:
                return SourceResponse(url, {"User-Agent": UA_PC, "Referer": "https://music.91q.com/player"})
        raise ProviderError("千千音乐未返回可用下载地址")


class SodaProvider(MusicProvider):
    source = "soda"

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        params = urlencode({"q": keyword, "cursor": 0, "search_method": "input", "aid": "386088", "device_platform": "web", "channel": "pc_web"})
        payload = self.get_json(f"https://api.qishui.com/luna/pc/search/track?{params}", {"User-Agent": UA_PC})
        groups = (payload or {}).get("result_groups") or []
        rows = (groups[0].get("data") if groups else []) or []
        songs: list[Song] = []
        for item in rows[:limit]:
            track = ((item.get("entity") or {}).get("track") or {})
            track_id = str(track.get("id") or "").strip()
            if not track_id:
                continue
            artists = "、".join(str(a.get("name") or "").strip() for a in track.get("artists") or [] if a.get("name"))
            album = track.get("album") or {}
            cover = _soda_image(album.get("url_cover") or {})
            songs.append(Song(track_id, self.source, str(track.get("name") or "Unknown"), artists or "Unknown", str(album.get("name") or ""), cover, int((track.get("duration") or 0) / 1000), _extra_json({"track_id": track_id})))
        return songs

    def parse_sync(self, link: str) -> Song | None:
        match = re.search(r"track/(\d+)", link)
        track_id = match.group(1) if match else ""
        return Song(track_id, self.source, f"Soda_{track_id}", "Unknown", extra=_extra_json({"track_id": track_id})) if track_id else None

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        track_id = _extra(song).get("track_id") or song.id
        params = urlencode({"track_id": track_id, "media_type": "track", "aid": "386088", "device_platform": "web", "channel": "pc_web"})
        v2 = self.get_json(f"https://api.qishui.com/luna/pc/track_v2?{params}", {"User-Agent": UA_PC})
        player_url = (((v2 or {}).get("track_player") or {}).get("url_player_info") or "")
        if not player_url:
            raise ProviderError("汽水音乐未返回播放器信息")
        info = self.get_json(player_url, {"User-Agent": UA_PC})
        play_list = ((((info or {}).get("Result") or {}).get("Data") or {}).get("PlayInfoList") or [])
        if not play_list:
            raise ProviderError("汽水音乐未返回音频流")
        play_list.sort(key=lambda item: (int(item.get("Size") or 0), int(item.get("Bitrate") or 0)), reverse=True)
        best = play_list[0]
        url = _first(best.get("MainPlayUrl"), best.get("BackupPlayUrl"))
        play_auth = str(best.get("PlayAuth") or "")
        ext = str(best.get("Format") or "m4a").lower()
        if not url:
            raise ProviderError("汽水音乐未返回可用下载地址")
        processor = (lambda data, auth=play_auth: _decrypt_soda_audio(data, auth)) if play_auth else None
        return SourceResponse(url, {"User-Agent": UA_PC}, ext or "m4a", processor)


class BilibiliProvider(MusicProvider):
    source = "bilibili"

    def search_sync(self, keyword: str, limit: int) -> list[Song]:
        params = urlencode({"search_type": "video", "keyword": keyword, "page": 1, "page_size": limit})
        payload = self.get_json(f"https://api.bilibili.com/x/web-interface/search/type?{params}", {"User-Agent": UA_PC, "Referer": "https://www.bilibili.com/"})
        rows = (((payload or {}).get("data") or {}).get("result") or [])
        songs: list[Song] = []
        for item in rows[:limit]:
            bvid = str(item.get("bvid") or "").strip()
            if not bvid:
                continue
            try:
                view = self._fetch_view(bvid)
                pages = ((view.get("data") or {}).get("pages") or [])
                if not pages:
                    continue
                page = pages[0]
                cid = str(page.get("cid") or "").strip()
                if not cid:
                    continue
                root_title = _clean_html(str(item.get("title") or (view.get("data") or {}).get("title") or "Unknown"))
                part = str(page.get("part") or "").strip()
                title = root_title if not part or part == root_title else f"{root_title} - {part}"
                cover = _normalize_cover(str(item.get("pic") or (view.get("data") or {}).get("pic") or ""))
                songs.append(Song(f"{bvid}|{cid}", self.source, title, str(item.get("author") or ((view.get("data") or {}).get("owner") or {}).get("name") or "Unknown"), bvid, cover, int(page.get("duration") or 0), _extra_json({"bvid": bvid, "cid": cid})))
            except Exception as exc:
                logger.debug(f"[MusicDL] bilibili detail skipped: {exc}")
        return songs

    def parse_sync(self, link: str) -> Song | None:
        match = re.search(r"(BV[0-9A-Za-z]+)", link)
        if not match:
            return None
        bvid = match.group(1)
        view = self._fetch_view(bvid)
        data = view.get("data") or {}
        pages = data.get("pages") or []
        if not pages:
            return None
        page_no_match = re.search(r"[?&]p=(\d+)", link)
        page_no = max(1, int(page_no_match.group(1))) if page_no_match else 1
        page = pages[min(page_no - 1, len(pages) - 1)]
        cid = str(page.get("cid") or "")
        part = str(page.get("part") or "")
        title = str(data.get("title") or "Unknown")
        name = title if not part or part == title else f"{title} - {part}"
        return Song(f"{bvid}|{cid}", self.source, name, str(((data.get("owner") or {}).get("name")) or "Unknown"), bvid, _normalize_cover(str(data.get("pic") or "")), int(page.get("duration") or 0), _extra_json({"bvid": bvid, "cid": cid}))

    def get_download_url_sync(self, song: Song) -> SourceResponse:
        extra = _extra(song)
        bvid = extra.get("bvid")
        cid = extra.get("cid")
        if not bvid or not cid:
            parts = song.id.split("|")
            if len(parts) == 2:
                bvid, cid = parts
        if not bvid or not cid:
            raise ProviderError("Bilibili 视频 ID 无效")
        params = urlencode({"fnval": 80, "qn": 127, "bvid": bvid, "cid": cid})
        payload = self.get_json(f"https://api.bilibili.com/x/player/playurl?{params}", {"User-Agent": UA_PC, "Referer": "https://www.bilibili.com/"})
        data = (payload or {}).get("data") or {}
        dash = data.get("dash") or {}
        candidates: list[tuple[int, str]] = []
        flac = ((dash.get("flac") or {}).get("audio") or {})
        if flac.get("baseUrl"):
            candidates.append((int(flac.get("id") or 0), flac.get("baseUrl")))
        for item in ((dash.get("dolby") or {}).get("audio") or []):
            if item.get("baseUrl"):
                candidates.append((int(item.get("id") or 0), item.get("baseUrl")))
        for item in dash.get("audio") or []:
            if item.get("baseUrl"):
                candidates.append((int(item.get("id") or 0), item.get("baseUrl")))
        if candidates:
            candidates.sort(reverse=True)
            return SourceResponse(candidates[0][1], {"User-Agent": UA_PC, "Referer": "https://www.bilibili.com/"}, "m4a")
        durl = data.get("durl") or []
        if durl and durl[0].get("url"):
            return SourceResponse(durl[0]["url"], {"User-Agent": UA_PC, "Referer": "https://www.bilibili.com/"}, "mp4")
        raise ProviderError("Bilibili 未返回可用音频流")

    def _fetch_view(self, bvid: str) -> dict[str, Any]:
        return self.get_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", {"User-Agent": UA_PC, "Referer": "https://www.bilibili.com/"})


def _extra(song: Song) -> dict[str, str]:
    try:
        parsed = json.loads(song.extra or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if v is not None}


def _extra_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _first(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def _normalize_cover(value: str) -> str:
    value = value.strip()
    if value.startswith("//"):
        return "https:" + value
    return value


def _pick_image(images: list[dict[str, Any]]) -> str:
    for item in images:
        if int(item.get("width") or 0) == 300 and item.get("url"):
            return str(item["url"])
    return str(images[0].get("url") or "") if images else ""


def _pick_stream(streams: dict[str, str]) -> tuple[str, str]:
    for key in ("flac", "mp33", "mp32", "mp3", "ogg"):
        url = streams.get(key)
        if url:
            return url, "mp3" if key in {"mp33", "mp32"} else key
    return "", ""


def _jam_call(path: str) -> str:
    rand_str = str(random.random())
    digest = hashlib.sha1((path + rand_str).encode("utf-8")).hexdigest()
    return f"${digest}*{rand_str}~"


def _qianqian_signed(params: dict[str, Any]) -> dict[str, str]:
    values = {str(k): str(v) for k, v in params.items()}
    values["timestamp"] = str(int(time.time()))
    raw = "&".join(f"{key}={values[key]}" for key in sorted(values)) + QIANQIAN_SECRET
    values["sign"] = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return values


def _join_qianqian_artists(artists: list[dict[str, Any]]) -> str:
    picked = []
    for item in artists:
        name = str(item.get("name") or "").strip()
        if name and int(item.get("artistType") or 0) == 38 and name not in picked:
            picked.append(name)
    if not picked:
        for item in artists:
            name = str(item.get("name") or "").strip()
            if name and name not in picked:
                picked.append(name)
    return "、".join(picked)


def _soda_image(img: dict[str, Any]) -> str:
    urls = img.get("urls") or []
    if not urls:
        return ""
    cover = str(urls[0] or "").strip()
    uri = str(img.get("uri") or "").strip()
    if uri and uri not in cover:
        cover += uri
    if cover and "~" not in cover:
        cover += "~c5_375x375.jpg"
    return cover


def _decrypt_soda_audio(data: bytes, play_auth: str) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except Exception as exc:
        raise ProviderError("汽水音乐解密需要 cryptography 库") from exc
    key = bytes.fromhex(_soda_extract_key(play_auth))
    moov = _find_box(data, "moov", 0, len(data))
    try:
        stbl = _find_box(data, "stbl", moov[0], moov[0] + moov[1])
    except ProviderError:
        trak = _find_box(data, "trak", moov[0] + 8, moov[0] + moov[1])
        mdia = _find_box(data, "mdia", trak[0] + 8, trak[0] + trak[1])
        minf = _find_box(data, "minf", mdia[0] + 8, mdia[0] + mdia[1])
        stbl = _find_box(data, "stbl", minf[0] + 8, minf[0] + minf[1])
    stsz = _find_box(data, "stsz", stbl[0] + 8, stbl[0] + stbl[1])
    sizes = _parse_stsz(stsz[2])
    try:
        senc = _find_box(data, "senc", moov[0] + 8, moov[0] + moov[1])
    except ProviderError:
        senc = _find_box(data, "senc", stbl[0] + 8, stbl[0] + stbl[1])
    ivs = _parse_senc(senc[2])
    mdat = _find_box(data, "mdat", 0, len(data))
    out = bytearray(data)
    read_ptr = mdat[0] + 8
    chunks = bytearray()
    for i, size in enumerate(sizes):
        if read_ptr + size > len(out):
            break
        chunk = bytes(out[read_ptr : read_ptr + size])
        if i < len(ivs):
            iv = ivs[i]
            if len(iv) < 16:
                iv = iv + b"\x00" * (16 - len(iv))
            decryptor = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()
            chunks.extend(decryptor.update(chunk) + decryptor.finalize())
        else:
            chunks.extend(chunk)
        read_ptr += size
    if len(chunks) != mdat[1] - 8:
        raise ProviderError("汽水音乐解密失败：decrypted size mismatch")
    out[mdat[0] + 8 : mdat[0] + mdat[1]] = chunks
    try:
        stsd = _find_box(bytes(out), "stsd", stbl[0] + 8, stbl[0] + stbl[1])
        stsd_data = out[stsd[0] : stsd[0] + stsd[1]]
        idx = bytes(stsd_data).find(b"enca")
        if idx >= 0:
            stsd_data[idx : idx + 4] = b"mp4a"
    except ProviderError:
        pass
    return bytes(out)


def _find_box(data: bytes, box_type: str, start: int, end: int) -> tuple[int, int, bytes]:
    end = min(end, len(data))
    pos = max(0, start)
    target = box_type.encode("ascii")
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        if size < 8 or pos + size > len(data):
            break
        if data[pos + 4 : pos + 8] == target:
            return pos, size, data[pos + 8 : pos + size]
        pos += size
    raise ProviderError(f"{box_type} box not found")


def _parse_stsz(data: bytes) -> list[int]:
    if len(data) < 12:
        return []
    fixed = struct.unpack(">I", data[4:8])[0]
    count = struct.unpack(">I", data[8:12])[0]
    if fixed:
        return [fixed] * count
    sizes = []
    for i in range(count):
        start = 12 + i * 4
        if start + 4 <= len(data):
            sizes.append(struct.unpack(">I", data[start : start + 4])[0])
    return sizes


def _parse_senc(data: bytes) -> list[bytes]:
    if len(data) < 8:
        return []
    flags = struct.unpack(">I", data[:4])[0] & 0x00FFFFFF
    count = struct.unpack(">I", data[4:8])[0]
    ptr = 8
    has_sub = bool(flags & 0x02)
    ivs = []
    for _ in range(count):
        if ptr + 8 > len(data):
            break
        ivs.append(data[ptr : ptr + 8])
        ptr += 8
        if has_sub:
            if ptr + 2 > len(data):
                break
            sub_count = struct.unpack(">H", data[ptr : ptr + 2])[0]
            ptr += 2 + sub_count * 6
    return ivs


def _soda_extract_key(play_auth: str) -> str:
    raw = base64.b64decode(play_auth)
    if len(raw) < 3:
        raise ProviderError("汽水音乐解密失败：auth data too short")
    padding_len = (raw[0] ^ raw[1] ^ raw[2]) - 48
    if len(raw) < padding_len + 2:
        raise ProviderError("汽水音乐解密失败：invalid padding length")
    inner_input = raw[1 : len(raw) - padding_len]
    tmp = _decrypt_spade_inner(inner_input)
    if not tmp:
        raise ProviderError("汽水音乐解密失败")
    skip = _decode_base36(tmp[0])
    end = 1 + (len(raw) - padding_len - 2) - skip
    if end > len(tmp) or end < 1:
        raise ProviderError("汽水音乐解密失败：index out of bounds")
    return tmp[1:end].decode("utf-8", errors="replace")


def _decrypt_spade_inner(key_bytes: bytes) -> bytes:
    buff = bytes([0xFA, 0x55]) + key_bytes
    result = bytearray(len(key_bytes))
    for i, byte in enumerate(key_bytes):
        value = int(byte ^ buff[i]) - int(i).bit_count() - 21
        while value < 0:
            value += 255
        result[i] = value & 0xFF
    return bytes(result)


def _decode_base36(value: int) -> int:
    if 48 <= value <= 57:
        return value - 48
    if 97 <= value <= 122:
        return value - 87
    if 65 <= value <= 90:
        return value - 55
    return 0xFF
