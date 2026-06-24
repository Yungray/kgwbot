# Agit 챗봇 검색 스펙 & 성능 개선 전략

> 대상 파일: `agit.py`
> 최종 갱신: 2026-05-26
> 범위: 키워드 추출 → 쿼리 확장 → 검색 → 후보 병합 → 관련도 재랭킹 → 처리상태 보강

이 문서는 **(A) 현재 적용된 검색 스펙**과 **(B) 검색 성능 개선 전략(완료분 + 로드맵)** 두 부분으로 구성된다.

---

# Part A. 현재 적용된 검색 스펙

## A-1. 검색 관련 도구(Tools)

| 도구 | 용도 | 검색 특성 |
|---|---|---|
| `search_posts` | 키워드로 글 검색 | 단발 키워드, `recent`/`relevant` 정렬, multi-group merge |
| `find_similar_cases` | 답변 초안용 유사 과거 사례 탐색 | **핵심 파이프라인** — 키워드 추출·동의어 확장·recent+relevant 패스·관련도 재랭킹·댓글 맥락 보강 |
| `get_group_stats` | 그룹 글 수 + 최근 5건 | 빈 쿼리 recent |
| `get_group_task_stats` | 기간별 글/task 상태 집계 | `date_range=specific` + `is_task`, 페이지 전수 순회 |
| `fetch_thread_detail` | 단일 글 본문 + 댓글 전체 | 처리 결말 확인용 |
| `list_available_groups` | 접근 가능 그룹 목록 | — |

## A-2. 그룹·토큰 모델

- `GROUPS` 화이트리스트: 각 그룹은 `ids`(여러 Agit group_id), `voc_report_ids`(VOC 리포트 모드 전용), `id_names`(사람이 읽는 이름), `token_env`(그룹별 OAuth 토큰 환경변수)를 가짐.
- **모드별 대상 그룹 분기**: `PINNED_MODE == "report"`이면 `voc_report_ids`만 검색(카카오그룹 개선/문의 아지트로 한정).
- **세션 pin**: `PINNED_GROUP`이 설정되면 LLM이 다른 그룹으로 호출해도 강제 override → 결과 누수 방지.
- **토큰 해석 순서**: 그룹 `token_env` → 비어있으면 `AGIT_TOKEN`(전역) fallback.
- 여러 `group_id`는 각각 호출 후 `message_id` 기준 dedupe + merge (`_search_multi`).

## A-3. 키워드 추출 파이프라인 (`_extract_keywords`, 상한 8개)

추출 후보는 세 경로에서 모임:

1. **고신호 정규식 패턴** (`_HIGH_SIGNAL_PATTERNS`, 가중치 +2.0)
   - 이메일, `ABC-123` 류 코드, `1234-xxx` 문서번호, `~신청서/정산서/품의서/계정과목/결재선/전표/문서번호` 등 도메인 접미 명사, 따옴표 인용구
2. **도메인/상태 용어 부분일치** (`_DOMAIN_TERMS` ∪ `_STATUS_TERMS`, 가중치 +1.5)
3. **단어 분할 + 인접 bigram**

### 정규화 규칙 (`_normalize_token`)

- 앞뒤 구두점 제거
- 다글자 어미·조사 제거(`_MULTI_SUFFIXES`): `입니다/습니다/합니다/됩니다/주세요/되는/하는/에서는/에서/으로/에는/에도/이나/거나/에게/부터/까지/처럼/라고/관련/보다/세요` (긴 것부터 매칭)
- **단일 조사 제거**(`_SAFE_JOSA` = `을 를 은 는 이 가 의 에 도 만 와 과 께 로`): 어간 2자 이상 보존, **공백 없는 단일 단어에만** 적용
  - `알림이→알림`, `정산서를→정산서`, `일본어로→일본어`
  - 보호: `경로/종로/신청서`(2자) 유지, 구 `기안금액 초과`의 마지막 음절 `과` 보존
- `로/서/고`는 명사 말음 오제거 위험이 커 단일 조사 세트에서 `로`만 (3자 이상 가드와 함께) 포함, `서/고`는 제외

### 술어 배제 (`_looks_predicate`)

- 말음이 `다 요 까 죠 네 군 냐 니`이면 동사·형용사·종결형으로 보고 키워드/구 후보에서 제외
- 효과: `권한 문제일까요`, `들어갑니다 권한` 같은 잡음 구가 검색 쿼리를 잠식하지 않음

### bigram 규칙

- **두 토큰 모두 명사형**(len≥2, 비-stopword, 비-술어)일 때만 생성
- 도메인/상태 용어를 포함해야 함
- 가중치 = `max(w1, w2) + 0.7` → 최고 단일어보다 살짝만 높여 **단일 도메인어와 자연스럽게 섞이게** 함 (전량 bigram화 방지 → recall 보호)

### 키워드 가중치 (`_keyword_weight`)

| 조건 | 가중치 |
|---|---|
| 저가치어(`_LOW_VALUE_WORDS`: 문의/요청/확인/오류…) | 0.5 |
| 도메인·상태 용어 | 3.0 |
| 영문+숫자 혼합(코드·문서번호 류) | 3.0 |
| 길이 ≥ 8 | 2.2 |
| 길이 ≥ 4 | 1.5 |
| 그 외 | 1.0 |

## A-4. 동의어·은어 확장 (`_SYNONYMS`, `_expand_search_queries`)

- Agit 검색이 **substring exact 매칭**이라 공식 명칭과 실무 표현이 서로 안 잡히는 문제를 보완.
- 현재 사전:

  | 표제어 | 변형 |
  |---|---|
  | 정산서 | 지출정산서, 정산 |
  | 마이너스 | 역정산, 예산 복구, 마이너스 전표 |
  | 법인카드 | 법카 |
  | 결재선 | 승인선, 결재 라인 |
  | 출장신청서 | 출장비, 출장 신청 |
  | 휴가 | 연차, 반차 |
  | 발령 | 인사발령 |
  | 전표 | 회계전표 |
  | 계정과목 | 비용계정 |

- 양방향 부분일치(`key in q` 또는 `q in key`)로 변형을 덧붙이고, 원래 쿼리 우선 보존 + 중복 제거 + 상한(기본 6) 적용.

## A-5. 검색·후보 수집 (`find_similar_cases` 본체)

1. `keywords = _extract_keywords(query_text, 8)`
2. `search_queries = _expand_search_queries(keywords[:4], limit=6)`
3. **검색 task 구성**:
   - 전체 쿼리 → `recent` 정렬
   - 상위 3개 쿼리 → `relevant` 정렬도 추가
   - 각 task는 `_search_multi`로 그룹 내 모든 group_id 호출 후 merge
4. task당 점수 누적:
   - `current_score = kw_weight + body_bonus(본문 포함 1.0) + title_bonus(템플릿명 0.4) + relevant_bonus(0.3) + 쿼리순서 보너스(max(0, 0.6 − idx·0.08))`
   - 같은 글 재등장 시 `score += 1`, `match_score += current_score`, matched_keywords 누적
5. rate limit 보호: task 간 `sleep(0.8)`, group_id 간 `sleep(0.5)`, 댓글 조회 간 `sleep(0.15)`

## A-6. 관련도 재랭킹

### 본문 관련도 (`_relevance_score`)

추출된 **전체 키워드**(검색에 쓴 쿼리뿐 아니라 8개 전부)로 후보의 `original_body + body_preview + template_name`을 재채점:

```
각 키워드 kw:
  count = haystack.count(kw)
  tf    = count / (count + 1.5)          # BM25 유사 포화 (1→0.4, 다회→1 수렴)
  total += weight(kw) * (1 + tf)
coverage = 매칭된 서로 다른 키워드 수 / 전체 키워드 수
relevance = total * (0.6 + 0.4 * coverage)   # 여러 단서가 맞는 글 우대
```

- 효과 검증(합성 본문): 전 키워드 매칭+반복 17.6 > 부분 매칭 10.5 > 단일 매칭 2.7 > 무관 0.0

### 종합 점수 (`_case_quality_score`)

| 구성 요소 | 기여 |
|---|---|
| `match_score`(키워드 누적 점수) | 기본 |
| `text_relevance` | × 0.5 |
| 댓글 수 | `min(children,5) × 0.7`, 댓글>0이면 +1.5 |
| 최신성 | recency_rank ≤ 3 → +2.0, ≤ 10 → +0.8 |
| group_title 존재 | +0.3 |
| 처리상태 | resolved/scheduled +3.0, closed_after_reply +2.5, needs_user_action +2.0, needs_review +1.0, unresolved −1.0 |

### 2단계 랭킹 + 댓글 보강

1. 1차 정렬(rank_score) → 상위 **3건**만 댓글/처리 맥락 실조회(`_fetch_thread_resolution`)로 보강
2. 보강된 처리상태로 rank_score 재계산 → 최종 정렬 → `top_cases[:top_k]`

## A-7. 처리상태 분류 (`_classify_resolution`, `_RESOLUTION_RULES`)

댓글 본문 마커로 결말을 분류: `resolved`(완료/반영/해결…), `scheduled`(반영 예정…), `needs_user_action`(확인 부탁/재시도…), `needs_review`(검토/확인 중…), `unresolved`(불가/미지원…). 답변 초안 작성 시 "근거로 쓸 수 있는 사례"(`can_use_as_answer_basis`) 판정에 사용.

## A-8. 응답에 포함되는 메타

`find_similar_cases` 반환: `matched_keywords`, `searched_keywords`(확장된 쿼리), `total_candidates`, `inspected_threads`, `recall_note`, `ranking_note`, `top_cases`(각 case에 `rank_score`, `text_relevance`, `recency_rank`, `last_activity_at`, `resolution_status`, `latest_reply_summary`, `original_body` 포함).

---

# Part B. 검색 성능 개선 전략

검색 품질은 **두 축**으로 분리해 다룬다. 둘은 서로 다른 문제이며 해법도 다르다.

- **Precision / 랭킹**: 후보 집합 *안에서* 관련도 순서를 잘 매기는 것
- **Recall**: 정답이 후보 집합에 *들어오게* 하는 것 — *랭킹을 아무리 잘해도 풀에 없으면 못 올림*

## B-1. 완료된 개선 (현 코드 반영)

### 1차 — Precision / 랭킹
1. **한국어 조사 정규화**: 단일 조사 제거로 `알림이→알림` 등, API 매칭 recall 손실 차단
2. **잡음 bigram 제거**: 술어 붙은 구(`권한 문제일까요`)를 배제, bigram 가중치 하향으로 단일 도메인어와 혼합 → 강한 단서가 검색에서 잘려나가지 않음
3. **본문 관련도 재랭킹**: binary `kw in body`를 TF 포화 + 키워드 커버리지 점수로 대체

### 2차 — Recall (실패 사례 "2022 마이너스 정산" 대응)
4. **`relevant` 정렬 병행**: `recent`만으로는 최근 글에 밀려 못 잡는 과거 글을, 상위 쿼리의 relevant 패스로 후보 풀에 진입
5. **도메인 동의어 확장**: 공식 명칭↔구어체(`마이너스→예산 복구/마이너스 전표`) 함께 검색
6. **도메인 사전 보강**: `마이너스 정산` 등 누락 개념 등록 → 상위 키워드 진입 + 동의어 발동
7. **(버그픽스)** 조사 제거가 구의 마지막 음절(`기안금액 초과→…초`)을 깎던 문제를 단일 단어 한정으로 수정

### 3차 — Recall 실측 기반 (2026-05-27, 전자결재 그룹 실데이터 검증)

> 실측으로 2차의 한계가 드러남: `relevant` 패스가 **page 1만** 조회해, 묻힌 과거 글(예: 2022)이 relevant page 2~3에 몰려 있어 **후보 풀에 진입조차 못 함**. "2022년"을 명시해도 top5가 전부 최신 글이던 문제.

8. **`relevant` 패스 페이징** (`RELEVANT_PASS_PAGES`): page 1→N 으로 파고들어 묻힌 과거 글을 풀에 진입시킴. 효과 확인: "2022 마이너스 정산" top5에 2022 글 진입(이전 0건).
9. **시간 의도 추출**(`_extract_date_range`): 질문의 연도·상대표현(`2022년`/`작년`/`재작년`)을 잡아 `date_range=specific` 기간 한정 패스 추가. 날짜 의도가 있으면 비-날짜 relevant 깊이를 줄여 중복 호출 회피.
10. **최신성 보너스 하향**: `recency_rank≤3` +2.0→+0.8, `≤10` +0.8→+0.3. 과거 글이 최신 글에 밀려 묻히던 문제 완화(처리상태 +3.0은 유지).
11. **死동의어 제거**: 실데이터 0건 확인된 `역정산`·`승인선` 삭제(task·sleep 낭비 차단). `반차`는 2건이라 유지.

## B-2. 다음 단계 (단기, 바로 구현 가능)

| 전략 | 내용 | 비고 |
|---|---|---|
| 가중치 실데이터 튜닝 | `text_relevance × 0.5`, `relevant_bonus 0.3`을 실호출 결과로 보정 | 토큰 필요 |
| 동의어 사전 확충 | 운영팀 실제 VOC 로그에서 은어·약어 수집 | 데이터 기반 |
| 레이턴시 최적화 | 3차 변경으로 호출당 지연 ↑(연도 명시 ~40s, 미명시 ~28s). `RELEVANT_PASS_*`·sleep·recent 쿼리 수 추가 축소, 캐싱 병행 | 콜 수 ↔ recall ↔ 속도 |
| "결과 희박" 시 재시도 | 후보 수가 임계 미만이면 키워드 재조합 후 재검색 | 도구/프롬프트 협업 |
| 상위 후보 검사 확대 | resolution 보강(`inspected_count`)이 top3만이라, 페이징으로 새로 진입한 과거 글이 처리상태 보너스를 못 받음 | 콜 수 ↔ 랭킹 정확도 |
| ~~시계열 date-window 보류~~ | **반증·구현됨(B-1 3차 #9).** relevant 패스가 과거를 흡수한다던 기존 판단은 실측으로 틀림 — relevant page 1엔 과거 글이 없었음 | — |

## B-3. 중장기 (인프라 동반)

| 전략 | 내용 | 난이도 |
|---|---|---|
| **시맨틱/벡터 검색** | 임베딩 기반 의미 검색 — 키워드 불일치(구어체↔공식어)의 근본 해법. 자체 인덱스 구축 필요(Agit API엔 없음) | 높음 (별도 프로젝트) |
| 후보 페이징 확대 | `find_similar_cases`의 단일 페이지 조회를 N페이지로 확장해 recall↑ | 중 (콜 수·rate limit 주의) |
| 평가 하네스 | 정답 셋(쿼리→기대 글) 기반 recall@k / precision@k 자동 측정 | 중 — 튜닝의 전제 조건 |
| 캐싱 | 동일 키워드 검색 결과 단기 캐시로 콜 절감 | 중 |

## B-4. 알려진 제약 / 주의

- **Agit API 인덱싱은 서드파티 영역** — "장기 아카이브 인덱싱 최적화"는 우리가 제어할 수 없음. 우리가 손댈 수 있는 건 **쿼리 전략(정렬·동의어·기간·페이징)**뿐.
- **사용자 시점 힌트(연도 등)에 의존하는 방식**은 보조 수단일 뿐, 검색 성능 개선으로 포장하지 않는다(부담을 사용자에게 전가). 단, 사용자가 자발적으로 준 연도 의도(`2022년`)는 폐기하지 않고 `date_range`로 활용한다(B-1 3차 #9).
- 3차 변경으로 `find_similar_cases` 호출당 API 콜이 더 증가(연도 명시 시 ~15 task × group_id 수) → **응답 지연 ~28~40s**. 품질(recall) 우선 선택의 트레이드오프이며 `RELEVANT_PASS_*` 상수로 튜닝 가능. 근본적 지연 해소는 B-3(캐싱/벡터 인덱스) 영역.
- **실데이터 검증 완료(2026-05-27)**: 2022 글 후보 진입 여부를 전자결재 그룹 실호출로 확인 — page-1 천장 가설 확정 및 수정 효과(2022 글 top5 진입) 검증. 가중치 미세 적정성은 여전히 정답셋(평가 하네스) 부재로 정성 판단 수준.
