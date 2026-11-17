#!/usr/bin/env python3
"""카드에서 `카드/_INDEX.md` 를 다시 만든다. Drive 에 올릴 수 있는 카드(공개·내부 + 개인정보 확인 완료)만 넣는다.

목록은 카드의 파생물이다. 직접 고치지 말고 카드를 고친 뒤 이 스크립트를 실행한다.

  python3 rebuild_index.py --root ~/국감작업본
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from archive_lib import (CARD_SUFFIX, DIR_CARD, INDEX_COLUMNS, PARSE_SUFFIX, SKILL_DIR, iter_cards, publishable,
                         resolve_root)


def _cell(v: str) -> str:
    return str(v).replace("|", "/").replace("\n", " ").strip()


def render_index(root: Path) -> str:
    rows = []
    for p, fm in iter_cards(root):
        if fm and fm.get("id") and publishable(fm):
            rows.append(fm)
    rows.sort(key=lambda f: (f.get("submitted", ""), f["id"]), reverse=True)
    head = (SKILL_DIR / "assets" / "_INDEX-template.md").read_text(encoding="utf-8")
    if not head.endswith("\n"):
        head += "\n"
    lines = []
    for f in rows:
        cells = {
            "id": f["id"], "제출일": f.get("submitted", ""), "상임위": f.get("committee", ""),
            "피감기관": f.get("agency", ""), "질의자": f.get("author", ""), "유형": f.get("kind", ""),
            "버전": f"v{f.get('version', '')}", "회신": f.get("reply_status", ""), "주제": f.get("topic", ""),
            "원본 파일명": f.get("source_name", ""), "카드": f"{f['id']}{CARD_SUFFIX}", "파싱": f"{f['id']}{PARSE_SUFFIX}",
        }
        lines.append("| " + " | ".join(_cell(cells[c]) for c in INDEX_COLUMNS) + " |")
    return head + "\n".join(lines) + ("\n" if lines else "")


def rebuild(root: Path) -> int:
    text = render_index(root)
    out = root / DIR_CARD / "_INDEX.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return sum(1 for ln in text.splitlines() if ln.startswith("| ") and not ln.startswith("| id"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    a = ap.parse_args()
    root = resolve_root(a.root)
    if not root.is_dir():
        print(f"오류: 폴더가 없습니다: {root}", file=sys.stderr)
        sys.exit(2)
    print(f"카드/_INDEX.md: 게시 대상 {rebuild(root)}건")


if __name__ == "__main__":
    main()
