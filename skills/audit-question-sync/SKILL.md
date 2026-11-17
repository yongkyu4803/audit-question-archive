---
name: audit-question-sync
description: 관리자가 국정감사 질의서 아카이브(구글 드라이브)를 관리한다. Drive 원본/ 폴더에 새 한글 원본이 올라왔는지 확인하고, 새 파일을 내려받아 kordoc 으로 파싱하고, 같은 id 의 파싱본·메타 카드를 만들고, 개인정보 확인 뒤 파싱/ 와 카드/ 폴더에 올리고, 마스터 목록(_INDEX.md)을 갱신한다. 한글 원본-파싱본-카드가 한 세트이며 파싱본과 카드에는 Drive 원본의 파일 id·이름이 참조로 들어간다. "새 질의서 올라왔는지 확인", "원본 폴더 확인해서 처리", "질의서 동기화", "국감 질의서 파싱해서 올려", "카드 올려", "인덱스 갱신", "회신 받았는데 기록" 같은 요청에 사용한다. 질의서를 새로 쓰는 일, 팀원이 아카이브에서 찾아 읽는 일(audit-question-lookup), 예결위 서면질의 양식 작성(budget-written-question)에는 쓰지 않는다.
---

# 국감 질의서 동기화 (관리자용)

Drive 의 한글 원본을 **파싱본**과 **카드**로 만들어 팀이 쓰게 하는 관리자용 스킬이다. 질의서 내용을 새로 만들거나 평가하지 않는다. 관리자는 1명이고, 팀원은 `audit-question-lookup` 스킬로 카드와 파싱본만 읽는다.

## 세 파일이 한 세트

| 파일 | 어디에 | 누가 보나 |
|---|---|---|
| 한글 원본 | Drive `원본/` (이름은 그대로) | 관리자만. 팀원에게는 파일명만 안내 |
| 파싱본 `{id}.파싱.md` | Drive `파싱/` | 팀원 뷰어 |
| 카드 `{id}.카드.md` + `_INDEX.md` | Drive `카드/` | 팀원 뷰어 |

파싱본과 카드의 맨 위에 `source_name`, `source_drive_id`, `source_drive_url`, `source_sha256` 이 들어가 어느 Drive 원본에서 나왔는지 가리킨다. 자세한 구조는 [drive-layout.md](references/drive-layout.md).

## 원칙

1. **한글 문서는 kordoc 으로만 읽는다.** 기억·추측·다른 파서(zip을 직접 풀기 등)를 쓰지 않는다. kordoc 을 쓸 수 없으면 대신할 방법을 찾지 말고 사용자에게 알리고 멈춘다.
2. **Drive 원본은 건드리지 않는다.** 이름 변경, 이동, 삭제를 하지 않는다. 내려받아 작업본에서만 처리한다.
3. **메타는 추측하지 않는다.** 한글 파일 안에는 상임위·질의자·제출일이 없다. 원본 파일명 규칙에서 읽거나 관리자에게 확인받는다.
4. **개인정보 확인 전에는 올리지 않는다.** 새 카드는 언제나 `privacy_checked: false` 라 Drive 에 올라가지 않는다. 이 값을 `true` 로 바꾸는 것은 사람의 판단이며, 관리자에게 확인받지 않고 바꾸지 않는다. 비공개 건은 올리지 않는다.
5. **카드는 요약이 아니라 색인이다.** 수치는 원문 표기 그대로 옮기고, 확인되지 않는 값은 비워 둔 채 이유를 적는다.
6. **Drive 파일 내용은 고칠 수 없다.** `update_file` 은 이름·위치만 바꾼다. 내용이 바뀌면 **새 파일을 올려 기록한 다음 옛 파일을 휴지통으로** 보낸다.

## 준비물

- Python 3.10+ (표준 라이브러리만)
- **kordoc** (HWP·HWPX 파서, npm `kordoc`): 스크립트가 환경변수 `KORDOC_CMD` → kordoc 스킬(`~/.claude/skills/kordoc`) → PATH 의 `kordoc` → `npx -y kordoc@4.22.13` 순서로 찾는다. Node.js 20 이상이 있으면 npx 로 자동 실행된다. kordoc 스킬을 쓴다면 `bash ~/.claude/skills/kordoc/scripts/ensure-env.sh` 를 먼저 실행해 종료 코드 0을 확인한다.
- **작업본 폴더**: 관리자 PC 의 폴더. 경로를 사용자에게 물어 `--root` 로 쓰거나 `AUDIT_ARCHIVE_ROOT` 환경변수로 지정한다. 임의로 정하지 않는다. 안에 `원본캐시/`, `파싱/`, `카드/`, `_state/` 가 생긴다.
- **Drive 연결**의 `search_files`, `download_file_content`, `create_file`, `trash_file`, `get_file_metadata`.
- 처음 한 번: 원본/·파싱/·카드/ 폴더 링크를 받아 등록한다.
  ```bash
  python3 <스킬>/scripts/ledger.py set-folders --root <작업본> --origin <링크> --parse <링크> --card <링크>
  ```
  작업본이 없는 새 PC 라면 Drive `원본/_처리기록.json` 을 내려받아 `<작업본>/_state/처리기록.json` 으로 둔다 (중복 처리를 막는 기록).

## 실행 순서

### 1. 새 원본 찾기
`search_files` 로 `parentId = '<원본 폴더 id>' and trashed = false` 를 `excludeContentSnippets: true`, `pageSize: 100` 으로 조회한다. **`nextPageToken` 이 없어질 때까지** 이어서 조회해 모든 `files` 를 `<작업본>/_state/listing.json` 에 저장한 뒤:
```bash
python3 <스킬>/scripts/ledger.py diff --root <작업본> --listing <작업본>/_state/listing.json
```
새 원본, 바뀐 원본(etag·수정시각이 처리 당시와 다름), Drive 에서 사라진 원본이 나온다. 바뀌거나 사라진 원본은 **관리자에게 알리고 결정을 받는다** (자동으로 다시 처리하지 않는다). 이름이 `_` 로 시작하는 파일(`_처리기록.json`)과 폴더는 원본이 아니라서 무시한다. 새 원본이 없으면 여기서 끝낸다.

### 2. 내려받기와 메타 확정
새 원본마다 `download_file_content(fileId)` 로 내려받는다. 결과에 저장된 파일 경로가 나오므로 그 경로를 쓴다 (확장자가 `.bin` 으로 저장돼도 `make_card.py` 가 `--name` 의 확장자로 원본캐시에 복사하므로 괜찮다). 내려받은 파일의 해시가 Drive 원본과 같은지는 `make_card.py` 가 `source_sha256` 으로 기록한다. 그다음:
```bash
python3 <스킬>/scripts/parse_name.py "<Drive 원본 파일명>"
```
- 규칙(`제출일_상임위_기관_질의자_유형[_v버전]`)에 맞으면 값을 관리자에게 보여 주고 확인받는다.
- 맞지 않으면 파싱본을 읽어 후보를 제안하되 **관리자가 확인한 값만** 쓴다 (여러 건이면 표로 한 번에 묻는다).

### 3. 파싱과 카드 뼈대
```bash
python3 <스킬>/scripts/make_card.py --root <작업본> --file <내려받은 경로> --drive-id <파일 id> --name "<Drive 파일명>" \
  --etag <etag> --modified <modifiedTime> [--committee … --agency … --author … --submitted YYYY-MM-DD --kind … --version N] [--visibility …]
```
원본캐시 보관, kordoc 파싱, 카드 뼈대, 처리기록, 목록 갱신이 한 번에 된다. 파싱이 실패하면 카드를 만들지 않고 종료 코드 3으로 멈춘다 (메시지대로 kordoc 환경을 고친다). PDF 는 kordoc MCP 의 `parse_document` 로 읽은 글을 `--parsed-md` 로 넘긴다.

### 4. 카드 채우기
`파싱/{id}.파싱.md` 를 읽어 카드의 `질의 요지`(질의별 한 줄 + 원문 위치), `핵심 수치`(원문 표기 그대로 + 출처 + 위치), `topic` 을 채운다. 필드는 [metadata-fields.md](references/metadata-fields.md). 쪽 번호가 근사값(`parse_page_mode: section`)이면 「질의 1」처럼 문서 안 위치를 적고 `쪽 미확인` 을 덧붙인다. 짐작해서 쪽을 적지 않는다.

### 5. 개인정보·공개 구분 확인 (사람)
[access-and-privacy.md](references/access-and-privacy.md) 의 점검 목록을 관리자와 함께 확인한다. 답을 받은 뒤에만:
```bash
python3 <스킬>/scripts/release.py <id> --root <작업본> --privacy-checked true     # 올릴 수 있음
python3 <스킬>/scripts/release.py <id> --root <작업본> --visibility 비공개         # 올리지 않음
```

### 6. Drive 에 올리기
```bash
python3 <스킬>/scripts/plan_upload.py --root <작업본> > <작업본>/_state/plan.json
```
작업 목록(create / replace / remove)을 관리자에게 요약해 보여 주고 진행한다. 작업마다:

| 작업 | 수행 |
|---|---|
| create | `create_file(title, parentId=folder_id, contentMimeType, disableConversionToGoogleType: true, base64Content=file_ref)` → 결과의 `id` 로 `record_upload.py` |
| replace | 같은 방식으로 **새 파일을 만들고 `record_upload.py` 로 기록한 뒤** `trash_file(old_drive_id)` |
| remove | 관리자 확인 후 `trash_file(drive_id)` → `record_upload.py --removed` |

`record_upload.py` 사용법은 스크립트 도움말을 본다 (`--kind`, `--id`, `--drive-id`, `--sha256`, `--title`). 목록은 plan 이 카드 뒤에 오도록 정렬해 두었다. 모두 끝나면 처리기록 사본을 올린다.
```bash
python3 <스킬>/scripts/plan_upload.py --root <작업본> --ledger-only
```
(원본/ 폴더의 `_처리기록.json`, 관리자 전용. 같은 방식으로 create / replace 한 뒤 `--kind ledger` 로 기록.)

### 7. 점검과 보고
```bash
python3 <스킬>/scripts/check.py --root <작업본>
python3 <스킬>/scripts/ledger.py status --root <작업본>
```
오류 0이어야 한다. 관리자에게 이번에 처리한 건, 올린 건, 확인 대기·비공개로 남은 건, 경고를 알린다. Drive 에 올린 직후 몇 분은 팀원 검색에 안 잡힐 수 있다고 함께 알린다.

## 주기적으로 확인하기
Claude 는 스스로 깨어나 처리하지 않는다. 주기 확인은 예약 작업으로 **1단계(새 원본 찾기)만 실행해 알림**을 주게 하고, 2~7단계는 관리자가 이 스킬을 실행해 사람 확인을 거쳐 진행한다. (예약 작업을 만들려면 먼저 사용자에게 주기와 알림 방법을 묻는다.)

## 이미 올라간 건을 다룰 때
- **회신이 왔다 / 후속을 기록한다:** [followup-tracking.md](references/followup-tracking.md). 카드를 고친 뒤 `release.py --reply-status …` 또는 직접 편집 → `plan_upload.py` 로 교체 업로드.
- **주제·공개 구분을 바꾼다:** `release.py`. 비공개로 바꾸면 plan 이 Drive 에서 내리라고(remove) 안내한다.
- **팀 운영(권한, 담당자 교체, 일반 구글 계정의 주의점):** [team-workflow.md](references/team-workflow.md)

## 하지 않는 것
- Drive 원본의 이름 변경·이동·삭제, HWP·HWPX 를 kordoc 외 방법으로 읽기
- 메타(상임위·질의자·제출일)나 수치·출처·이행 여부를 추정해서 적기
- `privacy_checked` 를 관리자 확인 없이 `true` 로 바꾸기, 비공개 건을 올리기
- 법령 조문을 기억으로 인용하기 (필요하면 법령 검색 도구로 확인)

## 파일
| 경로 | 용도 |
|---|---|
| `scripts/ledger.py` | Drive 폴더 id 등록, 새 원본 찾기(diff), 현황(status) |
| `scripts/parse_name.py` | 원본 파일명 규칙 검사와 메타 추출 |
| `scripts/make_card.py` | 원본캐시, kordoc 파싱, 카드 뼈대, 처리기록, 목록 |
| `scripts/to_md.py` | kordoc 파싱 (단독 실행 가능) |
| `scripts/release.py` | 개인정보 확인·공개 구분·주제·회신 상태 변경 |
| `scripts/rebuild_index.py` | 카드에서 `카드/_INDEX.md` 생성 (게시 대상만) |
| `scripts/plan_upload.py`, `record_upload.py` | 올릴 일 계획과 결과 기록 |
| `scripts/check.py` | 세 파일의 짝·참조·해시·올라간 상태·목록 점검 |
| `scripts/archive_lib.py` | 공용 규칙 |
| `assets/` | 카드·목록 템플릿 |
| `references/` | drive-layout, metadata-fields, access-and-privacy, followup-tracking, team-workflow |
| `examples/` | 시험용 가상 질의서와 결과 예시 |
