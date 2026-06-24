# 웹앱 배포 가이드

이 문서는 Agit CS·VOC 챗봇을 로컬 실행이 아니라 웹앱처럼 배포해서 접속하는 방법을 설명합니다.

## 1. 배포 형태

이 앱은 아래 구조입니다.

```text
브라우저 UI
  ↓
FastAPI 서버
  ↓
Agit API + Gemini API
```

`AGIT_TOKEN`, `GEMINI_API_KEY` 같은 민감한 값은 브라우저에 노출하면 안 됩니다. 따라서 배포 시에도 토큰은 서버 환경변수로 넣어야 합니다.

## 2. 포함된 배포 파일

웹앱 배포용 zip에는 아래 파일이 포함됩니다.

```text
Dockerfile
.dockerignore
requirements.txt
agit.py
chatbot.py
webapp/
docs/
.env.example
```

실제 `.env` 파일은 포함하지 않습니다.

## 3. 필요한 환경변수

배포 환경에 아래 환경변수를 등록합니다.

| 변수 | 필수 여부 | 설명 |
|---|---:|---|
| `GEMINI_API_KEY` | 필수 | Gemini API 호출용 키 |
| `AGIT_TOKEN` | 권장 | 공통 Agit OAuth 토큰 |
| `AGIT_TOKEN_ELEC` | 선택 | 전자결재 전용 Agit 토큰 |
| `AGIT_TOKEN_HR` | 선택 | 인사 시스템 전용 Agit 토큰 |
| `AGIT_USER_ID` | 선택 | 사용자 ID 표시/조회 보조용 |
| `AGIT_USER_NAME` | 선택 | 화면 하단 사용자명 표시용 |
| `GEMINI_MODEL` | 선택 | 기본 Gemini 모델 |

`AGIT_TOKEN_ELEC`, `AGIT_TOKEN_HR`이 비어 있으면 `AGIT_TOKEN`으로 fallback합니다.

## 4. Google Cloud Run 배포

Cloud Run을 사용할 수 있으면 가장 단순합니다.

### 방법 A. 소스에서 바로 배포

압축을 풀고 해당 폴더에서 아래 명령어를 실행합니다.

```bash
gcloud run deploy agit-chatbot \
  --source . \
  --region asia-northeast3 \
  --allow-unauthenticated
```

배포 후 Cloud Run 콘솔에서 환경변수를 등록합니다.

```text
GEMINI_API_KEY
AGIT_TOKEN
AGIT_TOKEN_ELEC
AGIT_TOKEN_HR
AGIT_USER_ID
AGIT_USER_NAME
GEMINI_MODEL
```

토큰/API Key는 가능하면 Google Secret Manager로 관리하는 것을 권장합니다.

### 방법 B. Docker 이미지로 배포

```bash
gcloud builds submit --tag asia-northeast3-docker.pkg.dev/PROJECT_ID/agit/agit-chatbot
```

```bash
gcloud run deploy agit-chatbot \
  --image asia-northeast3-docker.pkg.dev/PROJECT_ID/agit/agit-chatbot \
  --region asia-northeast3 \
  --allow-unauthenticated
```

`PROJECT_ID`는 회사 GCP 프로젝트 ID로 바꿔야 합니다.

## 5. AI Studio / Firebase Studio에서 열 때

Google AI Studio Build가 Python 서버 실행을 직접 지원하지 않는 환경이라면, zip을 넣어도 바로 실행되지 않을 수 있습니다.

이 경우 아래 중 하나로 진행하세요.

| 환경 | 권장 방식 |
|---|---|
| Firebase Studio | zip 또는 GitHub로 열고, 터미널에서 FastAPI 실행 |
| AI Studio Build | 코드 참고/수정용으로 사용하고, 배포는 Cloud Run 사용 |
| Cloud Run | 이 패키지의 `Dockerfile`로 바로 배포 |

Studio 계열 환경에서 실행 명령을 직접 지정할 수 있다면 아래 명령을 사용합니다.

```bash
uvicorn webapp.server:app --host 0.0.0.0 --port ${PORT:-8080}
```

## 6. 로컬에서 Docker로 미리 확인

배포 전 로컬에서 Docker로 확인할 수 있습니다.

```bash
docker build -t agit-chatbot .
```

```bash
docker run --rm -p 8080:8080 \
  -e GEMINI_API_KEY="..." \
  -e AGIT_TOKEN="..." \
  -e AGIT_TOKEN_ELEC="..." \
  -e AGIT_TOKEN_HR="..." \
  agit-chatbot
```

브라우저에서 접속합니다.

```text
http://localhost:8080
```

## 7. 배포 후 확인할 것

배포 URL에 접속해서 아래를 확인합니다.

1. 화면이 열리는지
2. 좌측 모듈 목록이 보이는지
3. `답변 가이드` 탭에서 질문을 보낼 수 있는지
4. `VOC 리포트` 탭에서 리포트가 생성되는지
5. `통계 리포트` 탭에서 기간/조회 그룹 선택 후 숫자가 계산되는지

## 8. 보안 주의사항

- 실제 `.env` 파일은 zip에 넣지 마세요.
- `GEMINI_API_KEY`, `AGIT_TOKEN`은 브라우저 코드에 넣지 마세요.
- Cloud Run 서비스를 외부 공개할 경우 사내 인증/IAP 적용을 검토하세요.
- Agit 토큰은 최소 권한으로 발급하는 것을 권장합니다.

## 9. 문제 해결

### 앱이 시작되지 않음

환경변수가 빠졌을 가능성이 큽니다. 로그에서 아래 메시지를 확인하세요.

```text
GEMINI_API_KEY 환경변수가 비어 있습니다
Agit 토큰이 하나도 설정되어 있지 않습니다
```

### 통계가 예상과 다름

통계 리포트는 아래 기준으로 계산합니다.

- 선택한 모듈/하위 그룹
- 시작일/종료일
- 봇 작성 글 제외 여부
- Agit 요청 상태값
- `has_more=false`까지 페이지 순회한 실제 글 목록

### 502 또는 timeout 발생

Agit API 호출이나 Gemini 응답이 오래 걸릴 수 있습니다. Cloud Run timeout을 늘리는 것을 검토하세요.

```bash
gcloud run services update agit-chatbot \
  --region asia-northeast3 \
  --timeout 300
```
