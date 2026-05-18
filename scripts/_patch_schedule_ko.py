"""학사일정 ISO→한글 복원 패치 (audit P2-12 부분 적용, -14pp 회귀 해소).

raw bytes 모드 + CRLF. 한글은 hex escape.
"""
from pathlib import Path

PATH = Path(__file__).parent.parent / 'app/graphdb/academic_graph.py'
data = PATH.read_bytes()

# 한글 hex
HAK = b'\xed\x95\x99\xec\x82\xac\xec\x9d\xbc\xec\xa0\x95'  # 학사일정
BAS = b'\xec\x9e\xa5\xeb\xb0\x94\xea\xb5\xac\xeb\x8b\x88 \xec\x8b\xa0\xec\xb2\xad'  # 장바구니 신청
OCU = b'OCU \xeb\x82\xa9\xeb\xb6\x80\xea\xb8\xb0\xea\xb0\x84'  # OCU 납부기간
NABU = b'\xeb\x82\xa9\xeb\xb6\x80\xea\xb8\xb0\xea\xb0\x84'  # 납부기간
HYU = b'\xec\x98\xa8\xeb\x9d\xbc\xec\x9d\xb8 \xed\x9c\xb4/\xeb\xb3\xb5\xed\x95\x99 \xec\x8b\xa0\xec\xb2\xad'  # 온라인 휴/복학 신청
HEADER_KO = b'# 2026-05-18 audit P2-12 \xeb\xb6\x80\xeb\xb6\x84 \xec\xa0\x81\xec\x9a\xa9 (\xed\x9a\x8c\xea\xb7\x80 -14pp \xed\x95\xb4\xec\x86\x8c): context \xed\x95\x9c\xea\xb8\x80 \xeb\xb3\x80\xed\x99\x98'  # audit P2-12 ... context 한글 변환


def patch_bytes(old: bytes, new: bytes, label: str) -> None:
    global data
    count = data.count(old)
    if count == 0:
        raise AssertionError(f"[{label}] old NOT FOUND")
    if count > 1:
        raise AssertionError(f"[{label}] matches {count} times")
    data = data.replace(old, new, 1)
    print(f"OK [{label}]")


CRLF = b'\r\n'

# === P1: L1586 _schedule_to_result (indent 8) ===
# CRLF prefix로 line boundary 강제 — 16-space indent에서 substring 매칭 회피.
P1_OLD = CRLF + b'        period = start if start == end else f"{start}\\u301C{end}"' + CRLF
P1_NEW = (
    CRLF
    + b'        ' + HEADER_KO + CRLF
    + b'        period = self._format_date(start) if start == end else self._format_period(start, end)' + CRLF
)
patch_bytes(P1_OLD, P1_NEW, "P1 _schedule_to_result L1586")


# === P4: L2532 학사일정 fallback (indent 16) ===
P4_OLD = CRLF + b'                period = start if start == end else f"{start}\\u301C{end}"' + CRLF
P4_NEW = (
    CRLF
    + b'                ' + HEADER_KO + CRLF
    + b'                period = self._format_date(start) if start == end else self._format_period(start, end)' + CRLF
)
patch_bytes(P4_OLD, P4_NEW, "P4 schedule fallback L2532")


# === P2: L2154 장바구니 ===
P2_OLD = b'                context = f"[' + HAK + b']\\n- ' + BAS + b': {start}~{end}"' + CRLF
P2_NEW = (
    b'                _period_ko = self._format_period(start, end)' + CRLF
    + b'                context = f"[' + HAK + b']\\n- ' + BAS + b': {_period_ko}"' + CRLF
)
patch_bytes(P2_OLD, P2_NEW, "P2 장바구니 L2154")


# === P3: L2212 OCU 납부 ===
P3_OLD = b'                        context = f"[' + OCU + b']\\n- ' + NABU + b': {start}~{end}"' + CRLF
P3_NEW = (
    b'                        _period_ko = self._format_period(start, end)' + CRLF
    + b'                        context = f"[' + OCU + b']\\n- ' + NABU + b': {_period_ko}"' + CRLF
)
patch_bytes(P3_OLD, P3_NEW, "P3 OCU 납부 L2212")


# === P5: L2863 휴/복학 ===
P5_OLD = b'                context = f"[' + HAK + b']\\n- ' + HYU + b': {start}~{end}"' + CRLF
P5_NEW = (
    b'                _period_ko = self._format_period(start, end)' + CRLF
    + b'                context = f"[' + HAK + b']\\n- ' + HYU + b': {_period_ko}"' + CRLF
)
patch_bytes(P5_OLD, P5_NEW, "P5 휴/복학 L2863")


PATH.write_bytes(data)
print(f"\n[saved] {PATH}")
