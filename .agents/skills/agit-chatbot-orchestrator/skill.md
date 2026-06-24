---
name: agit-chatbot-orchestrator
description: "Agit 챗봇 프로젝트의 전체 작업 조율 마스터 스킬. 요청 유형에 따라 product-planner → ux-ui-designer → prompt-engineer → qa-reviewer 순으로 또는 병렬로 호출. '챗봇 개선', '신규 기능', '리팩토링', '전체 작업 조율' 트리거."
---

# Agit Chatbot Orchestrator — 팀 전체 조율

요청을 분석해 적절한 전문가 에이전트를 단계적으로 또는 병렬로 호출해 작업을 완수한다.

## 시나리오 → 에이전트 구성

| 시나리오 | 에이전트 순서 |
|---|---|
| **신규 기능 추가** | product-planner → ux-ui-designer + prompt-engineer (병렬) → qa-reviewer |
| **UI 개선만** | ux-ui-designer → qa-reviewer |
| **LLM 응답 품질 개선** | prompt-engineer → qa-reviewer |
| **모듈 분리·강제 같은 구조 변경** | product-planner → prompt-engineer + ux-ui-designer (병렬) → qa-reviewer |
| **버그·이슈** | qa-reviewer (진단) → 영향 영역에 맞는 전문가 → qa-reviewer (재검증) |
| **PRD 작성만** | product-planner → qa-reviewer (선택) |

## Phase별 워크플로우

### Phase 1: 요청 해석
- 사용자 요청에서 핵심 의도 추출 (기능 추가? 개선? 버그?)
- 영향 영역 식별 (기획 / 디자인 / 프롬프트 / 코드)
- 모듈 영향 (전자결재만? 인사만? 공통?)

### Phase 2: 에이전트 구성
- 위 시나리오 표 참고해 팀 결정
- 병렬 가능 항목 식별 (디자인 + 프롬프트는 보통 병렬)
- 검토 시점 결정 (최종? 각 단계?)

### Phase 3: 실행
- 각 에이전트에 맥락(요청 + 이전 단계 산출물) 전달
- 산출물 형식: 기획 → User Story, 디자인 → 화면 명세, 프롬프트 → Before/After, 검토 → 리포트

### Phase 4: 통합·전달
- 모든 산출물을 단일 응답에 정리
- 변경 영향(파일·모듈·세션·토큰) 명시
- 다음 액션 (서버 재시작? 토큰 추가? 등) 안내

## 에이전트 간 데이터 흐름

```
사용자 요청
    │
    ▼
product-planner (US/AC) ──┐
                          │
                          ├──→ ux-ui-designer (화면 명세)
                          │
                          └──→ prompt-engineer (프롬프트 수정안)
                                       │
                                       ▼
                          qa-reviewer (통합 검토)
                                       │
                                       ▼
                                   최종 산출물
```

## 출력 규칙

- 어떤 에이전트가 무엇을 했는지 명시
- 의존성과 병렬 가능성 명확화
- 코드 수정 영역은 파일 단위로 (`webapp/server.py`, `agit.py`, `webapp/static/app.js` 등)
- 사용자 후속 액션 마지막에 별도 섹션으로

## 점검 체크리스트

- [ ] 요청에 맞는 에이전트만 호출 (과도한 호출 회피)
- [ ] 병렬 호출 가능한 항목은 병렬화
- [ ] qa-reviewer가 최종 통합 점검
- [ ] 모듈 독립성 유지 확인 (pinned_group, per-module session)
- [ ] 서버 재시작/캐시 새로고침 등 운영 조치 안내
