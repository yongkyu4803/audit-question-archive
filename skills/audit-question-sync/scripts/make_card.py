#!/usr/bin/env python3
"""Drive 에서 내려받은 한글 원본 1건을 처리한다: 원본캐시 보관 → kordoc 파싱 → 카드 뼈대 → 처리기록 → 목록.

세 파일(한글 원본 / 파싱본 / 카드)은 같은 id 를 쓰고, 파싱본과 카드에는 Drive 원본의 파일 id 와 이름이 참조로 들어간다.
메타(상임위·기관·질의자·제출일·유형)는 한글 파일 안에 없다. 원본 파일명이 규칙(제출일_상임위_기관_질의자_유형[_v버전])에
맞으면 자동으로 읽고, 아니면 --committee 등으로 직접 준다. 값은 사람이 확인한 것만 넣는다.

새 카드는 항상 개인정보 확인 전(privacy_checked: false)이라 Drive 에 올라가지 않는다. release.py 로 확인 결과를 반영한다.

예:
  python3 make_card.py --file ~/Downloads/내려받은.hwpx --drive-id 1AbC... --name 2026-10-14_기획재정위원회_한국은행_홍길동_서면질의.hwpx
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from archive_lib import (CARD_SUFFIX, DIR_CACHE, DIR_CARD, DIR_PARSE, KINDS, PARSE_SUFFIX, SKILL_DIR, STATUSES,
                         VISIBILITIES, card_path, check_name_part, iter_cards, load_ledger, make_id, now_utc,
                         parse_filename, parse_path, resolve_root, save_ledger, sha256_file, valid_date)
from rebuild_index import rebuild
from to_md import KORDOC_EXTS, KordocError, convert, wrap_markdown


def die(msg: str, code: int = 1):
    print(f"오류: {msg}", file=sys.stderr)
    sys.exit(code)


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root")
    p.add_argument("--file", required=True, help="내려받은 원본 파일 경로")
    p.add_argument("--drive-id", required=True, help="Drive 원본/ 폴더에서의 파일 id")
    p.add_argument("--name", help="Drive 에서의 원본 파일 이름 (기본: 내려받은 파일 이름)")
    p.add_argument("--drive-url", default="")
    p.add_argument("--etag", default="")
    p.add_argument("--modified", default="", help="Drive 의 modifiedTime")
    p.add_argument("--committee"); p.add_argument("--agency"); p.add_argument("--author")
    p.add_argument("--submitted", help="제출일 YYYY-MM-DD")
    p.add_argument("--kind", choices=KINDS)
    p.add_argument("--version", type=int)
    p.add_argument("--status", choices=STATUSES, default="제출")
    p.add_argument("--visibility", choices=VISIBILITIES, default="내부")
    p.add_argument("--topic", default="")
    p.add_argument("--parsed-md", help="PDF 처럼 이 스크립트가 못 읽는 형식: kordoc MCP 로 이미 읽은 마크다운 파일")
    a = p.parse_args()

    root = resolve_root(a.root)
    src = Path(a.file).expanduser().resolve()
    if not src.is_file():
        die(f"원본 파일이 없습니다: {src}")
    name = a.name or src.name
    ext = Path(name).suffix.lower() or src.suffix.lower()

    # ----- 메타 확정 -----
    auto = parse_filename(name) or {}
    meta = {k: getattr(a, k) if getattr(a, k) is not None else auto.get(k)
            for k in ("committee", "agency", "author", "submitted", "kind", "version")}
    if meta["version"] is None:
        meta["version"] = 1
    missing = [k for k in ("committee", "agency", "author", "submitted", "kind") if not meta[k]]
    if missing:
        die("원본 파일명이 규칙(제출일_상임위_기관_질의자_유형[_v버전])에 맞지 않아 다음 값을 직접 받아야 합니다: "
            + ", ".join(missing) + "\n  한글 파일 안에는 이 값들이 없으니 추측하지 말고 담당자에게 확인하세요.", 4)
    for label, val in (("상임위", meta["committee"]), ("피감기관", meta["agency"]), ("질의자", meta["author"])):
        e = check_name_part(label, val)
        if e:
            die(e)
    if not valid_date(meta["submitted"]):
        die(f"제출일 '{meta['submitted']}' 은 YYYY-MM-DD 형식의 실제 날짜여야 합니다.")
    if meta["version"] < 1:
        die("버전은 1 이상이어야 합니다.")
    if "|" in a.topic or "\n" in a.topic:
        die("주제에는 '|' 와 줄바꿈을 쓸 수 없습니다.")

    cid = make_id(meta["submitted"], meta["committee"], meta["agency"], meta["author"], meta["kind"], meta["version"])
    year = meta["submitted"][:4]
    led = load_ledger(root)

    # ----- 중복 확인 -----
    if a.drive_id in led["originals"] and led["originals"][a.drive_id].get("card_id"):
        die(f"이미 처리한 원본입니다 (카드 {led['originals'][a.drive_id]['card_id']})")
    if card_path(root, cid).exists() or parse_path(root, cid).exists():
        die(f"같은 id 의 카드나 파싱본이 이미 있습니다 (덮어쓰지 않습니다): {cid}")
    for _, fm in iter_cards(root):
        if fm and fm.get("source_drive_id") == a.drive_id:
            die(f"같은 Drive 원본을 가리키는 카드가 이미 있습니다: {fm.get('id')}")

    # ----- 원본캐시 -----
    cache_dir = root / DIR_CACHE
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{a.drive_id}__{name}"
    if not cache_file.exists():
        shutil.copy2(src, cache_file)
    sha = sha256_file(cache_file)
    url = a.drive_url or f"https://drive.google.com/file/d/{a.drive_id}/view"

    # ----- 파싱 -----
    ref = {"id": cid, "source_name": name, "source_drive_id": a.drive_id, "source_drive_url": url, "source_sha256": sha}
    pfile = parse_path(root, cid)
    parse_problem = None
    page_mode = ""
    if a.parsed_md:
        body = Path(a.parsed_md).expanduser().read_text(encoding="utf-8")
        pfile.parent.mkdir(parents=True, exist_ok=True)
        pfile.write_text(wrap_markdown(body, ref, {"kordoc_version": "mcp", "page_mode": "unknown", "page_count": "",
                                                    "converted_utc": now_utc()}), encoding="utf-8")
        page_mode = "unknown"
    elif ext in KORDOC_EXTS:
        try:
            info = convert(cache_file, pfile, ref)
            page_mode = info["page_mode"]
            print(f"파싱:  {DIR_PARSE}/{pfile.name} (kordoc {info['kordoc_version']}, 쪽 기준 {page_mode}, {info['page_count']}쪽)")
        except KordocError as e:
            parse_problem = str(e)
    else:
        parse_problem = f"{ext} 는 스크립트로 파싱하지 않습니다. kordoc MCP parse_document 로 읽은 글을 --parsed-md 로 넘기세요."
    if parse_problem:
        die(f"파싱본을 만들지 못해 카드를 만들지 않았습니다: {parse_problem}", 3)

    # ----- 카드 -----
    values = {
        "id": cid, "year": year, "committee": meta["committee"], "agency": meta["agency"], "author": meta["author"],
        "submitted": meta["submitted"], "kind": meta["kind"], "topic": a.topic, "version": str(meta["version"]),
        "status": a.status, "source_name": name, "source_drive_id": a.drive_id, "source_drive_url": url,
        "source_sha256": sha, "parse_file": pfile.name, "parse_page_mode": page_mode, "visibility": a.visibility,
    }
    card_text = (SKILL_DIR / "assets" / "card-template.md").read_text(encoding="utf-8")
    for k, v in values.items():
        card_text = card_text.replace("{{" + k + "}}", v)
    cfile = card_path(root, cid)
    cfile.parent.mkdir(parents=True, exist_ok=True)
    cfile.write_text(card_text, encoding="utf-8")

    # ----- 처리기록·목록 -----
    led["originals"][a.drive_id] = {"name": name, "etag": a.etag, "modified": a.modified, "sha256": sha,
                                    "card_id": cid, "processed_utc": now_utc()}
    save_ledger(root, led)
    n = rebuild(root)

    print(f"원본:  {DIR_CACHE}/{cache_file.name}")
    print(f"카드:  {DIR_CARD}/{cfile.name}  (개인정보 확인 전 — 아직 Drive 에 올라가지 않음)")
    print(f"목록:  게시 대상 {n}건")
    print("다음: 파싱본을 읽어 카드의 '질의 요지'·'핵심 수치'·topic 을 채우고, 사람이 개인정보를 확인한 뒤 release.py 를 실행하세요.")


if __name__ == "__main__":
    main()
