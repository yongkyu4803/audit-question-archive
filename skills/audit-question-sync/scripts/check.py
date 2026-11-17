#!/usr/bin/env python3
"""작업본 점검. 오류가 있으면 종료 코드 1, 경고만 있으면 0.

  python3 check.py --root ~/국감작업본

세 파일(한글 원본·파싱본·카드)이 같은 id 로 짝을 이루고 서로를 가리키는지, 원본이 바뀌지 않았는지,
Drive 에 올라가면 안 되는 카드가 올라가 있지 않은지, 목록이 카드와 같은지 본다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from archive_lib import (CARD_SUFFIX, DIR_CACHE, DIR_CARD, DIR_PARSE, KINDS, PARSE_SUFFIX, REPLY_STATUSES,
                         REQUIRED_FIELDS, STATUSES, VISIBILITIES, check_name_part, is_true, iter_cards, load_ledger,
                         make_id, parse_front, parse_path, publishable, resolve_root, sha256_file, valid_date)
from rebuild_index import render_index


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    a = ap.parse_args()
    root = resolve_root(a.root)
    if not root.is_dir():
        print(f"오류: 폴더가 없습니다: {root}", file=sys.stderr)
        sys.exit(2)

    errors, warns = [], []

    def err(p, m):
        errors.append(f"[오류] {Path(p).relative_to(root).as_posix() if isinstance(p, Path) else p}: {m}")

    def warn(p, m):
        warns.append(f"[경고] {Path(p).relative_to(root).as_posix() if isinstance(p, Path) else p}: {m}")

    led = load_ledger(root)
    uploads, originals = led["uploads"], led["originals"]
    if not all(k in led.get("folders", {}) for k in ("원본", "파싱", "카드")):
        warn("_state/처리기록.json", "Drive 폴더 id(원본/파싱/카드)가 등록되지 않았습니다. ledger.py set-folders")

    cards = list(iter_cards(root))
    ids_seen = {}
    for cp, fm in cards:
        if fm is None:
            err(cp, "머리말(---)이 없습니다")
            continue
        missing = [k for k in REQUIRED_FIELDS if not str(fm.get(k, "")).strip()]
        if missing:
            err(cp, f"필수 필드 비어 있음: {', '.join(missing)}")
            continue
        cid = fm["id"]
        if cid in ids_seen:
            err(cp, f"같은 id 의 카드가 이미 있습니다: {ids_seen[cid].name}")
        ids_seen[cid] = cp

        for label, key in (("상임위", "committee"), ("피감기관", "agency"), ("질의자", "author")):
            m = check_name_part(label, fm[key])
            if m:
                err(cp, m)
        if not valid_date(fm["submitted"]):
            err(cp, f"submitted '{fm['submitted']}' 은 YYYY-MM-DD 날짜가 아닙니다")
        elif fm["submitted"][:4] != fm["year"]:
            err(cp, f"year({fm['year']}) 와 submitted({fm['submitted']}) 의 연도가 다릅니다")
        for key, allowed in (("kind", KINDS), ("status", STATUSES), ("visibility", VISIBILITIES),
                             ("reply_status", REPLY_STATUSES)):
            if fm[key] not in allowed:
                err(cp, f"{key} '{fm[key]}' 은 {allowed} 중 하나여야 합니다")
        if not fm["version"].isdigit() or int(fm["version"]) < 1:
            err(cp, f"version '{fm['version']}' 은 1 이상의 정수여야 합니다")
            continue
        expect = make_id(fm["submitted"], fm["committee"], fm["agency"], fm["author"], fm["kind"], int(fm["version"]))
        if cid != expect:
            err(cp, f"id 가 필드로 만든 값과 다릅니다 (기대: {expect})")
        if cp.name != f"{cid}{CARD_SUFFIX}":
            err(cp, f"파일 이름이 {cid}{CARD_SUFFIX} 이어야 합니다")

        # 파싱본: 같은 id, 같은 Drive 원본을 가리켜야 한다
        pp = parse_path(root, cid)
        if not pp.is_file():
            err(cp, f"파싱본이 없습니다: {DIR_PARSE}/{pp.name}")
        else:
            pfm, _ = parse_front(pp.read_text(encoding="utf-8"))
            if pfm is None:
                err(pp, "머리말(---)이 없습니다")
            else:
                for k_card, k_parse in (("id", "id"), ("source_drive_id", "source_drive_id"),
                                        ("source_name", "source_name"), ("source_sha256", "source_sha256")):
                    if pfm.get(k_parse) != fm[k_card]:
                        err(pp, f"{k_parse} 가 카드와 다릅니다 (파싱본: {pfm.get(k_parse)!r}, 카드: {fm[k_card]!r})")
                if pfm.get("page_mode") != fm.get("parse_page_mode"):
                    warn(cp, "카드의 parse_page_mode 가 파싱본의 page_mode 와 다릅니다")
        if fm.get("parse_file") != f"{cid}{PARSE_SUFFIX}":
            err(cp, f"parse_file 이 {cid}{PARSE_SUFFIX} 이어야 합니다")

        # 한글 원본(원본캐시): 있으면 해시가 같아야 한다
        cache = root / DIR_CACHE / f"{fm['source_drive_id']}__{fm['source_name']}"
        if cache.is_file():
            if sha256_file(cache) != fm["source_sha256"]:
                err(cp, "원본캐시의 파일이 파싱 당시와 다릅니다 (source_sha256 불일치). 원본이 바뀌었는지 확인하세요")
        else:
            warn(cp, "원본캐시에 한글 원본이 없습니다 (정리했다면 무시해도 됩니다)")

        # 처리기록
        o = originals.get(fm["source_drive_id"])
        if o is None or o.get("card_id") != cid:
            err(cp, "처리기록의 원본 항목이 이 카드를 가리키지 않습니다")

        # 올라가도 되는지
        pub = publishable(fm)
        up_card, up_parse = uploads.get(f"card:{cid}"), uploads.get(f"parse:{cid}")
        if not pub and (up_card or up_parse):
            err(cp, "Drive 에 올라가 있으면 안 되는 카드입니다 (비공개이거나 개인정보 확인 전). plan_upload.py 의 remove 작업을 수행하세요")
        if pub:
            if not up_card or not up_parse:
                warn(cp, "게시 대상인데 아직 Drive 에 올리지 않았습니다 (plan_upload.py)")
            elif up_card.get("sha256") != sha256_file(cp) or up_parse.get("sha256") != sha256_file(pp):
                warn(cp, "Drive 에 올린 뒤 내용이 바뀌었습니다 (plan_upload.py 로 교체)")
            if not fm.get("topic"):
                warn(cp, "topic(목록의 '주제')이 비어 있습니다")
            if fm.get("parse_page_mode") != "page":
                warn(cp, "쪽 번호가 근사값(구역 단위)입니다. '원문 쪽'을 한글에서 확인했는지 점검하세요")
        elif not is_true(fm.get("privacy_checked")) and fm["visibility"] != "비공개":
            warn(cp, "개인정보 확인 대기 중 (privacy_checked: false)")
        if fm["reply_status"] in ("수령", "부분수령") and not fm.get("reply_date"):
            warn(cp, "회신을 받았는데 reply_date 가 비어 있습니다")
        prior = fm.get("prior", [])
        for pid in prior if isinstance(prior, list) else [prior]:
            if pid and pid not in {f["id"] for _, f in cards if f}:
                warn(cp, f"prior '{pid}' 에 해당하는 카드를 찾지 못했습니다")

    # 카드 없는 파싱본, 카드 없이 기록된 원본
    card_ids = {fm["id"] for _, fm in cards if fm and fm.get("id")}
    pdir = root / DIR_PARSE
    if pdir.is_dir():
        for p in sorted(pdir.glob(f"*{PARSE_SUFFIX}")):
            if p.name[: -len(PARSE_SUFFIX)] not in card_ids:
                err(p, "짝이 되는 카드가 없는 파싱본입니다")
    for did, o in originals.items():
        if o.get("card_id") and o["card_id"] not in card_ids:
            err("_state/처리기록.json", f"원본 {o.get('name')} 의 카드 {o['card_id']} 가 작업본에 없습니다")

    # 목록
    idx = root / DIR_CARD / "_INDEX.md"
    expected = render_index(root)
    if not idx.exists():
        if cards:
            err(DIR_CARD + "/_INDEX.md", "목록이 없습니다. rebuild_index.py 를 실행하세요")
    elif idx.read_text(encoding="utf-8") != expected:
        err(DIR_CARD + "/_INDEX.md", "목록이 카드와 다릅니다. rebuild_index.py 를 실행하세요")
    elif (up := uploads.get("index")) and up.get("sha256") != sha256_file(idx):
        warn(DIR_CARD + "/_INDEX.md", "Drive 에 올린 목록과 다릅니다 (plan_upload.py 로 교체)")

    for line in errors + warns:
        print(line)
    print(f"\n카드 {len(cards)}건 점검 — 오류 {len(errors)}, 경고 {len(warns)}")
    if not errors:
        print("점검 통과" + (" (경고 확인 필요)" if warns else ""))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
