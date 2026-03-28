"""
크롤러 실제 동작 테스트 스크립트
실행: .venv/Scripts/python scripts/test_crawl.py
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

from app.crawler.notice_crawler import NoticeCrawler, _current_semester_label, _current_semester_start

def main():
    print("\n" + "="*60)
    print("BUFS 공지사항 크롤러 실행 테스트")
    print("="*60)
    print(f"현재 학기: {_current_semester_label()}")
    print(f"수집 기준일: {_current_semester_start()} 이후 게시글만 수집")
    print("="*60 + "\n")

    crawler = NoticeCrawler()
    items = crawler.crawl()

    if not items:
        print("❌ 수집된 게시글이 없습니다.")
        print("   - 네트워크 연결을 확인하세요.")
        print("   - 사이트 HTML 구조가 변경됐을 수 있습니다.")
        return

    print(f"✅ 총 {len(items)}건 수집 완료\n")

    for i, item in enumerate(items, 1):
        print(f"[{i:02d}] {item.title}")
        print(f"      날짜: {item.metadata.get('post_date', 'N/A')}")
        print(f"      URL : {item.source_id}")
        print(f"      해시: {item.content_hash[:12]}...")
        print(f"      본문 길이: {len(item.content)}자")
        if item.attachments:
            print(f"      첨부파일: {len(item.attachments)}개")
            for att in item.attachments:
                print(f"        - {att}")
        print()

    print("="*60)
    print(f"변경 감지 시뮬레이션 (ChangeDetector)")
    print("="*60)

    from app.crawler.change_detector import ChangeDetector
    from unittest.mock import patch
    import tempfile, json
    from pathlib import Path as P

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = P(tmp)
        hash_file = tmp_path / "hashes.json"

        with patch("app.crawler.change_detector.CRAWL_META_DIR", tmp_path), \
             patch("app.crawler.change_detector.HASH_FILE", hash_file):

            detector = ChangeDetector()

            # 1차 감지 (전부 NEW)
            events1 = detector.detect(items)
            new_count = sum(1 for e in events1 if e.change_type.value == "new")
            print(f"1차 감지: NEW={new_count}건")

            detector.commit(events1)

            # 2차 감지 (동일 내용 → 변경 없음)
            events2 = detector.detect(items)
            print(f"2차 감지 (동일 내용): 이벤트={len(events2)}건 (0이어야 정상)")

    print("\n✅ 크롤러 정상 동작 확인!")

if __name__ == "__main__":
    main()
