"""Goods database: load QR → cargo info + drop-off coordinates from YAML."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class GoodsInfo:
    qr_code: str
    name: str
    place_x: float
    place_y: float
    place_z: float


class GoodsDatabase:
    def __init__(self, db_path: str) -> None:
        self._goods: dict[str, GoodsInfo] = {}
        path = Path(db_path)
        if path.exists():
            self._load(path)

    def _load(self, path: Path) -> None:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.error("Failed to load goods database %s: %s", path, exc)
            return

        goods = data.get("goods", {}) or {}
        for qr_code, info in goods.items():
            self._goods[qr_code] = GoodsInfo(
                qr_code=qr_code,
                name=str(info.get("name", qr_code)),
                place_x=float(info.get("place_x", 0.0)),
                place_y=float(info.get("place_y", 0.0)),
                place_z=float(info.get("place_z", 0.5)),
            )
        logger.info("GoodsDatabase: loaded %d goods from %s", len(self._goods), path)

    def lookup(self, qr_data: str) -> Optional[GoodsInfo]:
        return self._goods.get(qr_data)

    @property
    def count(self) -> int:
        return len(self._goods)
