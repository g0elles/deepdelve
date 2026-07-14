import atexit
import logging
import os
import sys

try:
    from playwright.sync_api import sync_playwright, Error as PlaywrightError
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

# Set once a launch fails because the browser binary itself is missing (pip install gets you the
# playwright package, but `playwright install chromium` is a separate ~150-300MB download) — a run
# with 15 stubbed fetches must not retry a doomed launch 15 times, each paying the launch-attempt
# cost before failing.
_launch_failed = False

# Real (non-headless) Chromium recovers sites headless Chromium cannot — confirmed live 2026-07-14:
# MDPI hard-blocks headless Chromium at the network/edge level (an immediate Akamai "Access Denied"
# fired before any JS/DOM even loads, so JS-side stealth tweaks — navigator.webdriver override,
# custom UA/plugins/locale, --disable-blink-features=AutomationControlled — made zero difference),
# but a genuinely headed browser sailed straight through, both with a real X session and under a
# freshly-started virtual one (Xvfb via pyvirtualdisplay, DISPLAY unset beforehand to confirm it
# wasn't just riding the real desktop's session). So headed is tried first whenever a display is or
# can be made available; only a genuinely display-less environment (no DISPLAY, no Xvfb binary
# installed, or on a non-Linux OS where a virtual X display doesn't apply) falls back to headless.
_virtual_display = None
_virtual_display_failed = False


def _display_available() -> bool:
    """Best-effort: get a real or virtual display so headed Chromium can run. Returns False only
    when there's no way to attempt headed mode at all — the caller then falls back to headless.
    Starts at most ONE Xvfb instance per process (reused across fetches, not restarted per call —
    spawning an X server takes real time)."""
    global _virtual_display, _virtual_display_failed
    if os.environ.get("DISPLAY"):
        return True  # A real (or already-started virtual) display is present.
    if sys.platform != "linux":
        # Windows/macOS normally already have a real desktop session for a headed launch to use;
        # pyvirtualdisplay/Xvfb is Linux-only (X11), doesn't apply here. If there's genuinely no
        # display (e.g. a Windows service with no interactive session), Chromium's own launch
        # failure is caught by the caller and falls back to headless.
        return True
    if _virtual_display is not None:
        return True
    if _virtual_display_failed:
        return False
    try:
        from pyvirtualdisplay import Display
        _virtual_display = Display(visible=False, size=(1920, 1080))
        _virtual_display.start()
        atexit.register(_virtual_display.stop)
        return True
    except Exception:
        # pyvirtualdisplay not installed, or its underlying Xvfb binary isn't (apt install xvfb) —
        # fails open to headless rather than blocking the fetch fallback entirely.
        _virtual_display_failed = True
        return False


def fetch_via_headless_browser(url: str, timeout_ms: int = 30000) -> str | None:
    """Best-effort fallback fetch for pages that bot-wall a plain httpx GET (Akamai/Cloudflare JS
    challenges, browser-version-sniffing blocks, or a headless-specific fingerprint block — see
    _display_available's docstring) — see tools/web.py::_fetch_raw's HTML branch, which only calls
    this after a plain fetch already came back looking like a stub. Tries a real/virtual-display
    headed browser first (recovers more sites), falls back to headless if no display is available.
    Returns the rendered page's raw HTML, or None on ANY failure (missing browser binary, no
    display, navigation timeout, crash) so the caller can fall back to the original stub result
    rather than losing the run. Never raises."""
    global _launch_failed
    if not _PLAYWRIGHT_AVAILABLE or _launch_failed:
        return None

    headed = _display_available()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # Give client-side bot challenges (Akamai's meta-refresh/JS-verify interstitials,
                # etc.) a moment to resolve and redirect before reading the DOM.
                page.wait_for_timeout(2000)
                return page.content()
            finally:
                browser.close()
    except PlaywrightError as e:
        if "Executable doesn't exist" in str(e):
            _launch_failed = True
            logging.warning(
                "playwright is installed but its browser binary is not — run "
                "`playwright install chromium` to enable the headless-fetch fallback. "
                "Disabling it for the rest of this run."
            )
        elif headed:
            # Headed launch can fail in ways headless wouldn't (a broken/no display we thought was
            # usable) — one retry in headless mode rather than giving up the whole fallback.
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    try:
                        page = browser.new_page()
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(2000)
                        return page.content()
                    finally:
                        browser.close()
            except Exception:
                return None
        return None
    except Exception:
        return None
