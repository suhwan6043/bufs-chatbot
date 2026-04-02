"""
PDF 버전 추적기

PDF 파일의 SHA-256 해시를 기반으로 변경 여부를 추적합니다.
변경되지 않은 PDF에 대한 불필요한 재파싱/재빌드를 방지합니다.

3원칙 중 원칙 3 (증분 업데이트 + 버전 관리) 구현.
"""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CRAWL_META_DIR = Path(__file__).parent.parent.parent / "data" / "crawl_meta"
STORE_PATH = CRAWL_META_DIR / "pdf_versions.json"


class PdfVersionTracker:
    """PDF 파일의 SHA-256 해시로 변경 여부를 추적합니다."""

    def __init__(self, store_path: Path = STORE_PATH):
        self._store_path = store_path
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            if self._store_path.exists():
                with open(self._store_path, encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("pdf_versions.json 로드 실패: %s", e)
        return {}

    def _save(self) -> None:
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._store_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _compute_hash(file_path: str) -> str:
        """파일의 SHA-256 해시를 계산합니다."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def has_changed(self, pdf_path: str) -> bool:
        """PDF 파일이 마지막 인제스트 이후 변경되었는지 확인합니다.

        Returns:
            True: 변경됨 (또는 최초 인제스트)
            False: 변경 없음
        """
        path = Path(pdf_path).resolve()
        if not path.exists():
            logger.warning("PDF 파일 없음: %s", path)
            return True

        current_hash = self._compute_hash(str(path))
        key = str(path)
        entry = self._data.get(key)

        if entry is None:
            logger.info("PDF 최초 인제스트: %s", path.name)
            return True

        if entry.get("sha256") != current_hash:
            logger.info("PDF 변경 감지: %s (해시 불일치)", path.name)
            return True

        logger.info("PDF 미변경: %s (해시 일치)", path.name)
        return False

    def get_hash(self, pdf_path: str) -> Optional[str]:
        """PDF 파일의 현재 SHA-256 해시를 반환합니다."""
        path = Path(pdf_path).resolve()
        if not path.exists():
            return None
        return self._compute_hash(str(path))

    def update(
        self,
        pdf_path: str,
        node_count: int = 0,
        edge_count: int = 0,
    ) -> None:
        """PDF 인제스트 완료 후 버전 정보를 업데이트합니다."""
        path = Path(pdf_path).resolve()
        current_hash = self._compute_hash(str(path))
        key = str(path)

        now = datetime.now().isoformat(timespec="seconds")
        entry = self._data.get(key, {})

        self._data[key] = {
            "sha256": current_hash,
            "file_size": path.stat().st_size,
            "last_ingested": now,
            "first_ingested": entry.get("first_ingested", now),
            "node_count": node_count,
            "edge_count": edge_count,
        }
        self._save()
        logger.info(
            "PDF 버전 기록 완료: %s (노드=%d, 엣지=%d)",
            path.name, node_count, edge_count,
        )
