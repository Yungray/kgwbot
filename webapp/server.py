"""Agit 챗봇 — FastAPI 웹 서버 (Google Gemini 기반).

실행:
  cd agit_chatbot/
  python3 -m uvicorn webapp.server:app --reload --port 8765

접속:
  http://localhost:8765
"""
import os
import re
import sys
import time
import json
import uuid
import base64
import binascii
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

import markdown as md_mod

# .env 자동 로드
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# agit_chatbot/ 디렉토리를 sys.path에 추가 (agit 모듈 import 위해)
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

from agit import TOOLS, GROUPS, available_token_envs, _resolve_token, PINNED_GROUP, PINNED_MODE
from agit import get_group_task_stats


# ───────────────────────────────────────────────────────────
# 환경변수 체크
# ───────────────────────────────────────────────────────────
if not os.environ.get("GEMINI_API_KEY"):
    print("❌ GEMINI_API_KEY 환경변수가 비어 있습니다", file=sys.stderr)
    sys.exit(1)

# Agit 토큰 상태 진단: 그룹별 token_env / 전역 AGIT_TOKEN 중 최소 1개 필요
_global_token_set = bool((os.environ.get("AGIT_TOKEN") or "").strip())
_group_token_status: list[tuple[str, str, bool]] = []
for _name, _meta in GROUPS.items():
    _env = _meta.get("token_env", "")
    _has_env = bool((os.environ.get(_env) or "").strip()) if _env else False
    _group_token_status.append((_name, _env, _has_env))

if not _global_token_set and not any(ok for _, _, ok in _group_token_status):
    print(
        "❌ Agit 토큰이 하나도 설정되어 있지 않습니다. "
        f".env에 AGIT_TOKEN 또는 {', '.join(available_token_envs())} 중 하나 이상을 채우세요.",
        file=sys.stderr,
    )
    sys.exit(1)

# 시작 로그: 그룹별 활성 상태 출력
print("🔑 Agit 토큰 상태:", file=sys.stderr)
print(f"   · 전역(AGIT_TOKEN): {'✅' if _global_token_set else '⚠️ 미설정 (그룹 토큰만 사용)'}", file=sys.stderr)
for _name, _env, _has in _group_token_status:
    _ids = (GROUPS.get(_name) or {}).get("ids") or []
    _id_str = f" [ids: {', '.join(_ids)}]" if _ids else " [ids 없음]"
    if _has:
        _src = f"{_env} (전용){_id_str}"
    elif _global_token_set:
        if _ids:
            _src = f"AGIT_TOKEN (fallback){_id_str}"
        else:
            _src = "AGIT_TOKEN (fallback) ⚠️ ids 없음 — 결과에 다른 그룹 데이터가 섞일 수 있음"
    else:
        _src = "❌ 미설정 — 호출 시 에러"
    print(f"   · {_name:<10s} → {_src}", file=sys.stderr)


# ───────────────────────────────────────────────────────────
# Gemini 모델 카탈로그 — UI에서 사용자가 자유 전환할 4개 후보
# ───────────────────────────────────────────────────────────
AVAILABLE_MODELS: list[dict] = [
    {
        "id":   "gemini-3.1-pro-preview",
        "label": "Gemini 3.1 Pro",
        "desc":  "최고 성능 · 복잡한 추론 · 멀티모달",
        "tier":  "pro",
    },
    {
        "id":   "gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "desc":  "최신 Flash · 속도·비용 균형 (신규)",
        "tier":  "flash",
    },
    {
        "id":   "gemini-3-flash-preview",
        "label": "Gemini 3 Flash",
        "desc":  "속도·비용 균형 (3-series Flash)",
        "tier":  "flash",
    },
    {
        "id":   "gemini-3.1-flash-lite",
        "label": "Gemini 3.1 Flash Lite",
        "desc":  "고효율 · 대량 처리 · 무료 tier",
        "tier":  "lite",
    },
    {
        "id":   "gemini-3.1-flash-lite-preview",
        "label": "Gemini 3.1 Flash Lite (Preview)",
        "desc":  "Flash-Lite preview · 실험/검증용",
        "tier":  "lite",
    },
]
_AVAILABLE_MODEL_IDS: set[str] = {m["id"] for m in AVAILABLE_MODELS}

# 환경 변수에 지정된 모델이 카탈로그 밖이면 호환을 위해 항목으로 추가 후 기본값으로 사용
_env_model = (os.environ.get("GEMINI_MODEL") or "").strip()
if _env_model and _env_model not in _AVAILABLE_MODEL_IDS:
    AVAILABLE_MODELS.insert(0, {
        "id": _env_model,
        "label": f"{_env_model}",
        "desc": ".env에서 지정한 모델",
        "tier": "env",
    })
    _AVAILABLE_MODEL_IDS.add(_env_model)
    print(f"⚠️  GEMINI_MODEL={_env_model} 가 기본 카탈로그에 없어 사용자 정의 항목으로 추가", file=sys.stderr)

DEFAULT_MODEL = _env_model if _env_model in _AVAILABLE_MODEL_IDS else AVAILABLE_MODELS[0]["id"]
# 호환용 별칭 (기존 코드가 GEMINI_MODEL을 참조)
GEMINI_MODEL = DEFAULT_MODEL


def resolve_model(requested: str | None) -> str:
    """클라이언트가 보낸 모델 id가 카탈로그에 있으면 그걸, 아니면 DEFAULT_MODEL."""
    if requested and requested in _AVAILABLE_MODEL_IDS:
        return requested
    return DEFAULT_MODEL


# ───────────────────────────────────────────────────────────
# Gemini 3 쿡북 권장 튜닝 — 모델별 thinking_level 매핑 + 공통 설정
# ───────────────────────────────────────────────────────────
# Gemini 3 권장사항:
#   - temperature는 1.0 유지 (낮추면 thinking 성능 저하 + 복잡 추론에서 looping 가능)
#   - thinking_level을 명시하지 않으면 비싼 default(Pro=high) 사용 → 명시로 비용 절감
#   - 이미지 분석은 OCR/구조화 정도라 thinking 'low'로 충분
MODEL_TUNING: dict[str, dict] = {
    "gemini-3.1-pro-preview":        {"thinking_level": "medium", "img_thinking_level": "low"},
    "gemini-3.5-flash":              {"thinking_level": "medium", "img_thinking_level": "low"},
    "gemini-3-flash-preview":        {"thinking_level": "medium", "img_thinking_level": "low"},
    "gemini-3.1-flash-lite":         {"thinking_level": "low",    "img_thinking_level": "low"},
    "gemini-3.1-flash-lite-preview": {"thinking_level": "low",    "img_thinking_level": "low"},
}
CHAT_TEMPERATURE = 1.0
CHAT_MAX_OUTPUT_TOKENS = 16384  # 가이드 모드의 ①~⑥ 섹션 + 표를 위한 여유분
IMAGE_TEMPERATURE = 1.0
IMAGE_MAX_OUTPUT_TOKENS = 1024

# 모드별 thinking_level 오버라이드 — guide는 모델 default 유지, report는 통계/요약이라 빠른 처리
MODE_THINKING_OVERRIDE: dict[str, str] = {
    "report": "low",
    "stats": "low",
}

# Tool calling 모드: VALIDATED는 함수 스키마 위반(잘못된 인자 타입) 자동 감소
TOOL_CALLING_MODE = "VALIDATED"

# 이미지 입력 해상도 — Global 설정(v1beta 호환, v1alpha 불필요).
# HIGH = 1120 tokens/image, 화면 캡처 OCR 정확도 최대화. CS 스크린샷 use case에 최적.
IMAGE_MEDIA_RESOLUTION = "MEDIA_RESOLUTION_HIGH"


def _model_tuning(model: str) -> dict:
    return MODEL_TUNING.get(model, {"thinking_level": "medium", "img_thinking_level": "low"})


def _build_thinking_config(level: str | None):
    """thinking_level이 None이면 None 반환. SDK가 지원 안 하면 silent fallback."""
    if not level:
        return None
    try:
        return types.ThinkingConfig(thinking_level=level)
    except (AttributeError, TypeError):
        print(f"⚠️  현재 google-genai SDK가 thinking_level={level!r}을 지원하지 않아 무시", file=sys.stderr)
        return None


def _build_tool_config(mode: str):
    """function_calling_config.mode가 SDK에서 지원 안 되면 silent fallback."""
    if not mode:
        return None
    try:
        return types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode=mode)
        )
    except (AttributeError, TypeError, ValueError) as e:
        print(f"⚠️  tool_config mode={mode!r} 지원 안 함 — SDK default(AUTO) 사용 ({e})", file=sys.stderr)
        return None


def _build_media_resolution(level: str | None):
    """media_resolution 미지원 SDK에서는 None 반환 (silent fallback)."""
    if not level:
        return None
    try:
        return getattr(types.MediaResolution, level)
    except AttributeError:
        print(f"⚠️  media_resolution={level!r} 지원 안 함 — 기본 해상도 사용", file=sys.stderr)
        return None


def build_chat_config(
    model: str,
    mode: Literal["guide", "report", "stats"],
    system_instruction: str,
) -> "types.GenerateContentConfig":
    """채팅(도구 호출 포함) GenerateContentConfig 빌더 — mode 인지."""
    tuning = _model_tuning(model)
    # report 모드는 통계 위주라 thinking 낮춰 latency/cost 절감
    thinking_level = MODE_THINKING_OVERRIDE.get(mode) or tuning.get("thinking_level")

    kwargs: dict = {
        "system_instruction": system_instruction,
        "tools": TOOLS,
        "temperature": CHAT_TEMPERATURE,
        "max_output_tokens": CHAT_MAX_OUTPUT_TOKENS,
    }
    thinking = _build_thinking_config(thinking_level)
    if thinking is not None:
        kwargs["thinking_config"] = thinking
    tool_cfg = _build_tool_config(TOOL_CALLING_MODE)
    if tool_cfg is not None:
        kwargs["tool_config"] = tool_cfg
    return types.GenerateContentConfig(**kwargs)


def build_image_config(model: str) -> "types.GenerateContentConfig":
    """이미지 분석(OCR/구조화) GenerateContentConfig — thinking 낮추고 해상도 높여 정확도↑."""
    tuning = _model_tuning(model)
    kwargs: dict = {
        "temperature": IMAGE_TEMPERATURE,
        "max_output_tokens": IMAGE_MAX_OUTPUT_TOKENS,
    }
    thinking = _build_thinking_config(tuning.get("img_thinking_level"))
    if thinking is not None:
        kwargs["thinking_config"] = thinking
    media_res = _build_media_resolution(IMAGE_MEDIA_RESOLUTION)
    if media_res is not None:
        kwargs["media_resolution"] = media_res
    return types.GenerateContentConfig(**kwargs)


# ───────────────────────────────────────────────────────────
# 시스템 프롬프트 — 모드별 분기
# ───────────────────────────────────────────────────────────
_GROUP_LINE = "\n".join(f"- '{g}'" for g in GROUPS.keys())

SYSTEM_BASE = f"""너는 카카오/디케이테크인 사내 아지트(Agit) CS·VOC 데이터를 다루는 한국어 어시스턴트야.

## 사용 가능한 그룹
{_GROUP_LINE}

## 공통 원칙
- 항상 한국어로, 정중하고 명료한 사내 응대 톤(~합니다 체)으로 답변
- 모든 원문 인용은 마크다운 링크 형태로 URL 포함: `[제목 요약](url)`
- 도구 응답의 raw JSON을 그대로 노출하지 말고 자연어로 풀어 설명
- 데이터가 없으면 솔직히 "찾을 수 없음"이라고 답변
- "최신 개선분"과 우선 근거 사례를 식별할 때는 도구 결과의 `rank_score`, `resolution_status`, `resolution_label`, `latest_reply_summary`, `past_response_pattern`, `resolution_basis`, `can_use_as_answer_basis`, `last_activity_at`, `recency_rank`, `children_count`, `matched_keywords`를 근거로 사용
- 참고 근거, 출처, 분포를 설명할 때는 아지트 숫자 ID보다 `group_title`, `group_titles`, `per_group_counts`의 사람이 읽는 그룹명을 우선 사용
- 도구 결과에 `per_group_counts`가 있으면 답변에 작은 메타 줄로 노출: "📊 그룹별 분포: 전자결재시스템 개선/버그 요청 (N건), 카카오그룹 전자결재시스템 개선 및 문의 (M건)"
- `per_group_counts`가 없고 `per_id_counts`만 있을 때만 숫자 ID를 보조 정보로 사용하되, 사용자에게는 가능한 한 그룹명 중심으로 설명
- 어떤 조회 결과든 도구 결과의 `original_body` 또는 `body`를 근거/사례 설명에 함께 포함
"""

SYSTEM_GUIDE_MODE = """
## [현재 모드: 답변 가이드 — CS 대응 특화]
사용자가 새로 받은 CS 문의에 대한 답변 초안을 만드는 모드야.
이 모드의 핵심은 **'어떤 사례를 근거로 답변하는지'와 '그 사례가 얼마나 최신인지'를 답변자가 한눈에 검증할 수 있게 하는 것**이야.

### 워크플로우 (반드시 이 순서)
1. **find_similar_cases** 호출 → 유사 사례 5~10건 확보
2. `find_similar_cases`가 제공한 `resolution_label`, `latest_reply_at`, `latest_reply_summary`를 우선 확인
3. 최종 응대 본문이 꼭 필요하거나 상충하면 `rank_score` 상위 1~2건에 대해 **fetch_thread_detail** 호출 → 최종 응대(최신 댓글) 보강
4. 아래 포맷으로 답변

### 필수 판단 원칙
- 답변 초안은 반드시 과거 사례의 `past_response_pattern`과 `resolution_basis`를 반영해서 작성
- 과거 사례가 실제 해결/반영/확인 요청으로 끝났는지 `resolution_label`로 먼저 말하고, 해결 근거가 부족하면 "운영팀 확인 필요"로 분리
- `latest_reply_summary`가 비어 있거나 `댓글 있음(본문 없음)`이면 해결됐다고 단정하지 말 것
- `can_use_as_answer_basis=false`인 사례는 참고 사례로만 쓰고 권장 답변의 핵심 근거로 삼지 말 것

### 출력 포맷 (반드시 이 구조로, 모든 섹션 출력)

**🔍 데이터 메타** *(맨 위에 박스 형태로)*
- 검색 대상 그룹: …
- 검색된 유사 사례: **N건** (그 중 댓글로 처리 완료된 건: M건)
- 최신 사례 일자: **YYYY-MM-DD** *(이 답변의 신뢰 기준선)*
- 사용한 키워드: `kw1`, `kw2`, …
- 우선 근거 기준: `rank_score` 상위 사례 + `resolution_label` + 최신 댓글 메타데이터
- 데이터 조회 시각: (오늘 시각 — 한국 시각)

**① 한 줄 요약**
> 이 문의는 어떤 유형이고, 과거 N건의 유사 사례가 있습니다. 가장 최근 처리 사례는 YYYY-MM-DD입니다.

**② 권장 답변 초안** *(그대로 복붙 가능한 응대문)*
```
(여기에 정중한 사내 응대문 — ~합니다 체)
```

**③ 과거 처리 패턴 요약** *(답변 초안의 근거. 반드시 출력)*
- 과거에는 어떤 식으로 답변했는가: (`past_response_pattern` 기반)
- 어떤 문제/원인으로 보았는가: (`original_body`, `matched_keywords`, 이미지 분석 기반)
- 해결/처리 가능 여부: (`resolution_label`, `resolution_basis` 기반)
- 이번 문의에 그대로 적용 가능한 부분 / 확인이 필요한 부분:

**④ 근거 사례** *(rank_score와 최신성 기준, 최신 → 과거)*
| # | recency | 작성일 | 출처 그룹 | 댓글수 | 처리 상태 | 과거 답변/해결 근거 | 핵심 원문 |
|---|---------|--------|-----------|--------|-----------|--------------------|-----------|
| 1 | ⭐ 최신 | YYYY-MM-DD | … | N | 처리 완료/확인 필요/미응답 | latest_reply_summary + resolution_basis | [original_body 요약](url) |
| 2 | … | … | … | … | … | … | [original_body 요약](url) |

- recency 컬럼 표기: `recency_rank`가 1이면 `⭐ 최신`, 2~3이면 `🟢 최근`, 그 이하는 `🟡 과거`
- 사례의 출처 그룹은 숫자 ID가 아니라 `group_title`로 설명
- 표에는 반드시 `original_body` 요약과 `latest_reply_summary`/`past_response_pattern`/`resolution_basis`를 반영
- 근거 사례 표의 `핵심 원문`은 반드시 `[원문 요약](url)` 형태의 마크다운 링크로 출력하고, 별도 원문/출처 링크 컬럼은 만들지 말 것
- 마지막 활동(last_activity_at)이 1년 이상 지났으면 작성일 옆에 ⚠️ 표시

**⑤ 과거 → 현재 변화** *(해당될 때만 출력, 없으면 "확인된 정책 변화 없음"이라고 명시)*
- 과거(YYYY-MM)에는 X 방식으로 처리했으나, 최근(YYYY-MM)에는 Y로 개선되었습니다. 근거: [#message_id](url) → [#message_id](url)

**⑥ ⚠️ 정확도 점검**
- 사례 간 일치 여부: (모두 일관 / 일부 상충 — 상충 항목 명시)
- 최신성 신뢰도: (최근 N개월 내 사례 기반 / 1년 이상 지난 사례 위주 — 운영팀 재확인 권장)
- 답변 초안 사용 전 확인 권장 사항: (있으면 1~2줄)

### 톤 가이드 (답변 초안 작성 시)
- 정중하고 명료한 사내 응대 톤(~합니다 체)
- 모호한 표현 지양("아마도", "추정" 등은 ⑥ 점검 항목으로 분리)
- 사례에서 확인되지 않은 추가 정보는 추측하지 말고 "운영팀 확인 후 안내드리겠습니다"로 처리
"""

SYSTEM_REPORT_MODE = """
## [현재 모드: VOC 리포트]
지정된 그룹/주제의 VOC 데이터를 통계·요약 리포트로 만드는 모드야.
VOC 리포트는 반드시 각 모듈의 `카카오그룹 ...시스템 개선 및 문의` 아지트만 분석 대상으로 사용한다.
예: 전자결재는 `카카오그룹 전자결재시스템 개선 및 문의`, 인사 시스템은 `카카오그룹 인사시스템 개선 및 문의`.

### 워크플로우
1. **내용 분류 중심 VOC 리포트(기본)**: **collect_voc_cases** 를 호출해 기간 내 글을 본문·댓글 기준으로 수집·분류한다.
   - 한 모듈에는 `기능 개선 요청`, `신규 양식 추가`, `버그·에러`, `사용 문의` 등 성격이 다른 사례가 섞여 있다. 반드시 `voc_category`별로 묶어서 정리한다.
   - 각 사례는 `original_body`(본문)와 `latest_reply_summary`·`resolution_label`(댓글 처리 결말)을 **함께 읽고** 요약한다. 본문만 보고 처리 상태를 단정하지 말 것.
   - `voc_category`는 1차 규칙 분류이므로, `category_signals`와 본문·댓글을 근거로 오분류를 보정한다(예: "오류 때문에 개선 요청"은 맥락상 버그·에러로 재분류 가능).
2. **기간·요청 상태 건수 집계**가 핵심인 경우(요청/진행/완료 건수)는 **get_group_task_stats** 를 우선 호출해 페이지 순회 기반 정확 집계를 확보한다. 내용 분류 리포트와 함께 쓰면 "건수(상태) + 성격(카테고리)" 두 축을 모두 보여줄 수 있다.
3. 보조적으로 특정 키워드·사례를 더 찾을 때만 **search_posts** / **get_group_stats** 사용.
4. 아래 포맷으로 출력.

### VOC 내용 분류 원칙 (collect_voc_cases 기반)
- 카테고리 택소노미: `기능 개선 요청` · `신규 양식 추가` · `버그·에러` · `사용 문의` · `기타`
- `category_distribution`로 카테고리별 건수 분포를 ②/③에 반드시 노출
- 각 카테고리의 대표 사례는 `inspect_comments`로 댓글을 조회한 건(=`resolution_label`이 채워진 건)을 우선 인용 — 본문 + 최종 처리 결말을 한 줄씩 요약
- 댓글 미조회 건은 검색 메타 기반 추정 상태이므로 "처리 결말 미확인"으로 구분 표기
- `truncated=true`면 모수가 max_cases에서 잘렸음을 리포트에 명시

### 기간별 요청 통계 원칙 (get_group_task_stats 사용 시)
- 사용자가 "YYYY-MM-DD부터 YYYY-MM-DD까지", "4월 13일부터 5월 21일까지"처럼 기간과 그룹을 지정하면 `get_group_task_stats`를 사용
- `요청`, `진행`, `완료`는 Agit `is_task=true` + `task_status` 기준임을 리포트에 명시
- 전체 작성 글 수 대비 `요청/진행/완료/승인/요청 아님·기타`가 서로 어떻게 구성되는지 표로 출력
- 도구 결과의 `per_group_counts`가 있으면 하위 아지트별 분포를 함께 표시

### 출력 포맷

**① 리포트 개요**
- 그룹: …
- 기간/모수: 최근 N건 (기간: 자동 또는 사용자 지정)
- 생성 시각: (오늘 날짜)

**② 핵심 지표**
| 항목 | 값 |
|------|-----|
| 총 건수 | … |
| 미응답(댓글 0건) | … |
| 가장 활발한 작성자 TOP3 | … |

**②-1 카테고리 분포** *(collect_voc_cases의 category_distribution 기반, 반드시 출력)*
| 카테고리 | 건수 | 비중 | 대표 처리 상태 |
|----------|------|------|----------------|
| 기능 개선 요청 | … | …% | … |
| 신규 양식 추가 | … | …% | … |
| 버그·에러 | … | …% | … |
| 사용 문의 | … | …% | … |
| 기타 | … | …% | … |

**③ 주요 이슈 클러스터 (카테고리별로 3~5개)**
각 클러스터는 반드시 본문(`original_body`)과 댓글 처리 결말(`latest_reply_summary`/`resolution_label`)을 함께 요약한다.
1. **[카테고리] 이슈명** — 건수, 대표 사례 [본문 요약](url)
   - 주요 내용/증상 요약: (`original_body` 기반)
   - 처리 결말: (`resolution_label` + `latest_reply_summary` 기반, 미조회 건은 "처리 결말 미확인")

**④ 시계열 동향** (가능하면)
- 기간 내 어느 카테고리가 증가/감소 추세인지 (collect_voc_cases를 기간을 나눠 2회 호출하거나 created_at 분포로 판단)

**⑤ 권고 액션**
- 운영팀에게: …
- 제품팀에게: …
"""

SYSTEM_STATS_MODE = """
## [현재 모드: 통계 리포트]
선택한 그룹과 기간의 아지트 글 수, 요청 상태별 건수를 정확히 집계해 요약 보고서를 만드는 모드야.

### 워크플로우
1. 사용자의 메시지에서 `조회 그룹`, `조회 대상 그룹 ID`, `조회 기간`, `봇 작성 글 제외` 값을 확인
2. `조회 대상 그룹 ID`가 있으면 **get_group_task_stats** 호출 시 `target_group_id`에 그대로 전달. 없거나 "모듈 전체"이면 비워둠
3. 반드시 **get_group_task_stats** 를 호출해 `page=1`부터 `has_more=false`까지 순회한 실제 글 목록 기준 집계 확보
4. 도구 결과의 숫자를 임의로 보정하거나 추정하지 말고 그대로 사용
5. 아래 포맷으로 출력

### 집계 기준
- `전체 작성 글 수`: 지정 기간·그룹의 원글(parent) 기준 wall 글 수
- `요청`, `진행`, `완료`, `승인`: Agit `is_task=true` + `task_status` 기준
- `요청 아님/기타`: 전체 작성 글 수 - 전체 task_status 합계
- `per_group_counts`가 있으면 하위 아지트별 분포를 함께 표시

### 출력 포맷

**① 리포트 개요**
- 조회 그룹: …
- 조회 대상: … (`target_group_title` 또는 모듈 전체)
- 조회 기간: YYYY-MM-DD ~ YYYY-MM-DD
- 집계 기준: Agit `search.total_search` 페이지 순회(`has_more=false`까지), 원글 기준

**② 핵심 지표**
| 항목 | 건수 |
|------|------|
| 전체 작성 글 | … |
| 요청 | … |
| 진행 | … |
| 완료 | … |
| 승인 | … |
| 요청 아님/기타 | … |

**③ 상태별 해석**
- 전체 대비 완료 비중, 진행 중 건수, 요청 잔여 건수를 짧게 설명
- 숫자가 0인 항목도 누락하지 말고 표시

**④ 하위 아지트별 분포**
- 도구 결과의 `per_group_counts` 기준으로 표시. 없으면 "확인된 하위 분포 없음"이라고 작성

**⑤ 운영 참고 사항**
- 데이터 기준의 한계: 요청/진행/완료는 task로 등록된 글 기준임을 명시
- `count_mismatch_note`가 있으면 API `total_count`와 페이지 순회 결과가 다름을 함께 표시
- 추가 확인이 필요한 경우 짧게 제안
"""

def _build_system(mode: Literal["guide", "report", "stats"], pinned_group: str | None) -> str:
    if mode == "guide":
        mode_text = SYSTEM_GUIDE_MODE
    elif mode == "stats":
        mode_text = SYSTEM_STATS_MODE
    else:
        mode_text = SYSTEM_REPORT_MODE
    full = SYSTEM_BASE + mode_text
    if pinned_group:
        full += (
            f"\n\n## [⚠️ 현재 세션의 고정 그룹 — 강제 적용]\n"
            f"- 사용자가 사이드바에서 '{pinned_group}' 그룹을 선택했습니다.\n"
            f"- 모든 도구 호출의 group_name은 자동으로 '{pinned_group}'으로 강제됩니다 "
            f"(LLM이 다른 그룹 이름을 넣어도 서버에서 override).\n"
            f"- 사용자 메시지에 다른 그룹 단어가 나와도, 검색 결과는 항상 '{pinned_group}'에서만 나옵니다.\n"
            f"- 사용자가 '다른 그룹을 조회해줘'라고 요청하면 "
            f"\"좌측 드롭다운에서 그룹을 변경해주세요\"라고 안내하세요.\n"
            f"- 답변에서 다른 그룹의 결과를 인용하지 마세요."
        )
    return full


# ───────────────────────────────────────────────────────────
# Gemini 클라이언트 + 세션 보관
# ───────────────────────────────────────────────────────────
# 회사 GCP에서 발급된 API 키를 GEMINI_API_KEY로 사용.
# (Vertex AI를 쓰는 환경이면 GOOGLE_APPLICATION_CREDENTIALS + vertexai=True 옵션으로 전환 필요)
_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# 세션 ID → {"history": List[types.Content]}
_sessions: dict[str, dict] = {}
_session_meta: dict[str, dict] = {}


MAX_CHAT_IMAGES = 3
MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024


class ChatImage(BaseModel):
    name: str | None = None
    mime_type: str = Field(default="image/png")
    data: str
    size: int | None = None


def _decode_chat_image(image: ChatImage) -> tuple[str, bytes]:
    mime_type = (image.mime_type or "image/png").strip().lower()
    if not mime_type.startswith("image/"):
        raise ValueError("이미지 파일만 첨부할 수 있습니다")
    try:
        raw = base64.b64decode(image.data, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"이미지 base64 디코딩 실패: {e}") from e
    if len(raw) > MAX_CHAT_IMAGE_BYTES:
        raise ValueError("이미지는 5MB 이하만 첨부할 수 있습니다")
    return mime_type, raw


def analyze_images_for_search(images: list[ChatImage], model: str | None = None) -> str:
    if not images:
        return ""
    if len(images) > MAX_CHAT_IMAGES:
        raise ValueError(f"이미지는 최대 {MAX_CHAT_IMAGES}개까지 첨부할 수 있습니다")

    parts = [
        types.Part.from_text(text=(
            "다음 이미지는 사내 CS 문의와 함께 첨부된 화면 캡처입니다. "
            "검색 정확도를 높이기 위해 한국어로 간결하게 구조화해 주세요.\n\n"
            "출력 형식:\n"
            "- 감지된 화면/업무:\n"
            "- 이미지에 보이는 주요 문구/OCR:\n"
            "- 오류/증상:\n"
            "- 검색 키워드: `키워드1`, `키워드2`, ...\n"
            "- 확인 필요 사항:\n\n"
            "이미지에 없는 내용은 추측하지 말고 '확인 불가'라고 쓰세요."
        ))
    ]
    for image in images:
        mime_type, raw = _decode_chat_image(image)
        parts.append(types.Part.from_bytes(data=raw, mime_type=mime_type))

    chosen = model or DEFAULT_MODEL
    response = _client.models.generate_content(
        model=chosen,
        contents=parts,
        config=build_image_config(chosen),
    )
    return (getattr(response, "text", None) or "").strip()


def _compose_user_message(user_message: str, image_context: str) -> str:
    user_message = (user_message or "").strip()
    if not image_context:
        return user_message
    return (
        "[사용자 첨부 이미지 분석]\n"
        f"{image_context}\n\n"
        "[사용자 입력]\n"
        f"{user_message or '(텍스트 입력 없음)'}\n\n"
        "위 이미지 분석과 사용자 입력을 함께 사용해 유사 사례를 검색하고, "
        "답변의 데이터 메타에 이미지 분석 내용을 요약해 주세요."
    )


def run_chat_turn(
    session_id: str,
    user_message: str,
    mode: Literal["guide", "report", "stats"],
    pinned_group: str | None,
    image_context: str = "",
    model: str | None = None,
) -> tuple[str, list[dict]]:
    """user 메시지 1턴 실행. Gemini SDK가 도구 호출을 자동 처리.

    Args:
        model: 호출에 사용할 모델 id (UI에서 선택한 값). None이면 DEFAULT_MODEL.

    Returns:
        (final_text, tool_calls) — tool_calls는 이번 턴에 호출된 도구만
    """
    state = _sessions.setdefault(session_id, {"history": []})
    prev_history = list(state["history"])  # 얕은 복사로 길이 기록
    prev_len = len(prev_history)

    chosen_model = model or DEFAULT_MODEL
    chat = _client.chats.create(
        model=chosen_model,
        config=build_chat_config(chosen_model, mode, _build_system(mode, pinned_group)),
        history=prev_history,
    )

    # pinned_group을 도구 함수가 인자보다 우선 적용하도록 context에 set
    _ctx_token = PINNED_GROUP.set(pinned_group)
    _mode_token = PINNED_MODE.set(mode)
    try:
        response = chat.send_message(_compose_user_message(user_message, image_context))
    finally:
        PINNED_GROUP.reset(_ctx_token)
        PINNED_MODE.reset(_mode_token)

    # 갱신된 전체 history 저장 (다음 턴에서 history 인자로 재사용)
    full_history = chat.get_history()
    state["history"] = full_history

    # 이번 턴에 새로 추가된 부분에서 function_call 추출 → UI 칩 표시용
    tool_calls: list[dict] = []
    for content in full_history[prev_len:]:
        for part in (getattr(content, "parts", None) or []):
            fc = getattr(part, "function_call", None)
            if fc and fc.name:
                tool_calls.append({
                    "name": fc.name,
                    "args": dict(fc.args) if fc.args else {},
                })

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        text = "*(응답이 비어있습니다. 다시 시도해 주세요.)*"
    return text, tool_calls


# ───────────────────────────────────────────────────────────
# FastAPI 앱
# ───────────────────────────────────────────────────────────
app = FastAPI(title="Agit CS·VOC 챗봇", version="0.2.0")

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    mode: Literal["guide", "report", "stats"] = "guide"
    group: str | None = None
    images: list[ChatImage] = Field(default_factory=list)
    model: str | None = None  # UI에서 선택한 Gemini 모델 id (없으면 DEFAULT_MODEL)


class ChatResponse(BaseModel):
    session_id: str
    text: str
    tool_calls: list[dict] = []
    mode: str
    group: str | None = None
    model: str = ""  # 실제 호출에 사용된 모델 id


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user_id = (os.environ.get("AGIT_USER_ID") or "").strip()
    user_name = (os.environ.get("AGIT_USER_NAME") or "").strip()
    if not user_name:
        user_name = f"User #{user_id}" if user_id else "운영자"
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "groups": list(GROUPS.keys()),
            "tools": [t.__name__ for t in TOOLS],
            "model": DEFAULT_MODEL,
            "models": AVAILABLE_MODELS,
            "stats_group_options": {
                name: [
                    {"id": str(gid), "title": (meta.get("id_names") or {}).get(str(gid), str(gid))}
                    for gid in (meta.get("ids") or [])
                ]
                for name, meta in GROUPS.items()
            },
            "user_name": user_name,
            "user_id": user_id,
        },
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.message.strip() and not req.images:
        raise HTTPException(status_code=400, detail="message 또는 image는 비어있을 수 없습니다")

    session_id = req.session_id or str(uuid.uuid4())
    group = req.group if (req.group and req.group not in ("", "전체")) else None
    if group and group not in GROUPS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 그룹: {group}")

    chosen_model = resolve_model(req.model)
    _session_meta[session_id] = {"mode": req.mode, "group": group, "model": chosen_model}

    try:
        image_context = analyze_images_for_search(req.images, model=chosen_model)
        text, tool_calls = run_chat_turn(
            session_id, req.message, req.mode, group, image_context, model=chosen_model
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini 호출 실패: {type(e).__name__}: {e}")

    return ChatResponse(
        session_id=session_id,
        text=text,
        tool_calls=tool_calls,
        mode=req.mode,
        group=group,
        model=chosen_model,
    )


@app.post("/api/session/new")
async def new_session():
    sid = str(uuid.uuid4())
    return {"session_id": sid}


@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
    _sessions.pop(session_id, None)
    _session_meta.pop(session_id, None)
    return {"ok": True}


@app.get("/api/meta")
async def get_meta():
    return {
        "groups": list(GROUPS.keys()),
        "tools": [t.__name__ for t in TOOLS],
        "model": DEFAULT_MODEL,
        "models": AVAILABLE_MODELS,
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "active_sessions": len(_sessions), "model": GEMINI_MODEL}


# ───────────────────────────────────────────────────────────
# 처리현황 대시보드
# ───────────────────────────────────────────────────────────
# 대시보드가 우선 고정하는 기본 하위 아지트: 카카오그룹 전자결재시스템 개선 및 문의
DASHBOARD_DEFAULT_GROUP = "전자결재"
DASHBOARD_DEFAULT_TARGET_ID = "300100971"


def _dashboard_group_options() -> dict:
    """그룹명 → [{id, title}] (모듈별 하위 아지트 목록). 대시보드 셀렉터용."""
    return {
        name: [
            {"id": str(gid), "title": (meta.get("id_names") or {}).get(str(gid), str(gid))}
            for gid in (meta.get("ids") or [])
        ]
        for name, meta in GROUPS.items()
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user_id = (os.environ.get("AGIT_USER_ID") or "").strip()
    user_name = (os.environ.get("AGIT_USER_NAME") or "").strip()
    if not user_name:
        user_name = f"User #{user_id}" if user_id else "운영자"
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "groups": list(GROUPS.keys()),
            "group_options": _dashboard_group_options(),
            "default_group": DASHBOARD_DEFAULT_GROUP,
            "default_target_id": DASHBOARD_DEFAULT_TARGET_ID,
            "model": DEFAULT_MODEL,
            "user_name": user_name,
        },
    )


# 같은 조건 재조회 시 Agit 페이지 전수 순회를 아끼는 TTL 캐시 (LLM 무관, Agit 부하만 절감).
# 여러 탭/사용자가 봐도 TTL 내에서는 1회 집계로 수렴. 키: 호출 인자 전체.
import threading

_STATS_CACHE: dict = {}
_STATS_CACHE_LOCK = threading.Lock()
STATS_CACHE_TTL = 60  # 초


def _cached_group_task_stats(nocache: bool = False, **kwargs) -> dict:
    key = tuple(sorted(kwargs.items()))
    now = time.time()
    if not nocache:
        with _STATS_CACHE_LOCK:
            ent = _STATS_CACHE.get(key)
            if ent and now - ent["ts"] < STATS_CACHE_TTL:
                res = dict(ent["data"])
                res["cached"] = True
                res["generated_at"] = ent["generated_at"]
                res["cache_age_sec"] = round(now - ent["ts"], 1)
                return res
    res = get_group_task_stats(**kwargs)
    # ts/generated_at은 집계 '완료' 시점 기준 — 그래야 cache_age·TTL이 데이터 신선도를 정확히 반영
    stored_ts = time.time()
    generated_at = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    if not res.get("error"):
        with _STATS_CACHE_LOCK:
            _STATS_CACHE[key] = {"ts": stored_ts, "data": res, "generated_at": generated_at}
    res = dict(res)
    res["cached"] = False
    res["generated_at"] = None if res.get("error") else generated_at
    res["cache_age_sec"] = 0
    return res


def _prev_period(date_start: str, date_end: str):
    """직전 동일 길이 기간 (전 기간 대비용). 실패 시 (None, None)."""
    try:
        s = datetime.strptime(date_start, "%Y-%m-%d").date()
        e = datetime.strptime(date_end, "%Y-%m-%d").date()
    except ValueError:
        return None, None
    length = (e - s).days + 1
    prev_end = s - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start.isoformat(), prev_end.isoformat()


def _build_dashboard_payload(group, date_start, date_end, target_group_id, exclude_bot, compare, nocache=False):
    result = _cached_group_task_stats(
        nocache=nocache,
        group_name=group, date_start=date_start, date_end=date_end,
        exclude_bot=exclude_bot, target_group_id=target_group_id, include_rows=True,
    )
    if compare and not result.get("error"):
        ps, pe = _prev_period(result["date_start"], result["date_end"])
        if ps:
            prev = _cached_group_task_stats(
                nocache=nocache,
                group_name=group, date_start=ps, date_end=pe,
                exclude_bot=exclude_bot, target_group_id=target_group_id, include_rows=False,
            )
            if not prev.get("error"):
                cur_s = {k: v["count"] for k, v in result["task_status_counts"].items()}
                prev_s = {k: v["count"] for k, v in prev["task_status_counts"].items()}
                result["comparison"] = {
                    "prev_start": ps, "prev_end": pe,
                    "prev_total": prev["total_posts"],
                    "total_delta": result["total_posts"] - prev["total_posts"],
                    "status_prev": prev_s,
                    "status_delta": {k: cur_s.get(k, 0) - prev_s.get(k, 0) for k in cur_s},
                }
    return result


# ── AI 요약 (Gemini) ───────────────────────────────────────
# 요약은 글 내용에만 의존 → message_id 단위로 영구(프로세스 수명) 캐시.
# 자동·전체 요약이지만 캐시로 재조회/필터/정렬 시 재호출 0.
_SUMMARY_CACHE: dict = {}
_SUMMARY_LOCK = threading.Lock()


class SummarizeItem(BaseModel):
    message_id: int
    template: str = ""
    text: str = ""


class SummarizeRequest(BaseModel):
    items: list[SummarizeItem] = Field(default_factory=list)


def _summarize_batch(items: list) -> dict:
    """캐시에 없는 항목만 Gemini로 요약. {message_id(str): summary} 반환(요청 전체)."""
    result: dict = {}
    todo = []
    with _SUMMARY_LOCK:
        for it in items:
            cached = _SUMMARY_CACHE.get(it.message_id)
            if cached is not None:
                result[str(it.message_id)] = cached
            else:
                todo.append(it)
    if not todo:
        return result

    payload = [
        {"id": it.message_id, "t": (it.template or "")[:40], "b": (it.text or "")[:160]}
        for it in todo
    ]
    instruction = (
        "사내 전자결재/인사 문의 글 목록이다. 각 글이 '무슨 요청/문의인지' "
        "15자 이내 한국어 명사구로 요약하라. 핵심만, 추측 금지. "
        "입력과 동일 순서로 JSON 배열만 출력: [{\"id\":정수,\"s\":\"요약\"}]"
    )
    try:
        resp = _client.models.generate_content(
            model=DEFAULT_MODEL,
            contents=[types.Part.from_text(text=instruction + "\n\n" + json.dumps(payload, ensure_ascii=False))],
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
        )
        arr = json.loads((getattr(resp, "text", None) or "[]").strip() or "[]")
        got = {int(o["id"]): str(o.get("s") or "").strip() for o in arr if isinstance(o, dict) and "id" in o}
    except Exception as e:
        print(f"⚠️  AI 요약 실패: {type(e).__name__}: {e}", file=sys.stderr)
        got = {}

    with _SUMMARY_LOCK:
        for it in todo:
            s = got.get(it.message_id, "")
            if s:
                _SUMMARY_CACHE[it.message_id] = s
            result[str(it.message_id)] = s
    return result


@app.post("/api/dashboard/summarize")
async def dashboard_summarize(req: SummarizeRequest):
    import anyio
    items = req.items[:50]  # 호출당 상한 (클라이언트는 더 작게 청크)
    summaries = await anyio.to_thread.run_sync(lambda: _summarize_batch(items))
    return {"summaries": summaries}


@app.get("/api/dashboard/stats")
async def dashboard_stats(
    group: str,
    date_start: str,
    date_end: str,
    target_group_id: str = "",
    exclude_bot: bool = False,
    compare: bool = False,
    nocache: bool = False,
):
    # get_group_task_stats가 그룹/날짜/target_group_id 검증을 내부에서 수행하고
    # 잘못된 입력 시 {"error": ...} 반환. 페이지 전수 순회 + 5콜 병렬이라 블로킹 →
    # 스레드풀에서 실행해 이벤트 루프 점유 회피.
    import anyio
    return await anyio.to_thread.run_sync(
        lambda: _build_dashboard_payload(
            group, date_start, date_end, target_group_id, exclude_bot, compare, nocache
        )
    )


# ───────────────────────────────────────────────────────────
# HTML 리포트 다운로드
# ───────────────────────────────────────────────────────────
REPORT_CSS = """
* { box-sizing: border-box; }
body {
  font-family: 'Inter', 'SF Pro Display', system-ui, -apple-system, sans-serif;
  color: #111827;
  background: #F9FAFB;
  margin: 0; padding: 0;
  line-height: 1.65;
  font-size: 14px;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
.report-header {
  background: white;
  border-bottom: 1px solid #E5E7EB;
  padding: 28px 48px;
}
.report-header h1 {
  margin: 0;
  font-size: 26px;
  font-weight: 800;
  color: #111827;
  letter-spacing: -0.01em;
}
.report-header .subtitle {
  margin-top: 6px;
  font-size: 13px;
  color: #4B5563;
}
.report-meta {
  margin-top: 12px;
  display: inline-flex;
  gap: 16px;
  font-size: 12px;
  color: #6B7280;
}
.report-meta span::before {
  content: '●';
  color: #6366F1;
  margin-right: 6px;
  font-size: 8px;
  vertical-align: middle;
}
.report-body {
  max-width: 920px;
  margin: 32px auto;
  background: white;
  border: 1px solid #E5E7EB;
  border-radius: 12px;
  padding: 48px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
.report-footer {
  text-align: center;
  font-size: 11px;
  color: #9CA3AF;
  padding: 20px 0 40px;
}
.report-body h1, .report-body h2, .report-body h3 {
  color: #111827;
  margin-top: 1.6em;
  margin-bottom: 0.6em;
  font-weight: 700;
}
.report-body h1 { font-size: 22px; }
.report-body h2 {
  font-size: 18px;
  border-bottom: 2px solid #EEF2FF;
  padding-bottom: 6px;
}
.report-body h3 { font-size: 15px; color: #4B5563; }
.report-body strong { color: #111827; font-weight: 700; }
.report-body a {
  color: #6366F1;
  text-decoration: none;
  border-bottom: 1px solid #C7D2FE;
}
.report-body a:hover { color: #4F46E5; }
.report-body table {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
  font-size: 13px;
}
.report-body th {
  background: #F3F4F6;
  color: #374151;
  font-weight: 600;
  padding: 10px 12px;
  text-align: left;
  border-bottom: 2px solid #E5E7EB;
}
.report-body td {
  padding: 10px 12px;
  border-bottom: 1px solid #F3F4F6;
  vertical-align: top;
}
.report-body tr:last-child td { border-bottom: none; }
.report-body code {
  background: #F3F4F6;
  color: #4338CA;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.report-body pre {
  background: #1F2937;
  color: #F9FAFB;
  padding: 16px;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.55;
}
.report-body pre code {
  background: transparent;
  color: inherit;
  padding: 0;
}
.report-body blockquote {
  border-left: 4px solid #6366F1;
  margin: 16px 0;
  padding: 10px 18px;
  background: #EEF2FF;
  color: #4B5563;
  border-radius: 0 8px 8px 0;
}
.report-body ul, .report-body ol { padding-left: 24px; }
.report-body li { margin: 4px 0; }
.report-body hr {
  border: none;
  border-top: 1px solid #E5E7EB;
  margin: 28px 0;
}
@media print {
  body { background: white; }
  .report-body { box-shadow: none; border: none; padding: 24px 0; margin: 0; max-width: 100%; }
  .report-footer { display: none; }
  .report-header { padding: 16px 0; border-bottom: 2px solid #111827; }
  a { color: #111827 !important; border-bottom: 1px dotted #6B7280 !important; }
}
"""


class ReportRequest(BaseModel):
    markdown: str
    title: str | None = None
    subtitle: str | None = None


def _safe_filename(s: str) -> str:
    s = re.sub(r"[^\w가-힣\-\.]+", "_", s.strip())
    return s[:80] or "voc-report"


@app.post("/api/report/html")
async def report_html(req: ReportRequest):
    if not (req.markdown or "").strip():
        raise HTTPException(status_code=400, detail="markdown이 비어있습니다")

    body_html = md_mod.markdown(
        req.markdown,
        extensions=["extra", "tables", "fenced_code", "nl2br", "sane_lists"],
    )

    title = (req.title or "Agit VOC 리포트").strip()
    subtitle = (req.subtitle or "").strip()
    now_kst = datetime.now(timezone(timedelta(hours=9)))
    now_str = now_kst.strftime("%Y-%m-%d %H:%M KST")

    subtitle_html = f'<div class="subtitle">{subtitle}</div>' if subtitle else ""

    html_doc = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>{REPORT_CSS}</style>
</head>
<body>
<header class="report-header">
  <h1>{title}</h1>
  {subtitle_html}
  <div class="report-meta">
    <span>생성 시각: {now_str}</span>
    <span>출처: Agit CS·VOC 챗봇</span>
    <span>모델: {GEMINI_MODEL}</span>
  </div>
</header>
<main class="report-body">
{body_html}
</main>
<footer class="report-footer">© Agit CS·VOC Bot · 본 문서는 자동 생성된 분석 리포트입니다.</footer>
</body>
</html>
"""

    filename = f"{_safe_filename(title)}-{now_kst.strftime('%Y%m%d-%H%M%S')}.html"
    return Response(
        content=html_doc,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
