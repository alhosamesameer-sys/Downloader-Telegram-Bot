from pathlib import Path

def ensure_runtime_dirs(root: str) -> None:
    base=Path(root)
    for name in ("downloads", "audio", "cache"):
        (base / name).mkdir(parents=True, exist_ok=True)
