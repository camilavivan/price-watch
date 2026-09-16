# 到手价监控（price-watch）

个人用的 **京东 / 淘宝 / 拼多多**「到手价」监控：**QQ 官方开放平台 Bot**（好友私聊命令）为主，服务器上的 **Web 管理页**（调试用，请加 ADMIN_TOKEN）。

> **诚实声明**：自动抓取可能违反电商平台服务条款，且接口随时失效；本项目仅供个人学习与自用，**不保证**价格准确性或抓取成功率。淘宝/拼多多以 HTML 尽力解析为主，失败则标记「需手动更新」。

## 设计参考（ideas only）

本仓库实现为原创代码；架构思路参考了以下开源项目（**未复制其大段版权代码**）：

| 项目 | 借鉴点 |
|------|--------|
| [PriceDive](https://github.com/DAILtech/PriceDive) | 商品中心 + `platform`/`url` 目标；SQLite 价格历史；**不做**其模拟随机价，京东仍真实尽力拉取 |
| [MarketEye](https://github.com/dachengzi065-gif/marketeye) | 丢链接即监控；`check` 引擎：fetch→parse→compare→alert→snapshot；通用 CNY/USD 正则与 UA；0.5% 价格噪声；FastAPI+SQLite+调度 |
| ecommerce-price-analysis | 清晰的平台 collector/adapter 分层；请求间 rate limit；轻度中文文本归一 |
| [manmanbuy_js_crack](https://github.com/PPsteven/manmanbuy_js_crack) / [mall-monitor](https://github.com/zhangbincheng1997/mall-monitor) / [hamflx gist](https://gist.github.com/hamflx/41cf079dbf81b25d59a1d5da08a2c68d) | 慢慢买 HistoryLowest ticket + `getHistoryTrend` token 方案（思路） |

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  单容器 price-watch                                          │
│                                                             │
│   QQ 私聊 ──WS出站──► Node Bot ──HTTP──► FastAPI /api/bot/*  │
│                         ▲                    │              │
│                         │ BOT_NOTIFY_URL     ▼              │
│                         └────────── 告警推送  check_watch    │
│                                              │              │
│                         SQLite ◄── history ◄─┘              │
│                         adapters: jd / taobao / pdd         │
│                         (+ generic HTML fallback)           │
└─────────────────────────────────────────────────────────────┘
         │
         ▼ 宿主机 :8080（调试 Web）
```

| 进程 | 职责 | 网络 |
|------|------|------|
| FastAPI（uvicorn） | SQLite、调度抓价、调试 Web、内部 REST `/api/bot/*`、URL 归一化 | 宿主机 `${WEB_PORT:-8080}` → 容器 `8080` |
| Node QQ Bot | QQ 官方 **WebSocket 出站**、私聊命令、主动推送告警 | **无对外端口**；notify 仅 `127.0.0.1:8091` |

告警只推送给监控归属用户（`owner_openid`）。各用户只能看到/管理自己的监控。

**不做**：个人微信 / 企业微信；`allowUsers` 门禁；什么值得买（SMZDM）官方 API。OneBot（NapCat 等）已降级，默认关闭。

## URL 归一化

添加监控时先走 `app/url_normalize.py`：

1. 从 host **自动识别**平台：`jd` / `taobao|tmall` / `pdd`
2. 展开并清洗 URL，去掉 `utm_*`、`jd_pop`、`extension_id` 等追踪参数
3. 提取 `sku_id`（京东 path、淘宝/天猫 `id=`、拼多多 `goods_id`）
4. 写入 `canonical_url`；唯一键：
   - 有 sku：`(owner_openid, platform, sku_id)`
   - 无 sku：`(owner_openid, url)`

未知平台会拒绝并提示使用三大电商链接。

### 短链展开（QQ 分享粘贴）

`resolve_url` 对 `m.tb.cn` / `3.jd.hk` / `u.jd.com` / `s.click.taobao.com` 等短链做 **多跳展开**（最多 8 跳）：

1. 用浏览器 UA 发 GET（必要时对 `tb.cn` 再试一次移动 UA）
2. 若有 `Location` 头则跟随；否则从 HTML 解析下一跳：
   - `var url = '...'`
   - `window.location` / `location.href`
   - meta refresh `url=`
   - `og:url`
   - 页面内第一条商品链接（`item.taobao.com` / `detail.tmall.com` / `item.jd.com` 等）
3. 京东最终页尽量抽出 `skuId` → `https://item.jd.com/{sku}.html`
4. 淘宝/天猫抽出 `id=` → `item.taobao.com` 或 `detail.tmall.com`

分享文案里的 `https://m.tb.cn/...` / `https://e.tb.cn/...` / `https://3.jd.hk/...` 会被自动提取（即使夹在 💲🔐、淘口令、【京东】等杂质中）。「标题」/【标题】也会作为监控名称提示传入。

### 手淘链接

支持常见手机淘宝分享/详情 URL，并归一化为带 `id=` 的稳定桌面链：

| 形态 | 示例 | 归一化 |
|------|------|--------|
| H5 详情 | `h5.m.taobao.com/awp/core/detail.htm?id=` | `item.taobao.com/item.htm?id=` |
| a 站短详情 | `a.m.taobao.com/i{id}.htm` | 同上（已带 id 时**不再**当短链展开） |
| market | `market.m.taobao.com/...?...id=` | 同上 |
| e 短链 | `e.tb.cn/...` | 多跳展开后抽 id |
| m.tb.cn | 已有 | 多跳展开 |

天猫 host 则落到 `detail.tmall.com/item.htm?id=`。

**限制**：纯淘口令（只有口令、**没有** `m.tb.cn` / `e.tb.cn` 等 URL）需要淘宝联盟等付费/授权 API，**本项目不支持**。请粘贴带短链的分享文案。

## 到手价公式

```
landing = max(list_price + tax_amount - coupon - full_reduction, 0)
```

即：**标价 + 税费 − 券 − 满减**（返利仅展示、不扣减）。国内商品 `tax_amount` 默认为 0；海淘 / 京东全球购 / jd.hk 等导入商品会尽量解析「税费 / 预估税费 / 进口税」。

若页面只给出已含税的 **含税价 / 预估合计 / 到手价**（且没有可分拆的商品价+税费），则将该金额记为 `list_price`，`tax_amount = 0`，避免重复加税。优先使用「商品价 + 税费」分拆。

## 抓取适配器

| 平台 | 策略 |
|------|------|
| 京东 `jd` | 商品 HTML/JSON（`pPrice`/`taxFee`）→ `p.3.cn` → 公开 ware 接口 → 失败则 `needs_manual`。**云 VPS 上 jd.hk/全球购常被风控或 SPA 壳无内嵌价**，需「填价」或配置出站代理 |
| 淘宝/天猫 `taobao` | HTML 通用解析（CNY/USD 正则 + 税费标记 + 标题/库存启发式）→ 失败则手动 |
| 拼多多 `pdd` | 同上 |

请求之间按 `fetch.rateLimitSeconds`（默认 1.5s）限速。

## 检查引擎（MarketEye 风格）

`check_watch(product)`：

1. **fetch**（adapter）
2. **update** 标价/税费/标题/图片等字段
3. 写入 **price_history** 快照
4. **evaluate** 告警：目标价 / 降幅% / 降幅¥ / 自采历史新低（含 0.5% 容差）；小于 `fetch.priceNoisePercent` 的抖动不触发「较上次下降」
5. 经 `BOT_NOTIFY_URL` 通知 QQ 归属用户（链接 / 短历史 / 可选图片）

## 谁能和机器人聊天？

个人开发者 **无法**在应用配置里用 `allowUsers` 做白名单——QQ 开放平台后台已经限制谁可以与机器人会话。

1. 打开 [QQ 开放平台](https://q.qq.com/) 控制台
2. 在机器人应用里 **手动添加** 可聊天的用户 / 好友
3. 被平台放行、能私聊到机器人的人，本服务一律接受命令
4. **无需**再配置 `allowUsers` / `allowAll`（已废弃）

数据隔离仍靠每条监控的 `owner_openid`。

## 创建 QQ 开放平台机器人

1. 打开 [QQ 开放平台](https://q.qq.com/) / QQ 机器人文档，创建机器人应用。
2. 获取 **AppID** 与 **AppSecret（Secret）**。
3. 订阅/启用相关能力，意图使用 **`GROUP_AND_C2C_EVENT`**。
4. 连接方式选 **WebSocket**（不要用需公网回调的 Webhook）。
5. 在平台后台把要用的好友加入可聊天名单。
6. 写入本地 `.env`（**不要提交到 Git**）：

```bash
cp .env.example .env
# QQ_BOT_APP_ID=...
# QQ_BOT_SECRET=...
# ADMIN_TOKEN=随机长字符串
```

7. `cp config.example.yaml config.yaml`（一般只需改 sandbox / sendImages）

## QQ 聊天命令（私聊 C2C；群消息同样解析）

| 命令 | 说明 |
|------|------|
| `帮助` | 命令说明 |
| `监控 <商品链接> [当前到手价] [目标价]` | 归一化 URL → 自动识别平台 → 添加监控。自动取价失败时可带当前到手价 |
| `列表` | 我的监控（id / 标题 / 到手价 / 目标） |
| `取消 <id>` | 删除我的监控 |
| `历史 <id>` | 近期到手价 + 近 N 天最低/均价/最高与走势（**优先慢慢买**，失败则本地自采） |
| `详情 <id>` / `详请 <id>` | 链接 + 到手价拆分（含税费，若 >0）+ 历史统计（标注来源）；有图则尝试发图 |
| `填价 <id> <到手价>` | 手动设到手价（标价=到手价，税费=0）；别名 `改价` / `手动价` |
| `填价 <id> <标价> <税费>` | 手动设标价+税费；到手价 = 标价 + 税费 − 券 − 满减 |
| `目标 <id> <价格>` | 只设置/修改目标价 |
| `登录状态` | 查看京东 Playwright 登录态是否可用 |

示例：

```text
监控 https://item.jd.com/100012043978.html 359.34
监控 https://npcitem.jd.hk/10066842682891.html 359.34 300
目标 1 300
填价 1 359.34
```

语义：创建个数字且自动价为空 → 当作**当前到手价**；自动价已有 → 当作**目标价**。两个数字 → 当前价、目标价。
分享文案里的规格数字（如「奶粉**2段**」「3罐装」）不会被当成到手价；请用 `监控 <链接> <真实到手价>` 或事后 `填价 <id> <价>`。

## 快速开始（Docker Compose · 单容器）

```bash
git clone https://github.com/camilavivan/price-watch.git
cd price-watch
cp config.example.yaml config.yaml
cp .env.example .env
# 编辑 .env：QQ_BOT_APP_ID / QQ_BOT_SECRET / ADMIN_TOKEN
docker compose up -d --build
```

- 调试 UI：http://服务器IP:8080 （请设置 `ADMIN_TOKEN`）
- 健康检查：http://服务器IP:8080/health
- 数据：`./data`（SQLite）
- 镜像内同时跑 uvicorn + Node bot

### 已有部署升级（例：/opt/price-watch）

```bash
cd /opt/price-watch
git pull origin main
# 如有新增配置项，对照 config.example.yaml 合并进 config.yaml（fetch.* 等）
docker compose up -d --build
docker compose logs -f --tail=80
```

SQLite 会在启动时自动迁移（增加 `canonical_url`、调整唯一索引）；请先备份 `./data`。

### 本地开发（无 Docker）

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
mkdir -p data
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

# Bot（另开终端）
cd bot && npm install && npm run dev
```

## 配置要点

```yaml
scheduler:
  manualRiskBackoffHours: 12

fetch:
  rateLimitSeconds: 1.5
  priceNoisePercent: 0.5
  playwright:
    enabled: false

alerts:
  onBelowTarget: true
  dropPercent: 5
  dropYuan: 0
  onHistoryLow: true
  historyLowDays: 90
  historyLowTolerancePercent: 0.5

history:
  enabled: true
  external: manmanbuy
  softPriceHint: true

qqofficial:
  enabled: true
  sandbox: false
  mode: websocket
  sendImages: true
  notifyUrl: "http://127.0.0.1:8091/notify"

web:
  host: 0.0.0.0
  port: 8080

onebot:
  enabled: false
```

环境变量：`QQ_BOT_APP_ID` / `QQ_BOT_SECRET` / `ADMIN_TOKEN` / `DATABASE_URL` / `APP_API_BASE` / `BOT_NOTIFY_URL` / `WEB_PORT` / `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `JD_HTTP_PROXY`

## 价格历史（慢慢买 + 本地自采）

QQ「历史」「详情」展示时：

1. **优先**请求慢慢买 `getHistoryTrend`（非官方第三方），用其序列算最低/均价/最高与走势，并标注 **「来源：慢慢买」**
2. 若网络/403/验证码导致外部为空 → **回退**本地 `price_history`，标注 **「来源：本地自采」**
3. 本地表仍在每次成功抓价 /「填价」后写入，供告警「历史新低」与兜底展示

配置（`config.yaml`）：

```yaml
history:
  enabled: true
  external: manmanbuy   # 或 none 关闭外部
  cacheSeconds: 3600
  rateLimitSeconds: 2.0
  softPriceHint: true   # 京东/淘宝自动取价失败时，用慢慢买最新点作软参考价
  softPriceHintPlatforms: ["jd", "taobao"]
```

> **关于什么值得买（SMZDM）**：官方开放平台需商务邀请，无自助开通。本项目**不依赖、不声称** SMZDM 官方 API。
>
> **慢慢买限制**：非官方接口；数据中心 IP 常返回裸 **402** / **403** / 验证码。实现为 best-effort，失败只打日志（明确记 blocked）、不拖垮 Bot、不假装取到价。公开脚本参考：[PPsteven/manmanbuy_js_crack](https://github.com/PPsteven/manmanbuy_js_crack)、[mall-monitor](https://github.com/zhangbincheng1997/mall-monitor)、[hamflx gist](https://gist.github.com/hamflx/41cf079dbf81b25d59a1d5da08a2c68d)。

## 构建超时 / 国内镜像

默认腾讯云 PyPI + npmmirror；若遇 403 可在 `.env` 改成阿里云：

```bash
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
PIP_TRUSTED_HOST=mirrors.aliyun.com
NPM_REGISTRY=https://registry.npmmirror.com
docker compose build --no-cache && docker compose up -d
```

或使用宿主机代理（`host.docker.internal`，不要用容器内 `127.0.0.1`）。

## 取不到价怎么监控

云主机上京东海淘 / `jd.hk` 自动取价常失败（风控页 / SPA 无价）；慢慢买在数据中心 IP 上常直接返回 **裸 `402`/`403`**（本项目会打日志并跳过，**不假装有历史价**）。

**今天就能用的做法（推荐）**：

1. 创建时带价：`监控 <链接> <你在 App 看到的到手价>` 或 `监控 <链接> <当前价> <目标价>`
2. 事后补价：`填价 <id> <到手价>`（或 `填价 <id> <标价> <税费>`）
3. 只改目标：`目标 <id> <价格>`
4. 设好到手价后，目标价/降幅告警才有意义；调度器对风控类 `needs_manual` 会 **backoff 数小时**（默认 12h），避免每分钟空刷京东

可选进阶：启用 Playwright + 扫码登录后，风控页可再试浏览器取价（见下节）。未登录或仍失败时路径不变：继续「填价」。

## 京东自动取价失败（云 VPS）与代理 / 填价

在腾讯云等 VPS 上，京东全球购 / `*.jd.hk` 经常无法自动取价：

- `mitem.jd.hk` 等会跳到 `cfe.m.jd.com/.../risk_handler`（页面标题「京东验证」）
- `npcitem.jd.hk` / `item.jd.hk` 常返回 SPA 壳（约 35KB），HTML 内**没有** `pPrice` / `taxFee`
- `color.jd.hk` / `api.m.jd.com` 返回 `no access` / `API does not exist`（需签名 h5st）
- 部分环境 `p.3.cn` / `pe.3.cn` DNS 解析失败

因此 HTML 解析在风控/SPA 场景下**不可能成功**。本项目会写入明确的 `last_error`（反爬/风控），并支持：

1. **QQ 手动填价**（推荐兜底）  
   `填价 <id> <到手价>` 或 `填价 <id> <标价> <税费>`（也可用 `改价` / `手动价`）
2. **出站 HTTP 代理**（可选，仍可能命中风控）  
   在 ImmortalWrt / 宿主机 Clash 等开启 HTTP 端口后，写入 `.env`：

```bash
HTTP_PROXY=http://host.docker.internal:7890
HTTPS_PROXY=http://host.docker.internal:7890
# 仅覆盖京东请求时可用：
# JD_HTTP_PROXY=http://192.168.1.1:7890
```

`docker-compose.yml` 已把上述变量传入 `app` 容器。代理只改善出口 IP/线路，**不保证**绕过京东验证；失败时请用「填价」。


## 反爬 / 取价思路（公开仓库归纳 + 本项目落地）

公开监控/比价项目常见做法（**本仓库未整段复制其代码**）：

| 思路 | 公开来源举例 | 本项目 |
|------|--------------|--------|
| 浏览器自动化 + 登录 Cookie（Playwright/Puppeteer） | 各类 JD/TB 监控脚本 | ✅ 可选 `fetch.playwright`；Web「浏览器登录」/ `python -m app.browser_login jd` |
| 出站 HTTP/SOCKS 代理换出口 IP | mall-monitor 代理池、自建 Clash | ✅ `HTTP_PROXY` / `HTTPS_PROXY` / `JD_HTTP_PROXY` |
| 电商联盟 / 开放平台官方价 | 淘宝客、京东联盟 | ❌ 需申请与签名；不做 |
| 手动「填价」兜底 | 运营向工具常见 | ✅ QQ `填价` / `改价` / `手动价` |
| 第三方历史价（慢慢买） | manmanbuy_js_crack、mall-monitor、hamflx | ✅ `app/history_external.py` best-effort |
| 短链多跳展开 + 手淘 URL 归一 | 分享监控类项目 | ✅ `resolve_url` + `h5`/`a.m`/`e.tb.cn` |

实操建议：云 VPS 上京东全球购优先 **填价**；代理可选；历史走势看慢慢买，失败则看本地自采。


## Playwright 自动取价（可选）

镜像已包含 Playwright Chromium（体积大约 **+300MB**）。默认 **关闭**。

1. `config.yaml`：

```yaml
fetch:
  playwright:
    enabled: true
    headless: true
    storageStatePath: "./data/browser/jd_storage.json"
    userDataDir: "./data/browser/profile"
    loginScreenshotPath: "./data/browser/login.png"
```

2. 首次登录（二选一）：
   - Web：打开 `/browser/jd` →「开始登录」→ 用京东 App 扫截图里的码 →「刷新状态」
   - CLI：`docker compose exec app python -m app.browser_login jd --wait 300`
3. QQ 可发 `登录状态` 查看是否有 pin/thor 等 Cookie 线索
4. 之后京东 HTTP 风控/SPA 失败时，adapter 会再试 Playwright；仍失败则 `needs_manual` + 请「填价」

`./data` 已挂载，登录态落在 `./data/browser/`。

## 测试

```bash
python -m unittest discover -s tests -v
```

覆盖 URL 归一化（含手淘）、慢慢买 token/datePrice 解析、告警判定（目标价 / 降幅 / 历史新低 / 噪声抑制）。

## 技术栈

- **app**：Python 3.12、FastAPI、SQLAlchemy async、APScheduler、Jinja2、httpx
- **bot**：Node 20、`qq-official-bot`、TypeScript（源码在 `bot/`，镜像构建期编译）
- Docker Compose（**单服务** `app`）

## License

MIT — 请自行遵守各电商平台与 QQ 开放平台使用条款。
