"""Browser helpers: cookie paste (JD/TB/PDD) + optional Playwright."""

from app.browser.cookies_common import all_platforms_status, platform_status
from app.browser.jd_session import status_dict

__all__ = ["status_dict", "all_platforms_status", "platform_status"]
