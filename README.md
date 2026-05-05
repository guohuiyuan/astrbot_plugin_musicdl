# astrbot_plugin_musicdl

纯 Python 聚合音乐点歌插件。插件内部实现音乐搜索、结果选择、换源、下载和音频发送流程，不需要额外启动 `go-music-dl` 服务或命令行程序。

## 功能

- 支持多个音乐平台并发搜索。
- 支持回复编号下载单首或多首歌曲。
- 支持 `r1` / `换源1` 为搜索结果换源。
- 支持直接解析部分平台歌曲链接。
- 支持使用 `go-music-dl` 风格的配置参数：`downloadToLocal`、`downloadDir`、`cliPageSize`、`downloadConcurrency` 和 `cookies`。
- 支持汽水音乐加密音频解密，依赖由插件 `requirements.txt` 自动安装。

## 安装

将本插件目录放入 AstrBot 的插件目录后启用即可。

插件包含 `requirements.txt`：

```text
cryptography>=44.0.3
```

AstrBot 会在插件加载/安装流程中自动安装缺失依赖。`cryptography` 主要用于汽水音乐音频解密。

## 用法

```text
/music 周杰伦
/music -s qq,kuwo 稻香
/music -s all 花海
/music -s default 晴天
/music https://y.qq.com/n/ryqq/songDetail/xxxx
/music_sources
/music_cancel
```

搜索后回复编号下载：

```text
1
1 2
r1
换源1
取消
```

## 来源选择

默认搜索源参考 `go-music-dl` 的默认行为：

```text
netease,qq,kugou,kuwo,migu,qianqian,soda
```

默认不会搜索以下来源，但仍可通过 `-s all` 或显式指定启用：

```text
fivesing,jamendo,joox,bilibili
```

示例：

```text
/music -s bilibili 起风了
/music -s jamendo piano
/music -s fivesing 原创歌曲
```

默认源是运行时行为，不作为插件配置项单独保存；如需临时指定来源，请在命令中使用 `-s`。

## 当前内置来源

- `netease` 网易云音乐
- `qq` QQ音乐
- `kugou` 酷狗音乐
- `kuwo` 酷我音乐
- `migu` 咪咕音乐
- `fivesing` 5sing
- `jamendo` Jamendo (CC)
- `joox` JOOX
- `qianqian` 千千音乐
- `soda` 汽水音乐
- `bilibili` Bilibili

## 配置项

插件配置项使用 `go-music-dl` 已有参数名，不额外新增自定义设置名。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `downloadToLocal` | bool | `false` | 对应 `go-music-dl` 的 `downloadToLocal`。关闭时音频仅作为 AstrBot 消息发送，发送后清理临时文件；开启时保留到 `downloadDir`。 |
| `downloadDir` | string | `data/downloads` | 对应 `go-music-dl` 的 `downloadDir`。音频下载目录。 |
| `cliPageSize` | int | `50` | 对应 `go-music-dl` 的 `cliPageSize`。点歌搜索结果最多展示的歌曲数量。 |
| `downloadConcurrency` | int | `3` | 对应 `go-music-dl` 的 `downloadConcurrency`。批量选择多首歌曲时的并发下载数，范围 `1-5`。 |
| `cookies` | object | `{}` | 对应 `go-music-dl` 的 Cookie 管理。键名可用：`netease`,`qq`,`kugou`,`kuwo`,`migu`,`fivesing`,`jamendo`,`joox`,`qianqian`,`soda`,`bilibili`。 |

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

- 部分歌曲可能因为版权、会员、地区限制、平台风控或接口变更无法下载。
- 填写平台 Cookie 可以提高部分平台的搜索和下载可用率。
- 本插件只负责在 AstrBot 内完成点歌和音频发送，不提供音乐版权内容。

## 致谢

本插件的多平台设计、来源命名、默认源规则以及部分接口行为参考并致敬 `go-music-dl` 及其 `music-lib` 实现。

本插件是面向 AstrBot 的纯 Python 实现：不调用 `go-music-dl` 二进制、Web 服务或 CLI，只在实现思路和接口适配上参考其优秀工作。
