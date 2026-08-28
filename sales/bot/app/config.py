from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    token: str
    admin_chat_id: int | None
    database_path: str
    website_url: str
    channel_url: str
    presentation_url: str
    brochure_url: str
    poll_timeout: int

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("PU_SALES_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("PU_SALES_BOT_TOKEN is required")
        admin_raw = os.getenv("PU_SALES_ADMIN_CHAT_ID", "").strip()
        return cls(
            token=token,
            admin_chat_id=int(admin_raw) if admin_raw else None,
            database_path=os.getenv("PU_SALES_DATABASE", "/app/data/sales_bot.sqlite3"),
            website_url=os.getenv("PU_SALES_WEBSITE_URL", "https://puworkspace.ru"),
            channel_url=os.getenv("PU_SALES_CHANNEL_URL", "https://t.me/puworkspace"),
            presentation_url=os.getenv(
                "PU_SALES_PRESENTATION_URL",
                "https://puworkspace.ru/assets/PU_Workspace_One_Page_Offer.pdf",
            ).strip(),
            brochure_url=os.getenv(
                "PU_SALES_BROCHURE_URL",
                "https://puworkspace.ru/assets/PU_Workspace_Early_Access_Brochure.pdf",
            ).strip(),
            poll_timeout=max(5, min(50, int(os.getenv("PU_SALES_POLL_TIMEOUT", "30")))),
        )
