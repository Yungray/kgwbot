# Agit CS·VOC 챗봇 — 변경 이력

날짜는 한국 시각 기준. 각 항목은 "왜 했는가"를 우선 기록한다.

---

## 2026-05-20 — Gemini 3 쿡북 최적 설정 (Phase 1·2)

### Phase 1: 안전한 튜닝
- `temperature` 0.3/0.1 → **1.0** (chat·image 공통)
  - 이유: Gemini 3 공식 권장. 낮추면 thinking 성능 저하 + 복잡 추론 looping
- `thinking_level` 모델별 명시 (Pro/Flash=**medium**, Lite=**low**)
  - 이유: default가 Pro=high라 비용 60-75% 더 발생. 명시로 제어
- `max_output_tokens` chat=16384 / image=1024
  - 이유: guide 모드 ①~⑥ 섹션 + 표 여유, image는 짧은 구조화 출력만
- 이미지 분석도 별도 `build_image_config()`로 분리 — thinking=low 강제

### Phase 2: 정확도·체감 향상
- 이미지 입력 `media_resolution=MEDIA_RESOLUTION_HIGH` (1120 tokens/image)
  - 이유: CS 스크린샷 OCR 정확도 우선. v1beta로 충분, v1alpha 미사용. 비용 미미 (Lite 0.4원/장)
- `tool_config.function_calling_config.mode=VALIDATED`
  - 이유: 함수 호출 시 스키마 위반(잘못된 인자) 자동 감소
- 모드별 thinking 오버라이드 — `report` 모드는 thinking=low 강제
  - 이유: VOC 리포트는 통계·요약 위주라 깊은 추론 불필요, latency·비용 절감

**파일**: `webapp/server.py` (`MODEL_TUNING`, `build_chat_config`, `build_image_config`)

---

## 2026-05-20 — UI 모델 셀렉터 (4개 Gemini 모델)

- 토픽바 우측 `🧠 [모델명] ▾` 드롭다운
- 카탈로그: Gemini 3.1 Pro / Gemini 3 Flash / Gemini 3.1 Flash Lite / Lite Preview
- tier 배지(pro=핑크, flash=블루, lite=그린)로 시각 구분
- localStorage `agit_model` 영속, 사이드바 user-card 모델명 동기화
- `/api/chat` 요청·응답에 `model` 필드 추가

이유: Pro·Flash·Lite 간 비용/품질 트레이드오프를 운영자가 실시간 조정. 환경 변수 `GEMINI_MODEL`이 카탈로그 밖이면 자동 추가 + ⚠️ 경고.

**파일**: `server.py (AVAILABLE_MODELS, resolve_model)`, `templates/index.html (model-selector)`, `static/app.js (applyModel)`, `static/style.css (.model-trigger, .model-menu)`

---

## 2026-05-20 — UX 마찰 해소 (4건)

- **U1** Suggest 카드 클릭 시 `[여기에 ...]` placeholder 영역 자동 선택 → 사용자가 바로 타이핑하면 그 영역만 덮어쓰기
- **U4** 도구 chip을 함수명 → 한글 친화 라벨로 매핑 (`🔍 글 검색`, `🧭 유사 사례 검색`, `📜 본문 상세 조회`, `📊 그룹 현황 집계`, `📋 그룹 목록`). 원본 함수명+args는 호버 tooltip 유지
- **U7** 에러·네트워크 실패 응답 버블 하단에 `↻ 다시 시도` 버튼. 클릭 시 마지막 에러 + 직전 사용자 메시지 제거 후 재전송
- **V4** topbar `세션 없음`/`세션 XXXX` 배지 제거 (노이즈)
- **V5** user-card의 "Agit Bot" → `AGIT_USER_NAME` 또는 `User #{AGIT_USER_ID}`로 실 데이터화

이유: 운영자 인터뷰 없이 자체 진단한 UX 마찰 항목 중 영향도 高·난이도 低인 빠른 윈 묶음.

---

## 2026-05-20 — 모듈별 세션·메시지 분리

- localStorage `agit_sessions` = `{모듈명: "uuid"}` 매핑
- localStorage `agit_msgs_<모듈명>` 키로 메시지 이력 모듈별 저장
- 모듈 전환 시 메시지 자동 swap, 환영 카드 토글
- 새 대화는 현재 모듈만 리셋 (다른 모듈 유지)
- 비동기 안전성: 응답 도착 전에 모듈 바꿔도 결과는 원래 모듈에 귀속

이유: 모듈 토큰을 분리해도 화면 위에서 대화 이력이 섞여 있으면 분리 의미가 약해짐. UX·데이터 모두 모듈 경계 일관.

---

## 2026-05-20 — LNB ERP-style 라이트 테마

- 다크 사이드바 → 라이트 사이드바 (Customer Portal 변형)
- 모듈 카드(전자결재, 인사 시스템)가 사이드바 최상단에 배치
- 드롭다운·모드 토글·빠른 액션은 사이드바에서 모두 제거 → 본문 영역으로 이동
- topbar에 `🗂️ 전자결재 · 답변 가이드` 형태 모듈+모드 인디케이터
- 모드 탭(`💬 답변 가이드` / `📑 VOC 리포트`)을 topbar 아래로
- 환영 화면 4개 추천 액션 카드 (suggest-grid)
- "전체" 그룹 옵션 제거 — 항상 모듈 하나는 pin 상태

이유: 사내 ERP/어드민 일반적인 LNB 패턴에 맞추어 발견성·일관성 향상.

---

## 2026-05-20 — 한국어 IME Enter 두 번 전송 방지

- keydown 핸들러에 `isComposing` / `keyCode === 229` 가드 추가

이유: 한국어 입력 마지막 글자 commit이 Enter와 충돌해서 같은 메시지가 두 번 전송되던 문제.

---

## 2026-05-20 — `PINNED_GROUP` contextvar 강제

- `agit.py`에 `PINNED_GROUP: ContextVar[str | None]` 도입
- 도구 함수 4개(`search_posts`, `find_similar_cases`, `fetch_thread_detail`, `get_group_stats`)가 인자 `group_name`보다 PINNED_GROUP을 우선 적용 (`_effective_group()` 헬퍼)
- `run_chat_turn()`에서 `chat.send_message` 전후로 PINNED_GROUP set/reset
- 시스템 프롬프트에도 강제 적용 명문화 + "다른 그룹 요청 시 드롭다운 변경 안내"

이유: 사이드바에서 인사 시스템을 pin해도 LLM이 직전 대화의 "전자결재" 단어에 끌려 `get_group_stats(group_name="전자결재")`를 호출하는 사고 발생. 시스템 프롬프트만으로는 강제력 부족.

---

## 2026-05-20 — 다중 group_id 지원 + 진단

- `GROUPS`의 `id: str` → `ids: list[str]`로 확장
  - 전자결재: `["300019184", "300100971"]`
  - 인사 시스템: `["300045170", "300102359"]`
- `_search_multi(client, query, group_ids, **kwargs)` 헬퍼 도입
  - 각 ID로 순차 호출 후 `message_id` 기준 dedupe + merge
  - `per_id_counts: {gid: count}` 응답에 포함 → ID별 응답 수 진단
  - 호출 간 0.5초 sleep (rate limit 보호)
- 도구 함수 4개를 `_search_multi`로 라우팅
- 시스템 프롬프트에 "한쪽이 0이면 토큰 scope 점검 필요" 가이드 추가

이유: 한 모듈에 여러 아지트 그룹이 매핑된 경우 누락 없이 검색. 운영자가 토큰 권한 누락을 즉시 발견 가능.

---

## 2026-05-19 — 모듈별 토큰 분리 + 그룹 id 의존성 정리

- `GROUPS = {name: {id, token_env}}` 구조로 확장 (기존 `{name: id}`)
- `_resolve_token()`: 그룹 토큰 우선 → 없으면 `AGIT_TOKEN` fallback
- `get_client_for(group_name)`: 그룹별 AgitClient lazy cache (인스턴스 분리)
- 시작 시 stderr에 그룹별 토큰 상태 진단 출력
- `AGIT_HR_GROUP_ID` 환경 변수 폐기 — 토큰이 어드민에서 그룹에 scope되어 발급되므로 코드 의존성 제거

이유: 검색 정확도 향상 + 권한 격리. 토큰 만료/회수 시 영향 범위 한정.

---

## 2026-05-19 — 폴더 위치 이동 (~/agit_chatbot)

- `~/Desktop/agit_chatbot` → `~/agit_chatbot`

이유: macOS TCC(파일 접근 보호)가 Desktop/Downloads/Documents 폴더의 파일을 Claude Code 등 외부 앱에서 읽지 못하게 차단. 홈 직하로 옮기면 권한 부여 없이도 접근 가능.

---

## 초기 상태 (2026-05-19 이전)

- `agit.py` + `chatbot.py` + `webapp/server.py`
- 단일 `AGIT_TOKEN`, 단일 `GROUPS` dict (이름 → id)
- `chats.create` + tools=TOOLS 자동 함수 호출
- `localhost:8765` uvicorn 단일 모듈 단일 모드
