# ── Forensic Pipeline — Dockerfile ──────────────────────────────────────────
# Python 3.11-slim 기반. agents/ 디렉토리가 앱 루트.
#
# 빌드:
#   docker build -t forensic-pipeline .
#
# 실행 (SQLite, 로컬):
#   docker run -p 8000:8000 \
#     -e ANTHROPIC_API_KEY=sk-ant-... \
#     -e SEC_USER_AGENT=you@example.com \
#     forensic-pipeline
#
# 실행 (Postgres):
#   docker run -p 8000:8000 \
#     -e ANTHROPIC_API_KEY=sk-ant-... \
#     -e SEC_USER_AGENT=you@example.com \
#     -e DATABASE_URL=postgresql://user:pass@host:5432/forensic \
#     forensic-pipeline
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# 시스템 패키지 (lxml 빌드 의존성)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libxml2-dev \
        libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements 먼저 복사 → 레이어 캐시 활용
COPY agents/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY agents/ .

# 리포트 디렉토리 생성 (Excel 출력용)
RUN mkdir -p reports

# 리포트 볼륨만 선언 (forensic.db는 /tmp에 저장하므로 VOLUME 불필요)
# 주의: VOLUME에 파일 경로를 넣으면 Docker가 해당 경로를 디렉토리로 만들어
#       SQLite가 파일을 열지 못하는 문제가 생김 → 제거
VOLUME ["/app/reports"]

EXPOSE 8000

# uvicorn으로 FastAPI 실행
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
