"""DB 레이어 — SQLite (기본) / Postgres (DATABASE_URL 설정 시).

SQLAlchemy Core를 사용해 동기 방식으로 작성.
(asyncio 환경에서는 run_sync / executor로 호출)

테이블:
  forensic_runs   — 실행 메타데이터
  agent_reports   — 각 서브에이전트 결과 (FK: run_id)

환경변수:
  DATABASE_URL    — 미설정 시 agents/ 디렉토리의 forensic.db (SQLite)
                    Postgres 예: postgresql://user:pass@host:5432/dbname
"""

from __future__ import annotations

import json
import os
import datetime
from pathlib import Path

from sqlalchemy import (
    create_engine, text,
    Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey,
    MetaData, Table, Index,
)
from sqlalchemy.engine import Engine

# ---------------------------------------------------------------------------
# Engine 초기화
# ---------------------------------------------------------------------------

def _make_engine() -> Engine:
    url = os.getenv("DATABASE_URL")
    if url:
        # Postgres: psycopg2 드라이버 (requirements에 추가)
        # Railway / Render 등이 postgresql:// 로 제공하는 경우 대응
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return create_engine(url, pool_pre_ping=True)
    else:
        # SQLite fallback — agents/ 디렉토리에 저장
        db_path = Path(__file__).parent / "forensic.db"
        return create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


engine: Engine = _make_engine()
metadata = MetaData()

# ---------------------------------------------------------------------------
# 테이블 정의
# ---------------------------------------------------------------------------

forensic_runs = Table(
    "forensic_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ticker",          String(16),  nullable=False, index=True),
    Column("run_at",          DateTime,    nullable=False, default=datetime.datetime.utcnow),
    Column("forensic_score",  Float,       nullable=True),   # 0(최악) ~ 100(최우량)
    Column("tier",            Integer,     nullable=True),   # 1~4
    Column("tier_label",      String(32),  nullable=True),
    Column("short_thesis",    Text,        nullable=True),   # Opus 최종 의견
    Column("skip_orchestrator", Boolean,   nullable=False, default=False),
    Column("weights_json",    Text,        nullable=True),   # DEFAULT_WEIGHTS JSON
    Column("error",           Text,        nullable=True),   # 분석 실패 시 에러 메시지
    Column("duration_sec",    Float,       nullable=True),
)

agent_reports = Table(
    "agent_reports",
    metadata,
    Column("id",        Integer, primary_key=True, autoincrement=True),
    Column("run_id",    Integer, ForeignKey("forensic_runs.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("agent_key", String(32),  nullable=False),   # "accruals", "revenue", …
    Column("score",     Float,       nullable=True),    # 0~100
    Column("flags_json",Text,        nullable=True),    # list[str] JSON
    Column("summary",   Text,        nullable=True),    # 에이전트 서술 요약
    Column("raw_json",  Text,        nullable=True),    # 전체 AgentReport JSON
)

Index("ix_agent_reports_run_agent", agent_reports.c.run_id, agent_reports.c.agent_key)

# ---------------------------------------------------------------------------
# 스키마 생성 (최초 실행 시)
# ---------------------------------------------------------------------------

def init_db() -> None:
    """테이블이 없으면 생성."""
    metadata.create_all(engine)


# ---------------------------------------------------------------------------
# CRUD 헬퍼
# ---------------------------------------------------------------------------

def save_result(result: dict) -> int:
    """ForensicResult dict를 DB에 저장하고 run_id 반환.

    result 구조 (orchestrator.py ForensicResult.__dict__ 기준):
      ticker, forensic_score, tier, tier_label, short_thesis,
      agent_reports (list[AgentReport dict]), skip_orchestrator,
      weights, error, duration_sec
    """
    init_db()  # 테이블 없으면 생성

    run_at = datetime.datetime.utcnow()
    weights = result.get("weights") or {}

    with engine.begin() as conn:
        ins = forensic_runs.insert().values(
            ticker            = result.get("ticker", "UNKNOWN"),
            run_at            = run_at,
            forensic_score    = result.get("forensic_score"),
            tier              = result.get("tier"),
            tier_label        = result.get("tier_label"),
            short_thesis      = result.get("short_thesis"),
            skip_orchestrator = bool(result.get("skip_orchestrator", False)),
            weights_json      = json.dumps(weights) if weights else None,
            error             = result.get("error"),
            duration_sec      = result.get("duration_sec"),
        )
        run_result = conn.execute(ins)
        run_id = run_result.inserted_primary_key[0]

        # 서브에이전트 리포트 저장
        for rep in result.get("agent_reports", []) or []:
            flags = rep.get("flags") or []
            conn.execute(agent_reports.insert().values(
                run_id     = run_id,
                agent_key  = rep.get("agent_key", ""),
                score      = rep.get("score"),
                flags_json = json.dumps(flags, ensure_ascii=False),
                summary    = rep.get("summary"),
                raw_json   = json.dumps(rep, ensure_ascii=False),
            ))

    return run_id


def get_results(ticker: str | None = None, limit: int = 50) -> list[dict]:
    """최근 분석 결과 조회. ticker 지정 시 해당 종목만."""
    init_db()
    with engine.connect() as conn:
        q = forensic_runs.select().order_by(forensic_runs.c.run_at.desc()).limit(limit)
        if ticker:
            q = q.where(forensic_runs.c.ticker == ticker.upper())
        rows = conn.execute(q).mappings().all()
        return [dict(r) for r in rows]


def get_run_detail(run_id: int) -> dict | None:
    """run_id 기준 상세 조회 (agent_reports 포함)."""
    init_db()
    with engine.connect() as conn:
        row = conn.execute(
            forensic_runs.select().where(forensic_runs.c.id == run_id)
        ).mappings().first()
        if not row:
            return None
        run = dict(row)

        reps = conn.execute(
            agent_reports.select().where(agent_reports.c.run_id == run_id)
        ).mappings().all()
        run["agent_reports"] = [dict(r) for r in reps]
        return run


def get_latest_by_ticker(ticker: str) -> dict | None:
    """특정 종목의 가장 최근 결과 (agent_reports 포함)."""
    init_db()
    with engine.connect() as conn:
        row = conn.execute(
            forensic_runs.select()
            .where(forensic_runs.c.ticker == ticker.upper())
            .order_by(forensic_runs.c.run_at.desc())
            .limit(1)
        ).mappings().first()
        if not row:
            return None
        run = dict(row)
        reps = conn.execute(
            agent_reports.select().where(agent_reports.c.run_id == run["id"])
        ).mappings().all()
        run["agent_reports"] = [dict(r) for r in reps]
        return run
