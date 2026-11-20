from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
MASTER_PATH = ROOT / "data" / "master.yaml"
BACKUPS_DIR = ROOT / "data" / "backups"
RESUMES_DIR = ROOT / "resumes"
STATE_DIR = ROOT / "state" / "runs"
TEMPLATES_DIR = ROOT / "app" / "templates"

load_dotenv(ROOT / ".env")

DEFAULT_CONFIG: dict[str, Any] = {
    "provider": "nvidia",
    "models": {
        "nvidia": "nvidia/nemotron-3.5-bolt-e4b",
        "ollama": "qwen3:4b-instruct-32k",
    },
    "tailoring": {
        "intensity": "balanced",
        "max_projects": 3,
        "max_experiences": 2,
        "max_skill_groups": 3,
        "max_bullets_per_item": 3,
        "include_summary": False,
    },
    "output_dir": "output",
}

PROVIDERS: dict[str, dict[str, str]] = {
    "nvidia": {
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "base_url_env": "NVIDIA_BASE_URL",
        "api_key_env": "NVIDIA_API_KEY",
    },
    "ollama": {
        "label": "Ollama (local)",
        "base_url": "http://localhost:11434/v1",
        "base_url_env": "OLLAMA_BASE_URL",
        "api_key_env": "",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
        if isinstance(data, dict):
            cfg = _deep_merge(cfg, data)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def provider_info(provider: str, cfg: dict[str, Any] | None = None) -> dict[str, str]:
    cfg = cfg or load_config()
    definition = PROVIDERS.get(provider)
    if not definition:
        raise ValueError(f"Unknown provider: {provider}")
    base_url = os.getenv(definition["base_url_env"], definition["base_url"]).rstrip("/")
    api_key = ""
    if definition["api_key_env"]:
        api_key = os.getenv(definition["api_key_env"], "").strip()
    model = (cfg.get("models") or {}).get(provider) or ""
    return {
        "name": provider,
        "label": definition["label"],
        "base_url": base_url,
        "api_key": api_key,
        "api_key_env": definition["api_key_env"],
        "model": model,
    }


def provider_key_status(provider: str) -> bool:
    definition = PROVIDERS.get(provider, {})
    env_name = definition.get("api_key_env", "")
    if not env_name:
        return True
    return bool(os.getenv(env_name, "").strip())


def output_root(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config()
    raw = Path(str(cfg.get("output_dir") or "output")).expanduser()
    if not raw.is_absolute():
        raw = ROOT / raw
    return raw


def ensure_dirs() -> None:
    for path in (MASTER_PATH.parent, BACKUPS_DIR, RESUMES_DIR, STATE_DIR, output_root()):
        path.mkdir(parents=True, exist_ok=True)
