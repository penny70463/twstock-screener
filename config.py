"""集中管理環境變數與篩選參數。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
RESULT_DIR = DATA_DIR / "results"

for _d in (DATA_DIR, CACHE_DIR, RESULT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _parse_windows(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    nvidia_api_key: str = os.getenv("NVIDIA_API_KEY", "")
    nvidia_base_url: str = os.getenv(
        "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
    )
    nvidia_model: str = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct")

    finmind_token: str = os.getenv("FINMIND_TOKEN", "")

    top_n: int = int(os.getenv("TOP_N", "200"))
    ma_windows: list[int] = field(
        default_factory=lambda: _parse_windows(os.getenv("MA_WINDOWS", "20,60,120,240"))
    )

    @property
    def max_ma(self) -> int:
        return max(self.ma_windows)

    def require_nvidia(self) -> None:
        if not self.nvidia_api_key:
            raise RuntimeError("缺少 NVIDIA_API_KEY，請在 .env 設定")


settings = Settings()
