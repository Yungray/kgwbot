"""Agit API 클라이언트 + Gemini가 호출할 도구 함수들."""
import os
import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Optional


# ───────────────────────────────────────────────────────────
# 세션별 pinned group — 서버가 chat 호출 직전에 set, 도구 함수가 인자보다 우선 적용.
# LLM이 다른 그룹으로 호출해도 강제 override되어 결과가 새지 않음.
# ───────────────────────────────────────────────────────────
PINNED_GROUP: ContextVar[Optional[str]] = ContextVar("PINNED_GROUP", default=None)
PINNED_MODE: ContextVar[Optional[str]] = ContextVar("PINNED_MODE", default=None)


def _effective_group(group_name: Optional[str]) -> Optional[str]:
    """pinned_group이 세션에 설정되어 있으면 그것을 반환, 아니면 인자 값 반환."""
    pinned = PINNED_GROUP.get()
    return pinned if pinned else group_name

BASE_URL = "https://api.agit.in/v2/search/total"
WALL_MESSAGE_URL = "https://api.agit.in/v2/wall_messages"

# ───────────────────────────────────────────────────────────
# 사용 가능한 그룹 화이트리스트 (LLM이 이름으로 참조).
# 각 그룹은 (1) 하나 이상의 Agit group_id 리스트와 (2) 자기 OAuth 토큰(token_env)을 가짐.
# 토큰이 어드민에서 해당 그룹에 scope되어 발급되면 ids는 보조 필터 역할 (이중 보호).
# token_env가 비어있을 때는 AGIT_TOKEN(전역)으로 fallback되므로 ids가 정확한 그룹 결과를 보장.
# 여러 group_id가 있을 경우 각 id로 별도 API 호출 후 message_id 기준 dedupe + merge.
# ───────────────────────────────────────────────────────────
GROUPS: dict[str, dict] = {
    "전자결재": {
        "ids": ["300019184", "300100971"],
        "voc_report_ids": ["300100971"],
        "id_names": {
            "300019184": "전자결재시스템 개선/버그 요청",
            "300100971": "카카오그룹 전자결재시스템 개선 및 문의",
        },
        "token_env": "AGIT_TOKEN_ELEC",
    },
    "인사 시스템": {
        "ids": ["300045170", "300102359"],
        "voc_report_ids": ["300102359"],
        "id_names": {
            "300045170": "인사시스템 개선 및 문의",
            "300102359": "카카오그룹 인사시스템 개선 및 문의",
        },
        "token_env": "AGIT_TOKEN_HR",
    },
}


def get_group_ids(name: str) -> list[str]:
    """그룹명 → group_id 리스트. 없거나 미설정이면 빈 리스트."""
    meta = GROUPS.get(name) or {}
    return list(meta.get("ids") or [])


def get_effective_group_ids(name: str) -> list[str]:
    """현재 모드에 맞는 group_id 리스트.

    VOC 리포트 모드에서는 카카오그룹 개선/문의 아지트만 분석 대상으로 제한한다.
    """
    meta = GROUPS.get(name) or {}
    if PINNED_MODE.get() == "report":
        return list(meta.get("voc_report_ids") or meta.get("ids") or [])
    return list(meta.get("ids") or [])


def get_group_id_names(name: str) -> dict[str, str]:
    """그룹명 → group_id별 표시명. 없으면 빈 dict."""
    meta = GROUPS.get(name) or {}
    return dict(meta.get("id_names") or {})


def _resolve_token(group_name: Optional[str]) -> Optional[str]:
    """그룹별 토큰 해석.

    - group_name 없음(전역 검색) → AGIT_TOKEN
    - group_name 있음 → 해당 그룹의 token_env 환경변수 → 비어있으면 AGIT_TOKEN으로 fallback
    """
    if group_name:
        meta = GROUPS.get(group_name) or {}
        env_name = meta.get("token_env")
        if env_name:
            token = (os.environ.get(env_name) or "").strip()
            if token:
                return token
    return (os.environ.get("AGIT_TOKEN") or "").strip() or None


def available_token_envs() -> list[str]:
    """GROUPS에 선언된 token_env 이름 목록."""
    return [m["token_env"] for m in GROUPS.values() if m.get("token_env")]


# ───────────────────────────────────────────────────────────
# API 클라이언트
# ───────────────────────────────────────────────────────────
class AgitClient:
    """Agit Search API 래퍼. Rate limit 자동 백오프 포함."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("AGIT_TOKEN")
        if not self.token:
            raise RuntimeError("AGIT_TOKEN 환경변수가 비어 있습니다")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def search(
        self,
        query: str = "",
        group_id: Optional[str] = None,
        target_id: Optional[int] = None,
        sort: str = "recent",
        scope: str = "all",
        parent: str = "all",
        page: int = 1,
        date_range: str = "any_time",
        date_start: Optional[str] = None,
        date_end: Optional[str] = None,
        exclude_bot: bool = False,
        exclude_private_group: bool = False,
        is_task: bool = False,
        task_assignee: Optional[str] = None,
        task_status: Optional[int] = None,
        template_id: Optional[int] = None,
        retries: int = 3,
    ) -> dict:
        params: dict = {
            "q": query,
            "page": page,
            "sort": sort,
            "scope": scope,
            "parent": parent,
            "sec": group_id if group_id else "global",
            "date_range": date_range,
        }
        if target_id:
            params["target_id"] = target_id
        if group_id:
            params["group_id"] = group_id
        if date_range == "specific":
            if date_start:
                params["date_start"] = date_start
            if date_end:
                params["date_end"] = date_end
        if exclude_bot:
            params["exclude_bot"] = "true"
        if exclude_private_group:
            params["exclude_private_group"] = "true"
        if is_task:
            params["is_task"] = "true"
            if task_assignee:
                params["task_assignee"] = task_assignee
            if task_status is not None:
                params["task_status"] = int(task_status)
        if template_id is not None:
            params["template_id"] = int(template_id)

        for attempt in range(retries):
            r = requests.get(BASE_URL, headers=self.headers, params=params, timeout=20)
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        # 429 재시도 소진 → 조용히 {}를 돌려주면 페이지 순회가 '마지막 페이지'로 오인해
        # 집계가 누락된다. 반드시 예외로 올려 호출부가 인지하도록 한다.
        raise RuntimeError(
            f"Agit 429 재시도 소진 (q={params.get('q')!r}, page={params.get('page')}, sec={params.get('sec')})"
        )

    def comment_ids(self, message_id: int, retries: int = 3) -> list[int]:
        """특정 wall message의 댓글 ID 목록을 조회."""
        url = f"{WALL_MESSAGE_URL}/{int(message_id)}/comment_ids"
        for attempt in range(retries):
            r = requests.get(url, headers=self.headers, timeout=20)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            raw_ids = (r.json() or {}).get("comment_ids") or []
            out: list[int] = []
            seen: set[int] = set()
            for cid in raw_ids:
                try:
                    cid_int = int(cid)
                except (TypeError, ValueError):
                    continue
                if cid_int in seen:
                    continue
                seen.add(cid_int)
                out.append(cid_int)
            return out
        return []

    def wall_message_detail(self, message_id: int, retries: int = 3) -> dict:
        """wall message 단건 상세 조회. 원글과 댓글 모두 같은 endpoint를 사용."""
        url = f"{WALL_MESSAGE_URL}/{int(message_id)}"
        for attempt in range(retries):
            r = requests.get(url, headers=self.headers, timeout=20)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return (r.json() or {}).get("wall_message") or {}
        return {}


# Lazy per-group client cache.
# key: group_name (또는 "__global__" — 그룹 없이 호출되는 경우)
_clients: dict[str, AgitClient] = {}


def get_client_for(group_name: Optional[str] = None) -> AgitClient:
    """그룹별 AgitClient 반환 (lazy + cached).

    - group_name 미지정 → AGIT_TOKEN으로 전역 클라이언트
    - group_name 지정 → 해당 그룹 token_env 우선, 비어있으면 AGIT_TOKEN fallback
    - 토큰이 어디서도 못 찾으면 RuntimeError (호출자가 에러 메시지를 사용자에게 노출)
    """
    key = group_name or "__global__"
    if key in _clients:
        return _clients[key]

    token = _resolve_token(group_name)
    if not token:
        if group_name:
            env_name = (GROUPS.get(group_name) or {}).get("token_env") or "AGIT_TOKEN"
            raise RuntimeError(
                f"'{group_name}' 그룹의 토큰이 비어 있습니다. "
                f".env에 {env_name}(우선) 또는 AGIT_TOKEN(fallback)을 설정한 뒤 서버를 재시작해주세요."
            )
        raise RuntimeError("AGIT_TOKEN 환경변수가 비어 있습니다 (전역 검색용)")

    _clients[key] = AgitClient(token=token)
    return _clients[key]


# 하위 호환: 기존 코드가 get_client()를 부를 때를 대비 (전역 클라이언트 반환)
def get_client() -> AgitClient:
    return get_client_for(None)


def _search_multi(
    client: AgitClient,
    query: str,
    group_ids: list[str],
    id_names: Optional[dict[str, str]] = None,
    **kwargs,
) -> dict:
    """여러 group_id에 대해 검색 후 message_id 기준으로 dedupe + merge.

    - group_ids 비어있으면 group_id 없이 1회 호출 (토큰 scope에 의존)
    - 각 id별 호출 결과의 wall_messages를 합치고, total_count는 합산
    - per_id_counts에 각 id별 응답 수를 기록 → 어떤 id가 비었는지 진단 가능
    """
    if not group_ids:
        data = client.search(query, group_id=None, **kwargs)
        return {
            "total_count": int(data.get("total_count") or 0),
            "wall_messages": data.get("wall_messages") or [],
            "per_id_counts": {"__no_id__": int(data.get("total_count") or 0)},
        }

    merged: list[dict] = []
    seen_ids: set = set()
    total = 0
    per_id_counts: dict[str, int] = {}
    per_group_counts: dict[str, int] = {}
    # Agit이 지속 요청 속도를 제한하므로(동시·고빈도 호출 시 429) 그룹별 호출은 순차 + 짧은 sleep.
    for i, gid in enumerate(group_ids):
        data = client.search(query, group_id=gid, **kwargs)
        cnt = int(data.get("total_count") or 0)
        per_id_counts[str(gid)] = cnt
        per_group_counts[(id_names or {}).get(str(gid), str(gid))] = cnt
        total += cnt
        for m in (data.get("wall_messages") or []):
            mid = m.get("message_id")
            if mid is None or mid in seen_ids:
                continue
            seen_ids.add(mid)
            merged.append(m)
        if i < len(group_ids) - 1:
            time.sleep(0.5)

    return {
        "total_count": total,
        "wall_messages": merged,
        "per_id_counts": per_id_counts,
        "per_group_counts": per_group_counts,
    }


# ───────────────────────────────────────────────────────────
# 유틸: 키워드 추출 (조사·종결어미 단순 제거)
# ───────────────────────────────────────────────────────────
_STOPWORDS = {
    "안녕", "하세요", "감사", "합니다", "있습니다", "있는", "해주세요",
    "부탁", "드립니다", "내용", "관련", "문의", "확인", "처리", "이번",
    "혹시", "정도", "이런", "저런", "그런", "위해", "통해", "대해",
    "되는", "되어", "하여", "하고", "하는", "하지", "이고", "이며",
    "또는", "또한", "그리고", "그러나", "다음", "이전", "이후", "현재",
    "당사", "저희", "우리", "여러", "각각",
}

_LOW_VALUE_WORDS = {
    "문의", "요청", "확인", "처리", "내용", "관련", "가능", "불가", "발생",
    "상세", "공유", "첨부", "화면", "오류", "에러", "문제", "수정", "개선",
}

_HIGH_SIGNAL_PATTERNS = [
    r"[A-Za-z0-9_.+-]+@[A-Za-z0-9_.-]+",
    r"\b[A-Z]{2,}[-_][A-Z0-9_-]+\b",
    r"\b\d{4,}[-_][A-Za-z0-9_-]+\b",
    r"\b[A-Za-z0-9가-힣]+(?:신청서|정산서|품의서|보고서|계약서|증명서|양식|메뉴|버튼|계정과목|결재선|전표|문서번호)\b",
    r"[\"'“”‘’]([^\"'“”‘’]{2,40})[\"'“”‘’]",
]

_DOMAIN_TERMS = {
    "전자결재", "결재", "결재선", "합의", "승인", "반려", "기안", "문서번호",
    "전표", "계정과목", "법인카드", "정산서", "정산", "출장신청서", "출장 신청서",
    "마이너스", "마이너스 정산", "역정산", "예산", "기안금액", "예산비대상",
    "양식", "양식지", "권한", "메뉴", "배치", "알림", "오류", "에러",
    "인사", "휴가", "근태", "발령", "조직도", "사번",
}

_STATUS_TERMS = {
    "안됩니다", "안됨", "실패", "누락", "미표시", "표시", "깨짐", "오표기",
    "일본어", "한국어", "권한없음", "권한이 없습니다", "접근 불가", "생성 안됨",
}


# 다글자 어미·조사 (긴 것부터 매칭해 잔여 음절이 남지 않도록 정렬).
_MULTI_SUFFIXES = [
    "에서는", "이라고", "입니다", "습니다", "합니다", "됩니다",
    "주세요", "되는", "하는", "에서", "으로", "에는", "에도",
    "이나", "거나", "에게", "부터", "까지", "처럼", "라고",
    "관련", "보다", "세요",
]
# 어간이 2자 이상 남을 때만 떼는 1글자 조사 (명사 말음으로 흔치 않은 것만 — '로/서/고'는 오제거 위험으로 제외).
_SAFE_JOSA = ("을", "를", "은", "는", "이", "가", "의", "에", "도", "만", "와", "과", "께", "로")
# 술어(동사·형용사·종결어미)로 보이는 토큰 말음 — 검색 쿼리로 쓰면 recall만 떨어지므로 키워드/구에서 배제.
_PREDICATE_TAIL = ("다", "요", "까", "죠", "네", "군", "냐", "니")


def _normalize_token(token: str) -> str:
    token = re.sub(r"^[\s\-_.,:;!?()[\]{}<>]+|[\s\-_.,:;!?()[\]{}<>]+$", "", token or "")
    for suffix in _MULTI_SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix) + 1:
            token = token[: -len(suffix)]
            break
    # 단일 조사 제거: '알림이' → '알림', '정산서를' → '정산서' (어간 2자 이상 보존).
    # 단어 단위에만 적용 — 구('기안금액 초과')는 마지막 단어 음절('과')이 잘리지 않도록 제외.
    if " " not in token and len(token) >= 3 and token[-1] in _SAFE_JOSA:
        token = token[:-1]
    return token.strip()


def _looks_predicate(token: str) -> bool:
    """동사·형용사·종결형으로 보이면 True (검색 키워드 후보에서 제외)."""
    return len(token) >= 2 and token[-1] in _PREDICATE_TAIL


def _keyword_weight(keyword: str) -> float:
    kw = keyword.strip()
    if not kw:
        return 0
    if kw in _LOW_VALUE_WORDS:
        return 0.5
    if kw in _DOMAIN_TERMS or kw in _STATUS_TERMS:
        return 3.0
    if re.search(r"\d", kw) and re.search(r"[A-Za-z가-힣]", kw):
        return 3.0
    if len(kw) >= 8:
        return 2.2
    if len(kw) >= 4:
        return 1.5
    return 1.0


def _extract_keywords(text: str, max_keywords: int = 8) -> list[str]:
    """CS 검색용 핵심 키워드 추출.

    화면명, 양식명, 오류 문구, 문서번호처럼 재검색 가치가 높은 단서를 먼저 고른다.
    """
    original = text or ""
    candidates: list[tuple[str, float]] = []

    for pattern in _HIGH_SIGNAL_PATTERNS:
        for match in re.finditer(pattern, original):
            value = match.group(1) if match.lastindex else match.group(0)
            value = _normalize_token(value)
            if len(value) >= 2:
                candidates.append((value, _keyword_weight(value) + 2.0))

    for phrase in sorted(_DOMAIN_TERMS | _STATUS_TERMS, key=len, reverse=True):
        if phrase in original:
            candidates.append((phrase, _keyword_weight(phrase) + 1.5))

    cleaned = re.sub(r"[^\w가-힣\s./_-]", " ", original)
    words = [_normalize_token(w) for w in cleaned.split()]
    for i, word in enumerate(words):
        if len(word) < 2 or word in _STOPWORDS or _looks_predicate(word):
            continue
        base_weight = _keyword_weight(word)
        if base_weight <= 0:
            continue
        candidates.append((word, base_weight))
        # bigram은 두 토큰 모두 명사형일 때만 — 술어가 붙은 잡음 구('권한 문제일까요')를 막는다.
        if i + 1 < len(words):
            nxt = words[i + 1]
            if len(nxt) >= 2 and nxt not in _STOPWORDS and not _looks_predicate(nxt):
                phrase = f"{word} {nxt}"
                if len(phrase) <= 30 and any(term in phrase for term in _DOMAIN_TERMS | _STATUS_TERMS):
                    # 구는 최고 단일 가중치보다 살짝만 높게 — 단일 도메인어와 자연스럽게 섞이도록.
                    candidates.append((phrase, max(base_weight, _keyword_weight(nxt)) + 0.7))

    scored: dict[str, float] = {}
    for keyword, weight in candidates:
        keyword = _normalize_token(keyword)
        if len(keyword) < 2 or keyword in _STOPWORDS:
            continue
        scored[keyword] = max(scored.get(keyword, 0), weight)

    return [
        keyword
        for keyword, _ in sorted(scored.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    ][:max_keywords]


# 도메인 동의어·은어 — 공식 명칭과 실무 표현을 함께 검색해 recall을 높인다.
# Agit 검색이 substring exact 매칭이라, '지출정산서'로 쓴 글과 '마이너스 정산'으로 쓴 글은
# 서로 안 잡히므로 양방향 변형을 쿼리에 추가한다.
_SYNONYMS: dict[str, list[str]] = {
    "정산서": ["지출정산서", "정산"],
    # "역정산"·"승인선"은 실데이터 0건(2026-05-27 전자결재 그룹 검증) → 死동의어로 제거.
    # Agit는 substring exact 매칭이라 실제 본문에 안 쓰는 표현은 task·sleep만 낭비함.
    "마이너스": ["예산 복구", "마이너스 전표"],
    "법인카드": ["법카"],
    "결재선": ["결재 라인"],
    "출장신청서": ["출장비", "출장 신청"],
    "휴가": ["연차", "반차"],
    "발령": ["인사발령"],
    "전표": ["회계전표"],
    "계정과목": ["비용계정"],
}


def _expand_search_queries(queries: list[str], limit: int = 6) -> list[str]:
    """도메인 동의어·은어를 덧붙여 검색 쿼리 집합을 확장한다 (공식 명칭 ↔ 실무 표현).

    원래 쿼리를 앞에 보존하고, 매칭되는 동의어를 뒤에 추가한 뒤 중복 제거·상한 적용.
    """
    expanded: list[str] = []
    seen: set[str] = set()

    def _add(q: str) -> None:
        q = (q or "").strip()
        if q and q not in seen:
            seen.add(q)
            expanded.append(q)

    for q in queries:
        _add(q)
    for q in queries:
        for key, variants in _SYNONYMS.items():
            if key in q or q in key:
                for v in variants:
                    _add(v)
    return expanded[:limit]


def _extract_date_range(text: str) -> Optional[tuple[str, str]]:
    """질문의 시간 의도(연도·상대표현)를 추출해 (date_start, date_end) YYYY-MM-DD로 반환.

    추출 못 하면 None. 명시 연도가 우선이며, 여러 연도면 최소~최대 연을 포괄한다.
    예) "2022년 마이너스 정산" → ('2022-01-01', '2022-12-31')
        "작년 출장비"          → 직전 해 1/1~12/31
    """
    if not text:
        return None
    # 명시 연도(2000~2099). "2022-01-01" 같은 날짜의 연도도 함께 잡힌다.
    # 숫자 경계만 본다 — "2022년"의 "년"(한글=\w)이 \b를 막으므로 \b는 쓰지 않는다.
    years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", text)]
    if not years:
        now_year = datetime.now().year
        if "재작년" in text:
            years = [now_year - 2]
        elif "작년" in text or "지난해" in text or "전년" in text:
            years = [now_year - 1]
        elif "올해" in text or "금년" in text:
            years = [now_year]
    if not years:
        return None
    lo, hi = min(years), max(years)
    return (f"{lo}-01-01", f"{hi}-12-31")


# find_similar_cases 후보 수집 패스 튜닝값 (콜 수 ↔ recall 트레이드오프).
# 늘리면 묻힌 과거 글 recall↑·지연↑. 줄이면 반대.
RELEVANT_PASS_QUERIES = 3   # relevant 정렬을 적용할 상위 쿼리 수
RELEVANT_PASS_PAGES = 2     # relevant 패스에서 파고들 페이지 깊이 (1→N). 실측상 page2면 과거 글 진입 충분
DATE_PASS_PAGES = 2         # 시간 의도 감지 시 기간 한정 relevant 패스 깊이


def _epochish(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _format_agit_time(value) -> str | None:
    epoch = _epochish(value)
    if not epoch:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def _relevance_score(text: str, keyword_weights: dict[str, float]) -> float:
    """후보 본문에 대한 로컬 관련도 점수.

    - 키워드 출현 빈도를 포화 함수로 반영 (1번 등장 vs 여러 번 등장을 구분하되 과대평가는 억제)
    - 등장한 '서로 다른' 키워드 수(coverage)에 보너스 — 한 키워드만 도배된 글보다 여러 단서가 맞는 글을 우대
    - 키워드 중요도(_keyword_weight)로 가중
    """
    if not text or not keyword_weights:
        return 0.0
    total = 0.0
    matched = 0
    for kw, weight in keyword_weights.items():
        if not kw:
            continue
        count = text.count(kw)
        if count > 0:
            matched += 1
            tf = count / (count + 1.5)  # BM25 유사 포화 (0→0, 1→0.4, 다회→1 수렴)
            total += float(weight) * (1.0 + tf)
    if matched == 0:
        return 0.0
    coverage = matched / len(keyword_weights)
    return round(total * (0.6 + 0.4 * coverage), 3)


def _case_quality_score(case: dict) -> float:
    children = int(case.get("children_count") or 0)
    recency_rank = int(case.get("recency_rank") or 9999)
    score = float(case.get("match_score") or case.get("score") or 0)
    score += float(case.get("text_relevance") or 0) * 0.5  # 본문 관련도(빈도·커버리지) 반영
    score += min(children, 5) * 0.7
    if children > 0:
        score += 1.5
    # 최신성은 "과거 유사 사례 탐색"에선 동점 가르기 수준이어야 함(주 신호 아님).
    # 과거 글이 최신 글에 밀려 묻히던 문제 대응으로 보너스를 하향(이전: +2.0/+0.8).
    # 처리상태(resolved +3.0 등)·본문 관련도가 우선되도록 둔다.
    if recency_rank <= 3:
        score += 0.8
    elif recency_rank <= 10:
        score += 0.3
    if case.get("group_title"):
        score += 0.3
    status = case.get("resolution_status")
    if status in {"resolved", "scheduled"}:
        score += 3.0
    elif status == "closed_after_reply":
        score += 2.5
    elif status == "needs_user_action":
        score += 2.0
    elif status == "needs_review":
        score += 1.0
    elif status == "unresolved":
        score -= 1.0
    return round(score, 3)


_RESOLUTION_RULES = [
    ("resolved", ["완료", "처리되었습니다", "반영되었습니다", "정상반영", "확인하였습니다", "수정되었습니다", "해결", "조치 완료", "배포 완료"]),
    ("scheduled", ["반영 예정", "배포 예정", "수정 예정", "개선 예정", "진행 예정", "검토 후 반영"]),
    ("needs_user_action", ["확인 부탁", "재시도", "다시 확인", "권한 확인", "승인 댓글", "정보 공유", "추가 확인"]),
    ("needs_review", ["검토", "확인 중", "담당자 확인", "운영팀 확인", "개발팀 확인", "확인해보겠습니다"]),
    ("unresolved", ["불가", "지원하지", "어렵습니다", "제한", "미지원"]),
]


def _classify_resolution(text: str, children_count: int) -> str:
    if children_count <= 0:
        return "no_reply"
    compact = re.sub(r"\s+", " ", text or "")
    for status, markers in _RESOLUTION_RULES:
        if any(marker in compact for marker in markers):
            return status
    return "replied"


def _clean_comment_text(text: str) -> str:
    text = text or ""
    text = re.sub(r"\{IMAGE:[^}]+\}", "", text)
    text = re.sub(r"\[(@?[^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _resolution_label(status: str) -> str:
    return {
        "resolved": "처리 완료",
        "closed_after_reply": "댓글 종료",
        "scheduled": "반영/처리 예정",
        "needs_user_action": "사용자 확인 필요",
        "needs_review": "운영/개발 확인 중",
        "unresolved": "처리 불가/제한",
        "replied": "댓글 응대 있음",
        "reply_without_text": "댓글 있음(본문 없음)",
        "no_reply": "미응답",
        "detail_unavailable": "상세 확인 실패",
    }.get(status, "상태 미분류")


def _apply_search_resolution_metadata(case: dict) -> None:
    """검색 결과에 포함된 댓글 메타데이터로 처리 맥락을 보강."""
    children = int(case.get("children_count") or 0)
    if children <= 0:
        status = "no_reply"
    elif case.get("is_comments_closed"):
        status = "closed_after_reply"
    else:
        status = "replied"

    latest_at = case.get("recent_commented_at") or case.get("last_activity_at")
    case.update({
        "resolution_status": status,
        "resolution_label": _resolution_label(status),
        "latest_reply_at": latest_at if children > 0 else None,
        "latest_reply_summary": (
            "검색 결과 메타데이터상 댓글 응대가 있습니다. 최종 문구 확인이 필요하면 fetch_thread_detail로 보강하세요."
            if children > 0 else ""
        ),
    })
    case.update(_derive_case_guidance(case))


def _comment_to_public(comment: dict) -> dict:
    body = _clean_comment_text(comment.get("text") or comment.get("message") or "")
    created_time = comment.get("created_time") or comment.get("updated_time")
    return {
        "message_id": comment.get("id") or comment.get("message_id"),
        "created_at": _format_agit_time(created_time),
        "created_time": created_time,
        "author": f"actor:{comment.get('actor_id')}" if comment.get("actor_id") else None,
        "body": _short(body, 1200),
        "is_empty": not bool(body),
    }


def _derive_case_guidance(context: dict) -> dict:
    latest = context.get("latest_reply_summary") or ""
    status = context.get("resolution_status") or "no_reply"
    label = context.get("resolution_label") or _resolution_label(status)
    if latest:
        response_pattern = f"과거 댓글에서는 '{_short(latest, 260)}' 흐름으로 안내했습니다."
        basis = f"{label}: 최신 의미 있는 댓글 기준으로 판단했습니다."
    elif status == "reply_without_text":
        response_pattern = "댓글은 있으나 본문이 비어 있어 과거 응대 문구는 확인되지 않았습니다."
        basis = "댓글 ID는 있으나 의미 있는 댓글 본문이 없습니다."
    elif status == "no_reply":
        response_pattern = "과거 응대 댓글이 없어 답변 패턴은 확인되지 않았습니다."
        basis = "댓글이 없는 사례입니다."
    else:
        response_pattern = "과거 응대 문구는 상세 확인이 필요합니다."
        basis = f"{label}: 검색/댓글 메타데이터 기준입니다."
    return {
        "past_response_pattern": response_pattern,
        "resolution_basis": basis,
        "can_use_as_answer_basis": status in {"resolved", "scheduled", "needs_user_action", "needs_review", "replied", "closed_after_reply"},
    }


def _fetch_comment_context(client: AgitClient, message_id: int, max_comments: int = 20) -> dict:
    comment_ids = client.comment_ids(int(message_id))
    if not comment_ids:
        status = "no_reply"
        result = {
            "comment_ids": [],
            "children_count": 0,
            "children": [],
            "meaningful_children_count": 0,
            "latest_reply_at": None,
            "latest_reply_summary": "",
            "resolution_status": status,
            "resolution_label": _resolution_label(status),
        }
        result.update(_derive_case_guidance(result))
        return result

    comments: list[dict] = []
    for cid in comment_ids[-max_comments:]:
        try:
            detail = client.wall_message_detail(cid)
        except Exception:
            continue
        if detail:
            comments.append(_comment_to_public(detail))
        time.sleep(0.15)

    comments.sort(key=lambda c: int(c.get("created_time") or 0))
    meaningful = [c for c in comments if not c.get("is_empty")]
    latest = meaningful[-1] if meaningful else None
    recent_text = "\n".join(c.get("body", "") for c in meaningful[-3:])
    status = _classify_resolution(recent_text, len(comments)) if meaningful else "reply_without_text"
    result = {
        "comment_ids": comment_ids,
        "children_count": len(comment_ids),
        "children": comments,
        "meaningful_children_count": len(meaningful),
        "latest_reply_at": latest.get("created_at") if latest else None,
        "latest_reply_summary": _short(latest.get("body", ""), 500) if latest else "",
        "resolution_status": status,
        "resolution_label": _resolution_label(status),
    }
    result.update(_derive_case_guidance(result))
    return result


def _fetch_thread_resolution(client: AgitClient, message_id: int) -> dict:
    """상위 후보 재랭킹용으로 최신 댓글 본문과 처리 맥락을 조회."""
    context = _fetch_comment_context(client, int(message_id), max_comments=8)
    return {
        "resolution_status": context["resolution_status"],
        "resolution_label": context["resolution_label"],
        "latest_reply_at": context["latest_reply_at"],
        "latest_reply_summary": context["latest_reply_summary"],
        "inspected_children_count": context["children_count"],
        "meaningful_children_count": context["meaningful_children_count"],
        "past_response_pattern": context["past_response_pattern"],
        "resolution_basis": context["resolution_basis"],
        "can_use_as_answer_basis": context["can_use_as_answer_basis"],
    }


# VOC 분류 택소노미 — 모듈 VOC가 섞여 들어오므로 본문/댓글/템플릿명으로 1차 규칙 분류한다.
# (LLM이 본문·댓글을 읽어 최종 보정; 여기서는 재현 가능한 규칙 라벨 + 신호를 제공)
# 우선순위: 신규 양식 → 버그·에러 → 기능 개선 요청 → 사용 문의 → 기타
_VOC_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("신규 양식 추가", (
        "신규 양식", "양식 추가", "양식추가", "서식 추가", "새 양식", "새로운 양식",
        "결재 양식", "문서 양식", "양식 신설", "템플릿 추가", "양식 등록", "양식 생성",
    )),
    ("버그·에러", (
        "오류", "에러", "버그", "안 됨", "안됨", "안돼", "안 돼", "안되", "되지 않",
        "실패", "먹통", "오작동", "작동 안", "동작 안", "표시 안", "노출 안", "누락",
        "error", "깨짐", "멈춤", "로딩", "튕", "비정상",
    )),
    ("기능 개선 요청", (
        "개선", "추가해", "추가 요청", "추가요청", "기능 요청", "변경 요청", "수정 요청",
        "불편", "했으면", "되었으면", "가능하게", "가능했으면", "노출해", "반영 요청",
        "건의", "제안", "요청드립니다", "필요합니다", "필요해", "보완",
    )),
    ("사용 문의", (
        "문의", "질문", "어떻게", "방법", "가능한가요", "가능 여부", "가능여부",
        "어디서", "어디에", "인가요", "되나요", "할 수 있나요", "확인 부탁", "안내 부탁",
    )),
]


def _classify_voc_category(message: dict) -> dict:
    """VOC 글을 본문·템플릿명 기준으로 1차 분류한다. (category, matched_signals, is_task)."""
    haystack = " ".join(filter(None, [
        _original_body(message, 1500),
        message.get("message") or "",
        message.get("group_message_template_name") or "",
    ])).lower()
    for category, signals in _VOC_CATEGORY_RULES:
        hit = [s for s in signals if s.lower() in haystack]
        if hit:
            return {"voc_category": category, "category_signals": hit[:4]}
    # 신호가 없으면 task 여부로 보정: 요청류 task는 '기능 개선 요청', 그 외 '기타'
    if message.get("is_task"):
        return {"voc_category": "기능 개선 요청", "category_signals": ["is_task"]}
    return {"voc_category": "기타", "category_signals": []}


def _short(text: str, n: int = 300) -> str:
    return (text or "").replace("\n", " ").strip()[:n]


_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")   # [텍스트](url) / ![alt](src) → 텍스트
_MD_TOKENS = re.compile(r"[`*_~#>|]+")               # 강조·헤더·인용·표 기호
_MD_WS = re.compile(r"\s+")


def _plaintext(text: str, n: int = 140) -> str:
    """마크다운 기호를 벗겨 평문 미리보기로 정리한다(목록 가독성용)."""
    t = text or ""
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_TOKENS.sub(" ", t)
    t = t.replace("\\", "")           # 이스케이프 백슬래시 제거 (\[ \* 등)
    t = _MD_WS.sub(" ", t).strip()
    return t[:n]


_CORP_RE = re.compile(r"법인\s*명?\s*[:：]\s*([^\n\r]+)")


def _extract_corp(text: str) -> str:
    """본문에서 '법인명 : XXX' 패턴의 요청 법인을 추출(없으면 빈 문자열)."""
    m = _CORP_RE.search(text or "")
    if not m:
        return ""
    v = _MD_TOKENS.sub("", m.group(1)).replace("\\", "")
    v = _MD_WS.sub(" ", v).strip()
    # 'ex) DKT' 같은 양식 예시 안내 꼬리표 제거
    v = re.split(r"\s*(?:ex\)|예\)|예시)", v, maxsplit=1)[0].strip()
    return v[:24]


def _original_body(message: dict, n: int = 2000) -> str:
    return _short(message.get("original_message") or message.get("message", ""), n)


# ═══════════════════════════════════════════════════════════
#   GEMINI 도구 함수 4개
#   (Gemini가 docstring을 읽어 자동으로 schema를 생성합니다)
# ═══════════════════════════════════════════════════════════


def search_posts(
    query: str,
    group_name: str = "",
    sort: str = "recent",
    limit: int = 10,
) -> dict:
    """아지트에서 글을 검색합니다. 키워드로 글 찾기, 특정 그룹 안에서만 찾기 등에 사용하세요.

    Args:
        query: 검색 키워드. 예: "법무 검토", "지출 정산서 모바일", "알림 누락"
        group_name: 그룹명. 비어있으면 전체 검색. 사용 가능한 값: "법무", "정보보호", "전자결재", "카카오워크2.0"
        sort: 정렬 방식. "recent" (최신순) 또는 "relevant" (관련도순)
        limit: 반환할 최대 글 수. 기본 10, 최대 20

    Returns:
        검색 결과 (total_count, results 배열 — message_id, created_at, author, group_title, body_preview, url 포함)
    """
    group_name = _effective_group(group_name) or ""  # 세션 pin이 있으면 강제 override
    group_ids = get_effective_group_ids(group_name) if group_name else []
    id_names = get_group_id_names(group_name) if group_name else {}
    try:
        client = get_client_for(group_name or None)
    except RuntimeError as e:
        return {"error": str(e)}
    limit = max(1, min(int(limit), 20))

    # 여러 group_id가 있으면 각각 호출 후 message_id로 dedupe + merge
    data = _search_multi(client, query, group_ids, id_names=id_names, sort=sort, parent="parent")
    msgs = data.get("wall_messages") or []

    results = []
    for m in msgs[:limit]:
        user = m.get("user") or {}
        results.append({
            "message_id": m.get("message_id"),
            "created_at": m.get("created_at"),
            "author": user.get("nickname"),
            "group_title": m.get("group_title"),
            "body_preview": _short(m.get("message", ""), 300),
            "original_body": _original_body(m, 2000),
            "url": m.get("url"),
            "children_count": m.get("children_count", 0),
        })

    return {
        "total_count": data.get("total_count", 0),
        "returned": len(results),
        "group_name": group_name or "전체",
        "per_group_counts": data.get("per_group_counts") or {},
        "results": results,
    }


def list_available_groups() -> dict:
    """챗봇이 접근 가능한 아지트 그룹의 이름·ID 목록을 반환합니다.
    사용자가 '어떤 그룹들 검색할 수 있어?' 같은 질문을 할 때 사용하세요.
    """
    return {
        "groups": [
            {
                "name": name,
                "group_ids": list(meta.get("ids") or []),
                "voc_report_ids": list(meta.get("voc_report_ids") or []),
                "group_titles": dict(meta.get("id_names") or {}),
                "token_env": meta.get("token_env", ""),
                "token_configured": bool(_resolve_token(name)),
            }
            for name, meta in GROUPS.items()
        ],
        "note": "다른 그룹을 추가하려면 agit.py의 GROUPS dict에 항목을 추가하고 .env에 토큰을 채우세요.",
    }


# find_similar_cases 결과 캐시. 동일 문의 재질의(챗봇 답변가이드 재시도 등) 시 ~수십초 검색을
# 0초로. 과거 사례 기반이라 신선도 민감도 낮음 → 10분 TTL. 키: (그룹, 모드, 정규화 쿼리, top_k).
_SIMILAR_CACHE: dict = {}
_SIMILAR_CACHE_TTL = 600  # 초 (10분)


def find_similar_cases(
    query_text: str,
    group_name: str,
    top_k: int = 5,
) -> dict:
    """주어진 문의/질문 텍스트와 유사한 과거 사례를 찾습니다.
    답변 초안을 짜기 전에 반드시 이 함수를 먼저 호출해서 과거 답변 패턴을 확인하세요.
    결과는 (1) 키워드 매칭 점수, (2) 최신성, (3) 상위 후보의 댓글/처리 맥락 가중치를 함께 노출합니다.

    Args:
        query_text: 분석할 문의 텍스트 전체. 사용자가 받은 문의 글을 그대로 넣으세요.
        group_name: 검색할 그룹명 (예: "전자결재", "법무"). 화이트리스트에 있어야 합니다.
        top_k: 반환할 유사 사례 수. 기본 5, 최대 10

    Returns:
        matched_keywords, searched_keywords, inspected_threads, total_candidates,
        top_cases (rank_score순) — 각 case에 last_activity_at, children_count, recency_rank,
        resolution_status, latest_reply_summary 포함
    """
    group_name = _effective_group(group_name) or group_name  # 세션 pin이 있으면 강제 override
    if group_name not in GROUPS:
        return {"error": f"알 수 없는 그룹: '{group_name}'. 사용 가능: {list(GROUPS.keys())}"}
    group_ids = get_effective_group_ids(group_name)  # report 모드에서는 VOC 전용 그룹만 사용
    id_names = get_group_id_names(group_name)
    try:
        client = get_client_for(group_name)
    except RuntimeError as e:
        return {"error": str(e)}

    top_k = max(1, min(int(top_k), 10))

    # 캐시 조회 — 동일 (그룹·모드·쿼리·top_k)면 검색 생략. 결과에 from_cache 플래그 부여.
    cache_key = (group_name, PINNED_MODE.get(), (query_text or "").strip(), top_k)
    _cached = _SIMILAR_CACHE.get(cache_key)
    if _cached and (time.time() - _cached[0]) < _SIMILAR_CACHE_TTL:
        return {**_cached[1], "from_cache": True}

    keywords = _extract_keywords(query_text, max_keywords=8)

    if not keywords:
        return {"error": "키워드를 추출하지 못했습니다. query_text를 더 구체적으로 넣어주세요."}

    seen: dict = {}
    keyword_weights = {kw: _keyword_weight(kw) for kw in keywords}
    # 동의어/은어를 더해 공식 명칭 외 실무 표현도 검색한다 (recall↑).
    search_queries = _expand_search_queries(keywords[:4], limit=6)
    # recall 보강: recent 정렬만으로는 최신 글에 밀려 못 잡는 과거 사례를,
    # 상위 쿼리의 relevant 정렬 패스를 page 2~3까지 파고들어 후보 풀에 진입시킨다.
    # (실측: 묻힌 과거 글은 relevant page 1엔 없고 page 2~3에 몰려 있음 — 2026-05-27)
    # 각 task: (keyword, sort, query_index, page, date_window|None)
    date_window = _extract_date_range(query_text)
    search_tasks = [(kw, "recent", i, 1, None) for i, kw in enumerate(search_queries)]
    # 날짜 의도가 있으면 과거 recall은 기간 한정 패스가 담당하므로, 비-날짜 relevant 깊이를
    # 1로 줄여 중복 호출을 피한다(지연↓). 없으면 페이징으로 묻힌 과거 글을 끌어올린다.
    relevant_pages = 1 if date_window else RELEVANT_PASS_PAGES
    for i, kw in enumerate(search_queries[:RELEVANT_PASS_QUERIES]):
        for page in range(1, relevant_pages + 1):
            search_tasks.append((kw, "relevant", i, page, None))
    # 시간 의도가 있으면(예: "2022년") 해당 기간으로 한정한 relevant 패스를 추가 —
    # 키워드만으로는 폐기되던 연도 의도를 date_range=specific로 직접 반영한다.
    if date_window:
        for i, kw in enumerate(search_queries[:RELEVANT_PASS_QUERIES]):
            for page in range(1, DATE_PASS_PAGES + 1):
                search_tasks.append((kw, "relevant", i, page, date_window))

    # 검색 task(recent/relevant/날짜 패스)는 순차 실행 + 짧은 sleep. Agit 지속 요청속도 제한 때문에
    # 병렬화는 429 백오프로 오히려 느려져(실측) 순차가 안전·최적. 제출 순서대로 결정적 병합.
    for kw, sort_mode, query_index, page, dwin in search_tasks:
        search_kwargs = dict(id_names=id_names, parent="all", sort=sort_mode, page=page)
        if dwin:
            search_kwargs.update(date_range="specific", date_start=dwin[0], date_end=dwin[1])
        try:
            data = _search_multi(client, kw, group_ids, **search_kwargs)
        except Exception as e:
            return {"error": f"검색 실패 (keyword={kw!r}, sort={sort_mode}, page={page}): {e}"}

        for m in (data.get("wall_messages") or []):
            mid = m["message_id"]
            kw_weight = keyword_weights.get(kw) or _keyword_weight(kw)
            body = m.get("message", "") or ""
            body_bonus = 1.0 if kw in body else 0.0
            title_bonus = 0.4 if kw in (m.get("group_message_template_name") or "") else 0.0
            relevant_bonus = 0.3 if sort_mode == "relevant" else 0.0
            current_score = kw_weight + body_bonus + title_bonus + relevant_bonus + max(0, 0.6 - query_index * 0.08)
            if mid in seen:
                seen[mid]["score"] += 1
                seen[mid]["match_score"] += current_score
                if kw not in seen[mid]["matched_keywords"]:
                    seen[mid]["matched_keywords"].append(kw)
            else:
                user = m.get("user") or {}
                last_activity = m.get("last_activity_at") or m.get("updated_at") or m.get("created_at")
                group_title = m.get("group_title") or id_names.get(str(m.get("group_id") or ""))
                seen[mid] = {
                    "message_id": mid,
                    "created_at": m.get("created_at"),
                    "last_activity_at": last_activity,
                    "author": user.get("nickname"),
                    "group_title": group_title,
                    "template_name": m.get("group_message_template_name"),
                    "body_preview": _short(m.get("message", ""), 400),
                    "original_body": _original_body(m, 2000),
                    "url": m.get("url"),
                    "children_count": m.get("children_count", 0),
                    "recent_commented_at": m.get("recent_commented_at"),
                    "recent_commented_time": m.get("recent_commented_time"),
                    "is_comments_closed": bool(m.get("is_comments_closed")),
                    "has_recent_reply": bool(m.get("children_count", 0)) and bool(last_activity),
                    "score": 1,
                    "match_score": current_score,
                    "matched_keywords": [kw],
                }
        time.sleep(0.5)  # Agit 레이트리밋 회피 (task 간 간격)

    # 1차 점수 → 최신성 순으로 정렬, recency_rank 부여
    by_recency = sorted(
        seen.values(),
        key=lambda x: (_epochish(x.get("last_activity_at")), str(x.get("last_activity_at") or "")),
        reverse=True,
    )
    recency_index = {c["message_id"]: i + 1 for i, c in enumerate(by_recency)}
    for c in seen.values():
        c["recency_rank"] = recency_index.get(c["message_id"])
        # 검색한 쿼리뿐 아니라 추출된 전체 키워드로 본문 관련도를 재계산.
        haystack = f"{c.get('original_body') or ''} {c.get('body_preview') or ''} {c.get('template_name') or ''}"
        c["text_relevance"] = _relevance_score(haystack, keyword_weights)
        c["rank_score"] = _case_quality_score(c)

    initial_ranked = sorted(
        seen.values(),
        key=lambda x: (
            -float(x.get("rank_score") or 0),
            -int(x.get("score") or 0),
            -_epochish(x.get("last_activity_at")),
            str(x.get("created_at") or ""),
        ),
    )

    inspected_count = min(3, len(initial_ranked))
    for case in initial_ranked[:inspected_count]:
        try:
            if int(case.get("children_count") or 0) > 0:
                case.update(_fetch_thread_resolution(client, int(case["message_id"])))
            else:
                _apply_search_resolution_metadata(case)
        except Exception as e:
            _apply_search_resolution_metadata(case)
            case["comment_fetch_error"] = f"{type(e).__name__}: {e}"
        case["rank_score"] = _case_quality_score(case)

    ranked = sorted(
        seen.values(),
        key=lambda x: (
            -float(x.get("rank_score") or 0),
            -int(x.get("score") or 0),
            -_epochish(x.get("last_activity_at")),
            str(x.get("created_at") or ""),
        ),
    )

    result = {
        "matched_keywords": keywords,
        "searched_keywords": search_queries,
        "group_name": group_name,
        "total_candidates": len(seen),
        "inspected_threads": inspected_count,
        "recall_note": "후보는 recent 정렬 전체 쿼리 + 상위 쿼리의 relevant 정렬 패스(page 1~N 페이징) + 시간 의도(연도) 감지 시 해당 기간 한정 패스 + 도메인 동의어 확장으로 수집했습니다. 최신 글에 밀려 묻혀 있던 과거 사례도 후보 풀에 진입합니다.",
        "date_window": list(date_window) if date_window else None,
        "ranking_note": "rank_score는 키워드 가중치, 본문 관련도(빈도·커버리지), 템플릿 일치, 댓글 수, 최신성, 그룹명, 상위 후보의 실제 최신 댓글 본문/처리 상태를 함께 반영합니다. 모든 top_cases에는 original_body가 포함됩니다.",
        "top_cases": ranked[:top_k],
    }
    _SIMILAR_CACHE[cache_key] = (time.time(), result)
    return result


def fetch_thread_detail(message_id: int, group_name: str = "") -> dict:
    """특정 글의 본문 전체 + 답글/댓글을 조회합니다.
    find_similar_cases로 후보 찾은 뒤, 가장 관련성 높은 1~2건의 '처리 결말'을 확인하려면 이 도구를 호출하세요.
    답변 가이드 작성 시 최신 댓글에서 운영팀의 최종 응대 패턴을 파악하는 용도입니다.

    Args:
        message_id: 조회할 글의 message_id (find_similar_cases 결과의 값)
        group_name: 그룹 힌트 (선택). 비어있어도 동작합니다.

    Returns:
        message_id, created_at, body, children (댓글 배열 — created_at, author, body),
        latest_reply_at, latest_reply_summary
    """
    group_name = _effective_group(group_name) or ""  # 세션 pin이 있으면 강제 override
    try:
        client = get_client_for(group_name or None)
    except RuntimeError as e:
        return {"error": str(e)}

    parent = client.wall_message_detail(int(message_id))
    if not parent:
        return {"error": f"message_id={message_id} 상세 조회 실패"}

    comment_context = _fetch_comment_context(client, int(message_id), max_comments=30)
    id_names = get_group_id_names(group_name) if group_name else {}
    group_id = str(parent.get("group_id") or "")

    return {
        "message_id": parent.get("id") or parent.get("message_id"),
        "created_at": _format_agit_time(parent.get("created_time") or parent.get("updated_time")),
        "author": f"actor:{parent.get('actor_id')}" if parent.get("actor_id") else None,
        "group_id": parent.get("group_id"),
        "group_title": id_names.get(group_id),
        "url": f"https://dkt.agit.in/g/{parent.get('group_id')}/wall/{parent.get('id') or message_id}" if parent.get("group_id") else None,
        "body": _short(parent.get("text") or parent.get("message") or "", 4000),
        "original_body": _short(parent.get("text") or parent.get("message") or "", 4000),
        "children_count": comment_context["children_count"],
        "meaningful_children_count": comment_context["meaningful_children_count"],
        "children": comment_context["children"][:20],
        "latest_reply_at": comment_context["latest_reply_at"],
        "latest_reply_summary": comment_context["latest_reply_summary"],
        "resolution_status": comment_context["resolution_status"],
        "resolution_label": comment_context["resolution_label"],
        "past_response_pattern": comment_context["past_response_pattern"],
        "resolution_basis": comment_context["resolution_basis"],
        "can_use_as_answer_basis": comment_context["can_use_as_answer_basis"],
    }


def get_group_stats(group_name: str) -> dict:
    """그룹의 전체 글 수와 최근 글 샘플(5건)을 반환합니다.
    사용자가 '카카오워크 2.0에 글이 몇 개 있어?' 또는 '최근 글 보여줘' 같은 질문을 할 때 사용하세요.

    Args:
        group_name: 그룹명 (예: "전자결재")

    Returns:
        group_name, group_id, total_posts (전체 글 수), recent_samples (최근 5건)
    """
    group_name = _effective_group(group_name) or group_name  # 세션 pin이 있으면 강제 override
    if group_name not in GROUPS:
        return {"error": f"알 수 없는 그룹: '{group_name}'. 사용 가능: {list(GROUPS.keys())}"}
    group_ids = get_effective_group_ids(group_name)
    id_names = get_group_id_names(group_name)
    try:
        client = get_client_for(group_name)
    except RuntimeError as e:
        return {"error": str(e)}

    data = _search_multi(client, "", group_ids, id_names=id_names, sort="recent", parent="parent")
    msgs = data.get("wall_messages") or []
    # 시간순 재정렬 (multi-group merge 후 created_at 기준)
    msgs.sort(key=lambda m: str(m.get("created_at") or ""), reverse=True)

    return {
        "group_name": group_name,
        "group_ids": group_ids,
        "group_titles": id_names,
        "per_id_counts": data.get("per_id_counts") or {},
        "per_group_counts": data.get("per_group_counts") or {},
        "total_posts": data.get("total_count", 0),
        "recent_samples": [
            {
                "created_at": m.get("created_at"),
                "author": (m.get("user") or {}).get("nickname"),
                "group_title": m.get("group_title") or id_names.get(str(m.get("group_id") or "")),
                "body_preview": _short(m.get("message", ""), 200),
                "original_body": _original_body(m, 2000),
                "url": m.get("url"),
                "children_count": m.get("children_count", 0),
            }
            for m in msgs[:5]
        ],
    }


_TASK_STATUS_LABELS = {
    0: "요청",
    1: "진행",
    2: "완료",
    3: "승인",
}


def _validate_yyyy_mm_dd(value: str, field_name: str) -> str:
    value = (value or "").strip()
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{field_name}는 YYYY-MM-DD 형식이어야 합니다. 입력값: {value!r}")
    return value


def _count_group_posts(
    client: AgitClient,
    group_ids: list[str],
    id_names: dict[str, str],
    date_start: str,
    date_end: str,
    *,
    is_task: bool = False,
    task_status: Optional[int] = None,
    exclude_bot: bool = False,
    collect_messages: bool = False,
    max_messages: Optional[int] = None,
) -> dict:
    """페이지를 끝까지 순회해 실제 wall_messages 기준으로 카운트.

    collect_messages=True면 순회한 원본 메시지 dict를 returned_messages 리스트로 함께 반환한다
    (VOC 분류 등 본문이 필요한 경로 재사용용). max_messages로 상한을 두면 그 수만큼만 모은 뒤
    조기 종료해 댓글 조회/지연 비용을 제한한다.
    """
    target_group_ids = group_ids or [None]
    seen_ids: set = set()
    merged: list[dict] = []
    per_id_counts: dict[str, int] = {}
    per_group_counts: dict[str, int] = {}
    api_total_count = 0
    pages_fetched: dict[str, int] = {}

    # Agit이 지속 요청 속도를 제한하므로(동시·고빈도 호출 시 429 → 백오프로 느려지고 페이지 누락)
    # 페이지/그룹 순회는 순차 + 짧은 sleep으로 레이트리밋을 회피한다(병렬화는 역효과로 실측 확인).
    # scope="all": 벽 글만이 아니라 그룹 내 전체 원글 집계 → 운영 기준과 일치(scope="wall"은 과소집계).
    stop = False
    for i, gid in enumerate(target_group_ids):
        gid_key = str(gid) if gid else "__no_id__"
        group_label = id_names.get(str(gid), gid_key) if gid else gid_key
        per_id_counts[gid_key] = 0
        per_group_counts[group_label] = 0
        page = 1
        while True:
            data = client.search(
                "", group_id=gid, sort="recent", scope="all", parent="parent",
                page=page, date_range="specific", date_start=date_start, date_end=date_end,
                exclude_bot=exclude_bot, is_task=is_task,
                task_assignee="all" if is_task else None, task_status=task_status,
            )
            if page == 1:
                api_total_count += int(data.get("total_count") or 0)
            page_messages = data.get("wall_messages") or []
            for m in page_messages:
                mid = m.get("message_id")
                if mid is None or mid in seen_ids:
                    continue
                seen_ids.add(mid)
                merged.append(m)
                per_id_counts[gid_key] += 1
                per_group_counts[group_label] += 1
            pages_fetched[gid_key] = page
            has_more = data.get("has_more")
            has_more = has_more is True or str(has_more).lower() == "true"
            if not has_more or not page_messages:
                break
            if max_messages is not None and len(merged) >= max_messages:
                stop = True
                break
            page += 1
            time.sleep(0.3)
        if stop:
            break
        if i < len(target_group_ids) - 1:
            time.sleep(0.5)

    return {
        "count": len(merged),
        "api_total_count": api_total_count,
        "per_id_counts": per_id_counts,
        "per_group_counts": per_group_counts,
        "pages_fetched": pages_fetched,
        "returned_messages": len(merged),
        "messages": merged if collect_messages else [],
    }


def get_group_task_stats(
    group_name: str,
    date_start: str,
    date_end: str,
    exclude_bot: bool = False,
    target_group_id: str = "",
    include_rows: bool = False,
) -> dict:
    """지정 기간·그룹의 전체 작성 글 수와 요청(task) 상태별 건수를 집계합니다.

    사용자가 "4월 13일부터 5월 21일까지 xx 그룹의 전체 글 수와 요청/진행/완료
    건수를 알려줘"처럼 기간 기반 통계 리포트를 요청할 때 사용하세요.

    Args:
        group_name: 그룹명 (예: "전자결재")
        date_start: 검색 시작일 (YYYY-MM-DD)
        date_end: 검색 종료일 (YYYY-MM-DD)
        exclude_bot: 봇 작성 글 제외 여부. 기본 false
        target_group_id: 특정 하위 아지트 group_id만 조회할 때 지정. 비우면 모듈 전체 합산

    Returns:
        total_posts, task_status_counts, non_task_or_other_count, per_group_counts.
        요청 상태는 Agit task_status 기준입니다: 0=요청, 1=진행, 2=완료, 3=승인.
    """
    group_name = _effective_group(group_name) or group_name
    if group_name not in GROUPS:
        return {"error": f"알 수 없는 그룹: '{group_name}'. 사용 가능: {list(GROUPS.keys())}"}

    try:
        date_start = _validate_yyyy_mm_dd(date_start, "date_start")
        date_end = _validate_yyyy_mm_dd(date_end, "date_end")
        if date_start > date_end:
            return {"error": f"date_start는 date_end보다 늦을 수 없습니다: {date_start} > {date_end}"}
        client = get_client_for(group_name)
    except (RuntimeError, ValueError) as e:
        return {"error": str(e)}

    group_ids = get_effective_group_ids(group_name)
    id_names = get_group_id_names(group_name)
    target_group_id = str(target_group_id or "").strip()
    if target_group_id:
        if target_group_id not in group_ids:
            return {
                "error": (
                    f"'{target_group_id}'는 '{group_name}' 모듈의 조회 가능 group_id가 아닙니다. "
                    f"사용 가능: {group_ids}"
                )
            }
        group_ids = [target_group_id]

    # total(1) + 상태별(4) 카운트. 각 패스는 서로 독립이고 내부에서 페이지 순회 시 sleep으로
    # 레이트리밋을 회피하므로, 패스 5개는 병렬로 띄워 좁은 기간(흔한 경우)의 응답을 단축한다.
    # (페이지/그룹 레벨까지 중첩 병렬화하면 지속 호출속도 제한에 걸려 429로 역효과 → 그 레벨은 순차 유지.)
    # 넓은 기간에서 429 재시도가 소진되면 client.search가 예외 → 잘못된 부분집계 대신 명확한 에러 반환.
    def _count_total():
        return _count_group_posts(
            client, group_ids, id_names, date_start, date_end,
            exclude_bot=exclude_bot, collect_messages=include_rows,
        )

    def _count_status(status: int):
        return _count_group_posts(
            client, group_ids, id_names, date_start, date_end,
            is_task=True, task_status=status, exclude_bot=exclude_bot,
            collect_messages=include_rows,
        )

    try:
        with ThreadPoolExecutor(max_workers=5) as ex:
            total_future = ex.submit(_count_total)
            status_futures = {s: ex.submit(_count_status, s) for s in _TASK_STATUS_LABELS}
            total = total_future.result()
            counted_by_status = {s: f.result() for s, f in status_futures.items()}
    except RuntimeError as e:
        return {"error": f"Agit 응답이 지연/제한되고 있습니다. 기간을 좁히거나 잠시 후 다시 시도해주세요. ({e})"}

    status_counts: dict[str, dict] = {}
    numeric_status_counts: dict[int, int] = {}
    for status, label in _TASK_STATUS_LABELS.items():
        counted = counted_by_status[status]
        numeric_status_counts[status] = counted["count"]
        status_counts[label] = {
            "task_status": status,
            "count": counted["count"],
            "api_total_count": counted["api_total_count"],
            "per_group_counts": counted["per_group_counts"],
            "per_id_counts": counted["per_id_counts"],
            "pages_fetched": counted["pages_fetched"],
        }

    requested_in_progress_done = sum(numeric_status_counts.get(s, 0) for s in (0, 1, 2))
    all_task_statuses = sum(numeric_status_counts.values())
    total_posts = total["count"]

    # include_rows=True면 전체 원글에 상태 라벨을 매핑한 행 목록을 만든다.
    # 상태별 패스의 message_id → 라벨 맵을 만들고, 전체 패스의 글에 매칭(미매칭=비task).
    rows: list[dict] = []
    if include_rows:
        status_by_id: dict = {}
        for status, label in _TASK_STATUS_LABELS.items():
            for m in (counted_by_status[status].get("messages") or []):
                mid = m.get("message_id")
                if mid is not None:
                    status_by_id[mid] = label
        for m in (total.get("messages") or []):
            mid = m.get("message_id")
            user = m.get("user") or {}
            gid = str(m.get("group_id") or "")
            rows.append({
                "message_id": mid,
                "created_at": m.get("created_at"),
                "status": status_by_id.get(mid, "비task"),
                "corp": _extract_corp(m.get("message", "") or ""),
                "template_name": m.get("group_message_template_name") or "",
                "body_preview": _plaintext(m.get("message", "") or "", 140),
                "author": user.get("nickname"),
                "group_title": m.get("group_title") or id_names.get(gid, gid),
                "children_count": m.get("children_count", 0),
                "url": m.get("url"),
            })
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)

    return {
        "group_name": group_name,
        "group_ids": group_ids,
        "group_titles": id_names,
        "target_group_id": target_group_id or "",
        "target_group_title": id_names.get(target_group_id, "") if target_group_id else "모듈 전체",
        "date_start": date_start,
        "date_end": date_end,
        "basis": "Agit search.total_search를 page=1부터 has_more=false까지 순회한 실제 wall_messages 기준입니다. 요청/진행/완료는 is_task=true + task_status 기준입니다.",
        "exclude_bot": bool(exclude_bot),
        "total_posts": total_posts,
        "api_total_posts": total["api_total_count"],
        "count_source": "paged_wall_messages",
        "total_pages_fetched": total["pages_fetched"],
        "task_status_counts": status_counts,
        "requested_in_progress_done_total": requested_in_progress_done,
        "approved_count": numeric_status_counts.get(3, 0),
        "non_task_or_other_count": max(0, total_posts - all_task_statuses),
        "per_group_counts": total["per_group_counts"],
        "per_id_counts": total["per_id_counts"],
        "count_mismatch_note": (
            f"API total_count({total['api_total_count']})와 페이지 순회 카운트({total_posts})가 다릅니다."
            if total["api_total_count"] != total_posts else ""
        ),
        "report_rows": [
            {"label": "전체 작성 글", "count": total_posts},
            {"label": "요청", "count": numeric_status_counts.get(0, 0)},
            {"label": "진행", "count": numeric_status_counts.get(1, 0)},
            {"label": "완료", "count": numeric_status_counts.get(2, 0)},
            {"label": "승인", "count": numeric_status_counts.get(3, 0)},
            {"label": "요청 아님/기타", "count": max(0, total_posts - all_task_statuses)},
        ],
        "rows": rows,
    }


def collect_voc_cases(
    group_name: str,
    date_start: str = "",
    date_end: str = "",
    max_cases: int = 40,
    inspect_comments: int = 8,
) -> dict:
    """VOC 아지트의 글을 본문 기준으로 수집·분류하고 대표 사례의 댓글 처리맥락을 붙입니다.

    VOC 리포트 모드에서 '기능 개선 요청', '신규 양식 추가', '버그·에러', '사용 문의'처럼
    한 모듈에 섞여 있는 다양한 VOC를 카테고리별로 정리할 때 사용하세요.
    get_group_task_stats가 상태별 '건수'를 집계한다면, 이 도구는 각 글의 '본문 + 댓글 처리 결말'을
    분류·요약해 이슈 클러스터의 실데이터 근거를 만듭니다. (find_similar_cases가 단일 문의의 유사
    사례를 찾는 것과 달리, 기간 전체의 VOC를 한 번에 분류합니다.)

    Args:
        group_name: 그룹명 (예: "전자결재"). report 모드에서는 자동으로 VOC 전용 아지트로 한정됩니다.
        date_start: 시작일 YYYY-MM-DD. 비우면 최근 90일을 기본 기간으로 사용.
        date_end: 종료일 YYYY-MM-DD. 비우면 오늘.
        max_cases: 수집·분류할 최대 글 수. 기본 40, 최대 80.
        inspect_comments: 댓글 처리맥락을 실제 조회할 대표 사례 수(카테고리 대표·댓글수 우선). 기본 8, 최대 15.

    Returns:
        category_distribution(카테고리별 건수), cases(각 글의 voc_category·original_body·
        resolution_label·latest_reply_summary 포함), inspected_count, date_start, date_end.
    """
    group_name = _effective_group(group_name) or group_name  # 세션 pin이 있으면 강제 override
    if group_name not in GROUPS:
        return {"error": f"알 수 없는 그룹: '{group_name}'. 사용 가능: {list(GROUPS.keys())}"}

    max_cases = max(1, min(int(max_cases), 80))
    inspect_comments = max(0, min(int(inspect_comments), 15))

    if not date_end:
        date_end = datetime.now().strftime("%Y-%m-%d")
    if not date_start:
        date_start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    try:
        date_start = _validate_yyyy_mm_dd(date_start, "date_start")
        date_end = _validate_yyyy_mm_dd(date_end, "date_end")
        if date_start > date_end:
            return {"error": f"date_start는 date_end보다 늦을 수 없습니다: {date_start} > {date_end}"}
        client = get_client_for(group_name)
    except (RuntimeError, ValueError) as e:
        return {"error": str(e)}

    group_ids = get_effective_group_ids(group_name)  # report 모드 → VOC 전용 아지트
    id_names = get_group_id_names(group_name)

    collected = _count_group_posts(
        client, group_ids, id_names, date_start, date_end,
        collect_messages=True, max_messages=max_cases,
    )
    # 페이지 단위 순회라 마지막 페이지에서 max_cases를 초과 수집될 수 있어 하드 캡으로 자른다(최신순 유지).
    raw_messages = collected.get("messages") or []
    messages = raw_messages[:max_cases]
    truncated = len(raw_messages) > max_cases

    # 1차 규칙 분류 + 본문/댓글 메타 정리
    cases: list[dict] = []
    for m in messages:
        user = m.get("user") or {}
        last_activity = m.get("last_activity_at") or m.get("updated_at") or m.get("created_at")
        case = {
            "message_id": m.get("message_id"),
            "created_at": m.get("created_at"),
            "last_activity_at": last_activity,
            "author": user.get("nickname"),
            "group_title": m.get("group_title") or id_names.get(str(m.get("group_id") or "")),
            "template_name": m.get("group_message_template_name"),
            "is_task": bool(m.get("is_task")),
            "task_status": m.get("task_status"),
            "original_body": _original_body(m, 1500),
            "body_preview": _short(m.get("message", ""), 300),
            "url": m.get("url"),
            "children_count": m.get("children_count", 0),
            "is_comments_closed": bool(m.get("is_comments_closed")),
            "recent_commented_at": m.get("recent_commented_at"),
        }
        case.update(_classify_voc_category(m))
        cases.append(case)

    # 대표 사례에만 실제 댓글 조회(처리 결말 확보) — 카테고리 분산 + 댓글 많은 순 우선
    commentable = [c for c in cases if int(c.get("children_count") or 0) > 0]
    commentable.sort(key=lambda c: int(c.get("children_count") or 0), reverse=True)
    inspected_ids: set = set()
    # 카테고리당 최소 1건은 우선 확보한 뒤 남은 예산을 댓글 많은 순으로 배분
    by_cat_first: list[dict] = []
    seen_cats: set = set()
    for c in commentable:
        if c["voc_category"] not in seen_cats:
            by_cat_first.append(c)
            seen_cats.add(c["voc_category"])
    inspect_order = by_cat_first + [c for c in commentable if c not in by_cat_first]
    for case in inspect_order[:inspect_comments]:
        try:
            case.update(_fetch_thread_resolution(client, int(case["message_id"])))
        except Exception as e:
            _apply_search_resolution_metadata(case)
            case["comment_fetch_error"] = f"{type(e).__name__}: {e}"
        inspected_ids.add(case["message_id"])
    # 조회하지 않은 글은 검색 메타 기반 처리상태로 보강(저비용)
    for case in cases:
        if case["message_id"] not in inspected_ids:
            _apply_search_resolution_metadata(case)

    # 카테고리 분포
    distribution: dict[str, int] = {}
    for c in cases:
        distribution[c["voc_category"]] = distribution.get(c["voc_category"], 0) + 1
    category_distribution = [
        {"category": cat, "count": cnt}
        for cat, cnt in sorted(distribution.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return {
        "group_name": group_name,
        "group_ids": group_ids,
        "group_titles": id_names,
        "date_start": date_start,
        "date_end": date_end,
        "collected_count": len(cases),
        "max_cases": max_cases,
        "inspected_count": len(inspected_ids),
        "truncated": truncated,
        "per_group_counts": collected.get("per_group_counts") or {},
        "category_distribution": category_distribution,
        "classification_note": (
            "voc_category는 본문·댓글·템플릿명 기반 1차 규칙 분류입니다. 카테고리: "
            "신규 양식 추가 / 버그·에러 / 기능 개선 요청 / 사용 문의 / 기타. "
            "category_signals(매칭 신호)와 original_body·latest_reply_summary를 읽어 최종 보정하세요."
        ),
        "cases": cases,
    }


# Gemini가 사용할 도구 목록 (chatbot.py에서 import)
TOOLS = [
    search_posts,
    list_available_groups,
    find_similar_cases,
    get_group_stats,
    get_group_task_stats,
    collect_voc_cases,
    fetch_thread_detail,
]


# ═══════════════════════════════════════════════════════════
#   ANTHROPIC CLAUDE 용 도구 스키마 + 디스패처
# ═══════════════════════════════════════════════════════════
_GROUP_NAMES = list(GROUPS.keys())

ANTHROPIC_TOOLS = [
    {
        "name": "search_posts",
        "description": "아지트에서 글을 검색합니다. 키워드로 글 찾기, 특정 그룹 안에서만 찾기 등에 사용.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드"},
                "group_name": {
                    "type": "string",
                    "enum": _GROUP_NAMES + [""],
                    "description": "그룹명 (비어있으면 전체 검색)",
                },
                "sort": {
                    "type": "string",
                    "enum": ["recent", "relevant"],
                    "description": "정렬 방식. recent=최신순, relevant=관련도순",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "반환할 최대 글 수"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_available_groups",
        "description": "챗봇이 접근 가능한 아지트 그룹의 이름·ID 목록을 반환합니다.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "find_similar_cases",
        "description": "주어진 문의 텍스트와 유사한 과거 사례를 찾습니다. 답변 가이드 작성 전 반드시 먼저 호출하세요. 결과에는 키워드 매칭 점수와 별도로 recency_rank, last_activity_at이 포함되어 '최신 개선분'을 식별할 수 있습니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query_text": {"type": "string", "description": "분석할 문의 텍스트 전체"},
                "group_name": {
                    "type": "string",
                    "enum": _GROUP_NAMES,
                    "description": "검색할 그룹명",
                },
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "description": "반환할 유사 사례 수"},
            },
            "required": ["query_text", "group_name"],
        },
    },
    {
        "name": "get_group_stats",
        "description": "그룹의 전체 글 수와 최근 글 샘플(5건)을 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_name": {"type": "string", "enum": _GROUP_NAMES, "description": "그룹명"},
            },
            "required": ["group_name"],
        },
    },
    {
        "name": "get_group_task_stats",
        "description": "지정 기간·그룹의 전체 작성 글 수와 Agit 요청(task) 상태별 건수(요청/진행/완료/승인)를 page=1부터 has_more=false까지 순회한 실제 wall_messages 기준으로 집계합니다. 기간 기반 통계 리포트에 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_name": {"type": "string", "enum": _GROUP_NAMES, "description": "그룹명"},
                "date_start": {"type": "string", "description": "검색 시작일. YYYY-MM-DD 형식"},
                "date_end": {"type": "string", "description": "검색 종료일. YYYY-MM-DD 형식"},
                "exclude_bot": {"type": "boolean", "description": "봇 작성 글 제외 여부. 기본 false"},
                "target_group_id": {"type": "string", "description": "특정 하위 아지트 group_id만 조회할 때 지정. 비우면 모듈 전체 합산"},
            },
            "required": ["group_name", "date_start", "date_end"],
        },
    },
    {
        "name": "collect_voc_cases",
        "description": "VOC 아지트의 글을 본문 기준으로 수집·분류(신규 양식 추가/버그·에러/기능 개선 요청/사용 문의/기타)하고 대표 사례의 댓글 처리 결말을 붙입니다. VOC 리포트의 이슈 클러스터를 추측이 아니라 실데이터로 만들 때 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_name": {"type": "string", "enum": _GROUP_NAMES, "description": "그룹명"},
                "date_start": {"type": "string", "description": "시작일 YYYY-MM-DD. 비우면 최근 90일"},
                "date_end": {"type": "string", "description": "종료일 YYYY-MM-DD. 비우면 오늘"},
                "max_cases": {"type": "integer", "minimum": 1, "maximum": 80, "description": "수집·분류할 최대 글 수. 기본 40"},
                "inspect_comments": {"type": "integer", "minimum": 0, "maximum": 15, "description": "댓글 처리맥락을 실제 조회할 대표 사례 수. 기본 8"},
            },
            "required": ["group_name"],
        },
    },
    {
        "name": "fetch_thread_detail",
        "description": "특정 글의 본문 전체 + 답글/댓글을 조회합니다. find_similar_cases로 찾은 후보 중 가장 관련성 높은 1~2건의 처리 결말(최신 댓글)을 확인할 때 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "integer", "description": "조회할 글의 message_id"},
                "group_name": {
                    "type": "string",
                    "enum": _GROUP_NAMES + [""],
                    "description": "그룹 힌트 (선택)",
                },
            },
            "required": ["message_id"],
        },
    },
]


_DISPATCH = {
    "search_posts": search_posts,
    "list_available_groups": list_available_groups,
    "find_similar_cases": find_similar_cases,
    "get_group_stats": get_group_stats,
    "get_group_task_stats": get_group_task_stats,
    "collect_voc_cases": collect_voc_cases,
    "fetch_thread_detail": fetch_thread_detail,
}


def call_tool(name: str, args: dict) -> dict:
    """Anthropic tool_use 결과를 받아 실제 Python 함수를 디스패치."""
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool: {name}"}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return {"error": f"invalid args for {name}: {e}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
