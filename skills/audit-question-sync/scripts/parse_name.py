#!/usr/bin/env python3
"""원본 파일명이 규칙(제출일_상임위_기관_질의자_유형[_v버전])에 맞는지 보고 메타를 JSON 으로 출력한다.

  python3 parse_name.py "2026-10-14_기획재정위원회_한국은행_홍길동_서면질의.hwpx"
맞지 않으면 {"ok": false} 와 종료 코드 1. 맞아도 값은 사람이 확인한 뒤 쓴다.
"""
import json
import sys

from archive_lib import parse_filename

if len(sys.argv) != 2:
    print(__doc__)
    sys.exit(2)
meta = parse_filename(sys.argv[1])
print(json.dumps({"ok": bool(meta), **(meta or {})}, ensure_ascii=False))
sys.exit(0 if meta else 1)
