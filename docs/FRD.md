# Agit CS·VOC 챗봇 — 기능 요구사항 정의서 (FRD)

| 항목 | 내용 |
|---|---|
| 문서 버전 | v0.3 |
| 작성일 | 2026-05-20 |
| 연계 문서 | [PRD.md](./PRD.md), [ARCHITECTURE.md](./ARCHITECTURE.md) |

본 문서는 PRD에서 정의한 제품 목적을 구체적인 기능 단위로 분해하고, 각 기능의 사용자 스토리·수용 조건·관련 파일을 정의한다.

---

## 0. 도메인 카테고리

| 카테고리 | 기능 |
|---|---|
| A. 모듈 관리 | A1 모듈 선택, A2 모듈별 토큰 분리, A3 그룹 ID 다중 검색 |
| B. 검색·답변 | B1 답변 가이드 생성, B2 VOC 리포트 생성, B3 도구 자동 호출 |
| C. 세션·이력 | C1 모듈별 세션 분리, C2 메시지 이력 영속, C3 새 대화 |
| D. 입력 UX | D1 텍스트 입력, D2 이미지 첨부 + OCR 분석, D3 추천 액션 카드 |
| E. 모델·구성 | E1 모델 선택, E2 모드 전환, E3 Gemini 튜닝 |
| F. 출력 | F1 마크다운 렌더, F2 근거 사례 + 출처, F3 HTML 리포트 다운로드 |
| G. 운영·진단 | G1 토큰 진단, G2 ID별 분포, G3 재시도, G4 진행률 |

---

## A. 모듈 관리

### A1. 모듈 선택
**As a** 운영자, **I want to** 사이드바에서 작업할 모듈을 클릭으로 선택하고, **So that** 이후 모든 검색·답변이 해당 모듈로만 한정되도록.

**AC**
1. Given 페이지 로드 직후, When 저장된 모듈이 없거나 화이트리스트 밖이면, Then 첫 모듈(전자결재)이 자동 선택된다
2. Given 모듈 클릭, When 사용자가 다른 모듈을 클릭, Then 사이드바 active 상태가 즉시 전환되고 topbar 인디케이터(`🗂️ 전자결재 · 답변 가이드`)도 갱신된다
3. Given 모듈 전환, When 전환 직후, Then 채팅 영역에 해당 모듈의 이전 메시지가 복원되거나(있을 시), 환영 카드가 표시된다(없을 시)
4. Given LLM이 사용자 메시지에 다른 모듈명을 언급, When 도구 호출 시점, Then `PINNED_GROUP` contextvar로 현재 선택 모듈이 강제 override되어 다른 모듈 데이터가 새지 않는다

**파일**: `webapp/static/app.js (applyModule)`, `agit.py (PINNED_GROUP)`, `webapp/server.py (run_chat_turn)`

### A2. 모듈별 토큰 분리
**As a** 운영자, **I want to** 모듈마다 별도 OAuth 토큰을 사용하고, **So that** 검색 권한이 어드민에서 부여한 그룹 범위로 자연스럽게 한정되도록.

**AC**
1. Given `.env`에 `AGIT_TOKEN_ELEC`, `AGIT_TOKEN_HR` 설정, When 모듈 검색 시, Then 해당 모듈 전용 토큰으로 클라이언트가 생성·캐시된다
2. Given 모듈 전용 토큰 미설정, When 호출 시, Then `AGIT_TOKEN`(전역)으로 fallback하되 stderr에 ⚠️ 표시
3. Given 모든 토큰 미설정, When 서버 시작 시, Then sys.exit(1)로 명확한 가이드 출력

**파일**: `agit.py (GROUPS, _resolve_token, get_client_for)`, `webapp/server.py (startup token diagnosis)`

### A3. 그룹 ID 다중 검색
**As a** 운영자, **I want to** 한 모듈에 매핑된 여러 아지트 그룹 ID를 동시에 검색하고, **So that** 글이 분산된 경우에도 누락 없이 후보를 확보하도록.

**AC**
1. Given GROUPS에 `ids: ["300019184", "300100971"]`, When 검색 시, Then 각 ID로 순차 호출 후 `message_id`로 dedupe하여 합산
2. Given ID별 응답 수, When 응답 반환 시, Then `per_id_counts` 필드로 노출되어 어느 ID가 비었는지 진단 가능
3. Given 호출 간 rate limit 보호, When ID 사이, Then 0.5초 sleep

**파일**: `agit.py (_search_multi)`, 도구 함수 4개 (`search_posts`, `find_similar_cases`, `get_group_stats`, `fetch_thread_detail`)

---

## B. 검색·답변

### B1. 답변 가이드 생성
**As a** 운영자, **I want to** 새 CS 문의 텍스트(또는 이미지)를 넣으면 과거 유사 사례 5~10건과 권장 답변 초안이 자동 생성되어, **So that** 그대로 또는 약간 수정 후 응대할 수 있도록.

**AC**
1. Given 답변 가이드 모드, When 메시지 전송, Then 워크플로우: `find_similar_cases` → 상위 1~2건에 대해 `fetch_thread_detail` → 답변 작성
2. 출력 포맷은 6개 섹션 고정 — ① 한 줄 요약 / ② 권장 답변 초안 / ③ 과거 처리 패턴 / ④ 근거 사례 표 / ⑤ 과거→현재 변화 / ⑥ 정확도 점검
3. 모든 사례에 마크다운 링크 `[원문 요약](url)`와 출처 그룹명(`group_title`) 포함
4. `resolution_label`, `recency_rank`로 처리 상태와 최신성을 표 컬럼으로 노출

**파일**: `webapp/server.py (SYSTEM_GUIDE_MODE)`

### B2. VOC 리포트 생성
**As a** PM, **I want to** 모듈의 최근 N건을 통계·이슈 클러스터링한 리포트를 받고, **So that** 정기 보고에 그대로 활용할 수 있도록.

**AC**
1. Given VOC 리포트 모드, When 모수 ≥ 15건 확보, Then 카테고리(에러/기능 요청/문의/기타) 자동 분류
2. 출력 5개 섹션 — ① 개요 / ② 핵심 지표 / ③ 주요 이슈 클러스터 / ④ 시계열 동향 / ⑤ 권고 액션
3. VOC 리포트는 모듈별 "카카오그룹 ...개선 및 문의" 아지트만 대상 (전자결재=300100971, 인사=300102359)
4. 답변 본문 하단에 `📥 HTML 리포트 다운로드` 버튼 노출

**파일**: `webapp/server.py (SYSTEM_REPORT_MODE, report_html)`

### B3. 도구 자동 호출
**As a** 운영자, **I want to** 사용자 메시지에서 LLM이 적절한 도구를 자동으로 골라 호출하고, **So that** 도구명을 외울 필요 없이 자연어로 작업할 수 있도록.

**AC**
1. 등록 도구 5개: `search_posts`, `find_similar_cases`, `fetch_thread_detail`, `get_group_stats`, `list_available_groups`
2. `tool_config.function_calling_config.mode = VALIDATED` 적용 — 스키마 위반 자동 감소
3. 호출된 도구는 응답 메시지 상단에 친화 라벨 chip으로 표시: `🔍 글 검색`, `🧭 유사 사례 검색`, `📜 본문 상세 조회`, `📊 그룹 현황 집계`, `📋 그룹 목록`

**파일**: `agit.py (TOOLS, _DISPATCH)`, `webapp/server.py (build_chat_config, _build_tool_config)`, `webapp/static/app.js (TOOL_LABELS)`

---

## C. 세션·이력

### C1. 모듈별 세션 분리
**AC**
1. localStorage `agit_sessions` = `{전자결재: "uuid", 인사 시스템: "uuid"}`로 세션 ID 매핑
2. 모듈 전환 시 현재 세션 ID가 토픽바·요청 헤더에 즉시 반영
3. 한 모듈에서 응답 대기 중 다른 모듈로 전환 시 → 응답은 원래 모듈에 귀속, 화면은 현재 모듈 기준

### C2. 메시지 이력 영속
**AC**
1. localStorage `agit_msgs_<모듈명>` 키로 모듈별 메시지 배열 저장
2. 페이지 새로고침 후에도 마지막 모듈의 이력 복원
3. 5MB 이상은 silent fail (quota 회피)

### C3. 새 대화
**AC**
1. 사이드바 `↻ 새 대화` 클릭 → 현재 모듈의 세션·이력만 초기화 (다른 모듈 유지)
2. 확인 다이얼로그에 현재 모듈명 명시
3. 서버측 세션도 `DELETE /api/session/{id}` 호출

**파일**: `webapp/static/app.js (sessionMap, messagesMap, reset handler)`, `webapp/server.py (delete_session)`

---

## D. 입력 UX

### D1. 텍스트 입력
**AC**
1. Enter = 전송, Shift+Enter = 줄바꿈
2. 한국어 IME 합성 중(`isComposing` 또는 `keyCode 229`) Enter는 무시 → 두 번 전송 방지
3. textarea는 입력 길이에 따라 자동 grow (max 180px)

### D2. 이미지 첨부 + OCR 분석
**AC**
1. 입력창에 이미지 paste(Cmd+V) 또는 드래그로 최대 3개 첨부 (각 5MB 이하)
2. 전송 시 서버가 Gemini로 이미지를 먼저 분석 (OCR + 키워드 추출), 결과를 `[사용자 첨부 이미지 분석]` 블록으로 user message에 prepend
3. 이미지 분석 config: `media_resolution=MEDIA_RESOLUTION_HIGH` (1120 tokens/image), `thinking_level=low`, `max_output_tokens=1024`

### D3. 추천 액션 카드
**AC**
1. 환영 상태(메시지 0건)에서 4개 카드 표시: 답변 가이드 만들기 / VOC 리포트 발행 / 그룹 현황 / 최근 공지
2. 카드 클릭 시 input에 prompt 자동 채움. `[여기에 ...]` placeholder가 있으면 해당 영역을 자동 선택 → 타이핑 시 즉시 덮어쓰기
3. 자동 전송하지 않음 — 사용자가 검토 후 Enter

**파일**: `webapp/templates/index.html (suggest-card)`, `webapp/static/app.js (paste handler, suggest-card listener)`

---

## E. 모델·구성

### E1. 모델 선택
**AC**
1. 토픽바 우측 `🧠 [모델명] ▾` 트리거 → 320px 드롭다운 메뉴
2. 카탈로그 4개: Gemini 3.1 Pro / Gemini 3 Flash / Gemini 3.1 Flash Lite / Lite Preview
3. 각 옵션에 label / model id (mono) / desc / tier 배지(pro=핑크, flash=블루, lite=그린)
4. 선택 시 localStorage `agit_model` 영속, 사이드바 user-card 모델명 동기화
5. `/api/chat` 요청에 `model` 필드 포함, 응답에 실제 사용 모델 반환

### E2. 모드 전환
**AC**
1. 모드 탭 (`💬 답변 가이드` / `📑 VOC 리포트`)
2. 모드별 시스템 프롬프트 분기 + 모드별 thinking_level 오버라이드 (`report` → low로 가속)
3. 모드 변경 시 hint 문구 즉시 갱신

### E3. Gemini 튜닝 (쿡북 권장)
**AC**
- temperature = 1.0 (chat, image 공통)
- max_output_tokens: chat 16384, image 1024
- thinking_level: Pro/Flash=medium, Lite=low, image=low
- media_resolution: image=HIGH
- tool_config.mode = VALIDATED
- SDK가 enum 미지원 시 silent fallback + ⚠️ stderr 경고

**파일**: `webapp/server.py (MODEL_TUNING, build_chat_config, build_image_config)`

---

## F. 출력

### F1. 마크다운 렌더
**AC**
1. `marked.js` + `DOMPurify` 사용
2. 모든 외부 링크에 `target="_blank" rel="noopener noreferrer"` 자동 부여
3. 표·코드 블록·인용 모두 디자인 토큰에 따른 스타일 적용

### F2. 근거 사례 + 출처
**AC**
1. 답변 가이드 모드는 ④ 근거 사례 표 출력 (recency / 작성일 / 출처 그룹 / 댓글수 / 처리 상태 / 과거 답변·해결 근거 / 핵심 원문 링크)
2. 핵심 원문은 반드시 `[원문 요약](url)` 마크다운 링크
3. 그룹은 숫자 ID가 아닌 `group_title` 사람이 읽는 이름으로 표시
4. `per_group_counts` 또는 `per_id_counts`를 답변 하단 메타 줄에 노출

### F3. HTML 리포트 다운로드
**AC**
1. VOC 리포트 모드 응답 하단에 `📥 HTML 리포트 다운로드` 버튼
2. `/api/report/html` POST → 인쇄 친화 CSS가 인라인된 HTML 파일 반환
3. 파일명: `voc-report-{모듈명}-{YYYYMMDD-HHMMSS}.html`

---

## G. 운영·진단

### G1. 토큰 진단
**AC**
1. 서버 시작 시 stderr에 모듈별 토큰 상태 출력
   ```
   🔑 Agit 토큰 상태:
      · 전역(AGIT_TOKEN): ✅
      · 전자결재  → AGIT_TOKEN_ELEC (전용) [ids: 300019184, 300100971]
      · 인사 시스템 → AGIT_TOKEN_HR (전용) [ids: 300045170, 300102359]
   ```

### G2. ID별 분포
**AC**
1. 멀티 그룹 검색 후 `per_id_counts: {gid: count}` 응답
2. 시스템 프롬프트에 "한쪽이 0이면 토큰 scope 점검 필요"를 답변에 명시하도록 지시

### G3. 재시도
**AC**
1. 에러·네트워크 실패 응답 버블에 `↻ 다시 시도` 버튼
2. 클릭 시 마지막 에러 + 직전 사용자 메시지를 이력에서 제거 후 동일 메시지로 재전송

### G4. 진행률 표시
**AC**
1. 분석 중 로딩 패널에 진행 단계 텍스트 + 경과 시간 + 진행률 바
2. 모듈별 상태(pending/done/error)를 사이드바 카드에 dot/배지로 표시
3. 응답 대기 중 다른 모듈 전환 시 → 해당 모듈 카드에 pending 표시 유지

**파일**: `webapp/static/app.js (PROGRESS_STEPS, refreshLoadingProgress, renderModuleStatuses)`

---

## H. 비기능 요구사항

| 항목 | 요구 |
|---|---|
| 응답 시간 (Lite, 텍스트만) | 10초 이내 (캐시 미적용) |
| 응답 시간 (Lite, 이미지 1장) | 15초 이내 |
| 동시 사용자 | 1명 (단일 운영자 가정) |
| 보안 — 토큰 노출 | `.env`는 git 추적 제외, 응답·UI에 토큰 미노출 |
| XSS | DOMPurify로 LLM 마크다운 렌더 sanitize |
| 접근성 | 키보드만으로 모듈·모드·모델 전환 가능 (탭/ESC) |
| 한글 IME | 합성 중 Enter 무시 (D1 AC #2) |
| 데이터 영속성 | 클라이언트는 localStorage, 서버는 in-memory dict (재시작 시 소실 — 백로그) |

---

## 부록: 화면별 기능 매핑

```
┌─ Sidebar (248px) ────────────┬─ Topbar (64px) ──────────────────────┐
│ [모듈] A1                    │ [모듈 인디케이터 + 모드] A1·E2        │
│  · 전자결재  [상태] G4       │            [모델 셀렉터] E1   [상태]  │
│  · 인사 시스템 [상태] G4     │                                       │
│                              ├─ Mode tabs (44px) E2                  │
│ [세션]                       │                                       │
│  · ↻ 새 대화 C3              ├─ Welcome state (메시지 0건일 때) D3   │
│                              │  · 4개 추천 카드                       │
│ [연결된 도구] B3              │  · 최근 질문 5건                       │
│  · search_posts              │                                       │
│  · find_similar_cases        ├─ Messages F1·F2·G3                    │
│  · ...                       │  · 사용자 / 봇 버블                    │
│                              │  · 도구 chip B3                        │
│ [User card]                  │  · 재시도 G3 / 다운로드 F3             │
│  · 이름 + 현재 모델           │  · 로딩 패널 G4                       │
└──────────────────────────────┴───────────────────────────────────────┘
                               │ Input area D1·D2                      │
                               │  · textarea + 첨부 이미지 + 전송       │
                               └───────────────────────────────────────┘
```
