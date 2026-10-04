"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_COMPETITIONS = ["PL", "PD", "SA", "BL1", "FL1", "CL"]
CLAUDE_MODEL = "claude-opus-5-5"


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader: KEY=VALUE lines; existing environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


@dataclass
class Settings:
    football_data_key: str | None = None
    competitions: list[str] = field(default_factory=lambda: list(DEFAULT_COMPETITIONS))
    lookahead_days: int = 2
    watch_interval_minutes: int = 60
    recent_matches: int = 10
    news_research: str = "auto"  # auto | on | off
    data_dir: Path = Path("data")
    claude_model: str = CLAUDE_MODEL

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "predictions.db"

    @property
    def dashboard_path(self) -> Path:
        return self.data_dir / "dashboard.html"

    def news_enabled(self) -> bool:
        if self.news_research == "off":
            return False
        if self.news_research == "on":
            return True
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        env = os.environ
        comps = [c.strip().upper() for c in env.get("FP_COMPETITIONS", "").split(",") if c.strip()]
        return cls(
            football_data_key=env.get("FOOTBALL_DATA_API_KEY") or None,
            competitions=comps or list(DEFAULT_COMPETITIONS),
            lookahead_days=int(env.get("FP_LOOKAHEAD_DAYS", 2)),
            watch_interval_minutes=int(env.get("FP_WATCH_INTERVAL_MINUTES", 60)),
            recent_matches=int(env.get("FP_RECENT_MATCHES", 10)),
            news_research=env.get("FP_NEWS_RESEARCH", "auto").lower(),
            data_dir=Path(env.get("FP_DATA_DIR", "data")),
        )
