import os
import sys
import yaml
import copy

def _get_config_path_from_args():
    for i, arg in enumerate(sys.argv):
        if arg in ["--config", "-c"] and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i+1])
    return None

# --- APPLICATION IDENTITY ---
APP_NAME = "deepdelve"                    # Used for config/log folders
APP_TITLE = "DeepDelve"                   # Used for UI branding
APP_DESCRIPTION = "Multi-agent deep web research and document analysis assistant with domain-specialized panels."

_DEFAULT_CONFIG_DIR = os.path.expanduser(f"~/.{APP_NAME}")
_CONFIG_PATH = _get_config_path_from_args() or os.path.join(_DEFAULT_CONFIG_DIR, "config.yaml")

_DEFAULTS = {
    "api": {
        "openai_base_url": "http://localhost:8080/v1",
        "openai_model": "local-model",
    },
    "settings": {
        "enable_thinking": False,
        "concurrency": {
            "max_concurrent_tasks": 1
        },
        # Headless-browser (Playwright) retry for a fetch that comes back looking like a bot-wall
        # stub (Akamai/Cloudflare JS challenges, browser-version-sniffing blocks — see
        # tools/web.py::_fetch_raw and ROADMAP.md's Springer/ScienceDirect/MDPI finding,
        # 2026-07-14). Default on: a no-op with no latency cost when Playwright isn't installed.
        "fetch": {
            "headless_fallback": True
        },
        "quotas": {},
        "workspace": {
            "type": "memory",
            "dir": "~/.{APP_NAME}/workspace"
        }
    }
}

cfg: dict = {}
# Snapshot of cfg as loaded from disk, BEFORE env-var overlays, CLI --depth/--style presets, or
# the workspace-dir tilde/abspath expansion below ever mutate the live `cfg` dict in place.
# save_config() persists against THIS, not `cfg` — see its docstring.
_file_cfg: dict = {}

def _deep_merge(base: dict, overlay: dict) -> dict:
    """Merge overlay into base, recursively for nested dicts."""
    result = copy.deepcopy(base)
    for k, v in overlay.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result

def load_config() -> dict:
    """Load config from YAML file, falling back to defaults for missing keys."""
    global cfg, _file_cfg
    file_cfg = {}

    if not os.path.exists(_CONFIG_PATH):
        # Lives under tools/ (a real installed package), not next to this loose top-level module
        # — [tool.setuptools.package-data] can only bundle data files inside an actual package,
        # not a py-module (second full audit, 2026-07-12, item 5).
        bundled_config = os.path.join(os.path.dirname(__file__), "tools", "config_template.yaml")
        os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
        if os.path.exists(bundled_config):
            import shutil
            shutil.copy(bundled_config, _CONFIG_PATH)
        else:
            with open(_CONFIG_PATH, "w") as f:
                yaml.dump(_DEFAULTS, f, default_flow_style=False, sort_keys=False)

    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, "r") as f:
            file_cfg = yaml.safe_load(f) or {}

    cfg = _deep_merge(_DEFAULTS, file_cfg)
    _file_cfg = copy.deepcopy(cfg)  # pristine, pre-expansion/pre-env-overlay snapshot

    # Expand APP_NAME placeholder and tilde (~) in workspace directory
    if "settings" in cfg and "workspace" in cfg["settings"]:
        ws = cfg["settings"]["workspace"]
        if "dir" in ws and isinstance(ws["dir"], str):
            dir_str = ws["dir"].replace("{APP_NAME}", APP_NAME)
            ws["dir"] = os.path.abspath(os.path.expanduser(dir_str))

    # Overlay API keys from environment if set (env takes priority for secrets)
    if os.environ.get("OPENAI_API_BASE"):
        cfg["api"]["openai_base_url"] = os.environ["OPENAI_API_BASE"]
    if os.environ.get("OPENAI_MODEL"):
        cfg["api"]["openai_model"] = os.environ["OPENAI_MODEL"]

    return cfg

# Settings the TUI actually exposes a toggle for (/toggle_thinking, /toggle_persistence) — the
# only things save_config() is allowed to persist. Anything else in the live `cfg` (an
# OPENAI_MODEL/OPENAI_API_BASE env-var overlay, a --depth/--style CLI preset's quota/style
# mutations, the tilde-expanded absolute workspace dir) is session-scoped and must never leak
# into the user's saved config.yaml.
_PERSISTABLE_SETTINGS_KEYS = ("enable_thinking", "enable_session_persistence")


def save_config() -> None:
    """Persist ONLY the specific runtime toggles the TUI exposes, applied onto the config AS
    LOADED FROM DISK (_file_cfg) — never the fully-live `cfg`, which by call time may carry an
    env var model override, a CLI preset's quota mutations, or the expanded workspace dir. A
    benchmark run started with an OPENAI_MODEL override, followed by one /toggle_thinking, used
    to silently rewrite the user's saved default model to whatever that run happened to use
    (second full audit, 2026-07-12, item 2)."""
    save_data = copy.deepcopy(_file_cfg)
    live_settings = cfg.get("settings", {})
    save_data.setdefault("settings", {})
    for key in _PERSISTABLE_SETTINGS_KEYS:
        if key in live_settings:
            save_data["settings"][key] = live_settings[key]

    # Strip out sensitive API keys before writing if any are stored in keys
    if "api" in save_data:
        save_data["api"].pop("openai_api_key", None)

    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(save_data, f, default_flow_style=False, sort_keys=False)

# Auto-initialize on import so it's globally available
load_config()
