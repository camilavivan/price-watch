import http from 'node:http';
import { segment, type Bot } from 'qq-official-bot';
import type { BotConfig } from './config.js';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyBot = Bot<any>;

export type AlertBody = {
  openid: string;
  text: string;
  title?: string;
  old_landing?: number | null;
  new_landing?: number | null;
  url?: string;
  reason?: string;
  history?: Array<{ at: string; landing: number }>;
  image_url?: string | null;
};

function readJson(req: http.IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch (e) {
        reject(e);
      }
    });
    req.on('error', reject);
  });
}

async function sendAlert(bot: AnyBot, cfg: BotConfig, body: AlertBody): Promise<void> {
  const openid = String(body.openid || '').trim();
  if (!openid) throw new Error('missing openid');
  let text = body.text || '';
  if (!text && body.title) {
    const oldS = body.old_landing != null ? `¥${Number(body.old_landing).toFixed(2)}` : '—';
    const newS = body.new_landing != null ? `¥${Number(body.new_landing).toFixed(2)}` : '—';
    text =
      `【到手价告警】${body.title}\n` +
      `到手价：${oldS} → ${newS}\n` +
      (body.reason ? `原因：${body.reason}\n` : '') +
      (body.url ? `链接：${body.url}\n` : '');
    if (body.history?.length) {
      text +=
        '近期价格：\n' +
        body.history.map((h) => `  ${h.at}  ¥${Number(h.landing).toFixed(2)}`).join('\n');
    }
  }
  if (!text) throw new Error('empty alert text');

  const imageUrl = cfg.sendImages ? body.image_url : null;
  if (imageUrl) {
    try {
      await bot.user(openid).send([segment.image(imageUrl), segment.text(text)]);
      return;
    } catch (err) {
      console.warn('[notify] image send failed, fallback text', err);
    }
  }
  await bot.user(openid).send(text);
}

export function startNotifyServer(bot: AnyBot, cfg: BotConfig): http.Server {
  const server = http.createServer(async (req, res) => {
    const url = req.url || '/';
    if (req.method === 'GET' && (url === '/health' || url === '/')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, service: 'price-watch-bot' }));
      return;
    }
    if (req.method === 'POST' && url.startsWith('/notify')) {
      if (cfg.adminToken) {
        const token = String(req.headers['x-admin-token'] || '');
        if (token !== cfg.adminToken) {
          res.writeHead(401, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ detail: 'unauthorized' }));
          return;
        }
      }
      try {
        const body = (await readJson(req)) as AlertBody;
        await sendAlert(bot, cfg, body);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        console.error('[notify] failed', msg);
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ detail: msg }));
      }
      return;
    }
    res.writeHead(404);
    res.end('not found');
  });

  server.listen(cfg.notifyPort, '0.0.0.0', () => {
    console.log(`[bot] internal notify listening on 0.0.0.0:${cfg.notifyPort}`);
  });
  return server;
}
