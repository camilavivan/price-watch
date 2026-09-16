# ---- Stage 1: build QQ bot (TypeScript → dist) ----
FROM node:20-slim AS bot-build
WORKDIR /bot

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY=localhost,127.0.0.1
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY} \
    no_proxy=${NO_PROXY}

COPY bot/package.json bot/package-lock.json* ./
RUN npm config set registry "${NPM_REGISTRY}" \
  && npm install
COPY bot/tsconfig.json ./
COPY bot/src ./src
RUN npm run build && npm prune --omit=dev

# ---- Stage 2: Python app + Node runtime (single image) ----
FROM python:3.12-slim

WORKDIR /app

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY=localhost,127.0.0.1
ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
ARG PIP_TRUSTED_HOST=mirrors.cloud.tencent.com
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY} \
    no_proxy=${NO_PROXY}

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node 20 runtime only (run bot dist); copy from official node image
COPY --from=node:20-slim /usr/local/bin/node /usr/local/bin/node

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir \
    -i "${PIP_INDEX_URL}" \
    --trusted-host "${PIP_TRUSTED_HOST}" \
    --default-timeout=120 \
    -r requirements.txt

# Playwright Chromium for optional JD browser fetch (~+300MB+).
# Avoid `playwright install --with-deps` on Debian Trixie/slim (missing
# ttf-unifont / ttf-ubuntu-font-family). Install Chromium + distro libs.
# Enable with fetch.playwright.enabled: true after Web「浏览器登录」or
#   python -m app.browser_login jd
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1
RUN set -eux; \
  apt-get update; \
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdrm2 libgbm1 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libpango-1.0-0 libcairo2 libdbus-1-3 libglib2.0-0 \
    libx11-6 libx11-xcb1 libxcb1 libxext6 \
    fonts-liberation fonts-unifont fonts-noto-color-emoji \
    fonts-noto-cjk \
    || true; \
  # Bookworm vs Trixie package name drift (*t64)
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 libcups2t64 libasound2t64 libgtk-3-0t64 \
    || DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libcups2 libasound2 libgtk-3-0 \
    || true; \
  rm -rf /var/lib/apt/lists/*; \
  playwright install chromium

# Clear build-time proxy so runtime requests are not forced through it
ENV HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy=

COPY app ./app
COPY config.example.yaml ./config.example.yaml
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Bot artifacts from build stage
COPY --from=bot-build /bot/package.json /app/bot/package.json
COPY --from=bot-build /bot/node_modules /app/bot/node_modules
COPY --from=bot-build /bot/dist /app/bot/dist

RUN mkdir -p /app/data /app/data/browser /ms-playwright

ENV PYTHONUNBUFFERED=1 \
    NODE_ENV=production \
    DATABASE_URL=sqlite+aiosqlite:///./data/pricewatch.db \
    APP_API_BASE=http://127.0.0.1:8080 \
    BOT_NOTIFY_URL=http://127.0.0.1:8091/notify \
    BOT_NOTIFY_PORT=8091

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=25s \
  CMD curl -fsS http://127.0.0.1:8080/health || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
