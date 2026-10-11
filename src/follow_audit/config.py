from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FA_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    state_dir: Path = Path("./state")
    host: str = "127.0.0.1"
    port: int = 8000
    allowed_hosts: str = ""  # extra Host names to accept, comma separated (see security.py)
    checker_enabled: bool = True
    check_interval_seconds: float = 3
    check_jitter_seconds: float = 3
    daily_request_cap: int = 5000
    recheck_live_days: int = 14
    recheck_unavailable_days: int = 3
    viewport_check_days: int = 10
    viewport_dwell_seconds: float = 1.0
    snooze_days: int = 30
    inactive_after_days: int = 365
    old_snapshot_days: int = 7
    high_follower_ratio: float = 10
    request_timeout_seconds: float = 30
    cooldown_base_minutes: int = 15
    cooldown_max_hours: int = 4
    scan_on_startup: bool = True
    watch_interval_seconds: float = 10  # how often data_dir is checked for new exports; 0 = off

    @property
    def db_path(self) -> Path:
        return self.state_dir / "follow_audit.db"

    @property
    def avatar_dir(self) -> Path:
        return self.state_dir / "avatars"
