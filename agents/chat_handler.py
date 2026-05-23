"""Chat Handler — Claude + Forensic Tools.

사용자의 자연어 메시지를 받아 Claude가 적절한 포렌식 툴을 호출.
대화 히스토리 유지. 분석은 백그라운드로 실행 후 job_id 반환.

툴:
  run_forensic_analysis  — 종목 포렌식 분석 시작 (비동기)
  get_latest_result      — DB에서 최근 분석 결과 조회
  list_recent_analyses   — 최근 분석 종목 목록
"""

from __future__ import annotations

import json
import os
from typing import Any

import anthropic
import db

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ---------------------------------------------------------------------------
# 시스템 프롬프트
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """당신은 AI 인프라 섹터 포렌식 회계 분석 전문가입니다.
Chanos-Schilit 전통의 공매도 리서치를 수행합니다.

사용자 요청에 따라 적절한 툴을 호출하고, 결과를 한국어로 명확하게 설명하세요.

포렌식 점수 기준:
  🔴 Tier 1 (0–30점):   Active Short — 심각한 회계 red flag
  🟡 Tier 2 (31–55점):  Monitor — 주의 필요
  🟠 Tier 3 (56–70점):  Avoid — 불확실
  🟢 Tier 4 (71–100점): Archive — 특이사항 없음

분석 요청 시: run_forensic_analysis 툴을 호출하세요.
이미 분석된 결과 조회 시: get_latest_result 툴을 호출하세요.
분석 목록 요청 시: list_recent_analyses 툴을 호출하세요.

분석이 시작되면 "분석을 시작했습니다. 약 3–5분 소요됩니다." 라고 안내하세요.
결과가 있으면 점수, Tier, 주요 red flag를 간결하게 요약하세요."""

# ---------------------------------------------------------------------------
# 툴 정의
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "run_forensic_analysis",
        "description": (
            "지정한 종목의 포렌식 회계 분석을 백그라운드로 시작합니다. "
            "SEC EDGAR 10-K/10-Q/8-K/Form4/XBRL 데이터를 수집하고 "
            "6개 에이전트가 병렬 분석합니다. 완료까지 3–5분 소요. "
            "job_id를 반환합니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "분석할 미국 주식 티커 리스트. 예: ['NVDA', 'MSFT']",
                },
                "skip_orchestrator": {
                    "type": "boolean",
                    "description": "True면 Opus 오케스트레이터 스킵 (빠른 테스트용). 기본 False.",
                    "default": False,
                },
            },
            "required": ["tickers"],
        },
    },
    {
        "name": "get_latest_result",
        "description": (
            "DB에 저장된 특정 종목의 가장 최근 포렌식 분석 결과를 가져옵니다. "
            "이미 분석된 종목이라면 즉시 결과를 반환합니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "조회할 종목 티커. 예: 'NVDA'",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "list_recent_analyses",
        "description": "최근에 분석된 종목 목록과 포렌식 점수를 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "최대 반환 개수 (기본 10)",
                    "default": 10,
                },
            },
        },
    },
]

# ---------------------------------------------------------------------------
# 툴 실행
# ---------------------------------------------------------------------------

def _execute_tool(
    tool_name: str,
    tool_input: dict,
    start_analysis_fn,  # api.py의 백그라운드 분석 시작 함수
) -> tuple[str, str | None]:
    """툴 실행 → (결과 텍스트, job_id 또는 None)"""

    if tool_name == "run_forensic_analysis":
        tickers = [t.upper().strip() for t in tool_input.get("tickers", [])]
        skip = tool_input.get("skip_orchestrator", False)
        job_id = start_analysis_fn(tickers, skip)
        result_text = json.dumps({
            "status": "started",
            "job_id": job_id,
            "tickers": tickers,
            "message": f"{', '.join(tickers)} 분석이 시작됐습니다. job_id={job_id}",
        }, ensure_ascii=False)
        return result_text, job_id

    elif tool_name == "get_latest_result":
        ticker = tool_input.get("ticker", "").upper()
        row = db.get_latest_by_ticker(ticker)
        if not row:
            return json.dumps({"error": f"{ticker} 분석 결과 없음. 먼저 분석을 실행하세요."}, ensure_ascii=False), None
        # datetime 직렬화
        if hasattr(row.get("run_at"), "isoformat"):
            row["run_at"] = row["run_at"].isoformat()
        # agent_reports 축약
        reports = row.pop("agent_reports", [])
        row["agent_summaries"] = [
            {"agent": r.get("agent_key"), "score": r.get("score"), "summary": (r.get("summary") or "")[:200]}
            for r in reports
        ]
        return json.dumps(row, ensure_ascii=False, default=str), None

    elif tool_name == "list_recent_analyses":
        limit = tool_input.get("limit", 10)
        rows = db.get_results(limit=limit)
        summary = [
            {
                "ticker": r.get("ticker"),
                "score": r.get("forensic_score"),
                "tier": r.get("tier"),
                "tier_label": r.get("tier_label"),
                "run_at": r["run_at"].isoformat() if hasattr(r.get("run_at"), "isoformat") else str(r.get("run_at")),
            }
            for r in rows
        ]
        return json.dumps(summary, ensure_ascii=False), None

    return json.dumps({"error": f"알 수 없는 툴: {tool_name}"}), None


# ---------------------------------------------------------------------------
# 메인 처리 함수
# ---------------------------------------------------------------------------

def process_message(
    message: str,
    history: list[dict],
    start_analysis_fn,
) -> dict[str, Any]:
    """사용자 메시지 처리 → Claude 응답 반환.

    Args:
        message:           사용자 입력 텍스트
        history:           이전 대화 [{role, content}, ...]
        start_analysis_fn: job_id 생성 + 백그라운드 분석 시작 함수

    Returns:
        {
          "response":        Claude 텍스트 응답,
          "job_id":          분석 시작된 경우 job_id (없으면 None),
          "updated_history": 업데이트된 대화 히스토리,
        }
    """
    messages = list(history) + [{"role": "user", "content": message}]
    job_id: str | None = None

    # Claude 호출 (툴 루프)
    while True:
        response = _client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # 응답을 히스토리에 추가
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # 텍스트 응답 추출
            text = "".join(
                block.text for block in response.content
                if hasattr(block, "text")
            )
            return {
                "response": text,
                "job_id": job_id,
                "updated_history": messages,
            }

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result_text, jid = _execute_tool(
                    block.name, block.input, start_analysis_fn
                )
                if jid:
                    job_id = jid
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

            messages.append({"role": "user", "content": tool_results})
            # 루프 계속 — Claude가 툴 결과를 보고 최종 응답 생성
            continue

        # 예상치 못한 stop_reason
        break

    return {
        "response": "(응답 생성 실패)",
        "job_id": job_id,
        "updated_history": messages,
    }
