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

# Set DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1 to always route headed Chromium to an Xvfb display, even
# when a real desktop DISPLAY is present: otherwise headed Chromium launches ON the user's real
# desktop and steals focus/pops a visible window on every fetch, which makes the machine unusable
# during a run (confirmed live, session_status 2026-09-26 — the prior workaround was manually
# unsetting DISPLAY/WAYLAND_DISPLAY before launching the whole pipeline process).
_FORCE_VIRTUAL_DISPLAY = os.environ.get("DEEPDELVE_FORCE_VIRTUAL_DISPLAY", "").strip().lower() in ("1", "true", "yes")


def _start_virtual_display() -> str | None:
    """Starts (once per process, reused across fetches) an Xvfb display and returns its DISPLAY
    string, or None if pyvirtualdisplay/Xvfb isn't available."""
    global _virtual_display, _virtual_display_failed
    if _virtual_display is not None:
        return _virtual_display.new_display_var
    if _virtual_display_failed:
        return None
    try:
        from pyvirtualdisplay import Display
        _virtual_display = Display(visible=False, size=(1920, 1080))
        _virtual_display.start()
        atexit.register(_virtual_display.stop)
        return _virtual_display.new_display_var
    except Exception:
        # pyvirtualdisplay not installed, or its underlying Xvfb binary isn't (apt install xvfb).
        _virtual_display_failed = True
        return None


def _display_env() -> tuple[bool, str | None]:
    """Best-effort: decide whether headed Chromium can run, and on which DISPLAY. Returns
    (headed_possible, display_override) where display_override is a DISPLAY string to pass to the
    browser subprocess's own env (leaving the current process's ambient DISPLAY untouched), or None
    to just inherit it. headed_possible=False means the caller falls back to headless."""
    if _FORCE_VIRTUAL_DISPLAY and sys.platform == "linux":
        d = _start_virtual_display()
        return (True, d) if d else (False, None)  # forced: never fall through to the real desktop
    if os.environ.get("DISPLAY"):
        return True, None  # A real (or already-started virtual) display is present.
    if sys.platform != "linux":
        # Windows/macOS normally already have a real desktop session for a headed launch to use;
        # pyvirtualdisplay/Xvfb is Linux-only (X11), doesn't apply here. If there's genuinely no
        # display (e.g. a Windows service with no interactive session), Chromium's own launch
        # failure is caught by the caller and falls back to headless.
        return True, None
    d = _start_virtual_display()
    return (True, d) if d else (False, None)


def _launch_env(display_override: str | None) -> dict[str, str] | None:
    """Env for the Chromium subprocess, or None to just inherit the caller's. Chromium's Ozone
    auto-detects Wayland via WAYLAND_DISPLAY and prefers the real compositor over an X11 DISPLAY
    override if it's left in the env — confirmed live (2026-09-29): overriding DISPLAY alone still
    popped a window on the real Wayland desktop. Must also drop WAYLAND_DISPLAY and pin
    XDG_SESSION_TYPE=x11 so Chromium can only reach the virtual X11 display, never the real one."""
    if not display_override:
        return None
    env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
    env["DISPLAY"] = display_override
    env["XDG_SESSION_TYPE"] = "x11"
    return env


def fetch_via_headless_browser(url: str, timeout_ms: int = 30000) -> str | None:
    """Best-effort fallback fetch for pages that bot-wall a plain httpx GET (Akamai/Cloudflare JS
    challenges, browser-version-sniffing blocks, or a headless-specific fingerprint block — see
    _display_env's docstring) — see tools/web.py::_fetch_raw's HTML branch, which only calls
    this after a plain fetch already came back looking like a stub. Tries a real/virtual-display
    headed browser first (recovers more sites), falls back to headless if no display is available.
    Returns the rendered page's raw HTML, or None on ANY failure (missing browser binary, no
    display, navigation timeout, crash) so the caller can fall back to the original stub result
    rather than losing the run. Never raises."""
    global _launch_failed
    if not _PLAYWRIGHT_AVAILABLE or _launch_failed:
        return None

    headed, display_override = _display_env()
    try:
        with sync_playwright() as p:
            launch_kwargs = {"headless": not headed}
            env = _launch_env(display_override)
            if env:
                launch_kwargs["env"] = env
            browser = p.chromium.launch(**launch_kwargs)
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
