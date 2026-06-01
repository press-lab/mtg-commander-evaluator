import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

from mtg_evaluator.config import settings

SCRYFALL_BULK_DATA_URL = "https://api.scryfall.com/bulk-data"
USER_AGENT = "MTGCommanderEvaluator/0.1 (contact: your-email@example.com)"
BULK_TYPE = "oracle_cards"


def _rate_limit() -> None:
    time.sleep(settings.scryfall_rate_limit_ms / 1000)


def fetch_bulk_metadata() -> dict:
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        resp = client.get(SCRYFALL_BULK_DATA_URL)
        resp.raise_for_status()
        _rate_limit()
        data = resp.json()

    for entry in data["data"]:
        if entry["type"] == BULK_TYPE:
            return entry

    raise ValueError(f"Bulk type '{BULK_TYPE}' not found in Scryfall bulk-data response")


def download_bulk_file(download_url: str) -> Path:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = data_dir / f"oracle_cards_{timestamp}.json"

    print(f"Downloading {download_url} -> {dest}")

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=300, follow_redirects=True) as client:
        with client.stream("GET", download_url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total * 100
                        print(f"\r  {pct:.1f}%  ({downloaded // 1024 // 1024} MB)", end="", flush=True)
    print()
    return dest


def load_cards_from_file(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compute_oracle_text_checksum(card: dict) -> str:
    oracle_text = card.get("oracle_text") or ""
    for face in card.get("card_faces", []):
        oracle_text += face.get("oracle_text") or ""
    return hashlib.sha256(oracle_text.encode("utf-8")).hexdigest()


def get_latest_cached_bulk_file() -> Path | None:
    data_dir = Path(settings.data_dir)
    files = sorted(data_dir.glob("oracle_cards_*.json"), reverse=True)
    return files[0] if files else None
