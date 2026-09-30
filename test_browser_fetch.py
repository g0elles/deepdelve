import sys
sys.path.insert(0, "src")


def test_force_virtual_display_ignores_real_display():
    """DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1 must never fall through to the real desktop DISPLAY,
    even when one is set — that's the whole point (headed Chromium was popping up on the user's
    real screen)."""
    from utils import browser_fetch as bf
    old_force, old_env = bf._FORCE_VIRTUAL_DISPLAY, dict(__import__("os").environ)
    try:
        bf._FORCE_VIRTUAL_DISPLAY = True
        __import__("os").environ["DISPLAY"] = ":0"  # simulate a real desktop session present
        bf._start_virtual_display = lambda: ":99"
        assert bf._display_env() == (True, ":99")
        bf._start_virtual_display = lambda: None  # Xvfb unavailable: must not fall back to :0
        assert bf._display_env() == (False, None)
    finally:
        bf._FORCE_VIRTUAL_DISPLAY = old_force
        __import__("os").environ.clear()
        __import__("os").environ.update(old_env)


def test_unforced_uses_real_display_when_present():
    from utils import browser_fetch as bf
    old_force, old_env = bf._FORCE_VIRTUAL_DISPLAY, dict(__import__("os").environ)
    try:
        bf._FORCE_VIRTUAL_DISPLAY = False
        __import__("os").environ["DISPLAY"] = ":0"
        assert bf._display_env() == (True, None)
    finally:
        bf._FORCE_VIRTUAL_DISPLAY = old_force
        __import__("os").environ.clear()
        __import__("os").environ.update(old_env)


def test_launch_env_strips_wayland_display():
    """Regression: overriding DISPLAY alone is not enough on a Wayland session — Chromium's Ozone
    still connects to the real compositor via WAYLAND_DISPLAY if it's left in the launch env
    (caught live 2026-09-29: two popups on the real desktop despite a correct DISPLAY override)."""
    from utils import browser_fetch as bf
    old_env = dict(__import__("os").environ)
    try:
        __import__("os").environ["WAYLAND_DISPLAY"] = "wayland-0"
        __import__("os").environ["DISPLAY"] = ":0"
        env = bf._launch_env(":99")
        assert "WAYLAND_DISPLAY" not in env
        assert env["DISPLAY"] == ":99"
        assert env["XDG_SESSION_TYPE"] == "x11"
        assert bf._launch_env(None) is None
    finally:
        __import__("os").environ.clear()
        __import__("os").environ.update(old_env)


if __name__ == "__main__":
    test_force_virtual_display_ignores_real_display()
    test_unforced_uses_real_display_when_present()
    test_launch_env_strips_wayland_display()
    print("OK")
