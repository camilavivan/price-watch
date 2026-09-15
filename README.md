# 到手价监控（price-watch）

个人用的 **京东 / 淘宝 / 拼多多**「到手价」监控：**QQ 官方开放平台 Bot**（好友私聊命令）为主，服务器上的 **Web 管理页**（调试用，请加 ADMIN_TOKEN）。

> **诚实声明**：自动抓取可能违反电商平台服务条款，且接口随时失效；本项目仅供个人学习与自用，**不保证**价格准确性或抓取成功率。淘宝/拼多多默认需手动更新价格。

## 架构

**单容器**同时跑：

| 进程 | 职责 | 网络 |
|------|------|------|
| FastAPI（uvicorn） | SQLite、调度抓价、调试 Web、内部 REST `/api/bot/*` | 宿主机 `${WEB_PORT:-8080}` → 容器 `8080` |
| Node QQ Bot | QQ 官方 **WebSocket 出站**、私聊命令、主动推送告警 | **无对外端口**；notify 仅 `127.0.0.1:8091` |

告警只推送给监控的归属用户（`owner_openid`）。各用户只能看到/管理自己的监控。

**不做**：个人微信 / 企业微信产品路径。OneBot（NapCat 等）已降级，默认关闭。

## 为什么没有公网 Bot 端口？

QQ 官方开放平台在 **WebSocket** 模式下由机器人**主动出站**连接腾讯网关，因此：

- **不需要**把 webhook 暴露到公网
- `docker-compose` 只发布 Web 端口；Bot 与 notify 均在容器内 localhost

## 谁能和机器人聊天？

个人开发者 **无法**在应用配置里用 `allowUsers` 做白名单——QQ 开放平台后台已经限制谁可以与机器人会话。

1. 打开 [QQ 开放平台](https://q.qq.com/) 控制台
2. 在机器人应用里 **手动添加** 可聊天的用户 / 好友
3. 被平台放行、能私聊到机器人的人，本服务一律接受命令
4. **无需**再配置 `allowUsers` / `allowAll`（已废弃）

数据隔离仍靠每条监控的 `owner_openid`：用户 A 看不到用户 B 的列表。

## 创建 QQ 开放平台机器人

1. 打开 [QQ 开放平台](https://q.qq.com/) / QQ 机器人文档，创建机器人应用。
2. 获取 **AppID** 与 **AppSecret（Secret）**。
3. 订阅/启用相关能力，意图使用 **`GROUP_AND_C2C_EVENT`**（私聊 C2C + 群，与本项目一致）。
4. 连接方式选 **WebSocket**（不要用需公网回调的 Webhook）。
5. 在平台后台把要用的好友加入可聊天名单。
6. 将 AppID / Secret 写入本地 `.env`（**不要提交到 Git**）：

```bash
cp .env.example .env
# 编辑 .env：
# QQ_BOT_APP_ID=你的AppID
# QQ_BOT_SECRET=你的Secret
# ADMIN_TOKEN=随机长字符串   # 建议设置
```

7. 复制配置：

```bash
cp config.example.yaml config.yaml
# 一般只需改 sandbox / sendImages；AppID/Secret 用环境变量
```

首次联调可把 `qqofficial.sandbox: true`。

## QQ 聊天命令（私聊 C2C；群消息同样解析）

| 命令 | 说明 |
|------|------|
| `帮助` | 命令说明 |
| `监控 <商品链接> [目标价]` | 为**当前用户**添加监控 |
| `列表` | 我的监控（id / 标题 / 到手价 / 目标） |
| `取消 <id>` | 删除我的监控 |
| `历史 <id>` | 最近 N 条自采到手价 + 近 N 天最低/均价/最高与文字走势 |
| `详情 <id>` / `详请 <id>` | 链接 + 到手价拆分 + 自采历史统计；有图则尝试发图 |

示例：`监控 https://item.jd.com/100012043978.html 99`

## 快速开始（Docker Compose · 单容器）

```bash
git clone https://github.com/camilavivan/price-watch.git
cd price-watch
cp config.example.yaml config.yaml
cp .env.example .env
# 编辑 .env：QQ_BOT_APP_ID / QQ_BOT_SECRET / ADMIN_TOKEN
# 在 QQ 开放平台后台加人（无需 allowUsers）
docker compose up -d --build
```

- 调试 UI：http://服务器IP:8080 （请设置 `ADMIN_TOKEN`）
- 健康检查：http://服务器IP:8080/health
- 数据：`./data`（SQLite）
- 镜像内同时跑 uvicorn + Node bot；bot 异常退出会由入口脚本自动重启

日常用户**只用 QQ**；Web 可查看全部监控（含各用户 `owner_openid`）。

### 本地开发（无 Docker）

```bash
# App
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
mkdir -p data
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

# Bot（另开终端；需本机可访问 App）
cd bot && npm install && npm run dev
# 环境变量：QQ_BOT_APP_ID QQ_BOT_SECRET APP_API_BASE=http://127.0.0.1:8080 BOT_NOTIFY_PORT=8091
```

## 配置要点

```yaml
qqofficial:
  enabled: true
  appId: ""
  secret: ""
  sandbox: false
  mode: websocket
  sendImages: true
  # allowUsers / allowAll 已废弃：在 QQ 开放平台后台加人即可
  notifyUrl: "http://127.0.0.1:8091/notify"   # 单容器本机

web:
  host: 0.0.0.0
  port: 8080

onebot:
  enabled: false   # 可选遗留路径，默认关
```

环境变量（勿提交真实值）：

- `QQ_BOT_APP_ID` / `QQ_BOT_SECRET`
- `ADMIN_TOKEN`
- `DATABASE_URL`
- `APP_API_BASE`（bot→app，默认 `http://127.0.0.1:8080`）
- `BOT_NOTIFY_URL`（app→bot，默认 `http://127.0.0.1:8091/notify`）
- `WEB_PORT`（宿主机 Web 端口，默认 `8080`）

## 自采价格历史（第一方）

每次成功抓价或手动更新都会写入 `price_history`（时间戳 + 到手价 + 来源）。系统据此计算近 N 天最低 / 最高 / 均价，并在接近窗口最低价时触发「历史新低」告警（可配天数与容差）。

```yaml
alerts:
  onHistoryLow: true
  historyLowDays: 90              # 30 / 90 / 180
  historyLowTolerancePercent: 0.5 # 当前价 ≤ 最低 × (1+0.5%) 视为新低
```

> **关于什么值得买（SMZDM）**：官方开放平台需商务邀请（联系 group-content@zhidemai.com），无自助开通。本项目**不依赖** SMZDM API，改用上述自采历史；若日后拿到对接密钥，可再另行接入。

## 构建超时 / 本机代理

若 `pip install` / `npm install` 出现超时（常见于国内云主机）：

**优先用国内镜像**（默认腾讯云 PyPI + npmmirror；若遇 403 可在 `.env` 改成阿里云）：

```bash
docker compose build --no-cache
docker compose up -d
```

**或使用宿主机代理**（Clash 等需允许「局域网连接 / Allow LAN」）：

```bash
# 写入 .env
HTTP_PROXY=http://host.docker.internal:7890
HTTPS_PROXY=http://host.docker.internal:7890

docker compose build --no-cache
docker compose up -d
```

注意：构建容器里的 `127.0.0.1` 不是宿主机，代理地址请用 `host.docker.internal`。

## 平台适配

| 平台 | 自动拉取 | 说明 |
|------|----------|------|
| 京东 `jd` | 尽力而为 | 公开价格接口；失败则「需手动更新」 |
| 淘宝 `taobao` | 否 | 存根，需手动/调试页更新 |
| 拼多多 `pdd` | 否 | 同上 |

## 数据模型

- 商品带 `owner_openid`（QQ 用户 id）
- 同一用户同一 `url` 唯一
- 告警经 bot 主动私聊该 openid（含标题、到手价 old→new、链接、短历史、自采历史统计；有 `image_url` 时尝试 `segment.image`，失败回退文字）
- 自采 `price_history`：每次成功 check/manual 追加；告警可含近 N 天历史新低

## 技术栈

- **app**：Python 3.12、FastAPI、SQLAlchemy async、APScheduler、Jinja2
- **bot**：Node 20、`qq-official-bot`、TypeScript（源码在 `bot/`，镜像构建期编译进同一镜像）
- Docker Compose（**单服务** `app`）

## License

MIT — 请自行遵守各电商平台与 QQ 开放平台使用条款。
