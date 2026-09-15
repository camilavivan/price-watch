/**
 * QQ Official Open Platform bot — WebSocket outbound, no public inbound port.
 * Mirrors warframe-bot qqofficial patterns (qq-official-bot, GROUP_AND_C2C_EVENT).
 */
import {
  Bot,
  ReceiverMode,
  segment,
  type GroupMessageEvent,
  type PrivateMessageEvent,
} from 'qq-official-bot';
import { isAllowed, loadConfig, type BotConfig } from './config.js';
import { handleCommand } from './commands.js';
import { startNotifyServer } from './notify-server.js';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyBot = Bot<any>;

function createBot(cfg: BotConfig): AnyBot {
  const common = {
    appid: cfg.appId,
    secret: cfg.secret,
    sandbox: cfg.sandbox,
    removeAt: true,
    intents: ['GROUP_AND_C2C_EVENT'] as const,
    logLevel: 'warn' as const,
  };
  // Prefer WEBSOCKET — no public webhook port required
  return new Bot({
    ...common,
    mode: ReceiverMode.WEBSOCKET,
  }) as AnyBot;
}

async function replyWithOptionalImage(
  event: { reply: (msg: unknown) => Promise<unknown> },
  text: string,
  imageUrl: string | null | undefined,
  sendImages: boolean,
): Promise<void> {
  if (sendImages && imageUrl) {
    try {
      await event.reply([segment.image(imageUrl), segment.text(text)]);
      return;
    } catch (err) {
      console.warn('[bot] image reply failed; falling back to text', err);
    }
  }
  await event.reply(text);
}

function wireCommands(bot: AnyBot, cfg: BotConfig): void {
  const onMessage = async (
    event: PrivateMessageEvent | GroupMessageEvent,
    openid: string,
  ) => {
    const text = String(event.raw_message ?? '').trim();
    if (!text || !openid) return;
    if (!isAllowed(cfg, openid)) {
      console.warn(JSON.stringify({ msg: 'denied user', openid }));
      try {
        await event.reply(
          `未授权：请管理员把下面这串 openid 写入 config.yaml 的 qqofficial.allowUsers 后重启 bot。\n你的 openid：\n${openid}`,
        );
      } catch {
        /* ignore */
      }
      return;
    }
    try {
      const result = await handleCommand(cfg, openid, text);
      await replyWithOptionalImage(event, result.text, result.imageUrl, cfg.sendImages);
    } catch (err) {
      console.error('[bot] command error', err);
      try {
        await event.reply(`处理失败：${err instanceof Error ? err.message : String(err)}`);
      } catch {
        /* ignore */
      }
    }
  };

  bot.on('message.private', (event: PrivateMessageEvent) => {
    const userId = String(event.user_id ?? '');
    void onMessage(event, userId);
  });

  bot.on('message.group', (event: GroupMessageEvent) => {
    const userId = String(event.user_id ?? '');
    void onMessage(event, userId);
  });
}

async function main(): Promise<void> {
  const cfg = loadConfig();
  if (!cfg.appId || !cfg.secret) {
    console.error(
      'QQ_BOT_APP_ID / QQ_BOT_SECRET (or qqofficial.appId/secret) are required',
    );
    process.exit(1);
  }
  if (cfg.mode !== 'websocket') {
    console.warn('[bot] forcing websocket mode (no public webhook port)');
  }

  console.log(
    JSON.stringify({
      msg: 'starting QQ official bot',
      sandbox: cfg.sandbox,
      sendImages: cfg.sendImages,
      allowUsers: cfg.allowUsers.length,
      allowAll: cfg.allowAll,
      appApiBase: cfg.appApiBase,
    }),
  );

  const bot = createBot(cfg);
  wireCommands(bot, cfg);
  await bot.start();
  console.log('[bot] QQ official WebSocket started');

  const server = startNotifyServer(bot, cfg);

  const shutdown = async () => {
    console.log('[bot] shutting down…');
    server.close();
    try {
      await bot.stop();
    } catch {
      /* ignore */
    }
    process.exit(0);
  };
  process.on('SIGINT', () => void shutdown());
  process.on('SIGTERM', () => void shutdown());
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
