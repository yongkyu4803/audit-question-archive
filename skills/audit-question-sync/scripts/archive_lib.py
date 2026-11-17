"""국감 질의서 동기화 공용 규칙. 모든 스크립트가 함께 쓴다. 표준 라이브러리만 사용한다.

작업본 폴더(관리자 PC)와 Drive 폴더의 대응:
  원본캐시/  ← Drive 원본/ 에서 내려받은 한글 원본 (관리자만, 올리지 않음)
  파싱/      → Drive 파싱/   ({id}.파싱.md)
  카드/      → Drive 카드/   ({id}.카드.md, _INDEX.md)
  _state/    처리기록.json (처리한 원본, 올린 파일, Drive 폴더 id)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent

DIR_CACHE, DIR_PARSE, DIR_CARD, DIR_STATE = "원본캐시", "파싱", "카드", "_state"
PARSE_SUFFIX, CARD_SUFFIX = ".파싱.md", ".카드.md"

KINDS = ["본질의", "서면질의", "요구자료", "보충질의", "종합질의"]
STATUSES = ["제출", "수정제출", "폐기"]
VISIBILITIES = ["공개", "내부", "비공개"]
REPLY_STATUSES = ["미수령", "수령", "부분수령", "해당없음"]

ORIGINAL_EXTS = {".hwpx", ".hwp", ".docx", ".doc", ".xlsx", ".pdf"}

BAD_NAME_CHARS = re.compile(r'[_/\\\s:*?"<>|]')

REQUIRED_FIELDS = [
    "id", "year", "committee", "agency", "author", "submitted", "kind", "version", "status",
    "source_name", "source_drive_id", "source_sha256", "visibility", "reply_status",
]

INDEX_COLUMNS = ["id", "제출일", "상임위", "피감기관", "질의자", "유형", "버전", "회신", "주제", "원본 파일명", "카드", "파싱"]


# ---------- 이름·값 규칙 ----------
def make_id(submitted: str, committee: str, agency: str, author: str, kind: str, version: int) -> str:
    return f"{submitted}_{committee}_{agency}_{author}_{kind}_v{version}"


def valid_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s))
    except ValueError:
        return False


def check_name_part(label: str, value: str) -> str | None:
    if not value:
        return f"{label} 이(가) 비어 있습니다"
    if BAD_NAME_CHARS.search(value):
        return f"{label} '{value}' 에 밑줄·공백·경로 문자가 있습니다 (공백 없이 붙여 쓰세요)"
    return None


def parse_filename(name: str) -> dict | None:
    """원본 파일명 규칙: 제출일_상임위_기관_질의자_유형[_v버전].확장자  (제출일은 YYYY-MM-DD 또는 YYYYMMDD).

    규칙에 맞지 않으면 None. 맞아도 값은 사람이 확인한다.
    """
    stem = Path(name).stem
    stem = re.sub(r"\s*\(\d+\)$", "", stem)  # 브라우저가 붙인 ' (1)'
    parts = stem.split("_")
    if len(parts) not in (5, 6):
        return None
    d = parts[0]
    if re.fullmatch(r"\d{8}", d):
        d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    if not valid_date(d):
        return None
    committee, agency, author, kind = parts[1:5]
    if kind not in KINDS or any(check_name_part("x", v) for v in (committee, agency, author)):
        return None
    version = 1
    if len(parts) == 6:
        m = re.fullmatch(r"v(\d+)", parts[5])
        if not m or int(m.group(1)) < 1:
            return None
        version = int(m.group(1))
    return {"submitted": d, "committee": committee, "agency": agency, "author": author, "kind": kind, "version": version}


def is_true(v) -> bool:
    return str(v).strip().lower() == "true"


def publishable(fm: dict) -> bool:
    """Drive 에 올려도 되는 카드: 공개·내부이면서 개인정보 확인이 끝난 것."""
    return fm.get("visibility") in ("공개", "내부") and is_true(fm.get("privacy_checked"))


# ---------- 해시·시간·Drive id ----------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def drive_id_from(s: str) -> str:
    """Drive 링크나 id 문자열에서 id 를 뽑는다."""
    s = s.strip()
    for pat in (r"/folders/([\w-]+)", r"/d/([\w-]+)", r"[?&]id=([\w-]+)"):
        m = re.search(pat, s)
        if m:
            return m.group(1)
    if re.fullmatch(r"[\w-]{10,}", s):
        return s
    raise ValueError(f"Drive id 로 읽을 수 없습니다: {s}")


# ---------- 머리말 파서 ----------
def _strip_quotes(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse_front(text: str) -> tuple[dict | None, str]:
    """맨 위 `---` 머리말을 읽는다. `key: 값`, `key: []`, `key: [a, b]`, 들여쓴 `- 항목` 만 지원."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None, text
    data: dict = {}
    key = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith((" ", "\t")) and raw.strip().startswith("- ") and key is not None:
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(_strip_quotes(raw.strip()[2:]))
            continue
        if ":" not in raw:
            continue
        k, _, v = raw.partition(":")
        key, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            data[key] = [_strip_quotes(x) for x in inner.split(",") if x.strip()] if inner else []
        else:
            data[key] = _strip_quotes(v)
    return data, "\n".join(lines[end + 1:])


def set_field(text: str, key: str, value: str) -> str:
    """머리말의 `key: 값` 한 줄을 바꾼다. 없으면 머리말 끝에 넣는다."""
    lines = text.split("\n")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    new = f"{key}: {value}".rstrip()
    for i in range(1, end):
        if re.match(rf"^{re.escape(key)}:", lines[i]):
            lines[i] = new
            return "\n".join(lines)
    lines.insert(end, new)
    return "\n".join(lines)


# ---------- 작업본 경로 ----------
def resolve_root(arg: str | None) -> Path:
    r = arg or os.environ.get("AUDIT_ARCHIVE_ROOT")
    if not r:
        raise SystemExit("오류: --root 또는 환경변수 AUDIT_ARCHIVE_ROOT 로 작업본 폴더를 지정하세요.")
    return Path(r).expanduser().resolve()


def card_path(root: Path, cid: str) -> Path:
    return root / DIR_CARD / f"{cid}{CARD_SUFFIX}"


def parse_path(root: Path, cid: str) -> Path:
    return root / DIR_PARSE / f"{cid}{PARSE_SUFFIX}"


def iter_cards(root: Path):
    """(경로, 머리말) 을 id 순으로. 머리말이 없는 카드는 fm=None."""
    d = root / DIR_CARD
    if not d.is_dir():
        return
    for p in sorted(d.glob(f"*{CARD_SUFFIX}")):
        fm, _ = parse_front(p.read_text(encoding="utf-8"))
        yield p, fm


# ---------- 처리기록 ----------
def ledger_path(root: Path) -> Path:
    return root / DIR_STATE / "처리기록.json"


def load_ledger(root: Path) -> dict:
    p = ledger_path(root)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"version": 1, "folders": {}, "originals": {}, "uploads": {}}


def save_ledger(root: Path, led: dict) -> None:
    p = ledger_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(led, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def ledger_snapshot_text(led: dict) -> str:
    """Drive 에 올릴 처리기록 사본. 자기 자신의 업로드 기록(uploads.ledger)은 뺀다 (해시가 계속 바뀌는 순환을 막는다)."""
    snap = json.loads(json.dumps(led))
    snap.get("uploads", {}).pop("ledger", None)
    return json.dumps(snap, ensure_ascii=False, indent=2) + "\n"
