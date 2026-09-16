"""FastAPI app — debug/admin UI + internal API for QQ bot."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import ADAPTERS, get_adapter
from app.api.bot import router as bot_router
from app.auth import auth_required, check_auth, require_auth, set_auth_cookie
from app.config import get_config, load_config
from app.db import get_db, init_db
from app.models import PriceHistory, Product
from app.scheduler import start_scheduler, stop_scheduler
from app.services import apply_price_update, check_product, check_watch, compute_landing
from app.url_normalize import normalize_url

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

PLATFORM_CHOICES = [
    ("jd", "京东"),
    ("taobao", "淘宝/天猫"),
    ("pdd", "拼多多"),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_config(force=True)
    Path("data").mkdir(parents=True, exist_ok=True)
    await init_db()
    start_scheduler()
    cfg = get_config()
    logger.info(
        "price-watch started (web=%s:%s qqofficial=%s onebot=%s)",
        cfg.web.host,
        cfg.web.port,
        cfg.qqofficial.enabled,
        cfg.onebot.enabled,
    )
    yield
    stop_scheduler()


app = FastAPI(title="到手价监控", version="0.3.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(bot_router)


def _ctx(request: Request, **kwargs):
    cfg = get_config()
    return {
        "request": request,
        "auth_required": auth_required(),
        "authed": check_auth(request),
        "platforms": PLATFORM_CHOICES,
        "qqofficial_enabled": cfg.qqofficial.enabled,
        "onebot_enabled": cfg.onebot.enabled,
        **kwargs,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "price-watch"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not auth_required():
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", _ctx(request, error=None))


@app.post("/login")
async def login_submit(request: Request, token: str = Form(...)):
    expected = (get_config().adminToken or "").strip()
    if token.strip() != expected:
        return templates.TemplateResponse(
            "login.html",
            _ctx(request, error="Token 不正确"),
            status_code=401,
        )
    resp = RedirectResponse("/", status_code=302)
    set_auth_cookie(resp, token.strip())
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login" if auth_required() else "/", status_code=302)
    resp.delete_cookie("admin_token")
    return resp


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Bot API uses its own X-Admin-Token check; skip cookie redirect
    public = (
        path in ("/health", "/login")
        or path.startswith("/static")
        or path.startswith("/api/bot")
    )
    if public or path == "/favicon.ico":
        return await call_next(request)
    if auth_required() and not check_auth(request):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "未授权"}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    q = await db.execute(select(Product).order_by(desc(Product.updated_at)))
    products = list(q.scalars().all())
    return templates.TemplateResponse(
        "index.html",
        _ctx(request, products=products, adapter_info=ADAPTERS),
    )


@app.get("/browser/jd", response_class=HTMLResponse)
async def browser_jd_page(request: Request):
    """京东 Playwright 登录：展示截图 + 状态，确认保存 storage_state。"""
    from app.browser.jd_session import (
        ensure_browser_dirs,
        login_screenshot_path,
        status_dict,
    )

    ensure_browser_dirs()
    st = status_dict()
    shot = login_screenshot_path()
    return templates.TemplateResponse(
        "browser_jd.html",
        _ctx(
            request,
            status=st,
            has_screenshot=shot.is_file(),
            screenshot_url="/browser/jd/screenshot" if shot.is_file() else None,
            message=None,
        ),
    )


@app.get("/browser/jd/screenshot")
async def browser_jd_screenshot():
    from app.browser.jd_session import login_screenshot_path

    shot = login_screenshot_path()
    if not shot.is_file():
        return JSONResponse({"detail": "no screenshot"}, status_code=404)
    return FileResponse(str(shot), media_type="image/png")


@app.post("/browser/jd/start")
async def browser_jd_start(request: Request):
    """Kick off background login screenshot session (short wait)."""
    import asyncio

    from app.browser.jd_session import ensure_browser_dirs, status_dict

    ensure_browser_dirs()

    async def _run():
        try:
            from app.browser_login import run_jd_login

            # Short wait for Web flow; user can re-click / use CLI for longer
            await run_jd_login(headed=False, wait_seconds=120)
        except Exception as e:
            logger.exception("browser login job failed: %s", e)

    asyncio.create_task(_run())
    st = status_dict()
    from app.browser.jd_session import login_screenshot_path

    shot = login_screenshot_path()
    return templates.TemplateResponse(
        "browser_jd.html",
        _ctx(
            request,
            status=st,
            has_screenshot=shot.is_file(),
            screenshot_url="/browser/jd/screenshot" if shot.is_file() else None,
            message="已在后台启动登录会话（约 2 分钟）。请扫码后点「刷新状态」；也可在容器内运行：python -m app.browser_login jd",
        ),
    )


@app.post("/browser/jd/refresh")
async def browser_jd_refresh(request: Request):
    from app.browser.jd_session import (
        ensure_browser_dirs,
        login_screenshot_path,
        status_dict,
    )

    ensure_browser_dirs()
    st = status_dict()
    shot = login_screenshot_path()
    return templates.TemplateResponse(
        "browser_jd.html",
        _ctx(
            request,
            status=st,
            has_screenshot=shot.is_file(),
            screenshot_url="/browser/jd/screenshot" if shot.is_file() else None,
            message="已刷新状态",
        ),
    )


@app.get("/products/new", response_class=HTMLResponse)
async def product_new(request: Request):
    return templates.TemplateResponse(
        "product_form.html",
        _ctx(request, product=None, title="添加商品（调试）"),
    )


@app.post("/products/new")
async def product_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    platform: str = Form(...),
    url: str = Form(""),
    sku_id: str = Form(""),
    owner_openid: str = Form(""),
    list_price: Optional[str] = Form(None),
    tax_amount: str = Form("0"),
    coupon_amount: str = Form("0"),
    full_reduction: str = Form("0"),
    rebate_estimate: str = Form("0"),
    target_price: Optional[str] = Form(None),
    check_interval_minutes: Optional[str] = Form(None),
    enabled: Optional[str] = Form(None),
    note: str = Form(""),
):
    require_auth(request)
    adapter = get_adapter(platform)
    raw_url = (url or "").strip()
    info = normalize_url(raw_url) if raw_url else None
    resolved_sku = (sku_id or "").strip() or (info.sku_id if info else None)
    canonical = info.canonical_url if info else (raw_url or None)
    store_url = canonical or raw_url
    if info and info.platform and info.platform != platform:
        # Prefer form platform but keep normalized sku/url when same family
        pass
    lp = float(list_price) if list_price not in (None, "") else None
    tax = float(tax_amount or 0)
    coupon = float(coupon_amount or 0)
    fr = float(full_reduction or 0)
    rebate = float(rebate_estimate or 0)
    landing = compute_landing(lp, coupon, fr, tax)
    product = Product(
        name=name.strip(),
        platform=platform,
        url=store_url,
        canonical_url=canonical,
        sku_id=resolved_sku,
        owner_openid=(owner_openid or "").strip() or None,
        list_price=lp,
        tax_amount=tax,
        coupon_amount=coupon,
        full_reduction=fr,
        rebate_estimate=rebate,
        landing_price=landing,
        target_price=float(target_price) if target_price not in (None, "") else None,
        check_interval_minutes=(
            int(check_interval_minutes)
            if check_interval_minutes not in (None, "")
            else None
        ),
        enabled=enabled is not None,
        needs_manual=not adapter.supports_auto,
        note=(note or "").strip() or None,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    if landing is not None:
        await apply_price_update(db, product, landing_price=landing, source="manual", send_alert=False)
    return RedirectResponse(f"/products/{product.id}", status_code=302)


@app.get("/products/{product_id}", response_class=HTMLResponse)
async def product_detail(
    request: Request,
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product:
        return HTMLResponse("商品不存在", status_code=404)
    hq = await db.execute(
        select(PriceHistory)
        .where(PriceHistory.product_id == product_id)
        .order_by(desc(PriceHistory.recorded_at))
        .limit(50)
    )
    history = list(hq.scalars().all())
    spark = list(reversed([h.landing_price for h in history[:30]]))
    return templates.TemplateResponse(
        "product_detail.html",
        _ctx(
            request,
            product=product,
            history=history,
            spark=spark,
            adapter=get_adapter(product.platform),
        ),
    )


@app.get("/products/{product_id}/edit", response_class=HTMLResponse)
async def product_edit(
    request: Request,
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product:
        return HTMLResponse("商品不存在", status_code=404)
    return templates.TemplateResponse(
        "product_form.html",
        _ctx(request, product=product, title="编辑商品"),
    )


@app.post("/products/{product_id}/edit")
async def product_update(
    request: Request,
    product_id: int,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    platform: str = Form(...),
    url: str = Form(""),
    sku_id: str = Form(""),
    owner_openid: str = Form(""),
    list_price: Optional[str] = Form(None),
    tax_amount: str = Form("0"),
    coupon_amount: str = Form("0"),
    full_reduction: str = Form("0"),
    rebate_estimate: str = Form("0"),
    target_price: Optional[str] = Form(None),
    check_interval_minutes: Optional[str] = Form(None),
    enabled: Optional[str] = Form(None),
    note: str = Form(""),
):
    require_auth(request)
    product = await db.get(Product, product_id)
    if not product:
        return HTMLResponse("商品不存在", status_code=404)
    adapter = get_adapter(platform)
    product.name = name.strip()
    product.platform = platform
    raw_url = (url or "").strip()
    info = normalize_url(raw_url) if raw_url else None
    product.url = (info.canonical_url if info else raw_url) or raw_url
    product.canonical_url = info.canonical_url if info else (raw_url or None)
    product.sku_id = (sku_id or "").strip() or (info.sku_id if info else None)
    product.owner_openid = (owner_openid or "").strip() or None
    product.list_price = float(list_price) if list_price not in (None, "") else None
    product.tax_amount = float(tax_amount or 0)
    product.coupon_amount = float(coupon_amount or 0)
    product.full_reduction = float(full_reduction or 0)
    product.rebate_estimate = float(rebate_estimate or 0)
    product.landing_price = compute_landing(
        product.list_price,
        product.coupon_amount,
        product.full_reduction,
        product.tax_amount,
    )
    product.target_price = float(target_price) if target_price not in (None, "") else None
    product.check_interval_minutes = (
        int(check_interval_minutes) if check_interval_minutes not in (None, "") else None
    )
    product.enabled = enabled is not None
    product.note = (note or "").strip() or None
    if not adapter.supports_auto:
        product.needs_manual = True
    await db.commit()
    return RedirectResponse(f"/products/{product.id}", status_code=302)


@app.post("/products/{product_id}/delete")
async def product_delete(
    request: Request,
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    require_auth(request)
    product = await db.get(Product, product_id)
    if product:
        hq = await db.execute(
            select(PriceHistory).where(PriceHistory.product_id == product_id)
        )
        for h in hq.scalars().all():
            await db.delete(h)
        await db.delete(product)
        await db.commit()
    return RedirectResponse("/", status_code=302)


@app.post("/products/{product_id}/check")
async def product_check(
    request: Request,
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    require_auth(request)
    product = await db.get(Product, product_id)
    if not product:
        return HTMLResponse("商品不存在", status_code=404)
    await check_product(db, product)
    return RedirectResponse(f"/products/{product_id}", status_code=302)


@app.post("/products/{product_id}/update-price")
async def product_update_price(
    request: Request,
    product_id: int,
    db: AsyncSession = Depends(get_db),
    list_price: Optional[str] = Form(None),
    tax_amount: Optional[str] = Form(None),
    coupon_amount: Optional[str] = Form(None),
    full_reduction: Optional[str] = Form(None),
    rebate_estimate: Optional[str] = Form(None),
    landing_price: Optional[str] = Form(None),
):
    require_auth(request)
    product = await db.get(Product, product_id)
    if not product:
        return HTMLResponse("商品不存在", status_code=404)

    def _f(v: Optional[str]) -> Optional[float]:
        if v is None or v == "":
            return None
        return float(v)

    await apply_price_update(
        db,
        product,
        list_price=_f(list_price),
        tax_amount=_f(tax_amount),
        coupon_amount=_f(coupon_amount),
        full_reduction=_f(full_reduction),
        rebate_estimate=_f(rebate_estimate),
        landing_price=_f(landing_price),
        source="manual",
        send_alert=True,
    )
    return RedirectResponse(f"/products/{product_id}", status_code=302)


@app.get("/api/products")
async def api_products(db: AsyncSession = Depends(get_db)):
    q = await db.execute(select(Product).order_by(Product.id))
    items = []
    for p in q.scalars().all():
        items.append(
            {
                "id": p.id,
                "name": p.name,
                "platform": p.platform,
                "url": p.url,
                "owner_openid": p.owner_openid,
                "landing_price": p.landing_price,
                "target_price": p.target_price,
                "enabled": p.enabled,
                "needs_manual": p.needs_manual,
            }
        )
    return {"products": items}
