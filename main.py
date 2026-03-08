"""
BUFS Academic Chatbot - 메인 진입점
Streamlit 채팅 UI를 실행합니다.

사용법:
    streamlit run main.py
"""

import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ui.chat_app import main

if __name__ == "__main__":
    main()
