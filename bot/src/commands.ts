import type { BotConfig } from './config.js';
import type { HistoryStats } from './api.js';
import {
  createWatch,
  deleteWatch,
  getBrowserLoginStatus,
  getHistory,
  getWatch,
  listWatches,
  updateWatchPrice,
  updateWatchTarget,
} from './api.js';

const HELP = `【到手价监控】命令
帮助 — 显示本说明
监控 <商品链接> [当前到手价] [目标价] — 添加监控
  · 自动取价失败时可带当前到手价，一条消息建好监控
  · 两个数字：当前价 目标价；一个数字：有自动价则当目标，否则当当前价
列表 — 查看我的监控
取消 <id> — 删除我的监控
历史 <id> — 近期到手价 + 历史统计/走势（优先慢慢买，失败则本地自采）
详情 <id> — 链接与到手价明细（也可写「详请」）
填价 <id> <到手价> — 手动设到手价（标价=到手价，税费=0）；别名：改价 / 手动价
填价 <id> <标价> <税费> — 手动设标价+税费，到手价=标价+税费−券−满减
目标 <id> <价格> — 只设置/修改目标价
登录状态 — 查看京东浏览器登录（Playwright）是否可用

说明：可直接粘贴带链接的分享文案（含【京东】/淘口令/手淘 h5·a.m / 粉丝福利购等），机器人会自动提取链接。
云服务器上京东全球购/jd.hk 常被风控拦截；取不到价时请在「监控」时带上当前到手价，或事后「填价」。
告警只推送给添加监控的你本人。历史走势优先慢慢买（非官方，VPS 常 402/403），本地自采作兜底。`;

const PLATFORM: Record<string, string> = {
  jd: '京东',
  taobao: '淘宝',
  pdd: '拼多多',
};

/** Trailing punctuation often glued to URLs in QQ share pastes */
const TRAILING_URL_JUNK = /[）)」』】"'“”‘’。，、！？!?,.;:\]\}>]+$/u;

/** Known commerce / short-link hosts (jd / taobao|tmall / pdd) */
const COMMERCE_HOST_RE =
  /(?:^|\.)(?:(?:\d+\.)?jd\.hk|jd\.com|u\.jd\.com|3\.cn|(?:m\.|e\.|s\.)?tb\.cn|a\.m\.taobao\.com|h5\.m\.taobao\.com|market\.m\.taobao\.com|taobao\.com|tmall\.com|tmall\.hk|pinduoduo\.com|yangkeduo\.com)$/i;

function fmtPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `¥${Number(v).toFixed(2)}`;
}

function fmtStats(s: HistoryStats | null | undefined): string {
  if (!s || !s.count) return '';
  const lowTag = s.is_history_low ? '是 · 历史新低' : '否';
  const src =
    s.source_label ||
    (s.source === 'manmanbuy' ? '来源：慢慢买' : '来源：本地自采');
  const scope = s.source === 'manmanbuy' ? '历史' : '自采';
  const lines = [
    `近${s.days}天${scope}：最低 ${fmtPrice(s.lowest)} / 均价 ${fmtPrice(s.avg)} / 最高 ${fmtPrice(s.highest)}（${s.count}点）`,
    src,
    `是否历史新低：${lowTag}`,
  ];
  if (s.sparkline) lines.push(`走势：${s.sparkline}`);
  return lines.join('\n');
}

/** Extract first http(s) URL; strip trailing punctuation from share pastes. */
export function extractFirstUrl(text: string): string | null {
  // Stop at whitespace, CJK, emoji, or common wrappers — share pastes glue junk to URLs
  const m = text.match(
    /https?:\/\/[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u{1f300}-\u{1faff}\u2600-\u27bf<>"'）)」』】\[\]{}|\\^`]+/iu,
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

/** Prefer known commerce short hosts when multiple URLs appear in paste. */
export function extractBestUrl(text: string): string | null {
  const re =
    /https?:\/\/[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u{1f300}-\u{1faff}\u2600-\u27bf<>"'）)」』】\[\]{}|\\^`]+/giu;
  const found: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    let url = m[0];
    let prev = '';
    while (url !== prev) {
      prev = url;
      url = url.replace(TRAILING_URL_JUNK, '');
    }
    if (/^https?:\/\/.+/i.test(url) && !found.includes(url)) found.push(url);
  }
  if (!found.length) return extractFirstUrl(text);

  const priority = (url: string): number => {
    try {
      const host = new URL(url).hostname.toLowerCase();
      if (
        /(?:^|\.)(?:m\.tb\.cn|tb\.cn|e\.tb\.cn|s\.tb\.cn|a\.m\.taobao\.com|h5\.m\.taobao\.com|u\.jd\.com|3\.jd\.com|3\.jd\.hk|3\.cn|p\.pinduoduo\.com)$/i.test(
          host,
        ) ||
        host.endsWith('.tb.cn')
      ) {
        return 0;
      }
      if (host.endsWith('.jd.hk') && !host.startsWith('item.')) return 1;
      if (COMMERCE_HOST_RE.test(host)) return 5;
    } catch {
      /* ignore */
    }
    return 50;
  };
  found.sort((a, b) => priority(a) - priority(b));
  return found[0];
}

/** Title hint from 「…」 / 【…】; strip coupon / platform prefixes. */
export function extractTitleHint(text: string): string | undefined {
  const skip = new Set(['京东', '淘宝', '天猫', '拼多多', 'JD', 'Taobao', 'Tmall']);
  const prefixes = [
    '询客服领券',
    '点击领取',
    '领券',
    '粉丝福利购',
    '福利购',
    '京东',
    '淘宝',
    '天猫',
    '拼多多',
  ];
  const candidates: string[] = [];
  for (const m of text.matchAll(/「([^」]{2,80})」/g)) candidates.push(m[1].trim());
  for (const m of text.matchAll(/【([^】]{2,80})】/g)) candidates.push(m[1].trim());
  for (let raw of candidates) {
    let t = raw.trim();
    if (!t || skip.has(t)) continue;
    const inner = t.match(/^【([^】]+)】$/);
    if (inner) t = inner[1].trim();
    t = t.replace(/^【[^】]{1,20}】\s*/, '').trim();
    for (const pref of prefixes) {
      if (t.startsWith(pref)) t = t.slice(pref.length).replace(/^[：:·—\-\s]+/, '');
    }
    t = t.trim();
    if (!t || skip.has(t) || t.length < 2) continue;
    if (/[\u4e00-\u9fffA-Za-z0-9]/.test(t)) return t.slice(0, 80);
  }
  return undefined;
}

function hasWatchKeyword(text: string): boolean {
  return /监控|盯价|加监控/.test(text);
}

/** Product / quantity units — digits glued to these are specs, not prices (e.g. 2段). */
const PRODUCT_UNIT_RE =
  /^(?:段|罐|盒|袋|瓶|件|岁|月|抽|片|斤|两|升|克|个|只|双|条|包|箱|桶|支|台|部|辆|kg|g|ml|L|人份)/iu;

/** Default min plausible retail when number is unlabeled (configurable mirror of fetch.minPlausiblePrice). */
export const DEFAULT_MIN_PLAUSIBLE_PRICE = 10;

export type CreatePrices = {
  /** Explicit current landing when two numbers or labeled */
  current?: number;
  /** Explicit target */
  target?: number;
  /**
   * Single trailing number after URL — server decides:
   * auto landing present → target; else → current.
   */
  trailing?: number;
  /** Unlabeled / unit-glued number we refused (for QQ reply). */
  rejected?: number;
};

function hasDecimalPlaces(n: number): boolean {
  return Math.abs(n - Math.round(n)) > 1e-9;
}

/**
 * Accept a candidate amount from share / 监控 text.
 * (a) currency / price keyword labeled → accept if > 0
 * (b) standalone after URL, not followed by product unit, and
 *     ≥ minPlausible OR has decimal places
 */
export function isAcceptableCreatePrice(
  n: number,
  opts: { labeled?: boolean; followedByUnit?: boolean; minPlausible?: number } = {},
): boolean {
  if (!Number.isFinite(n) || n < 0) return false;
  if (opts.followedByUnit) return false;
  const minP = opts.minPlausible ?? DEFAULT_MIN_PLAUSIBLE_PRICE;
  if (opts.labeled) return n > 0;
  if (hasDecimalPlaces(n)) return n > 0;
  return n >= minP;
}

type ScannedNum = { value: number; labeled: boolean; followedByUnit: boolean };

/**
 * Scan text for price-like numbers; skip unit-glued specs (奶粉2段).
 * Prefer ￥/¥/元 and 到手/现价/价格/填价 labeled amounts.
 */
function scanPriceNumbers(text: string, minPlausible: number): {
  accepted: number[];
  rejected: number[];
} {
  const accepted: number[] = [];
  const rejected: number[] = [];
  const seen = new Set<string>();

  const push = (item: ScannedNum) => {
    const key = `${item.value}|${item.labeled}|${item.followedByUnit}`;
    if (seen.has(key)) return;
    seen.add(key);
    if (isAcceptableCreatePrice(item.value, item)) {
      accepted.push(item.value);
    } else if (item.followedByUnit || (!item.labeled && item.value < minPlausible)) {
      rejected.push(item.value);
    }
  };

  // Labeled: 到手价/现价/价格/填价 / ￥¥ / …元
  const labeledRe =
    /(?:到手价?|现价|价格|填价|售价|促销价)\s*[：:=\s]*[￥¥]?\s*(\d+(?:\.\d+)?)|(?:[￥¥]\s*(\d+(?:\.\d+)?))|(\d+(?:\.\d+)?)\s*元/g;
  for (const m of text.matchAll(labeledRe)) {
    const raw = m[1] ?? m[2] ?? m[3];
    const n = Number(raw);
    if (Number.isFinite(n)) push({ value: n, labeled: true, followedByUnit: false });
  }

  // Bare numbers: reject if immediately followed by product unit (2段 / 900g)
  const bareRe = /(\d+(?:\.\d+)?)/g;
  for (const m of text.matchAll(bareRe)) {
    const n = Number(m[1]);
    if (!Number.isFinite(n)) continue;
    const after = text.slice(m.index! + m[0].length);
    const before = text.slice(Math.max(0, m.index! - 1), m.index!);
    if (before === '.' || /^\.\d/.test(after)) continue;
    const rest = after.replace(/^\s*/, '');
    const gluedOrUnit = PRODUCT_UNIT_RE.test(rest);
    push({
      value: n,
      labeled: false,
      followedByUnit: gluedOrUnit,
    });
  }

  return { accepted, rejected };
}

/**
 * Parse current / target / trailing prices from create / share-paste text.
 * - Two numbers after URL → current then target
 * - One number → trailing (ambiguous until auto-fetch result)
 * - 「目标价 xx」 alone → target
 * - Digits glued to 段/罐/盒/… are NOT prices (e.g. 奶粉2段)
 */
export function extractCreatePrices(
  text: string,
  url?: string | null,
  opts?: { minPlausible?: number },
): CreatePrices {
  const out: CreatePrices = {};
  const minPlausible = opts?.minPlausible ?? DEFAULT_MIN_PLAUSIBLE_PRICE;
  let targetKw: number | undefined;
  const kw = text.match(/目标价?\s*[：:=\s]*(\d+(?:\.\d+)?)/);
  if (kw) targetKw = Number(kw[1]);

  const nums: number[] = [];
  let rejected: number | undefined;

  if (url) {
    const idx = text.indexOf(url);
    if (idx >= 0) {
      let after = text.slice(idx + url.length);
      // Ignore 目标价 keyword number via separate path
      after = after.replace(/目标价?\s*[：:=\s]*\d+(?:\.\d+)?/g, ' ');
      const scanned = scanPriceNumbers(after, minPlausible);
      for (const n of scanned.accepted) {
        if (!nums.includes(n)) nums.push(n);
        if (nums.length >= 2) break;
      }
      if (scanned.rejected.length) rejected = scanned.rejected[0];
    }
  }
  if (!nums.length) {
    const m = text.match(
      /(?:监控|盯价|加监控)\s+\S+(?:\s+(\d+(?:\.\d+)?))?(?:\s+(\d+(?:\.\d+)?))?\s*$/,
    );
    const tryPush = (raw: string | undefined) => {
      if (!raw) return;
      const n = Number(raw);
      if (!isAcceptableCreatePrice(n, { minPlausible })) {
        if (n < minPlausible) rejected = rejected ?? n;
        return;
      }
      nums.push(n);
    };
    tryPush(m?.[1]);
    tryPush(m?.[2]);
  }

  if (nums.length >= 2) {
    out.current = nums[0];
    out.target = nums[1];
  } else if (nums.length === 1) {
    if (targetKw != null && Math.abs(nums[0] - targetKw) < 1e-9) {
      out.target = targetKw;
    } else if (targetKw != null) {
      out.current = nums[0];
      out.target = targetKw;
    } else {
      out.trailing = nums[0];
    }
  } else if (targetKw != null) {
    out.target = targetKw;
  }
  if (rejected != null && out.current == null && out.trailing == null) {
    out.rejected = rejected;
  }
  return out;
}

/** @deprecated use extractCreatePrices; kept for older call sites */
export function extractTargetPrice(text: string, url?: string | null): number | undefined {
  const p = extractCreatePrices(text, url);
  return p.target ?? p.trailing;
}

/** Parse 填价/改价/手动价 command. Exported for unit tests. */
export type FillPriceParsed =
  | { id: number; mode: 'landing'; landing: number }
  | { id: number; mode: 'list_tax'; list: number; tax: number };

export function parseFillPriceCommand(text: string): FillPriceParsed | null {
  const m = text
    .trim()
    .match(/^(?:填价|改价|手动价)\s+#?(\d+)\s+(\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?))?\s*$/);
  if (!m) return null;
  const id = Number(m[1]);
  const a = Number(m[2]);
  if (!Number.isFinite(id) || id <= 0 || !Number.isFinite(a) || a < 0) return null;
  if (m[3] !== undefined) {
    const tax = Number(m[3]);
    if (!Number.isFinite(tax) || tax < 0) return null;
    return { id, mode: 'list_tax', list: a, tax };
  }
  return { id, mode: 'landing', landing: a };
}

/** Parse 目标 <id> <price> */
export function parseTargetCommand(
  text: string,
): { id: number; target: number } | null {
  const m = text.trim().match(/^目标\s+#?(\d+)\s+(\d+(?:\.\d+)?)\s*元?\s*$/);
  if (!m) return null;
  const id = Number(m[1]);
  const target = Number(m[2]);
  if (!Number.isFinite(id) || id <= 0 || !Number.isFinite(target) || target < 0) return null;
  return { id, target };
}

function createReplyNoLanding(w: {
  id: number;
  last_error?: string | null;
}): string {
  const err = w.last_error || '';
  const blocked = /反爬|风控|拦截|无内嵌价格|京东验证/.test(err);
  const reason = blocked ? '京东拦截' : err ? '自动取价失败' : '暂无价格';
  return (
    `到手价：—（自动取价失败：${reason}）\n` +
    `请立刻发送：填价 ${w.id} <你看到的到手价>\n` +
    `设好价格后才会按目标价/降幅告警；云主机暂无法自动刷新京东海淘价。`
  );
}

function rejectedPriceHint(watchId: number, rejected: number | undefined | null): string {
  if (rejected == null || !Number.isFinite(rejected)) return '';
  const disp =
    Math.abs(rejected - Math.round(rejected)) < 1e-9
      ? String(Math.round(rejected))
      : String(rejected);
  return `\n未采用「${disp}」疑似规格（如2段），请发 \`填价 ${watchId} 真实到手价\``;
}

async function doCreateWatch(
  cfg: BotConfig,
  openid: string,
  url: string,
  prices: CreatePrices,
  name?: string,
): Promise<CommandResult> {
  if (!/^https?:\/\//i.test(url)) {
    return { text: '请提供以 http(s):// 开头的商品链接' };
  }
  try {
    const w = await createWatch(cfg, openid, url, {
      currentPrice: prices.current,
      targetPrice: prices.target,
      trailingPrice: prices.trailing,
      name,
    });
    const placeholder = /^(京东商品|淘宝商品|拼多多商品)/.test(w.name || '');
    const hint = placeholder
      ? '\n提示：暂未解析到商品标题，将在下次检查时重试。'
      : '';
    const taxLine =
      w.tax_amount != null && Number(w.tax_amount) > 0
        ? `税费：${fmtPrice(w.tax_amount)}\n`
        : '';

    const rejectedVal =
      w.rejected_manual_price ?? prices.rejected ?? null;
    const rejectLine =
      w.reject_message
        ? `\n${w.reject_message.replace(/请发真实到手价/, `请发 \`填价 ${w.id} 真实到手价\``)}`
        : rejectedPriceHint(w.id, rejectedVal);

    let priceBlock: string;
    if (w.landing_price != null) {
      const usedManual = Boolean(w.used_manual_current);
      priceBlock =
        `到手价：${fmtPrice(w.landing_price)}` +
        (usedManual ? '（已用你填写的价格建监控）' : '') +
        `\n目标价：${fmtPrice(w.target_price)}\n` +
        (usedManual
          ? '已用你填写的价格建监控；之后可用「填价」更新、「目标」改目标价。'
          : w.needs_manual
            ? '自动取价不完整，已保留当前价；可用「填价」更新。'
            : '已尝试拉取价格。');
    } else {
      priceBlock =
        createReplyNoLanding(w) +
        `\n目标价：${fmtPrice(w.target_price)}` +
        rejectLine;
    }

    return {
      text:
        `已添加监控 #${w.id}\n` +
        `${w.name}\n` +
        `平台：${PLATFORM[w.platform] || w.platform}\n` +
        taxLine +
        priceBlock +
        (w.landing_price != null ? rejectLine : '') +
        hint,
      imageUrl: w.image_url,
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
      return {
        text:
          '你还没有监控。发送：监控 <商品链接> [当前到手价] [目标价]，或直接粘贴带链接的分享文案',
      };
    }
    const lines = watches.map((w) => {
      const plat = PLATFORM[w.platform] || w.platform;
      return `#${w.id} [${plat}] ${w.name}\n  到手价 ${fmtPrice(w.landing_price)} · 目标 ${fmtPrice(w.target_price)}${w.needs_manual ? ' · 需手动' : ''}`;
    });
    return { text: `你的监控（${watches.length}）\n` + lines.join('\n') };
  }

  if (text === '登录状态' || text === '登陆状态') {
    try {
      const st = await getBrowserLoginStatus(cfg);
      if (!st.playwright_enabled) {
        return {
          text:
            'Playwright 未启用（config fetch.playwright.enabled=false）。\n' +
            '启用并完成 Web「浏览器登录」后，京东风控页可尝试自动取价；否则请继续用「填价」。',
        };
      }
      const ok = st.jd_logged_in_hint || st.has_storage_state;
      return {
        text:
          `Playwright：已启用\n` +
          `登录态：${ok ? '已检测到存储（可能可用）' : '未登录 / 无 storage_state'}\n` +
          (st.cookie_names?.length
            ? `Cookie 线索：${st.cookie_names.slice(0, 8).join(', ')}\n`
            : '') +
          (st.message ? `${st.message}\n` : '') +
          `路径：${st.storage_path || '—'}\n` +
          `未登录时请打开 Web 管理页「浏览器登录」，或：python -m app.browser_login jd`,
      };
    } catch (e) {
      return { text: `查询失败：${e instanceof Error ? e.message : String(e)}` };
    }
  }

  // Watch: keyword + URL, or bare commerce URL (share paste)
  const url = extractBestUrl(text) || extractFirstUrl(text);
  if (url && (hasWatchKeyword(text) || isCommerceUrl(url))) {
    const prices = extractCreatePrices(text, url);
    const name = extractTitleHint(text);
    return doCreateWatch(cfg, openid, url, prices, name);
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
      const {
        history: hist,
        history_stats: stats,
        external_history: extHist,
        history_source: histSrc,
      } = await getHistory(cfg, openid, id, 12);
      const useExt = histSrc === 'manmanbuy' && extHist && extHist.length > 0;
      const points = useExt ? extHist! : hist;
      if (!points.length && !(stats && stats.count)) {
        return { text: `#${id} 暂无价格历史（慢慢买不可用且尚无本地自采；多抓几次或稍后再试）` };
      }
      const label = useExt ? '近期到手价（慢慢买）' : '近期到手价（本地自采）';
      const lines = points.map((h) => {
        const t = h.recorded_at ? h.recorded_at.replace('T', ' ').slice(0, 16) : '?';
        return `${t}  ${fmtPrice(h.landing_price)}`;
      });
      const statsBlock = fmtStats(stats);
      return {
        text:
          `#${id} ${label}\n` +
          (lines.length ? lines.join('\n') : '（无逐点列表）') +
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
      const taxLine =
        w.tax_amount != null && Number(w.tax_amount) > 0
          ? `税费：${fmtPrice(w.tax_amount)}\n`
          : '';
      const body =
        `#${w.id} ${w.name}\n` +
        `平台：${plat}\n` +
        `标价：${fmtPrice(w.list_price)}\n` +
        taxLine +
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

  {
    const tgt = parseTargetCommand(text);
    if (tgt) {
      try {
        const w = await updateWatchTarget(cfg, openid, tgt.id, tgt.target);
        return {
          text:
            `已设置 #${w.id} 目标价 ${fmtPrice(w.target_price)}\n` +
            `${w.name}\n` +
            `当前到手价：${fmtPrice(w.landing_price)}` +
            (w.landing_price == null
              ? `\n（尚无到手价，请先：填价 ${w.id} <到手价>）`
              : ''),
        };
      } catch (e) {
        return { text: `设置目标价失败：${e instanceof Error ? e.message : String(e)}` };
      }
    }
  }

  {
    const fill = parseFillPriceCommand(text);
    if (fill) {
      try {
        let w;
        if (fill.mode === 'landing') {
          w = await updateWatchPrice(cfg, openid, fill.id, {
            landing_price: fill.landing,
          });
        } else {
          w = await updateWatchPrice(cfg, openid, fill.id, {
            list_price: fill.list,
            tax_amount: fill.tax,
          });
        }
        const taxLine =
          w.tax_amount != null && Number(w.tax_amount) > 0
            ? `税费：${fmtPrice(w.tax_amount)}\n`
            : '';
        return {
          text:
            `已更新 #${w.id} 价格\n` +
            `${w.name}\n` +
            `标价：${fmtPrice(w.list_price)}\n` +
            taxLine +
            `到手价：${fmtPrice(w.landing_price)}\n` +
            `目标价：${fmtPrice(w.target_price)}`,
          imageUrl: w.image_url,
        };
      } catch (e) {
        return { text: `填价失败：${e instanceof Error ? e.message : String(e)}` };
      }
    }
  }

  return { text: '未识别命令。发送「帮助」查看用法。可直接粘贴带链接的分享文案。' };
}
