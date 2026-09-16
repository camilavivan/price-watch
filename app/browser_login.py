"""CLI: python -m app.browser_login jd

Opens JD login page, writes QR/page screenshot to data/browser/login.png,
waits for cookies, saves storage_state.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("browser_login")


async def run_jd_login(*, headed: bool = False, wait_seconds: int = 180) -> int:
    from app.browser.jd_session import (
        ensure_browser_dirs,
        jd_logged_in_hint,
        load_storage_state,
        login_screenshot_path,
        storage_state_path,
        user_data_dir,
    )
    from app.config import load_config

    load_config(force=True)
    ensure_browser_dirs()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("请先安装: pip install playwright && playwright install chromium")
        return 2

    shot = login_screenshot_path()
    state_path = storage_state_path()
    headless = not headed

    login_urls = [
        "https://passport.jd.com/new/login.aspx",
        "https://plogin.m.jd.com/login/login",
    ]

    async with async_playwright() as p:
        # Persistent context helps keep profile under data/browser/profile
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir()),
            headless=headless,
            locale="zh-CN",
            viewport={"width": 420, "height": 780},
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        for url in login_urls:
            try:
                logger.info("打开登录页: %s", url)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(1500)
                await page.screenshot(path=str(shot), full_page=True)
                logger.info("已写入截图（扫码/登录）: %s", shot)
                break
            except Exception as e:
                logger.warning("打开 %s 失败: %s", url, e)

        logger.info(
            "请用京东 App 扫码或在浏览器窗口完成登录（最多等待 %ss）…", wait_seconds
        )
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            try:
                await context.storage_state(path=str(state_path))
            except Exception:
                pass
            # Also refresh screenshot periodically for Web UI
            try:
                await page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass
            state = load_storage_state()
            if jd_logged_in_hint(state):
                await context.storage_state(path=str(state_path))
                logger.info("检测到登录 Cookie，已保存: %s", state_path)
                await context.close()
                return 0
            await asyncio.sleep(3)

        # Final save even if hints weak — user may have logged in with other cookies
        try:
            await context.storage_state(path=str(state_path))
            logger.info("超时；仍已写入当前 storage_state: %s", state_path)
        except Exception as e:
            logger.error("保存 storage_state 失败: %s", e)
        await context.close()
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JD Playwright 登录，保存 storage_state")
    parser.add_argument("platform", nargs="?", default="jd", help="目前仅支持 jd")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="有界面模式（本机调试）；Docker 默认无头 + 截图扫码",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=180,
        help="等待登录秒数（默认 180）",
    )
    args = parser.parse_args(argv)
    if (args.platform or "").lower() not in ("jd", "jingdong", "京东"):
        logger.error("仅支持: python -m app.browser_login jd")
        return 2
    return asyncio.run(run_jd_login(headed=args.headed, wait_seconds=args.wait))


if __name__ == "__main__":
    sys.exit(main())
