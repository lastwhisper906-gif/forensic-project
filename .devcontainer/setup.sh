#!/usr/bin/env bash
# Codespace 생성 시 1회 실행되는 셋업 스크립트

set -e  # 에러 발생 시 즉시 중단

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Forensic Pipeline — Codespace 셋업"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. 시스템 패키지 (lxml 빌드 의존성) ───────────────────────────────────
echo "[1/4] 시스템 패키지 설치..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt-dev \
  > /dev/null 2>&1
echo "  ✓ 시스템 패키지"

# ── 2. Python 의존성 ─────────────────────────────────────────────────────
echo "[2/4] Python 패키지 설치 (agents/requirements.txt)..."
pip install --quiet --no-cache-dir -r agents/requirements.txt
echo "  ✓ Python 패키지"

# ── 3. Claude Code CLI ────────────────────────────────────────────────────
echo "[3/4] Claude Code CLI 설치..."
npm install -g @anthropic-ai/claude-code --silent
echo "  ✓ Claude Code $(claude --version 2>/dev/null || echo '설치됨')"

# ── 4. .env 파일 생성 (Codespaces Secrets → .env) ─────────────────────────
echo "[4/4] .env 파일 생성..."
cat > agents/.env << 'EOF'
# Codespaces Secrets에서 자동 주입된 값이 환경변수로 이미 설정되어 있음.
# 아래는 로컬 오버라이드용 (필요 시 직접 수정)
# ANTHROPIC_API_KEY=sk-ant-...
# SEC_USER_AGENT=forensic-app/1.0 you@email.com
# DATABASE_URL=
EOF
echo "  ✓ agents/.env 생성"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  셋업 완료! 사용 방법:"
echo ""
echo "  # Claude Code 실행"
echo "  cd agents && claude"
echo ""
echo "  # FastAPI 서버 실행"
echo "  cd agents && uvicorn api:app --host 0.0.0.0 --port 8000 --reload"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
