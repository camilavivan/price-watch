import type { BotConfig } from './config.js';

export type HistoryStats = {
  days: number;
  count: number;
  lowest: number | null;
  highest: number | null;
  avg: number | null;
  is_history_low: boolean;
  sparkline?: string;
};

export type Watch = {
  id: number;
  name: string;
  platform: string;
  url: string;
  landing_price: number | null;
  target_price: number | null;
  list_price: number | null;
  tax_amount: number;
  coupon_amount: number;
  full_reduction: number;
  rebate_estimate: number;
  needs_manual: boolean;
  image_url: string | null;
  last_error: string | null;
  history_stats?: HistoryStats | null;
};

async function request<T>(
  cfg: BotConfig,
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
  };
  if (cfg.adminToken) headers['X-Admin-Token'] = cfg.adminToken;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const res = await fetch(`${cfg.appApiBase}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data: unknown = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail =
      typeof data === 'object' && data && 'detail' in data
        ? String((data as { detail: unknown }).detail)
        : text.slice(0, 200);
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return data as T;
}

export async function listWatches(cfg: BotConfig, openid: string): Promise<Watch[]> {
  const data = await request<{ watches: Watch[] }>(
    cfg,
    'GET',
    `/api/bot/watches?openid=${encodeURIComponent(openid)}`,
  );
  return data.watches || [];
}

export async function createWatch(
  cfg: BotConfig,
  openid: string,
  url: string,
  targetPrice?: number,
  name?: string,
): Promise<Watch> {
  const data = await request<{ watch: Watch }>(cfg, 'POST', '/api/bot/watches', {
    openid,
    url,
    target_price: targetPrice ?? null,
    name: name || undefined,
  });
  return data.watch;
}

export async function deleteWatch(cfg: BotConfig, openid: string, id: number): Promise<void> {
  await request(cfg, 'DELETE', `/api/bot/watches/${id}?openid=${encodeURIComponent(openid)}`);
}

export async function getWatch(cfg: BotConfig, openid: string, id: number): Promise<Watch> {
  const data = await request<{ watch: Watch }>(
    cfg,
    'GET',
    `/api/bot/watches/${id}?openid=${encodeURIComponent(openid)}`,
  );
  return data.watch;
}

export type HistoryPoint = {
  landing_price: number;
  list_price: number | null;
  tax_amount: number;
  coupon_amount: number;
  full_reduction: number;
  source: string;
  recorded_at: string | null;
};

export async function getHistory(
  cfg: BotConfig,
  openid: string,
  id: number,
  limit = 10,
): Promise<{ history: HistoryPoint[]; history_stats?: HistoryStats | null }> {
  const data = await request<{ history: HistoryPoint[]; history_stats?: HistoryStats }>(
    cfg,
    'GET',
    `/api/bot/watches/${id}/history?openid=${encodeURIComponent(openid)}&limit=${limit}`,
  );
  return {
    history: data.history || [],
    history_stats: data.history_stats ?? null,
  };
}
