"""Load shared configuration with optional machine-specific overrides."""

import json
from pathlib import Path


def _merge(base, override):
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(repository_root):
    root = Path(repository_root).resolve()
    example = root / "configs/research_config.example.json"
    local = root / "configs/research_config.local.json"

    config = json.loads(example.read_text(encoding="utf-8-sig"))
    if local.exists():
        config = _merge(
            config,
            json.loads(local.read_text(encoding="utf-8-sig")),
        )

    raw = Path(config["raw_data_dir"]).expanduser()
    config["raw_data_dir"] = str(
        raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    )
    return config
