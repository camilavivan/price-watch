import fs from 'node:fs';
import path from 'node:path';
import YAML from 'yaml';

export type BotConfig = {
  appId: string;
  secret: string;
  sandbox: boolean;
  mode: 'websocket' | 'webhook';
  sendImages: boolean;
  adminToken: string;
  appApiBase: string;
  notifyPort: number;
};

type YamlRoot = {
  adminToken?: string;
  qqofficial?: {
    enabled?: boolean;
    appId?: string;
    secret?: string;
    sandbox?: boolean;
    mode?: string;
    sendImages?: boolean;
  };
};

function findConfigPath(): string | null {
  const candidates = [
    process.env.CONFIG_PATH,
    '/app/config.yaml',
    path.resolve(process.cwd(), 'config.yaml'),
    path.resolve(process.cwd(), '../config.yaml'),
  ].filter(Boolean) as string[];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return null;
}

export function loadConfig(): BotConfig {
  let yaml: YamlRoot = {};
  const cfgPath = findConfigPath();
  if (cfgPath) {
    yaml = YAML.parse(fs.readFileSync(cfgPath, 'utf8')) || {};
  }
  const q = yaml.qqofficial || {};

  const appId = (process.env.QQ_BOT_APP_ID || q.appId || '').trim();
  const secret = (process.env.QQ_BOT_SECRET || q.secret || '').trim();
  const sendEnv = process.env.QQ_BOT_SEND_IMAGES;
  let sendImages = q.sendImages !== false;
  if (sendEnv != null) {
    sendImages = ['1', 'true', 'yes', 'on'].includes(sendEnv.trim().toLowerCase());
  }

  return {
    appId,
    secret,
    sandbox: q.sandbox === true || process.env.QQ_BOT_SANDBOX === '1',
    mode: (q.mode === 'webhook' ? 'webhook' : 'websocket') as 'websocket' | 'webhook',
    sendImages,
    adminToken: (process.env.ADMIN_TOKEN || yaml.adminToken || '').trim(),
    // Single-container default: talk to uvicorn on localhost
    appApiBase: (process.env.APP_API_BASE || 'http://127.0.0.1:8080').replace(/\/$/, ''),
    notifyPort: Number(process.env.BOT_NOTIFY_PORT || 8091),
  };
}
