# Agit CS·VOC 챗봇 사용 가이드

이 문서는 개발자가 아닌 사용자가 전달받은 압축 파일로 챗봇을 실행하고 사용하는 방법을 설명합니다.

## 1. 준비물

아래 3가지만 준비하면 됩니다.

| 준비물 | 설명 |
|---|---|
| Python | 앱을 실행하기 위한 프로그램입니다. Python 3.11 이상 권장 |
| Agit 토큰 | 아지트 데이터를 조회하기 위한 접근 토큰입니다. 전달자 또는 관리자에게 요청하세요. |
| Gemini API Key | AI 답변 생성을 위한 Google Gemini API 키입니다. 전달자 또는 관리자에게 요청하세요. |

이미 전달자가 `.env` 파일까지 함께 제공했다면, 토큰/API Key 설정 단계는 건너뛰어도 됩니다.

## 2. 압축 풀기

1. 전달받은 `agit_chatbot_source.zip` 파일을 원하는 위치에 풉니다.
2. 예시 위치:

```text
내 문서/agit_chatbot
```

압축을 풀면 대략 아래와 같은 파일이 보입니다.

```text
agit.py
chatbot.py
requirements.txt
webapp/
docs/
.env.example
```

## 3. 설정 파일 만들기

1. `.env.example` 파일을 복사합니다.
2. 복사한 파일 이름을 `.env`로 바꿉니다.
3. `.env` 파일을 메모장, VS Code, TextEdit 등으로 엽니다.
4. 아래 값을 본인 환경에 맞게 채웁니다.

```text
AGIT_TOKEN=아지트_토큰
AGIT_TOKEN_ELEC=전자결재_전용_토큰
AGIT_TOKEN_HR=인사시스템_전용_토큰
AGIT_USER_ID=본인_아지트_USER_ID
GEMINI_API_KEY=Gemini_API_Key
GEMINI_MODEL=gemini-3.1-flash-lite
```

토큰이 하나만 있다면 `AGIT_TOKEN`만 채워도 실행할 수 있습니다. 전자결재/인사 시스템별 전용 토큰이 있으면 `AGIT_TOKEN_ELEC`, `AGIT_TOKEN_HR`도 채우면 더 정확하게 조회됩니다.

## 4. 처음 한 번만 설치하기

터미널을 열고 압축을 푼 폴더로 이동합니다.

macOS 예시:

```bash
cd ~/Documents/agit_chatbot
```

Windows 예시:

```bat
cd %USERPROFILE%\Documents\agit_chatbot
```

그 다음 아래 명령어를 실행합니다.

```bash
python3 -m pip install -r requirements.txt
```

Windows에서 `python3`가 동작하지 않으면 아래처럼 실행합니다.

```bat
python -m pip install -r requirements.txt
```

## 5. 앱 실행하기

압축을 푼 폴더에서 아래 명령어를 실행합니다.

```bash
python3 -m uvicorn webapp.server:app --port 8765
```

Windows에서 `python3`가 동작하지 않으면 아래처럼 실행합니다.

```bat
python -m uvicorn webapp.server:app --port 8765
```

터미널에 아래와 비슷한 문구가 보이면 정상 실행된 상태입니다.

```text
Uvicorn running on http://127.0.0.1:8765
```

## 6. 브라우저에서 접속하기

브라우저 주소창에 아래 주소를 입력합니다.

```text
http://127.0.0.1:8765
```

화면이 열리면 왼쪽에서 `전자결재` 또는 `인사 시스템` 모듈을 선택해 사용할 수 있습니다.

## 7. 주요 기능 사용법

### 답변 가이드

CS 문의에 대한 답변 초안을 만들 때 사용합니다.

1. 상단에서 `답변 가이드` 탭을 선택합니다.
2. 사용자 문의 내용을 입력합니다.
3. `전송`을 누릅니다.
4. 유사한 과거 사례와 권장 답변 초안이 생성됩니다.
5. 답변 초안 코드블록 우측 상단의 `복사` 버튼을 눌러 바로 복사할 수 있습니다.

### VOC 리포트

최근 문의/VOC를 요약 보고서 형태로 보고 싶을 때 사용합니다.

1. 상단에서 `VOC 리포트` 탭을 선택합니다.
2. 예시처럼 입력합니다.

```text
최근 30건 기준으로 주요 이슈를 정리해줘
```

3. 주요 이슈, 처리 경향, 권고 액션이 생성됩니다.

### 통계 리포트

기간별 전체 글 수와 요청/진행/완료 건수를 계산할 때 사용합니다.

1. 상단에서 `통계 리포트` 탭을 선택합니다.
2. `조회 그룹`에서 `모듈 전체` 또는 특정 하위 아지트 그룹을 선택합니다.
3. `시작일`, `종료일`을 선택합니다.
4. 필요하면 `봇 작성 글 제외`를 체크합니다.
5. `요약 보고서 생성`을 누릅니다.

통계 리포트는 페이지를 끝까지 순회해서 실제 글 목록 기준으로 집계합니다. 요청/진행/완료는 Agit의 요청 상태값 기준입니다.

## 8. 자주 발생하는 문제

### 브라우저가 열리지 않아요

터미널에 앱이 실행 중인지 확인하세요.

```text
Uvicorn running on http://127.0.0.1:8765
```

이 문구가 없다면 앱 실행 명령어를 다시 실행하세요.

### 8765 포트가 이미 사용 중이라고 나와요

다른 포트로 실행하면 됩니다.

```bash
python3 -m uvicorn webapp.server:app --port 8766
```

브라우저 주소도 아래처럼 바꿔 접속합니다.

```text
http://127.0.0.1:8766
```

### 토큰 오류가 나요

`.env` 파일에 `AGIT_TOKEN` 또는 모듈별 토큰이 들어 있는지 확인하세요.

```text
AGIT_TOKEN=...
AGIT_TOKEN_ELEC=...
AGIT_TOKEN_HR=...
```

토큰 값 앞뒤에 따옴표나 공백이 들어가지 않게 주의하세요.

### Gemini 오류가 나요

`.env` 파일에 `GEMINI_API_KEY`가 들어 있는지 확인하세요.

```text
GEMINI_API_KEY=...
```

키가 만료되었거나 권한이 없으면 관리자에게 새 키를 요청해야 합니다.

### 통계 숫자가 예상과 달라요

아래 기준을 확인하세요.

- 선택한 모듈이 맞는지
- `조회 그룹`이 `모듈 전체`인지 특정 하위 그룹인지
- 조회 기간이 맞는지
- `봇 작성 글 제외` 여부가 맞는지
- 요청/진행/완료는 일반 본문 문구가 아니라 Agit 요청 상태 기준인지

## 9. 종료하기

앱을 끄려면 실행 중인 터미널에서 아래 키를 누릅니다.

```text
Ctrl + C
```

브라우저 창은 그냥 닫아도 됩니다.

## 10. 전달할 때 주의사항

다른 사람에게 전달할 때는 실제 `.env` 파일을 포함하지 않는 것이 안전합니다.

전달 권장 파일:

```text
agit_chatbot_source.zip
```

포함해도 되는 파일:

```text
.env.example
```

포함하면 안 되는 파일:

```text
.env
```

`.env`에는 실제 토큰과 API Key가 들어 있으므로 외부 공유하지 마세요.
