# astrbot_plugin_musicdl

一款适配 AstrBot 的纯 Python 聚合点歌插件。插件可自主完成音乐搜索、结果筛选、音源切换、音频下载与消息发送全流程。内置网易云音乐、QQ 音乐、酷狗音乐、B 站、汽水音乐等十余主流平台，支持多渠道并发搜索与无损音质解析。

## 功能

- 支持多个音乐平台并发搜索。
- 支持三种搜索类型：`song`、`playlist`、`album`。
- 不指定来源时，按搜索类型使用默认多渠道搜索，并按来源轮询合并结果，避免首页被单一平台占满。
- 搜索结果列表显示来源、大小、码率、渠道和状态（有效 / 无效）。
- 支持搜索命令传入页码和每页数量，也支持在搜索会话中回复指令翻页。
- 支持回复编号下载单首或多首歌曲。
- 支持先选择歌单 / 专辑，再展开歌曲列表继续点歌。
- 支持 `r1` / `换源1` 为单曲搜索结果换源。
- 支持 `/music 链接` 或直接发送受支持的音乐链接来点歌。
- 支持以语音、群文件或两者同时发送下载后的音频。
- 支持点歌后以合并转发发送歌曲详细信息，平台不支持时自动降级为普通文本。
- 支持汽水音乐加密音频解密，依赖由插件 `requirements.txt` 自动安装。

## 安装

将本插件目录放入 AstrBot 的插件目录后启用即可。

插件包含 `requirements.txt`：

```text
cryptography>=44.0.3
```

AstrBot 会在插件加载 / 安装流程中自动安装缺失依赖。`cryptography` 主要用于汽水音乐音频解密。

## 命令

| 命令               | 说明                                             |
| ------------------ | ------------------------------------------------ |
| `/music`         | 显示点歌帮助。                                   |
| `/music_help`    | 显示点歌帮助。别名：`点歌帮助`、`搜歌帮助`。 |
| `/music 关键词`  | 搜索单曲。不指定来源时使用默认多渠道。           |
| `/点歌 关键词`   | `/music` 的中文别名。                          |
| `/搜歌 关键词`   | `/music` 的中文别名。                          |
| `/music_sources` | 查看所有来源能力和默认源标记。别名：`点歌源`。 |
| `/music_cancel`  | 取消当前点歌选择会话。别名：`取消点歌`。       |

## 用法

```text
/music 周杰伦
/music -t song 稻香
/music -t playlist 周杰伦
/music -t album 范特西
/music -s qq,kuwo -t song 稻香
/music -s all -t album piano
/music -s default 晴天
/music -p 2 -ps 20 周杰伦
/music -s all -t album -p 2 -ps 10 周杰伦
/music https://y.qq.com/n/ryqq/songDetail/xxxx
/music_sources
/music_cancel
```

也可以直接发送受支持的音乐链接来点歌：

```text
https://y.qq.com/n/ryqq/songDetail/xxxx
这首歌听一下 https://music.163.com/song?id=xxxx
```

### 参数

| 参数                                                                            | 说明                                                        | 示例                       |
| ------------------------------------------------------------------------------- | ----------------------------------------------------------- | -------------------------- |
| `-s`                                                                          | 指定来源。多个来源用逗号分隔。支持 `all` 和 `default`。 | `/music -s qq,kuwo 稻香` |
| `-t` / `-type`                                                              | 搜索类型。可选：`song`、`playlist`、`album`。         | `/music -t album 范特西` |
| `-p` / `--page` / `page` / `页`                                         | 搜索时直接跳到指定页。                                      | `/music -p 2 周杰伦`     |
| `-ps` / `--page-size` / `--pagesize` / `pagesize` / `size` / `每页` | 指定每页展示数量，范围 `1-100`。                          | `/music -ps 20 周杰伦`   |

### 搜索后回复

搜索后回复编号下载或展开：

```text
1
1 2
a
all
全部
r1
换源1
n
下一页
p
上一页
page 2
第 2 页
取消
```

选择规则：

- `song` 搜索结果：回复编号会下载并发送音频。
- `playlist` / `album` 搜索结果：回复编号会先展开歌单 / 专辑歌曲，再回复歌曲编号下载。
- `r1` / `换源1` 仅用于单曲结果列表，表示给第 1 首歌换源。
- `n` / `下一页` 会翻到下一页；`p` / `上一页` 会翻到上一页；`page 2` / `第 2 页` 会跳到指定页。

## 结果列表

单曲列表不再显示选择框，直接使用 `ID` 回复下载。列表顶部会显示完整的搜索渠道，默认单曲搜索会包含 `qianqian`。

单曲表格字段包括：

```text
ID, 歌曲状态, 歌名, 歌手, 专辑, 时长, 大小, 码率, 渠道
```

- `搜索渠道` 是完整的默认或指定渠道，例如 `netease`, `qq`, `kugou`, `kuwo`, `migu`, `qianqian`, `soda`。
- `渠道` 是单首歌曲的实际来源，例如 `netease`、`qq`、`kuwo`。
- `歌曲状态` 会显式标注 `✅ 有效` 或 `❌ 无效`。无效通常表示探测或下载链接不可用。
- `大小` 和 `码率` 由探针并发获取；获取不到时显示 `-`。

## 来源选择

默认搜索源参考 `go-music-dl` 的默认行为。可以通过 `-s` 临时指定来源，多个来源用逗号分隔，也可以使用 `all` 或 `default`。

### 按搜索类型默认渠道

| 搜索类型 | 已调通默认渠道 |
| --- | --- |
| `song` 单曲 | `netease` 网易云音乐、`qq` QQ音乐、`kugou` 酷狗音乐、`kuwo` 酷我音乐、`migu` 咪咕音乐、`qianqian` 千千音乐、`soda` 汽水音乐 |
| `album` 专辑 | `netease` 网易云音乐、`qq` QQ音乐、`kugou` 酷狗音乐、`kuwo` 酷我音乐、`migu` 咪咕音乐、`jamendo` Jamendo、`joox` JOOX、`qianqian` 千千音乐、`soda` 汽水音乐 |
| `playlist` 歌单 | `netease` 网易云音乐、`qq` QQ音乐、`kugou` 酷狗音乐、`kuwo` 酷我音乐、`migu` 咪咕音乐、`fivesing` 5sing、`joox` JOOX、`qianqian` 千千音乐、`soda` 汽水音乐、`bilibili` Bilibili |

示例：

```text
/music -s bilibili -t playlist 起风了
/music -s jamendo -t album piano
/music -s fivesing -t playlist 原创歌曲
/music -s all 周杰伦
/music -s default 周杰伦
```

默认源是运行时行为，不作为插件配置项单独保存。

## 来源能力

`/music_sources` 会显示每个来源是否属于默认单曲搜索源，以及是否支持单曲、歌单、专辑。

| 来源 | 单曲 `song` | 歌单 `playlist` | 专辑 `album` | 默认单曲搜索 |
| --- | --- | --- | --- | --- |
| `netease` 网易云音乐 | 是 | 是 | 是 | 是 |
| `qq` QQ音乐 | 是 | 是 | 是 | 是 |
| `kugou` 酷狗音乐 | 是 | 是 | 是 | 是 |
| `kuwo` 酷我音乐 | 是 | 是 | 是 | 是 |
| `migu` 咪咕音乐 | 是 | 是 | 是 | 是 |
| `fivesing` 5sing | 是 | 是 | 否 | 否 |
| `jamendo` Jamendo | 是 | 否 | 是 | 否 |
| `joox` JOOX | 是 | 是 | 是 | 否 |
| `qianqian` 千千音乐 | 是 | 是 | 是 | 是 |
| `soda` 汽水音乐 | 是 | 是 | 是 | 是 |
| `bilibili` Bilibili | 是 | 是 | 否 | 否 |

## 实际运行结果

> 以下为 2026-05-06 实际运行搜索结果，结果受平台接口、网络、Cookie 和关键词影响。`EMPTY` 表示该关键词本次返回空结果，不代表渠道能力永久不可用。

### `song` 歌曲搜索：`晴天`

| 渠道 | 状态 | 耗时 | 数量 | 样例 |
| --- | --- | ---: | ---: | --- |
| `netease` | OK | 5.86s | 4 | 晴天 (原唱 周杰伦) / RyaVocal / `2668397359` |
| `qq` | OK | 5.29s | 2 | 晴天 (深情版) / Lucky小爱 / `0042rlGx2WHBrG` |
| `kugou` | OK | 5.36s | 2 | 晴天 (温柔女声版) / 吉拉朵 / `91A662A6DD6F74A96B0A7609EAEFDF3A` |
| `kuwo` | OK | 16.50s | 2 | 晴天 / 周杰伦 / `228908` |
| `migu` | OK | 9.99s | 1 | 晴天娃娃 / 江语晨 / `600919000007741344\|E\|SQ` |
| `fivesing` | OK | 5.37s | 5 | 晴天 / Vk / `15622111\|fc` |
| `jamendo` | OK | 5.67s | 1 | Ching Tin Yu - 晴天雨 / Dylan Tinlun Chan / `751965` |
| `joox` | OK | 7.62s | 5 | 晴天 / 周杰倫 / `bLnv0PqDX_qAlIqapc+Okw==` |
| `qianqian` | OK | 5.66s | 2 | 等晴天 / 周深 / `T10065400429` |
| `soda` | OK | 6.48s | 5 | 晴天 / 搁浅 / `7381316977862445096` |
| `bilibili` | OK | 26.76s | 4 | 晴天-周杰伦【Hi-Res无损音质】 / 希声音乐 / `BV1NHxrz6Ek4\|32930269376` |

- **可用渠道**：`netease`, `qq`, `kugou`, `kuwo`, `migu`, `fivesing`, `jamendo`, `joox`, `qianqian`, `soda`, `bilibili`
- **空结果**：无
- **失败**：无

### `playlist` 歌单搜索：`周杰伦`

| 渠道 | 状态 | 耗时 | 数量 | 样例 |
| --- | --- | ---: | ---: | --- |
| `netease` | OK | 5.83s | 5 | 周杰伦-Jay 『网易云精选』 / Buradarrr / tracks=137 / `6792103822` |
| `qq` | OK | 5.45s | 5 | 周杰伦歌曲大全！一张歌单全听完 / 歌单狂魔 / tracks=223 / `3805603854` |
| `kugou` | OK | 5.66s | 5 | 周杰伦必听热歌｜聆听周式金曲，聆听青春记忆 / 酷乐推荐 / tracks=150 / `6409645` |
| `kuwo` | OK | 16.37s | 5 | 终于等到周杰伦，说好不哭你今天哭了吗？ / 第一天 / tracks=177 / `2867496601` |
| `migu` | OK | 9.81s | 5 | 周杰伦精选100首：青春百听不厌 / - / tracks=100 / `233754996` |
| `fivesing` | OK | 5.37s | 5 | 周杰伦 / ID: 54366916 / tracks=40 / `56ace0c4482b861d20f135e9` |
| `jamendo` | EMPTY | 5.67s | 0 | - |
| `joox` | OK | 7.80s | 5 | JOOX & 杰威爾音乐全典藏 / - / tracks=- / `qa5UUXpdaakAUZL3IUURLA==` |
| `qianqian` | EMPTY | 0.07s | 0 | - |
| `soda` | OK | 6.23s | 5 | 周杰伦全部歌曲 / 钢琴小马甲 / tracks=10 / `7532035153447649318` |
| `bilibili` | OK | 33.25s | 4 | 【周杰伦】周杰伦经典合集 / 清影音乐库 / tracks=70 / `bvid:BV1WfRCBPECr` |

- **可用渠道**：`netease`, `qq`, `kugou`, `kuwo`, `migu`, `fivesing`, `joox`, `soda`, `bilibili`
- **空结果**：`jamendo`, `qianqian`
- **失败**：无

### `album` 专辑搜索：`范特西`

| 渠道 | 状态 | 耗时 | 数量 | 样例 |
| --- | --- | ---: | ---: | --- |
| `netease` | OK | 5.83s | 5 | 范特西 - 流行Pop x 周杰伦 Type Beat / YKFireVibes / tracks=1 / `272566217` |
| `qq` | OK | 5.22s | 5 | 范特西 / 周杰伦 / tracks=- / `000I5jJB3blWeN` |
| `kugou` | OK | 9.01s | 5 | 范特西 / 周杰伦 / tracks=10 / `958706` |
| `kuwo` | OK | 16.50s | 5 | 范特西 / Jay&nbsp;Chou / tracks=10 / `1287` |
| `migu` | OK | 9.66s | 3 | 范特西 / 周杰伦 / tracks=- / `7948` |
| `fivesing` | EMPTY | 0.07s | 0 | - |
| `jamendo` | EMPTY | 5.67s | 0 | - |
| `joox` | OK | 8.02s | 3 | 范特西 / 周杰倫 / tracks=- / `C1CeCbh6qThtRTBU3oa5Ig==` |
| `qianqian` | OK | 10.47s | 5 | 范特西2 / 河北YE / tracks=2 / `P10003660107` |
| `soda` | OK | 11.29s | 5 | 范特西 / Jinhua Jue / tracks=10 / `7518690014478993409` |
| `bilibili` | EMPTY | 0.07s | 0 | - |

- **可用渠道**：`netease`, `qq`, `kugou`, `kuwo`, `migu`, `joox`, `qianqian`, `soda`
- **空结果**：`fivesing`, `jamendo`, `bilibili`
- **失败**：无

## 配置项

插件配置项尽量使用 `go-music-dl` 已有参数名。

| 配置项                  | 类型   | 默认值             | 说明                                                                                                                                                                 |
| ----------------------- | ------ | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `downloadToLocal`     | bool   | `false`          | 对应 `go-music-dl` 的 `downloadToLocal`。关闭时音频仅作为 AstrBot 消息发送，发送后清理临时文件；开启时保留到 `downloadDir`。                                   |
| `downloadDir`         | string | `data/downloads` | 对应 `go-music-dl` 的 `downloadDir`。音频下载目录，相对路径按 AstrBot 运行目录解析。                                                                             |
| `cliPageSize`         | int    | `50`             | 对应 `go-music-dl` 的 `cliPageSize`。搜索默认每页数量，命令中的 `-ps` 可以临时覆盖。                                                                           |
| `searchTimeout`      | float  | `6.0`            | 搜索阶段快返回等待时间，单位秒。超时未返回的慢渠道不会阻塞首屏结果。                                                                                               |
| `downloadConcurrency` | int    | `3`              | 对应 `go-music-dl` 的 `downloadConcurrency`。批量选择多首歌曲时的并发下载数，范围 `1-5`。                                                                      |
| `sendMode`            | string | `record`         | 点歌下载后的发送方式。`record` 发送语音；`file` 发送群文件；`both` 同时发送语音和群文件。                                                                      |
| `forwardSongInfo`     | bool   | `true`           | 点歌后是否发送歌曲详细信息。优先使用合并转发，不支持时降级为普通文本。                                                                                               |
| `probeConcurrency`    | int    | `5`              | 搜索结果探测大小和码率时的并发数，对齐 `go-music-dl` UI 探针行为。                                                                                                 |
| `cookies`             | object | `{}`             | 对应 `go-music-dl` 的 Cookie 管理。键名可用：`netease`,`qq`,`kugou`,`kuwo`,`migu`,`fivesing`,`jamendo`,`joox`,`qianqian`,`soda`,`bilibili`。 |

`webPageSize`、`embedDownload`、`vgChangeCover`、`vgChangeAudio`、`vgChangeLyric`、`vgExportVideo` 属于 `go-music-dl` Web 页面或视频生成流程，AstrBot 点歌插件当前不会使用，因此不放入插件配置。

Cookie 示例：

```json
{
  "qq": "uin=...; qm_keyst=...;",
  "netease": "MUSIC_U=...;",
  "bilibili": "SESSDATA=...;"
}
```

## 注意事项

- 部分歌曲可能因为版权、会员、地区限制、平台风控或接口变更无法搜索或下载。
- 搜索列表中的 `无效` 不一定表示歌曲不存在，也可能是当前账号、Cookie 或网络条件无法获取下载链接。
- 填写平台 Cookie 可以提高部分平台的搜索和下载可用率。
- 直接发链接点歌会拦截受支持的音乐链接消息并尝试下载，普通网页链接不会被处理。
- 本插件只负责在 AstrBot 内完成点歌和音频发送，不提供音乐版权内容。

## 体验 go-music-dl

本插件专注于 AstrBot 内的点歌和音频发送。如果你希望体验更完整的音乐搜索、下载、歌单 / 专辑、多端 UI 能力，欢迎下载体验 `go-music-dl`。

- **go-music-dl 项目地址**：[https://github.com/guohuiyuan/go-music-dl](https://github.com/guohuiyuan/go-music-dl)
- **music-lib 项目地址**：[https://github.com/guohuiyuan/music-lib](https://github.com/guohuiyuan/music-lib)
- **体验形态**：`go-music-dl` 提供 Web 页面、桌面端、安卓端等使用方式，适合直接体验完整功能。

## 致谢

感谢以下优秀的开源项目：

- **参考项目**：[go-music-dl](https://github.com/guohuiyuan/go-music-dl) - 多端音乐搜索、下载与体验工具，支持 Web 页面、桌面端、安卓端等形态。
- **核心库参考**：[music-lib](https://github.com/guohuiyuan/music-lib) - 音乐平台搜索、解析和下载能力实现。
