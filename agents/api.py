"""Forensic Pipeline — FastAPI 서버.

엔드포인트:
  GET   /                     — 채팅 UI (index.html)
  POST  /chat                 — Claude 채팅 (세션 유지, 툴 자동 실행)
  POST  /analyze              — 분석 요청 (비동기 실행)
  GET   /jobs/{job_id}        — 분석 상태 조회
  GET   /results              — 최근 분석 결과 목록
  GET   /results/{ticker}     — 특정 종목 최근 결과
  GET   /results/{ticker}/history — 분석 이력
  GET   /health               — 헬스체크

실행:
  uvicorn api:app --host 0.0.0.0 --port 8000 --reload

환경변수:
  ANTHROPIC_API_KEY  (필수)
  SEC_USER_AGENT     (필수)
  DATABASE_URL       (미설정 시 SQLite forensic.db)
  API_KEY            (미설정 시 인증 없음 — 프로덕션에서 반드시 설정)
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from dotenv import load_dotenv
load_dotenv()  # .env 파일 로드 (로컬 개발용)

from fastapi import FastAPI, BackgroundTasks, HTTPException, Header, Query
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import chat_handler

# ---------------------------------------------------------------------------
# In-memory 저장소
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}       # job_id → {status, ticker, run_id, error, started_at}
_sessions: dict[str, list] = {}   # session_id → 대화 히스토리 [{role, content}, ...]


# ---------------------------------------------------------------------------
# FastAPI 앱 초기화
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 DB 초기화."""
    db.init_db()
    yield

app = FastAPI(
    title="Forensic Accounting Pipeline",
    description=(
        "AI Infrastructure 주식 포렌식 회계 분석 API.\n\n"
        "Chanos-Schilit 전통의 공매도 후보 탐색."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Static 파일 서빙 (/static/...)
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ---------------------------------------------------------------------------
# API Key 인증 (선택적)
# ---------------------------------------------------------------------------

_API_KEY = os.getenv("API_KEY")  # 미설정 시 인증 비활성화


def _check_auth(x_api_key: str | None):
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key. Set X-API-Key header.")


# ---------------------------------------------------------------------------
# 요청/응답 모델
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=1, max_length=20, description="분석할 티커 목록 (최대 20개)")
    skip_orchestrator: bool = Field(False, description="True 시 Opus 오케스트레이터 건너뜀 (빠른 테스트용)")
    tier_max: int | None = Field(None, ge=1, le=4, description="이 Tier 이하만 DB 저장 (예: 2 = Tier 1~2만 저장)")


class JobStatus(BaseModel):
    job_id: str
    status: str       # "pending" | "running" | "completed" | "failed"
    tickers: list[str]
    started_at: float
    results: list[dict] | None = None
    error: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="사용자 메시지")
    session_id: str = Field(..., description="세션 ID (클라이언트에서 UUID 생성)")


class ChatResponse(BaseModel):
    response: str
    job_id: str | None = None
    session_id: str


# ---------------------------------------------------------------------------
# 백그라운드 분석 함수 (api.py 내부 사용 + chat_handler에 주입)
# ---------------------------------------------------------------------------

def _start_analysis(tickers: list[str], skip_orchestrator: bool = False) -> str:
    """job_id 생성 및 백그라운드 분석 예약. chat_handler에 주입할 함수."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "pending",
        "tickers": tickers,
        "started_at": time.time(),
        "results": None,
        "error": None,
    }

    req = AnalyzeRequest(tickers=tickers, skip_orchestrator=skip_orchestrator)

    async def _kick():
        await _run_analysis(job_id, req)

    # 현재 이벤트 루프에 태스크 등록
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_kick())
    except RuntimeError:
        # 루프가 없는 경우 (테스트 환경 등) — 무시
        pass

    return job_id


async def _run_analysis(job_id: str, request: AnalyzeRequest):
    """백그라운드에서 orchestrator.analyze_stock 실행."""
    _jobs[job_id]["status"] = "running"

    try:
        from orchestrator import analyze_stock, forensic_result_to_dict  # noqa: F401
    except Exception as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = f"orchestrator import error: {e}"
        return

    results = []
    for ticker in request.tickers:
        ticker = ticker.upper().strip()
        t0 = time.perf_counter()
        try:
            from orchestrator import analyze_stock, forensic_result_to_dict
            forensic_obj = await analyze_stock(
                ticker,
                skip_orchestrator=request.skip_orchestrator,
            )
            duration_sec = round(time.perf_counter() - t0, 1)
            # ForensicResult → DB 호환 dict 변환
            result = forensic_result_to_dict(
                forensic_obj,
                skip_orchestrator=request.skip_orchestrator,
                duration_sec=duration_sec,
            )
        except Exception as e:
            result = {
                "ticker":         ticker,
                "error":          str(e),
                "forensic_score": None,
                "tier":           None,
                "tier_label":     None,
                "duration_sec":   round(time.perf_counter() - t0, 1),
                "agent_reports":  [],
            }

        # Tier 필터
        tier = result.get("tier")
        should_save = (
            request.tier_max is None
            or tier is None
            or tier <= request.tier_max
        )
        if should_save:
            try:
                run_id = db.save_result(result)
                result["run_id"] = run_id
            except Exception as e:
                result["db_error"] = str(e)

        results.append(result)

    _jobs[job_id]["status"] = "completed"
    _jobs[job_id]["results"] = results


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def root():
    """채팅 UI로 리다이렉트."""
    return RedirectResponse(url="/static/index.html")


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """Claude 채팅 엔드포인트. 자연어로 분석 요청 가능.

    - 세션 히스토리 자동 유지 (session_id 기준)
    - Claude가 필요 시 분석 툴 자동 호출
    - 분석 시작 시 job_id 반환 → /jobs/{job_id} 로 폴링
    """
    # 세션 히스토리 로드 (없으면 빈 리스트)
    history = _sessions.get(req.session_id, [])

    try:
        result = chat_handler.process_message(
            message=req.message,
            history=history,
            start_analysis_fn=_start_analysis,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat processing error: {e}")

    # 세션 히스토리 업데이트 (최대 40턴 유지)
    updated_history = result["updated_history"]
    if len(updated_history) > 80:  # role당 1개 = 2개씩 → 40턴
        updated_history = updated_history[-80:]
    _sessions[req.session_id] = updated_history

    return ChatResponse(
        response=result["response"],
        job_id=result.get("job_id"),
        session_id=req.session_id,
    )


@app.post("/analyze", response_model=JobStatus, status_code=202)
async def analyze(
    req: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    x_api_key: Annotated[str | None, Header()] = None,
):
    """분석 요청을 받아 백그라운드에서 실행. job_id 즉시 반환."""
    _check_auth(x_api_key)

    job_id = str(uuid.uuid4())
    started_at = time.time()
    _jobs[job_id] = {
        "status": "pending",
        "tickers": [t.upper() for t in req.tickers],
        "started_at": started_at,
        "results": None,
        "error": None,
    }

    background_tasks.add_task(_run_analysis, job_id, req)

    return JobStatus(
        job_id=job_id,
        status="pending",
        tickers=_jobs[job_id]["tickers"],
        started_at=started_at,
    )


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(
    job_id: str,
    x_api_key: Annotated[str | None, Header()] = None,
):
    """분석 작업 상태 및 결과 조회."""
    _check_auth(x_api_key)
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(job_id=job_id, **job)


@app.get("/results")
def list_results(
    limit: int = Query(20, ge=1, le=200),
    x_api_key: Annotated[str | None, Header()] = None,
):
    """DB에 저장된 최근 분석 결과 목록."""
    _check_auth(x_api_key)
    rows = db.get_results(limit=limit)
    for r in rows:
        if hasattr(r.get("run_at"), "isoformat"):
            r["run_at"] = r["run_at"].isoformat()
    return {"count": len(rows), "results": rows}


@app.get("/results/{ticker}")
def get_ticker_result(
    ticker: str,
    x_api_key: Annotated[str | None, Header()] = None,
):
    """특정 종목의 가장 최근 분석 결과 (agent_reports 포함)."""
    _check_auth(x_api_key)
    row = db.get_latest_by_ticker(ticker.upper())
    if not row:
        raise HTTPException(status_code=404, detail=f"No result found for {ticker.upper()}")
    if hasattr(row.get("run_at"), "isoformat"):
        row["run_at"] = row["run_at"].isoformat()
    return row


@app.get("/results/{ticker}/history")
def get_ticker_history(
    ticker: str,
    limit: int = Query(10, ge=1, le=100),
    x_api_key: Annotated[str | None, Header()] = None,
):
    """특정 종목의 분석 이력 (score 추이 확인용)."""
    _check_auth(x_api_key)
    rows = db.get_results(ticker=ticker.upper(), limit=limit)
    for r in rows:
        if hasattr(r.get("run_at"), "isoformat"):
            r["run_at"] = r["run_at"].isoformat()
    return {"ticker": ticker.upper(), "count": len(rows), "history": rows}
