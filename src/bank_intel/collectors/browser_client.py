from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

@dataclass
class BrowserFetchResult:
    requested_url: str
    final_url: str
    status_code: int | None
    html: str
    fetch_method: str = "playwright"

class BrowserClient:
    """Standard Chromium fallback for public JS-rendered pages. Never used to bypass paywalls, authentication, CAPTCHA, or access controls."""
    def __init__(self, *, headless: bool = True, timeout_ms: int = 30000):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.playwright = None
        self.browser = None
        self.context = None

    def __enter__(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context(locale="en-US", timezone_id="Asia/Colombo", viewport={"width": 1600, "height": 1000})
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.context: self.context.close()
        if self.browser: self.browser.close()
        if self.playwright: self.playwright.stop()

    def _dismiss_common_overlays(self, page) -> None:
        for pattern in [r"^close$", r"^no thanks$", r"^not now$", r"^dismiss$", r"^accept$", r"^accept all$", r"^i agree$", r"^got it$"]:
            try:
                locator = page.get_by_role("button", name=re.compile(pattern, re.IGNORECASE))
                if locator.count() > 0 and locator.first.is_visible():
                    locator.first.click(timeout=1200)
            except Exception:
                pass
        for selector in ["[aria-label='Close']", "[aria-label='close']", ".modal-close", ".popup-close", ".close-popup", ".close"]:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    locator.first.click(timeout=1000)
            except Exception:
                pass

    def get(self, url: str) -> BrowserFetchResult:
        if self.context is None:
            raise RuntimeError("BrowserClient must be used inside a with statement.")
        page = self.context.new_page()
        page.set_default_timeout(self.timeout_ms)
        response = None
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(1200)
            self._dismiss_common_overlays(page)
            page.wait_for_timeout(300)
            return BrowserFetchResult(url, page.url, response.status if response else None, page.content())
        except PlaywrightTimeoutError:
            return BrowserFetchResult(url, page.url, response.status if response else None, page.content())
        finally:
            page.close()

    def get_via_listing(self, *, listing_url: str, target_url: str) -> BrowserFetchResult:
        if self.context is None:
            raise RuntimeError("BrowserClient must be used inside a with statement.")
        page = self.context.new_page()
        page.set_default_timeout(self.timeout_ms)
        article_response = None
        try:
            page.goto(listing_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(1200)
            self._dismiss_common_overlays(page)
            target_path = urlparse(target_url).path
            locator = None
            for href in [target_url, target_path, target_path + "/"]:
                try:
                    candidate = page.locator(f'a[href="{href}"]')
                    if candidate.count() > 0:
                        locator = candidate.first
                        break
                except Exception:
                    continue
            if locator is not None:
                try:
                    with page.expect_navigation(wait_until="domcontentloaded", timeout=self.timeout_ms) as navigation:
                        locator.click()
                    article_response = navigation.value
                except PlaywrightTimeoutError:
                    pass
            else:
                print("[BROWSER] Article link not found on listing page.")
                print("[BROWSER] Falling back to normal public URL navigation.")
                article_response = page.goto(target_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(1200)
            self._dismiss_common_overlays(page)
            page.wait_for_timeout(300)
            return BrowserFetchResult(target_url, page.url, article_response.status if article_response else None, page.content(), "playwright_listing_navigation")
        finally:
            page.close()
