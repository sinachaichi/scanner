from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ParsedConfig:
    """Immutable, fully-parsed representation of a single proxy URI."""
    protocol: str
    raw_link: str
    host: str
    port: int
    user_id: Optional[str]
    method: Optional[str]  # ss only — cipher name (e.g. aes-256-gcm)
    remark: str


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a full xray speed test against a single config."""
    config: ParsedConfig
    is_working: bool
    speed_kbps: float
