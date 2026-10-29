# 战舰世界战绩查询 · AstrBot

AstrBot 战舰世界战绩查询插件。查询与图片渲染由 [Hikari-core v2](https://github.com/wows-yuyuko/Hikari-core-v2)（yuyuko API）完成，指令路由沿用 kokomibot 的 KOOK 版本。

## 安装

在 AstrBot WebUI 的插件页面选择「从链接安装」，填入：

```text
https://github.com/VioL1n/astrbot_plugin_wows
```

安装后填写下方配置，再发送 `/wws ping` 检查加载状态，发送 `/wws bind 游戏昵称` 绑定账号，最后发送 `/wws me` 查询战绩。市场收录后也可以搜索「战舰世界战绩查询」安装。

## 环境要求

- Python **3.11 或 3.12**，由当前固定版本的 Hikari-core 要求决定。
- 安装 Hikari-core 的 Git 依赖需要运行 AstrBot 的环境提供 `git`，并能访问 GitHub 和 Python 包源。
- 运行时需要连接 yuyuko API；API Token 需由机器人管理员向 yuyuko 服务方申请，本插件不提供公共凭据。
- 图片渲染需要 Playwright Chromium。首次启动引擎时会下载浏览器和资源，建议至少 2 GB 内存，并预留浏览器的磁盘空间；完成前查询可能超时。
- Linux / Docker 环境还需安装 Chromium 的系统依赖。若日志提示缺少共享库，请在 **AstrBot 所在的容器或 Python 环境**中运行 `python -m playwright install --with-deps chromium`（系统依赖安装可能需要 root 权限），然后重载插件。
- 代码通过 AstrBot 通用消息接口接收指令、发送图片；不同消息适配器的图片发送及交互表现需要在实际部署中验证。QQ 官方机器人通过本地绑定识别用户账号。

## 配置

在 AstrBot WebUI → 插件 → astrbot_plugin_wows → 配置中填写：

- `yuyuko API Token`（格式 `APIKey:Token`），必填。
- `默认服务器`：亚服 / 欧服 / 美服 / 俄服 / 国服，默认亚服。自然语言查询和省略服务器的 `/wws bind` 都用它。

首次出图时 Hikari 会把 Chromium 下载到 `data/plugin_data/astrbot_plugin_wows/browsers`，可能需要几分钟。

## macOS 桌面版快速安装依赖（可选）

AstrBot 会在插件加载失败时自动用 pip 安装 `requirements.txt`，但 playwright 的 wheel 有 40 多 MB，镜像慢时要等很久。如果本机装过 uv，并且 uv/pip 缓存或 `~/Library/Caches/ms-playwright` 里已经有对应版本，可以改用脚本一次装好，通常几秒完成：

```bash
# 先退出 AstrBot（脚本会检查）
bash scripts/install_deps.sh
# 再启动 AstrBot
```

这个辅助脚本面向安装在 `/Applications/AstrBot.app` 的 macOS 桌面版，默认数据目录为 `~/.astrbot/data`。其他安装位置可设置 `ASTRBOT_PYTHON` 和 `ASTRBOT_DATA`；Linux / Docker 用户使用 AstrBot 的常规依赖安装流程即可。

脚本做了这几件事：
- 用 uv 把 hikari-core 和 playwright 装进 AstrBot 的 `data/site-packages`，并以 AstrBot 自带的包版本作为约束，不会改动核心依赖。
- 把本机已下载的同版本 Chromium 以 APFS 克隆的方式复制到插件数据目录，不额外占空间，首次出图也就不用再下载浏览器。

插件不能用独立的虚拟环境：AstrBot 插件和 AstrBot 跑在同一个 Python 进程里，只能 import AstrBot 的 site-packages。`playwright` 版本钉在 `1.63.0`，和 Chromium 1243 对应。

## 指令

```text
/wws ping                 检查插件是否在线
/wws help                 Hikari 指令帮助
/wws bind 游戏昵称          用默认服务器绑定自己的游戏账号
/wws bind 欧服 游戏昵称     指定服务器绑定（昵称含空格用半角括号：(AI deal)）
/wws me                   我的战绩
/wws me recent 30         我近 30 天的战绩
/wws recent 7             同上，近 7 天
/wws ship 大和             我的单船战绩
/wws 亚服 玩家昵称          查询他人
/wws recent 亚服 玩家昵称 7  他人近 7 天
/wws roll / sx / ban / box 等
```

`/wws` 后面的内容原样交给 Hikari-core 处理，完整指令见 Hikari-core 文档。需要选择时，由同一用户在规定时间内直接回复序号（群聊中 @机器人 后回复），发送“取消”退出。

## 自然语言查询

插件向 AstrBot 的 LLM 注册了三个函数工具：`wows_query`（查询战绩）、`wows_fun`（roll 船、扫雪、封号记录、圣诞船池）和 `wows_bind`（绑定账号）。需要在 AstrBot 中启用 LLM，并且所用模型支持函数调用。不用提服务器，默认使用配置里的服务器；明确说了服务器时以用户说的为准。例如：

```text
帮我绑定一下 XXX
查下我的战绩
我最近 30 天打得怎么样
看看 XXX 最近 7 天的随机战
我的大和数据
欧服 XXX 的战绩
帮我 roll 一条日本十级战列
看看我的扫雪收益
查下我的圣诞船池
国服 XXX 有没有封号记录
```

图片和文字结果由插件直接发出，LLM 只简短回应一句。roll 总是用自己绑定的账号；封号记录只支持国服（Hikari 限制）。

QQ 官方机器人拿不到 QQ 号，因此“查询自己”的指令使用插件本地保存的绑定（`data/plugin_data/astrbot_plugin_wows/user_bindings.sqlite3`），每个用户各自绑定。

## 数据与反馈

账号绑定保存在 AstrBot 的插件数据目录，包含聊天平台用户标识、游戏服务器、游戏账号 ID 和昵称；查询通过 Hikari-core 请求 yuyuko 等上游服务。不要把实际 Token、用户绑定数据库或运行日志提交到公开仓库。

如遇问题，请在本仓库 Issues 提供 AstrBot 版本、Python 版本、消息平台、部署方式、复现指令及已去除凭据的错误信息。`/wws ping` 只检查插件指令是否在线，不检查 Token 或图片渲染。

## 上游项目

本插件以 GNU GPL v3 发布，完整许可文本见 [LICENSE](LICENSE)。

- [Hikari-core v2](https://github.com/wows-yuyuko/Hikari-core-v2)：指令解析、战绩查询与图片渲染，依赖固定在 `requirements.txt` 所列提交。上游使用 GNU GPL v3，许可文本见 `third_party/Hikari-core-v2-LICENSE`。
- kokomibot 的 KOOK 适配实现：本插件初始指令路由和 Hikari 桥接的参考来源；在此基础上适配 AstrBot，并增加本地账号绑定、自然语言工具和交互选项处理。
