# 到手价监控（price-watch）

个人用的 **京东 / 淘宝 / 拼多多** 商品「到手价」监控 Web 应用：浏览器管理、Docker Compose 部署，支持 **QQ（OneBot v11）** 与 **企业微信群机器人** 推送告警。

> **诚实声明**：自动抓取可能违反电商平台服务条款，且接口随时失效；本项目仅供个人学习与自用，**不保证**价格准确性或抓取成功率。淘宝/拼多多默认需手动更新价格。

## 功能（MVP）

- 中文 Web 管理界面（FastAPI + Jinja2）
- 商品 CRUD：名称、平台、链接、SKU、目标价、检查间隔、启用开关
- 到手价模型：`标价 − 券面额 − 满减估算`；返利估算单独展示、不计入到手价
- 价格历史 + 简易 sparkline
- 告警：低于目标价，或较上次下降 X% / X 元
- 通知：
  - `qq_onebot`：OneBot v11 HTTP API（NapCat / go-cqhttp / Lagrange）
  - `wecom_webhook`：企业微信群机器人 Webhook（markdown/text）
- **个人微信没有官方 Bot API**，请使用企业微信群机器人

## 平台适配

| 平台 | 自动拉取 | 说明 |
|------|----------|------|
| 京东 `jd` | 尽力而为 | 调用公开价格接口模式；被拦时回退「需手动更新」，仍可定时对照上次价格，详情页有「更新价格」 |
| 淘宝 `taobao` | 否 | 存根适配器，明确标记「需手动更新」 |
| 拼多多 `pdd` | 否 | 同上 |

## 快速开始（Docker Compose）

```bash
git clone https://github.com/camilavivan/price-watch.git
cd price-watch
cp config.example.yaml config.yaml
cp .env.example .env   # 可选
# 编辑 config.yaml：OneBot / 企微 / adminToken
docker compose up -d --build
```

浏览器打开：http://localhost:8080  
健康检查：http://localhost:8080/health

数据目录 `./data` 会持久化 SQLite 数据库。

### 本地开发（无 Docker）

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
mkdir -p data
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

## 鉴权

- 默认：`adminToken` 为空 → **本机无鉴权**（适合家庭内网）
- 设置 `adminToken` 或环境变量 `ADMIN_TOKEN` 后：
  - 浏览器登录页写入 Cookie `admin_token`
  - 或请求头：`X-Admin-Token: <token>`

## 配置说明

复制 `config.example.yaml` → `config.yaml`：

```yaml
onebot:
  enabled: true
  # Docker → 宿主机 NapCat：http://host.docker.internal:5700
  # 同 compose 网络：http://napcat:5700
  apiBase: http://host.docker.internal:5700
  accessToken: ""
  notifyGroups: []    # 群号
  notifyUsers: []     # QQ 号

wecom:
  enabled: false
  webhookUrl: ""      # 或环境变量 WECOM_WEBHOOK_URL
```

敏感项也可用 `.env` / 环境变量覆盖（**不要把真实 Token 提交到 Git**）：

- `ADMIN_TOKEN`
- `ONEBOT_ACCESS_TOKEN`
- `WECOM_WEBHOOK_URL`
- `DATABASE_URL`（默认 SQLite；可选 Postgres：`postgresql+asyncpg://...`，需自行加依赖与 compose profile）

## 对接 NapCat / go-cqhttp / Lagrange

1. 在宿主机或同 compose 网络启动 OneBot 实现，开启 **HTTP API**（常见端口 `5700`）。
2. 本服务只需**主动调用** `send_private_msg` / `send_group_msg`，一般**不需要**接收反向事件。
3. `config.yaml`：
   - 容器访问宿主机：`apiBase: http://host.docker.internal:5700`（compose 已配 `extra_hosts`）
   - 同网络服务名：`apiBase: http://napcat:5700`
4. 若 API 开启鉴权，填写 `accessToken` 或 `ONEBOT_ACCESS_TOKEN`（Bearer）。
5. 配置 `notifyGroups` / `notifyUsers`。

`docker-compose.yml` 内附注释掉的 NapCat 示例服务，可按需启用。

## 对接企业微信群机器人

1. 企业微信群 → 添加群机器人 → 复制 Webhook 地址。
2. `wecom.enabled: true`，`webhookUrl` 填入；或设环境变量 `WECOM_WEBHOOK_URL`。
3. 告警以 markdown 发送（失败时回退 text）。

> 个人微信无官方开放的群机器人/Bot API，请使用企业微信。

## 添加商品

1. 打开 Web UI →「添加商品」
2. 选择平台、填写名称与链接；京东建议填 SKU 或使用 `item.jd.com/{sku}.html` 链接
3. 填写目标到手价；券/满减可先手工估算
4. 淘宝/拼多多：在详情页用「更新价格」录入当前标价与优惠
5. 京东：可点「立即检查」尝试自动拉标价，再补券/满减

## 告警消息内容

标题、平台、原/新手到价、链接、时间，以及触发原因（低于目标 / 降幅）。

## 技术栈

- Python 3.12、FastAPI、Jinja2、SQLAlchemy（asyncio）+ SQLite
- APScheduler（与 Web 同进程）
- httpx、Docker Compose

## License

MIT — 请自行遵守各电商平台与即时通讯服务的使用条款。
