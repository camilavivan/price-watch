import type { BotConfig } from './config.js';
import type { HistoryStats } from './api.js';
import {
  createWatch,
  deleteWatch,
  getHistory,
  getWatch,
  listWatches,
} from './api.js';

const HELP = `【到手价监控】命令
帮助 — 显示本说明
监控 <商品链接> [目标价] — 添加监控（自动识别京东/淘宝/拼多多并归一化链接）
列表 — 查看我的监控
取消 <id> — 删除我的监控
历史 <id> — 近期到手价 + 自采统计/走势
详情 <id> — 链接与到手价明细（也可写「详请」）

说明：可直接粘贴带链接的分享文案（含【京东】/淘口令/粉丝福利购等），机器人会自动提取链接。
告警只推送给添加监控的你本人。历史最低来自本机自采记录（非第三方）。`;

const PLATFORM: Record<string, string> = {
  jd: '京东',
  taobao: '淘宝',
  pdd: '拼多多',
};

/** Trailing punctuation often glued to URLs in QQ share pastes */
const TRAILING_URL_JUNK = /[）)」』】"'“”‘’。，、！？!?,.;:\]\}>]+$/u;

/** Known commerce / short-link hosts (jd / taobao|tmall / pdd) */
const COMMERCE_HOST_RE =
  /(?:^|\.)(?:(?:\d+\.)?jd\.hk|jd\.com|u\.jd\.com|3\.cn|(?:m\.|e\.|s\.)?tb\.cn|a\.m\.taobao\.com|taobao\.com|tmall\.com|tmall\.hk|pinduoduo\.com|yangkeduo\.com)$/i;

function fmtPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `¥${Number(v).toFixed(2)}`;
}

function fmtStats(s: HistoryStats | null | undefined): string {
  if (!s || !s.count) return '';
  const lowTag = s.is_history_low ? '是 · 历史新低' : '否';
  const lines = [
    `近${s.days}天自采：最低 ${fmtPrice(s.lowest)} / 均价 ${fmtPrice(s.avg)} / 最高 ${fmtPrice(s.highest)}（${s.count}点）`,
    `是否历史新低：${lowTag}`,
  ];
  if (s.sparkline) lines.push(`走势：${s.sparkline}`);
  return lines.join('\n');
}

/** Extract first http(s) URL; strip trailing punctuation from share pastes. */
export function extractFirstUrl(text: string): string | null {
  // Stop at whitespace, CJK, or common wrappers — share pastes glue junk to URLs
  const m = text.match(
    /https?:\/\/[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef<>"'）)」』】\[\]{}|\\^`]+/i,
  );
  if (!m) return null;
  let url = m[0];
  // Peel trailing junk repeatedly (e.g. ")。")
  let prev = '';
  while (url !== prev) {
    prev = url;
    url = url.replace(TRAILING_URL_JUNK, '');
  }
  if (!/^https?:\/\/.+/i.test(url)) return null;
  return url;
}

export function isCommerceUrl(url: string): boolean {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return COMMERCE_HOST_RE.test(host);
  } catch {
    return false;
  }
}

function hasWatchKeyword(text: string): boolean {
  return /监控|盯价|加监控/.test(text);
}

/** Optional target: 目标/目标价, or number right after URL when 监控-style. */
export function extractTargetPrice(text: string, url?: string | null): number | undefined {
  let m = text.match(/目标价?\s*[：:=\s]*(\d+(?:\.\d+)?)/);
  if (m) return Number(m[1]);

  if (url) {
    const idx = text.indexOf(url);
    if (idx >= 0) {
      const after = text.slice(idx + url.length);
      m = after.match(/^\s+(\d+(?:\.\d+)?)\s*元?\s*$/);
      if (m) return Number(m[1]);
      // Single trailing number on same/next fragment when keyword present
      if (hasWatchKeyword(text)) {
        m = after.match(/^\s+(\d+(?:\.\d+)?)\s*元?(?:\s|$)/);
        if (m) return Number(m[1]);
      }
    }
  }

  // Classic clean command: 监控 <url> <price>
  m = text.match(/(?:监控|盯价|加监控)\s+\S+\s+(\d+(?:\.\d+)?)\s*元?\s*$/);
  if (m) return Number(m[1]);

  return undefined;
}

async function doCreateWatch(
  cfg: BotConfig,
  openid: string,
  url: string,
  target?: number,
): Promise<CommandResult> {
  if (!/^https?:\/\//i.test(url)) {
    return { text: '请提供以 http(s):// 开头的商品链接' };
  }
  try {
    const w = await createWatch(cfg, openid, url, target);
    return {
      text:
        `已添加监控 #${w.id}\n` +
        `${w.name}\n` +
        `平台：${PLATFORM[w.platform] || w.platform}\n` +
        `到手价：${fmtPrice(w.landing_price)}\n` +
        `目标价：${fmtPrice(w.target_price)}\n` +
        (w.needs_manual ? '提示：该平台可能需在调试页手动更新价格。' : '已尝试拉取价格。'),
    };
  } catch (e) {
    return { text: `添加失败：${e instanceof Error ? e.message : String(e)}` };
  }
}

export type CommandResult = {
  text: string;
  imageUrl?: string | null;
};

export async function handleCommand(
  cfg: BotConfig,
  openid: string,
  raw: string,
): Promise<CommandResult> {
  const text = raw.trim().replace(/^[@＠]\S+\s*/, '');
  if (!text) return { text: HELP };

  if (text === '帮助' || text === 'help' || text === '?' || text === '？') {
    return { text: HELP };
  }

  if (text === '列表' || text === 'list') {
    const watches = await listWatches(cfg, openid);
    if (!watches.length) {
      return { text: '你还没有监控。发送：监控 <商品链接> [目标价]，或直接粘贴带链接的分享文案' };
    }
    const lines = watches.map((w) => {
      const plat = PLATFORM[w.platform] || w.platform;
      return `#${w.id} [${plat}] ${w.name}\n  到手价 ${fmtPrice(w.landing_price)} · 目标 ${fmtPrice(w.target_price)}${w.needs_manual ? ' · 需手动' : ''}`;
    });
    return { text: `你的监控（${watches.length}）\n` + lines.join('\n') };
  }

  // Watch: keyword + URL, or bare commerce URL (share paste)
  const url = extractFirstUrl(text);
  if (url && (hasWatchKeyword(text) || isCommerceUrl(url))) {
    const target = extractTargetPrice(text, url);
    return doCreateWatch(cfg, openid, url, target);
  }

  let m = text.match(/^取消\s+(\d+)$/);
  if (m) {
    const id = Number(m[1]);
    try {
      await deleteWatch(cfg, openid, id);
      return { text: `已取消监控 #${id}` };
    } catch (e) {
      return { text: `取消失败：${e instanceof Error ? e.message : String(e)}` };
    }
  }

  m = text.match(/^历史\s+(\d+)$/);
  if (m) {
    const id = Number(m[1]);
    try {
      const { history: hist, history_stats: stats } = await getHistory(cfg, openid, id, 12);
      if (!hist.length) return { text: `#${id} 暂无价格历史（多抓几次后会出现自采统计）` };
      const lines = hist.map((h) => {
        const t = h.recorded_at ? h.recorded_at.replace('T', ' ').slice(0, 16) : '?';
        return `${t}  ${fmtPrice(h.landing_price)}`;
      });
      const statsBlock = fmtStats(stats);
      return {
        text:
          `#${id} 近期到手价（自采）\n` +
          lines.join('\n') +
          (statsBlock ? `\n\n${statsBlock}` : ''),
      };
    } catch (e) {
      return { text: `查询失败：${e instanceof Error ? e.message : String(e)}` };
    }
  }

  m = text.match(/^(?:详情|详请)\s+(\d+)$/);
  if (m) {
    const id = Number(m[1]);
    try {
      const w = await getWatch(cfg, openid, id);
      const plat = PLATFORM[w.platform] || w.platform;
      const statsBlock = fmtStats(w.history_stats);
      const body =
        `#${w.id} ${w.name}\n` +
        `平台：${plat}\n` +
        `标价：${fmtPrice(w.list_price)}\n` +
        `券：${fmtPrice(w.coupon_amount)} · 满减：${fmtPrice(w.full_reduction)}\n` +
        `到手价：${fmtPrice(w.landing_price)}\n` +
        `目标价：${fmtPrice(w.target_price)}\n` +
        (statsBlock ? `${statsBlock}\n` : '') +
        `链接：${w.url || '—'}` +
        (w.last_error ? `\n最近错误：${w.last_error}` : '');
      return { text: body, imageUrl: w.image_url };
    } catch (e) {
      return { text: `查询失败：${e instanceof Error ? e.message : String(e)}` };
    }
  }

  return { text: '未识别命令。发送「帮助」查看用法。可直接粘贴带链接的分享文案。' };
}
