# Agit CS·VOC 챗봇 — 시스템 아키텍처

| 항목 | 내용 |
|---|---|
| 문서 버전 | v0.3 |
| 작성일 | 2026-05-20 |

---

## 1. 시스템 구성

```
┌─ 브라우저 (Chrome/Safari) ─────────────────────┐
│  ├─ index.html  (Jinja2 SSR)                   │
│  ├─ app.js      (state, fetch, markdown render)│
│  └─ style.css   (CollaboAI Admin 토큰)         │
└──────────────────┬─────────────────────────────┘
                   │ HTTP (localhost:8765)
                   ▼
┌─ FastAPI (webapp/server.py) ───────────────────┐
│  ├─ /                  Jinja2 페이지            │
│  ├─ POST /api/chat     채팅 (도구 자동 호출)    │
│  ├─ POST /api/report/html  리포트 다운로드      │
│  ├─ GET  /api/meta     모델/그룹/도구 카탈로그  │
│  ├─ GET  /api/health   상태                     │
│  └─ DELETE /api/session/{id}  세션 종료         │
└──────────────────┬─────────────────────────────┘
                   │
        ┌──────────┴───────────┐
        ▼                      ▼
┌─ Gemini SDK ────────┐  ┌─ agit.py ──────────────┐
│ google.genai        │  │ AgitClient (requests)  │
│  · chats.create()   │  │ TOOLS = 5 functions    │
│  · models           │  │ PINNED_GROUP contextvar│
│    .generate_content│  │ _search_multi (dedupe) │
└──────────┬──────────┘  └────────────┬───────────┘
           │                          │
           ▼                          ▼
   Gemini API                  Agit Search API
 (generativelanguage         (api.agit.in/v2/search)
  .googleapis.com)
```

---

## 2. 주요 파일·역할

| 파일 | 역할 | 라인 |
|---|---|---|
| `agit.py` | Agit Search API 래퍼, 도구 함수 5개, GROUPS·토큰 매핑, `PINNED_GROUP`/`PINNED_MODE` contextvar, `_search_multi` | ~1014 |
| `webapp/server.py` | FastAPI 앱, 시스템 프롬프트 (guide/report), Gemini Config 빌더, 이미지 분석, HTML 리포트 생성 | ~676 |
| `webapp/static/app.js` | 클라이언트 state, 모듈/모델/모드 핸들러, 메시지 영속, 로딩 progress | ~797 |
| `webapp/static/style.css` | 디자인 토큰 + 컴포넌트 스타일 | ~935 |
| `webapp/templates/index.html` | Jinja2 SSR 페이지 (sidebar / topbar / mode tabs / welcome / messages / input) | ~201 |
| `chatbot.py` | CLI 진입점 (deprecated 가능, 현재 사용 비중 낮음) | ~150 |
| `.env` | 시크릿: 토큰, GEMINI_API_KEY, GEMINI_MODEL | — |

---

## 3. 데이터 흐름 (답변 가이드 모드 1턴)

```
[User] textarea Enter
    ↓
[app.js] sendMessage()
  - currentGroup, currentMode, currentModel 캡처
  - 이미지 base64 인코딩
  - pushMessage(localStorage)
  - moduleStateMap[group] = pending
    ↓
[POST /api/chat] {message, session_id, mode, group, model, images}
    ↓
[server.py] chat() handler
  - resolve_model() → 카탈로그 검증
  - analyze_images_for_search() — Gemini로 이미지 OCR
  - run_chat_turn()
      - PINNED_GROUP.set(group) / PINNED_MODE.set(mode)
      - chats.create(model, config=build_chat_config(model, mode, system_prompt))
      - chat.send_message(user + image_context)
          ↓
          [Gemini] 자동 도구 호출 (function calling)
            ↓
            agit.py: find_similar_cases / fetch_thread_detail / ...
              - get_client_for(group_name)  ← PINNED_GROUP override
              - _search_multi(client, query, group_ids)
                  - 각 ID로 Agit API 호출
                  - message_id로 dedupe
              ↓
          [Gemini] 응답 텍스트 + tool_calls 추출
    ↓
[ChatResponse] {session_id, text, tool_calls, mode, group, model}
    ↓
[app.js] appendBotMessage() + pushMessage()
  - 마크다운 렌더 → DOMPurify sanitize
  - 도구 chip 표시 (TOOL_LABELS)
  - moduleStateMap[group] = done
```

---

## 4. 도구(Tools) 정의

| 도구 | 인자 | 동작 |
|---|---|---|
| `search_posts` | query, group_name, sort, limit | 그룹 내 글 검색, 다중 ID dedupe |
| `find_similar_cases` | query_text, group_name, top_k | 키워드 추출 후 키워드별 검색, 점수+최신성 정렬, top_k 반환 |
| `fetch_thread_detail` | message_id, group_name | 특정 글의 본문 + 답글 전체 조회 |
| `get_group_stats` | group_name | 그룹 전체 글 수 + 최근 5건 샘플 |
| `list_available_groups` | (none) | 화이트리스트 + 토큰 설정 여부 |

각 도구 함수의 첫 줄에서 `_effective_group(group_name)` 호출 → PINNED_GROUP이 set되어 있으면 그것으로 인자 강제 치환.

---

## 5. 디자인 토큰 (CollaboAI Admin Customer Portal 변형)

| 토큰 | 값 | 용도 |
|---|---|---|
| `--indigo-500` | `#6366F1` | Primary accent (active, button) |
| `--indigo-600` | `#4F46E5` | Primary hover |
| `--gray-50/100/200` | `#F9FAFB / #F3F4F6 / #E5E7EB` | 배경/border |
| `--gray-700/900/950` | `#374151 / #18212F / #111827` | 텍스트 계층 |
| `--success/warning/error-700/100` | semantic 페어 | 배지·도트 |

폰트: Inter (base 14px, line-height 1.5), 스페이싱: 8px base.

---

## 6. 환경 변수

| 변수 | 필수 | 설명 |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | AI Studio 또는 GCP API key |
| `GEMINI_MODEL` | 선택 | default 모델 id (카탈로그 밖이면 자동 추가) |
| `AGIT_TOKEN` | ⚠️ | 전역/fallback 토큰 (모듈 토큰 없을 때 사용) |
| `AGIT_TOKEN_ELEC` | 권장 | 전자결재 그룹 전용 토큰 |
| `AGIT_TOKEN_HR` | 권장 | 인사 시스템 그룹 전용 토큰 |
| `AGIT_USER_ID` | 선택 | target_id 파라미터 (현재 미사용) |
| `AGIT_USER_NAME` | 선택 | 사이드바 user-card 표시명 |

`AGIT_TOKEN` 또는 그룹별 토큰 중 **최소 하나는 필요**. 모두 비어있으면 서버 시작 거부.

---

## 7. 외부 의존성

| 의존 | 용도 |
|---|---|
| `google-genai` | Gemini SDK (chats, function calling, ThinkingConfig, MediaResolution) |
| `fastapi` + `uvicorn` | 웹 서버 |
| `jinja2` | SSR 템플릿 |
| `requests` | Agit API 호출 |
| `python-dotenv` | `.env` 로드 |
| `markdown` | HTML 리포트 생성 |
| `marked.js` (CDN) | 클라이언트 마크다운 렌더 |
| `DOMPurify` (CDN) | XSS 방지 |

---

## 8. 알려진 제약·기술 부채

| 항목 | 현황 | 대응 |
|---|---|---|
| 세션 in-memory | `_sessions: dict` — 재시작 시 소실, 다중 워커 시 분리 | SQLite/Redis 백로그 |
| `chatbot.py` CLI | 초기 진입점이었으나 현재 거의 미사용 | 추후 삭제 또는 별도 운영 도구로 분리 |
| 모듈 하드코딩 | `GROUPS = {...}` 코드 수정 필요 | 어드민 UI 백로그 |
| 토큰 만료 처리 | 401 발생 시 명시적 안내 없음 | 토큰 갱신 가이드 + 자동 재시도 백로그 |
| 모바일 UI | 900px 이하는 기본 대응만 | 본격 모바일 대응 백로그 |

---

## 9. 운영 절차

### 9.1 시작
```bash
cd ~/agit_chatbot
python3 -m uvicorn webapp.server:app --reload --port 8765
```

### 9.2 토큰 확인 (시작 시 stderr)
```
🔑 Agit 토큰 상태:
   · 전역(AGIT_TOKEN): ✅
   · 전자결재  → AGIT_TOKEN_ELEC (전용) [ids: 300019184, 300100971]
   · 인사 시스템 → AGIT_TOKEN_HR (전용) [ids: 300045170, 300102359]
```
모든 모듈이 ✅(전용)으로 표시되어야 정상.

### 9.3 진단 엔드포인트
- `GET /api/health` — 활성 세션 수, default 모델
- `GET /api/meta` — 그룹·도구·모델 카탈로그 (디버깅용 curl)

### 9.4 로컬 데이터 정리
브라우저에서 운영자가 데이터 누적 초기화하려면 DevTools → Application → Local Storage → 해당 origin → 키 삭제:
- `agit_sessions` — 모듈별 세션 ID
- `agit_msgs_<모듈명>` — 모듈별 메시지 이력
- `agit_recent_questions` — 최근 질문
- `agit_module_state` — 모듈 상태 (pending/done)
- `agit_model` / `agit_mode` / `agit_group` — 마지막 선택값
