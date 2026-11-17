#!/usr/bin/env python3
"""HWP·HWPX(·DOCX·XLSX) 원본을 kordoc 으로 Markdown 파싱본으로 바꾼다.

한글 문서는 반드시 kordoc 으로 읽는다. kordoc 을 찾지 못하면 다른 파서로 대신하지 않고 멈춘다.
(찾는 순서: 환경변수 KORDOC_CMD → kordoc 스킬의 engine/env.json → PATH 의 kordoc → npx kordoc@4.22.13)

파싱본 맨 위에는 어느 Drive 원본에서 만들었는지(source_*)와 변환 정보가 머리말로 들어간다.
쪽 기준: 한컴이 저장한 파일은 실제 쪽(page), 조판 정보가 없으면 구역 단위 근사(section).

예: python3 to_md.py 원본.hwpx --out 파싱/ID.파싱.md --id ID --name 원본이름.hwpx --drive-id DRIVEID
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from archive_lib import now_utc, sha256_file

KORDOC_EXTS = {".hwp", ".hwpx", ".docx", ".xlsx"}  # kordoc CLI(오프라인 구성)로 읽는 형식. PDF 는 kordoc MCP parse_document


class KordocError(RuntimeError):
    pass


def _version_of(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=180)
        return (r.stdout or "").strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else "unknown"
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"


def find_kordoc() -> tuple[list[str], str]:
    """kordoc 실행 명령(리스트)과 버전을 찾는다. 다른 파서로 대신하지 않는다.

    찾는 순서
      1. 환경변수 KORDOC_CMD   예: "node /경로/kordoc/dist/cli.js"  또는  "kordoc"
      2. kordoc 스킬(~/.claude/skills/kordoc, 환경변수 KORDOC_SKILL_DIR 로 바꿈)의 engine/env.json
      3. PATH 의 kordoc 명령 (npm i -g kordoc)
      4. npx -y kordoc@4.22.13  (Node.js 20 이상, 첫 실행 때 내려받음)
    """
    cmd = os.environ.get("KORDOC_CMD")
    if cmd:
        parts = shlex.split(cmd)
        return parts, _version_of(parts)

    skill_dir = Path(os.environ.get("KORDOC_SKILL_DIR", "~/.claude/skills/kordoc")).expanduser()
    env_json = skill_dir / "engine" / "env.json"
    if env_json.is_file():
        try:
            env = json.loads(env_json.read_text(encoding="utf-8"))
            node, cli = env.get("node"), env.get("kordoc_cli")
            if env.get("ok") and node and cli and Path(node).exists() and Path(cli).exists():
                return [node, cli], str(env.get("kordoc_version", ""))
        except (ValueError, OSError):
            pass

    exe = shutil.which("kordoc")
    if exe:
        return [exe], _version_of([exe])
    npx = shutil.which("npx")
    if npx:
        return [npx, "-y", "kordoc@4.22.13"], "4.22.13"
    raise KordocError(
        "kordoc 을 찾지 못했습니다. 다음 중 하나를 준비하세요 (다른 파서로 대신하지 않습니다).\n"
        "  - Node.js 20 이상 설치 (npx 로 자동 실행)\n"
        "  - npm i -g kordoc\n"
        "  - 환경변수 KORDOC_CMD 에 실행 명령 지정\n"
        "  - kordoc 스킬(~/.claude/skills/kordoc)의 scripts/ensure-env.sh 실행")


def _front(ref: dict, extra: dict) -> str:
    keys = ["id", "source_name", "source_drive_id", "source_drive_url", "source_sha256"]
    lines = ["---"] + [f"{k}: {ref.get(k, '')}" for k in keys] + [f"{k}: {v}" for k, v in extra.items()] + ["---"]
    return "\n".join(lines) + "\n"


def wrap_markdown(body: str, ref: dict, extra: dict) -> str:
    banner = f"> 원본: {ref.get('source_name', '')} (Drive 원본/ 폴더, 파일 id {ref.get('source_drive_id', '')})\n\n"
    return _front(ref, extra) + "\n" + banner + body.strip() + "\n"


def convert(src: Path, out_md: Path, ref: dict, force: bool = False) -> dict:
    """src 를 kordoc 으로 읽어 out_md 에 쓴다. ref 에는 id, source_name, source_drive_id, source_drive_url, source_sha256."""
    ext = src.suffix.lower()
    if ext not in KORDOC_EXTS:
        raise KordocError(f"{ext} 는 이 스크립트가 다루지 않습니다 (HWP·HWPX·DOCX·XLSX 만). "
                          "PDF 는 kordoc MCP 의 parse_document 로 읽은 글을 make_card.py --parsed-md 로 넘기세요.")
    if out_md.exists() and not force:
        raise KordocError(f"이미 파싱본이 있습니다 (덮어쓰려면 --force): {out_md}")

    base, version = find_kordoc()
    out_md.parent.mkdir(parents=True, exist_ok=True)
    tmp_json = out_md.parent / ".kordoc_tmp.json"
    env = dict(os.environ, KORDOC_OFFLINE="1")
    cmd = base + [str(src), "--no-images", "--table-format", "gfm", "--format", "json", "-o", str(tmp_json)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
        if r.returncode != 0 or not tmp_json.exists():
            raise KordocError(f"kordoc 변환 실패 (종료 {r.returncode}): {(r.stderr or r.stdout).strip()[-500:]}")
        data = json.loads(tmp_json.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        raise KordocError("kordoc 변환이 10분 안에 끝나지 않았습니다")
    finally:
        tmp_json.unlink(missing_ok=True)

    if not data.get("success", True):
        raise KordocError(f"kordoc 이 문서를 읽지 못했습니다: {data.get('error') or data.get('code') or data}")

    meta = data.get("metadata") or {}
    page_mode = meta.get("pageMode") or "unknown"
    pages = data.get("pages") or []
    label = "쪽" if page_mode == "page" else "구역"
    if pages:
        parts = []
        for p in pages:
            note = "" if page_mode == "page" else " — 쪽 번호 근사, 한글에서 확인"
            parts.append(f"<!-- {label} {p.get('pageNumber')}{note} -->\n\n{(p.get('markdown') or '').strip()}\n")
        body = "\n".join(parts)
    else:
        body = (data.get("markdown") or "").strip() + "\n"
    if not body.strip():
        raise KordocError("변환 결과가 비어 있습니다. 스캔본이거나 읽을 수 없는 파일일 수 있습니다.")

    info = {
        "kordoc_version": version, "page_mode": page_mode,
        "page_count": data.get("pageCount") or meta.get("pageCount") or len(pages),
        "converted_utc": now_utc(),
    }
    out_md.write_text(wrap_markdown(body, ref, info), encoding="utf-8")
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="원본 파일 (원본캐시/ 안의 것)")
    ap.add_argument("--out", required=True, help="파싱본 경로 (…/파싱/{id}.파싱.md)")
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", required=True, help="Drive 원본/ 에서의 파일 이름")
    ap.add_argument("--drive-id", required=True)
    ap.add_argument("--drive-url", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    src = Path(a.file).expanduser().resolve()
    if not src.is_file():
        print(f"오류: 파일이 없습니다: {src}", file=sys.stderr)
        sys.exit(1)
    ref = {"id": a.id, "source_name": a.name, "source_drive_id": a.drive_id,
           "source_drive_url": a.drive_url or f"https://drive.google.com/file/d/{a.drive_id}/view",
           "source_sha256": sha256_file(src)}
    try:
        info = convert(src, Path(a.out).expanduser().resolve(), ref, force=a.force)
    except KordocError as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"파싱: {a.out}  (kordoc {info['kordoc_version']}, 쪽 기준 {info['page_mode']}, {info['page_count']}쪽)")


if __name__ == "__main__":
    main()
