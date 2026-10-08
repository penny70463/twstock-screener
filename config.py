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
    # 用 `or` 而非 getenv 預設：環境變數被設為空字串時（如 CI 未填的 secret）也要 fallback
    nvidia_base_url: str = (
        os.getenv("NVIDIA_BASE_URL") or "https://integrate.api.nvidia.com/v1"
    )
    nvidia_model: str = os.getenv("NVIDIA_MODEL") or "meta/llama-3.1-70b-instruct"

    # 題材分類用哪一家。排程要換回 NVIDIA：THEME_LLM=nvidia
    theme_llm: str = (os.getenv("THEME_LLM") or "nvidia").strip().lower()
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_base_url: str = (
        os.getenv("GEMINI_BASE_URL")
        or "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    # 3.8-flash 在 2026-10-06 實測回 503；3.5-flash 18.7 秒回完同一批。
    gemini_model: str = os.getenv("GEMINI_MODEL") or "gemini-3.5-flash"
    # 本機 agy。排程用絕對路徑，cron 的 PATH 不含 ~/.local/bin。
    antigravity_bin: str = os.getenv("ANTIGRAVITY_BIN") or str(Path.home() / ".local" / "bin" / "agy")
    antigravity_model: str = os.getenv("ANTIGRAVITY_MODEL") or "gemini-3.1-pro-low"

    finmind_token: str = os.getenv("FINMIND_TOKEN", "")

    top_n: int = int(os.getenv("TOP_N", "200"))
    ma_windows: list[int] = field(
        default_factory=lambda: _parse_windows(os.getenv("MA_WINDOWS", "20,60,120,240"))
    )

    @property
    def max_ma(self) -> int:
        return max(self.ma_windows)

    @property
    def theme_model(self) -> str:
        if self.theme_llm == "gemini":
            return self.gemini_model
        if self.theme_llm == "antigravity":
            return self.antigravity_model
        return self.nvidia_model

    def require_nvidia(self) -> None:
        if not self.nvidia_api_key:
            raise RuntimeError("缺少 NVIDIA_API_KEY，請在 .env 設定")

    def require_theme_llm(self) -> None:
        if self.theme_llm == "gemini":
            if not self.gemini_api_key:
                raise RuntimeError("THEME_LLM=gemini 但缺少 GEMINI_API_KEY，請在 .env 設定")
            return
        if self.theme_llm == "antigravity":
            if not Path(self.antigravity_bin).is_file():
                raise RuntimeError(f"THEME_LLM=antigravity 但找不到 {self.antigravity_bin}")
            return
        if self.theme_llm != "nvidia":
            raise RuntimeError(
                f"不支援的 THEME_LLM={self.theme_llm}，可用 nvidia、gemini 或 antigravity"
            )
        self.require_nvidia()


settings = Settings()
