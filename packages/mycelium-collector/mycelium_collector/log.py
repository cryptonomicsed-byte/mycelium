from datetime import datetime, timezone

from . import config


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    try:
        with open(config.LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass
