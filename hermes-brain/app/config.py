from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from typing import Any, Dict

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency
    yaml = None


@dataclass(frozen=True)
class AppConfig:
    base_dir: Path
    knowledge_dir: Path
    data_dir: Path
    indexes_dir: Path
    index_path: Path
    models_dir: Path
    llama_bin: Path
    model_path: Path
    log_level: str
    chunk_size: int
    chunk_overlap: int
    top_k: int
    score_threshold: float
    local_confidence_threshold: float
    allow_web: bool
    web_max_sources: int
    web_timeout_seconds: int
    web_user_agent: str
    web_require_explicit: bool


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None:
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
            if isinstance(data, dict):
                return data
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return {}


def load_config(config_path: Path | None = None) -> AppConfig:
    base_dir = Path(__file__).resolve().parents[1]

    if config_path is None:
        env_path = os.getenv("HERMES_CONFIG")
        if env_path:
            config_path = Path(env_path)
        else:
            if (base_dir / "config.yaml").exists():
                config_path = base_dir / "config.yaml"
            elif (base_dir / "config.yml").exists():
                config_path = base_dir / "config.yml"

    file_cfg: Dict[str, Any] = _load_yaml(config_path) if config_path else {}

    knowledge_dir = Path(
        os.getenv("HERMES_KNOWLEDGE_DIR", file_cfg.get("knowledge_dir", base_dir / "knowledge"))
    )
    data_dir = Path(os.getenv("HERMES_DATA_DIR", file_cfg.get("data_dir", base_dir / "data")))
    indexes_dir = Path(
        os.getenv("HERMES_INDEXES_DIR", file_cfg.get("indexes_dir", data_dir / "indexes"))
    )
    index_path = Path(
        os.getenv("HERMES_INDEX_PATH", file_cfg.get("index_path", indexes_dir / "local_index.json"))
    )
    models_dir = Path(
        os.getenv("HERMES_MODELS_DIR", file_cfg.get("models_dir", data_dir / "models"))
    )
    llama_bin = Path(os.getenv("HERMES_LLAMA_BIN", file_cfg.get("llama_bin", "llama")))
    model_path = Path(
        os.getenv("HERMES_MODEL_PATH", file_cfg.get("model_path", models_dir / "model.gguf"))
    )

    log_level = os.getenv("HERMES_LOG_LEVEL", file_cfg.get("log_level", "INFO"))
    chunk_size = _parse_int(os.getenv("HERMES_CHUNK_SIZE", None), int(file_cfg.get("chunk_size", 900)))
    chunk_overlap = _parse_int(
        os.getenv("HERMES_CHUNK_OVERLAP", None), int(file_cfg.get("chunk_overlap", 120))
    )
    top_k = _parse_int(os.getenv("HERMES_TOP_K", None), int(file_cfg.get("top_k", 5)))
    score_threshold = _parse_float(
        os.getenv("HERMES_SCORE_THRESHOLD", None), float(file_cfg.get("score_threshold", 0.15))
    )
    allow_web = _parse_bool(os.getenv("HERMES_ALLOW_WEB", None), bool(file_cfg.get("allow_web", False)))
    web_max_sources = _parse_int(
        os.getenv("HERMES_WEB_MAX_SOURCES", None), int(file_cfg.get("web_max_sources", 3))
    )
    web_timeout_seconds = _parse_int(
        os.getenv("HERMES_WEB_TIMEOUT_SECONDS", None), int(file_cfg.get("web_timeout_seconds", 10))
    )
    web_user_agent = os.getenv(
        "HERMES_WEB_USER_AGENT", file_cfg.get("web_user_agent", "HERMES-Brain/0.1")
    )
    web_require_explicit = _parse_bool(
        os.getenv("HERMES_WEB_REQUIRE_EXPLICIT", None), bool(file_cfg.get("web_require_explicit", True))
    )

    return AppConfig(
        base_dir=base_dir,
        knowledge_dir=knowledge_dir,
        data_dir=data_dir,
        indexes_dir=indexes_dir,
        index_path=index_path,
        models_dir=models_dir,
        llama_bin=llama_bin,
        model_path=model_path,
        log_level=log_level,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        top_k=top_k,
        score_threshold=score_threshold,
        local_confidence_threshold=score_threshold,
        allow_web=allow_web,
        web_max_sources=web_max_sources,
        web_timeout_seconds=web_timeout_seconds,
        web_user_agent=web_user_agent,
        web_require_explicit=web_require_explicit,
    )
