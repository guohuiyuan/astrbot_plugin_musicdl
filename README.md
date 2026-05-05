# astrbot_plugin_musicdl

Pure Python multi-source music search and download plugin for AstrBot. It implements search, result selection, source switching, download, and audio-message sending internally. It does not require running the `go-music-dl` binary, CLI, or Web service.

## Features

- Concurrent search across multiple music platforms.
- Search types aligned with `go-music-dl`: `song`, `playlist`, and `album`.
- Reply with result numbers to download one or more songs.
- Reply with collection numbers to expand playlists or albums, then select songs from the expanded list.
- Use `r1` or `switch1`-style source switching for song results.
- Parse direct song links for supported platforms.
- Uses `go-music-dl`-style configuration keys: `downloadToLocal`, `downloadDir`, `cliPageSize`, `downloadConcurrency`, and `cookies`.
- Supports Soda Music encrypted audio decryption through the plugin `requirements.txt` dependency.

## Installation

Place this plugin directory under AstrBot's plugin directory and enable it.

The plugin includes `requirements.txt`:

```text
cryptography>=44.0.3
```

AstrBot should install missing plugin dependencies during plugin install/load. `cryptography` is used for Soda Music audio decryption.

## Usage

```text
/music jay chou
/music -t song rice field
/music -t playlist jay chou
/music -t album fantasy
/music -s qq,kuwo -t song rice field
/music -s all -t album piano
/music -s default sunny day
/music https://y.qq.com/n/ryqq/songDetail/xxxx
/music_sources
/music_cancel
```

Reply after search:

```text
1
1 2
r1
cancel
```

Selection behavior:

- `song`: reply with result numbers to download and send audio.
- `playlist` / `album`: reply with collection numbers to expand the collection into songs, then reply with song numbers to download.
- `r1` / source switching only applies to song result lists.

## Source selection

Default sources follow the `go-music-dl` runtime default behavior:

```text
netease,qq,kugou,kuwo,migu,qianqian,soda
```

The following sources are not searched by default, but can be enabled with `-s all` or explicit `-s` values:

```text
fivesing,jamendo,joox,bilibili
```

Examples:

```text
/music -s bilibili -t playlist wind
/music -s jamendo -t album piano
/music -s fivesing -t playlist original
```

The default source list is runtime behavior and is not stored as a separate plugin config item. Use `-s` to select sources per command.

## Source capabilities

`/music_sources` shows whether each source is default and whether it supports song, playlist, and album search.

| Source | Song | Playlist | Album | Default |
| --- | --- | --- | --- | --- |
| `netease` NetEase Cloud Music | Yes | Yes | Yes | Yes |
| `qq` QQ Music | Yes | Yes | Yes | Yes |
| `kugou` Kugou Music | Yes | Yes | Yes | Yes |
| `kuwo` Kuwo Music | Yes | Yes | Yes | Yes |
| `migu` Migu Music | Yes | Yes | Yes | Yes |
| `fivesing` 5sing | Yes | Yes | No | No |
| `jamendo` Jamendo (CC) | Yes | Yes | Yes | No |
| `joox` JOOX | Yes | Yes | Yes | No |
| `qianqian` Qianqian Music | Yes | Yes | Yes | Yes |
| `soda` Soda Music | Yes | Yes | Yes | Yes |
| `bilibili` Bilibili | Yes | Yes | No | No |

## Configuration

The plugin uses existing `go-music-dl` parameter names and avoids adding unrelated custom config keys.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `downloadToLocal` | bool | `false` | Matches `go-music-dl` `downloadToLocal`. When disabled, audio is sent as an AstrBot message and the temporary file is removed after sending. When enabled, files are kept under `downloadDir`. |
| `downloadDir` | string | `data/downloads` | Matches `go-music-dl` `downloadDir`. Audio download directory. |
| `cliPageSize` | int | `50` | Matches `go-music-dl` `cliPageSize`. Max displayed search results and expanded collection songs. |
| `downloadConcurrency` | int | `3` | Matches `go-music-dl` `downloadConcurrency`. Concurrent downloads when selecting multiple songs. Clamped to `1-5`. |
| `cookies` | object | `{}` | Matches `go-music-dl` cookie management. Keys: `netease`,`qq`,`kugou`,`kuwo`,`migu`,`fivesing`,`jamendo`,`joox`,`qianqian`,`soda`,`bilibili`. |

`webPageSize`, `embedDownload`, `vgChangeCover`, `vgChangeAudio`, `vgChangeLyric`, and `vgExportVideo` belong to `go-music-dl` Web/video generation flows and are not used by this AstrBot song-request plugin.

Cookie example:

```json
{
  "qq": "uin=...; qm_keyst=...;",
  "netease": "MUSIC_U=...;",
  "bilibili": "SESSDATA=...;"
}
```

## Notes

- Some tracks may fail because of copyright, membership, region restrictions, platform risk controls, or API changes.
- Platform cookies can improve search/download availability for some sources.
- This plugin only handles song requests and audio sending inside AstrBot. It does not provide copyrighted music content.

## Credits

The multi-platform design, source names, default source behavior, and part of the API behavior are inspired by and adapted from `go-music-dl` and its `music-lib` implementation.

This plugin is a pure Python AstrBot implementation. It does not call the `go-music-dl` binary, Web service, or CLI at runtime.
