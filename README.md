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

分享文案里的 `https://m.tb.cn/...` / `https://3.jd.hk/...` 会被自动提取（即使夹在 💲🔐、淘口令、【京东】等杂质中）。「标题」/【标题】也会作为监控名称提示传入。

**限制**：纯淘口令（只有口令、**没有** `m.tb.cn` 等 URL）需要淘宝联盟等付费/授权 API，**本项目不支持**。请粘贴带短链的分享文案。

## 到手价公式

```
landing = max(list_price + tax_amount - coupon - full_reduction, 0)
```

即：**标价 + 税费 − 券 − 满减**（返利仅展示、不扣减）。国内商品 `tax_amount` 默认为 0；海淘 / 京东全球购 / jd.hk 等导入商品会尽量解析「税费 / 预估税费 / 进口税」。

若页面只给出已含税的 **含税价 / 预估合计 / 到手价**（且没有可分拆的商品价+税费），则将该金额记为 `list_price`，`tax_amount = 0`，避免重复加税。优先使用「商品价 + 税费」分拆。

## 抓取适配器

| 平台 | 策略 |
|------|------|
| 京东 `jd` | `p.3.cn`（忽略 ≤0 / -1）→ 桌面 HTML/JSON（`pPrice`/`taxFee` 等）→ 移动/海淘页 → 可选公开 ware 接口 → 再失败 `needs_manual` |
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
| `监控 <商品链接> [目标价]` | 归一化 URL → 自动识别平台 → 为当前用户添加监控 |
| `列表` | 我的监控（id / 标题 / 到手价 / 目标） |
| `取消 <id>` | 删除我的监控 |
| `历史 <id>` | 最近 N 条自采到手价 + 近 N 天最低/均价/最高与文字走势 |
| `详情 <id>` / `详请 <id>` | 链接 + 到手价拆分（含税费，若 >0）+ 自采历史统计；有图则尝试发图 |

示例：`监控 https://item.jd.com/100012043978.html 99`

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
fetch:
  rateLimitSeconds: 1.5
  priceNoisePercent: 0.5

alerts:
  onBelowTarget: true
  dropPercent: 5
  dropYuan: 0
  onHistoryLow: true
  historyLowDays: 90
  historyLowTolerancePercent: 0.5

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

环境变量：`QQ_BOT_APP_ID` / `QQ_BOT_SECRET` / `ADMIN_TOKEN` / `DATABASE_URL` / `APP_API_BASE` / `BOT_NOTIFY_URL` / `WEB_PORT`

## 自采价格历史

每次成功抓价或手动更新写入 `price_history`。按窗口计算最低/最高/均价，接近窗口最低价时触发「历史新低」告警。

> **关于什么值得买（SMZDM）**：官方开放平台需商务邀请，无自助开通。本项目**不依赖** SMZDM API。

## 构建超时 / 国内镜像

默认腾讯云 PyPI + npmmirror；若遇 403 可在 `.env` 改成阿里云：

```bash
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
PIP_TRUSTED_HOST=mirrors.aliyun.com
NPM_REGISTRY=https://registry.npmmirror.com
docker compose build --no-cache && docker compose up -d
```

或使用宿主机代理（`host.docker.internal`，不要用容器内 `127.0.0.1`）。

## 测试

```bash
python -m unittest discover -s tests -v
```

覆盖 URL 归一化与告警判定（目标价 / 降幅 / 历史新低 / 噪声抑制）。

## 技术栈

- **app**：Python 3.12、FastAPI、SQLAlchemy async、APScheduler、Jinja2、httpx
- **bot**：Node 20、`qq-official-bot`、TypeScript（源码在 `bot/`，镜像构建期编译）
- Docker Compose（**单服务** `app`）

## License

MIT — 请自行遵守各电商平台与 QQ 开放平台使用条款。
