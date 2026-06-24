---
name: engineer-prompts
description: "Gemini 시스템 프롬프트와 도구 docstring을 설계·개선하는 절차. 모드별 출력 포맷, 도구 호출 유도, pinned_group 강제, 한국어 응대 톤 정의. '프롬프트 작성', '프롬프트 개선', '시스템 instruction', 'tool docstring' 트리거."
---

# Engineer Prompts — Gemini 프롬프트 설계

`webapp/server.py`의 시스템 프롬프트와 `agit.py`의 도구 docstring을 다룬다.

## 작업 절차

### 1. 변경 동기 파악
- 무엇이 잘못 작동하는가? (LLM이 모듈 무시, 출력 포맷 깨짐, 도구 미호출 등)
- 또는 어떤 기능을 추가하는가?
- 영향받는 모드(guide/report)와 도구 식별

### 2. 현재 프롬프트 분석
- `SYSTEM_BASE` — 공통 원칙
- `SYSTEM_GUIDE_MODE` — 답변 가이드 모드 워크플로우 + 출력 포맷
- `SYSTEM_REPORT_MODE` — VOC 리포트 모드 워크플로우 + 출력 포맷
- `_build_system()` — pinned_group 주입 로직
- 각 도구의 docstring (`agit.py`)

### 3. 변경안 작성
Before/After 비교로 명확하게:
```
Before:
  "도구 응답을 자연어로 풀어 설명"

After:
  "도구 응답을 자연어로 풀어 설명. per_id_counts가 있으면 답변 하단에 '📊 ID별 분포: ...'로 노출. 한쪽이 0이면 토큰 scope 점검 필요 명시"
```

### 4. 출력 포맷 정의
LLM에게 정확히 어떤 마크다운을 만들어야 하는지 지시:
- 섹션 헤더 (`**① 한 줄 요약**`, `**② 권장 답변 초안**`)
- 표 컬럼 (recency, 작성일, 마지막 활동, 작성자, …)
- 인용 링크 포맷 `[#message_id — YYYY-MM-DD](url)`
- 배지·아이콘 사용 규칙

### 5. 도구 docstring 갱신
Gemini SDK는 docstring으로 스키마를 자동 생성하므로:
- `Args:` 섹션에 각 인자 의미·예시·범위
- `Returns:` 섹션에 응답 구조 명시 (특히 새 필드 `per_id_counts` 같은)

### 6. 검증
- 실제 챗봇에서 1~2개 시나리오 돌려보기
- 응답이 의도한 포맷대로 나오는지 확인
- 모듈 변경 시 pinned_group 강제 여전히 작동하는지 회귀 점검

## 출력 규칙

- 변경 제안은 항상 Before / After + 이유 + 영향 범위
- 도구 docstring 변경 시 Gemini가 자동 재학습하므로 서버 재시작 안내
- 프롬프트 길이 증가 시 토큰 비용 영향 메모

## 점검 체크리스트

- [ ] 영향받는 모드/도구 명시
- [ ] Before/After 비교 포함
- [ ] 한국어 응대 톤 일관 (~합니다 체)
- [ ] pinned_group 강제 규칙 유지
- [ ] 출력 포맷이 시각 컴포넌트와 매칭
- [ ] 실제 응답 샘플로 검증 (1회 이상)
