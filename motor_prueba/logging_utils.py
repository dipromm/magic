import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class JsonlLogger:
    path: str
    flush_every: int = 1

    def __post_init__(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._n = 0
        # Mantener el handle abierto reduce muchísimo el overhead en RL
        self._f = open(self.path, "a", encoding="utf-8")

    def log(self, event: dict[str, Any]) -> None:
        event = dict(event)
        event.setdefault("ts", datetime.now(timezone.utc).isoformat())
        line = json.dumps(event, ensure_ascii=False)
        self._f.write(line + "\n")
        self._n += 1
        if self.flush_every > 0 and (self._n % self.flush_every == 0):
            self._f.flush()

    def close(self) -> None:
        try:
            self._f.flush()
            self._f.close()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()


class NullLogger:
    def log(self, event: dict[str, Any]) -> None:
        return


def make_logger(path: Optional[str]) -> NullLogger | JsonlLogger:
    return NullLogger() if not path else JsonlLogger(path=path)

