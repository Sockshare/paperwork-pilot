"""Settings storage.

Settings live in ONE small JSON file in the user's home folder:
    ~/.paperwork_pilot/config.json

Environment variables override the file (handy for developers):
    PAPERWORK_PROVIDER   = ollama | openai | gemini | offline
    PAPERWORK_MODEL      = model name (e.g. llama3.2:3b, gpt-4o-mini, gemini-2.0-flash)
    OLLAMA_HOST          = http://127.0.0.1:11434
    OPENAI_API_KEY       = sk-...
    OPENAI_BASE_URL      = https://api.openai.com/v1  (or LM Studio / Groq / etc.)
    GEMINI_API_KEY       = AI...
    PAPERWORK_DATA_DIR   = where history + config are stored
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

VALID_PROVIDERS = ("ollama", "openai", "gemini", "offline")

DEFAULT_MODELS = {
    "ollama": "llama3.2:3b",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "offline": "rules-engine",
}


def data_dir() -> Path:
    override = os.environ.get("PAPERWORK_DATA_DIR")
    base = Path(override) if override else Path.home() / ".paperwork_pilot"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)  # documents and keys live here: private to this user
    except OSError:
        pass
    return base


def config_path() -> Path:
    return data_dir() / "config.json"


@dataclass
class Settings:
    provider: str = "offline"          # which AI engine to use
    model: str = ""                    # blank = sensible default for the provider
    ollama_host: str = "http://127.0.0.1:11434"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_api_key: str = ""
    save_history: bool = True          # keep analyzed documents on this computer
    setup_complete: bool = False       # has the user finished the first-run screen?
    extra: dict = field(default_factory=dict)  # reserved for future use

    # ---- helpers -------------------------------------------------------
    def effective_model(self) -> str:
        return self.model or DEFAULT_MODELS.get(self.provider, "")

    def public_dict(self) -> dict:
        """Safe to send to the browser: API keys are masked."""
        d = asdict(self)
        d["model"] = self.effective_model()
        d["openai_api_key"] = _mask(self.openai_api_key)
        d["gemini_api_key"] = _mask(self.gemini_api_key)
        d["has_openai_key"] = bool(self.openai_api_key)
        d["has_gemini_key"] = bool(self.gemini_api_key)
        return d


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 12:
        return "*" * 8
    return secret[:3] + "*" * 8 + secret[-4:]


def _load_file() -> Settings:
    s = Settings()
    p = config_path()
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if hasattr(s, k) and k != "extra":
                    setattr(s, k, v)
        except (json.JSONDecodeError, OSError):
            pass  # a corrupt file should never stop the app from starting
    return s


def load_settings(include_env: bool = True) -> Settings:
    s = _load_file()
    if not include_env:
        return s

    # Environment overrides (developer convenience) — never written back to the file.
    env_provider = os.environ.get("PAPERWORK_PROVIDER")
    if env_provider in VALID_PROVIDERS:
        s.provider = env_provider
    if os.environ.get("PAPERWORK_MODEL"):
        s.model = os.environ["PAPERWORK_MODEL"]
    if os.environ.get("OLLAMA_HOST"):
        host = os.environ["OLLAMA_HOST"]
        if not host.startswith("http"):
            host = "http://" + host
        s.ollama_host = host
    if os.environ.get("OPENAI_API_KEY"):
        s.openai_api_key = os.environ["OPENAI_API_KEY"]
    if os.environ.get("OPENAI_BASE_URL"):
        s.openai_base_url = os.environ["OPENAI_BASE_URL"]
    if os.environ.get("GEMINI_API_KEY"):
        s.gemini_api_key = os.environ["GEMINI_API_KEY"]

    if s.provider not in VALID_PROVIDERS:
        s.provider = "offline"
    return s


def save_settings(s: Settings) -> None:
    p = config_path()
    p.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
    try:
        os.chmod(p, 0o600)  # keys are private to this user
    except OSError:
        pass


class SettingsError(ValueError):
    """A setting value the user typed is unusable. Message is user-friendly."""


def validate_changes(changes: dict) -> dict:
    """Clean up values from the browser. Raises SettingsError with a plain message."""
    from .llm.providers import normalize_host  # local import to avoid a cycle

    out = dict(changes)
    if "ollama_host" in out:
        try:
            out["ollama_host"] = normalize_host(str(out["ollama_host"]))
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
    if "openai_base_url" in out:
        url = str(out["openai_base_url"]).strip().rstrip("/") or "https://api.openai.com/v1"
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        out["openai_base_url"] = url
    if "provider" in out and out["provider"] not in VALID_PROVIDERS:
        raise SettingsError("Unknown AI option.")
    if "model" in out and out["model"] is not None:
        out["model"] = str(out["model"]).strip()[:120]
    return out


def update_settings(changes: dict) -> Settings:
    """Apply a partial update coming from the browser and persist it.

    Keys: a masked value ('sk-a****1234') means 'leave as is'; an empty string
    means 'remove the saved key'. Environment-provided values are never written.
    """
    changes = validate_changes(changes)
    s = load_settings(include_env=False)
    for k, v in changes.items():
        if k in ("openai_api_key", "gemini_api_key") and v and "*" in str(v):
            continue
        if hasattr(s, k) and k != "extra":
            setattr(s, k, v)
    if s.provider not in VALID_PROVIDERS:
        s.provider = "offline"
    save_settings(s)
    return load_settings()
