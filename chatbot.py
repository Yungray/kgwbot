"""Gemini 기반 Agit 챗봇 — CLI 진입점.

사용:
  cd agit_chatbot/
  pip install -r requirements.txt
  cp .env.example .env  # 그리고 .env에 토큰들 채우기
  python3 chatbot.py
"""
import os
import sys
from pathlib import Path

# .env 자동 로드 (있을 때만)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from google import genai
from google.genai import types

from agit import TOOLS, GROUPS


SYSTEM_INSTRUCTION = f"""너는 카카오/디케이테크인 사내 아지트(Agit) 데이터를 검색·분석해주는 한국어 어시스턴트야.

## 너의 역할
사용자의 자연어 요청을 받아서:
1. 적절한 도구(tool)를 골라 호출
2. 결과를 사용자가 읽기 좋게 한국어로 정리해서 답변
3. 원문 URL은 반드시 마크다운 링크로 포함

## 사용 가능한 그룹 (group_name)
{chr(10).join(f"- '{name}'" for name in GROUPS.keys())}

## 답변 규칙
- 항상 한국어로 답변
- 결과는 표 또는 불릿으로 구조화
- 검색 결과는 시간 역순(최신 → 과거)으로 정렬
- 데이터가 없으면 솔직히 "찾을 수 없음"이라고 답변
- 도구 응답의 raw JSON은 사용자에게 그대로 보여주지 말고 자연어로 풀어서 설명
- 답변 초안 작성을 요청받으면, 반드시 먼저 find_similar_cases를 호출해 과거 사례를 확인한 뒤 작성
- 참고 근거와 분포를 설명할 때는 아지트 숫자 ID보다 group_title, group_titles, per_group_counts의 사람이 읽는 그룹명을 우선 사용

## 답변 초안 작성 시
사용자가 "이런 문의에 답변해줘", "답변 초안 짜줘" 같은 요청을 하면:
1. find_similar_cases를 호출해 유사 사례 5건 확보
2. 사례들의 답변 패턴(운영팀이 어떻게 답했는지)을 분석
3. 인용된 사례 링크와 함께 답변 초안 작성
4. 답변 톤은 정중하고 명료한 사내 응대 스타일 (~합니다 체)

## 검색 결과 표시 포맷
| # | 일자 | 작성자 | 본문 미리보기 | 댓글 | 원문 |
|---|------|--------|---------------|------|------|

도구 호출 결과를 받으면 위 표 형식으로 정리해.
"""


def print_header() -> None:
    print()
    print("═" * 64)
    print("  🤖 Agit 챗봇 (Gemini 기반)")
    print("═" * 64)
    print("  명령: 'exit' / 'quit' / 'q' / '종료'  ·  Ctrl+C로도 종료")
    print()
    print("  예시 질문:")
    print("    · 법무 그룹에서 최근 검토 요청 5건 보여줘")
    print("    · 카카오워크 2.0에서 알림 오류 신고 정리해줘")
    print("    · 외부 감사인 LDAP 권한 문의 답변 초안 짜줘:")
    print("      [문의 텍스트 붙여넣기]")
    print("    · 전자결재 그룹에 글이 얼마나 있어?")
    print("═" * 64)
    print()


def check_env() -> bool:
    missing = []
    if not os.environ.get("GEMINI_API_KEY"):
        missing.append("GEMINI_API_KEY")
    if not os.environ.get("AGIT_TOKEN"):
        missing.append("AGIT_TOKEN")
    if missing:
        print(f"❌ 환경변수 누락: {', '.join(missing)}")
        print("   .env 파일에 추가하거나 export 하세요.")
        print("   .env.example을 참고하세요.")
        return False
    return True


def main() -> None:
    if not check_env():
        sys.exit(1)

    api_key = os.environ["GEMINI_API_KEY"]
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    client = genai.Client(api_key=api_key)

    # Chat session 생성 — Python 함수 객체를 tools에 그대로 넘기면
    # google-genai SDK가 자동으로 schema 생성 + 함수 자동 호출까지 수행
    chat = client.chats.create(
        model=model_name,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=TOOLS,
            temperature=0.3,
        ),
    )

    print_header()
    print(f"📡 모델: {model_name}")
    print(f"📚 사용 가능한 그룹: {', '.join(GROUPS.keys())}")
    print(f"🧰 등록된 도구: {len(TOOLS)}개")
    print()

    while True:
        try:
            user_input = input("👤 ▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n안녕히 가세요 👋")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "q", "종료"}:
            print("안녕히 가세요 👋")
            break

        try:
            response = chat.send_message(user_input)
            text = getattr(response, "text", None) or ""
            print()
            if text:
                print("🤖 ▸", text)
            else:
                # function call이 일어났지만 후속 텍스트 응답이 없을 때
                print("🤖 ▸ (도구 실행 완료 — 추가 응답 없음)")
            print()
        except Exception as e:
            print(f"\n❌ 오류: {type(e).__name__}: {e}\n")


if __name__ == "__main__":
    main()
