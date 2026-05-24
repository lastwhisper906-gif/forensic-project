"""call_metrics.py — Earnings Call / Release Language NLP 사전 계산 (Session 6).

LLM 없이 Python 정규식 + 카운팅으로 분기별 지표를 사전 계산.
Agent 5 (call_nlp) 가 이 결과를 받아 LLM으로 의미 해석.

데이터 소스 전략:
  A. 사용자 업로드 transcript 텍스트 (8~16분기)  ← 최우선
  B. SEC 8-K Item 2.02/7.01 earnings release (data_sources.sec_earnings_releases)
  두 경우 모두 동일한 분석 파이프라인 적용.

핵심 지표:
  1. KPI 빈도 트렌드 (GAAP vs Non-GAAP vs 신규 KPI)
  2. Hedging language 밀도 추이
  3. Confidence marker 빈도 추이
  4. Non-GAAP 지표 수 증가
  5. Q&A 회피 패턴 탐지
  6. 가이던스 품질 (구체적/범위확대/철회)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


# ===========================================================================
# 1) 어휘 사전
# ===========================================================================

# GAAP 핵심 KPI — 빠지면 red flag
KPI_VOCAB: dict[str, list[str]] = {
    "gaap_core": [
        r"\brevenue\b", r"\bnet income\b", r"\boperating income\b",
        r"\bgross profit\b", r"\boperating cash flow\b", r"\bearnings per share\b",
        r"\bEPS\b", r"\bEBIT\b",
    ],
    "non_gaap": [
        r"\badjusted EBITDA\b", r"\bnon[-\s]?GAAP\s+(?:EPS|earnings|operating|net income)\b",
        r"\bfree cash flow\b", r"\bFCF\b", r"\badjusted operating\b",
        r"\bnon[-\s]?GAAP\b", r"\badjusted\s+(?:gross\s+)?margin\b",
        r"\badjusted net income\b", r"\bcore\s+(?:earnings|revenue|income)\b",
    ],
    "new_kpis_to_watch": [
        r"\bannualized\s+(?:revenue\s+)?run[-\s]?rate\b", r"\bARR\b",
        r"\bbookings?\b", r"\bRPO\b", r"\bremaining performance obligations?\b",
        r"\bbacklog\b", r"\bcommitted\s+(?:contract\s+)?value\b",
        r"\bcontracted\s+future\s+revenue\b", r"\bnet\s+revenue\s+retention\b",
        r"\bNRR\b", r"\bgross\s+(?:retention|dollar)\b",
        r"\bpipeline\b", r"\bengagement\b", r"\bmomentum\b",
    ],
}

# Hedging language → 카테고리
HEDGING_VOCAB: dict[str, list[str]] = {
    "challenging": [
        r"\bchallenging\b", r"\btough(?:er)?\b", r"\bdifficult\b",
        r"\bheadwind\b", r"\bpressure\b",
    ],
    "timing": [
        r"\blumpy\b", r"\btiming\b", r"\bpush[-\s]?out\b", r"\bpushed\s+out\b",
        r"\bdelayed?\b", r"\bslipped?\b",
    ],
    "transitory": [
        r"\btransitory\b", r"\bone[-\s]?time\b", r"\btemporary\b",
        r"\bnormalize\b", r"\bnormalization\b", r"\banomalous\b",
    ],
    "macro": [
        r"\bmacro\b", r"\bmacroeconomic\b", r"\bcurrency\b",
        r"\bFX\s+impact\b", r"\bforeign\s+exchange\b",
    ],
    "uncertainty": [
        r"\bwe\s+believe\b", r"\bwe\s+expect\b", r"\bapproximately\b",
        r"\bwe\s+anticipate\b", r"\bmay\b", r"\bcould\b",
        r"\bsubject\s+to\b", r"\bcontingent\b", r"\bpreliminary\b",
        r"\bwe\s+(?:cannot|can't)\s+(?:guarantee|assure|promise)\b",
        r"\bno\s+assurance\b",
    ],
}

# Confidence markers (하락 추이 = 경고)
CONFIDENCE_MARKERS: list[str] = [
    r"\brecord\b", r"\bstrong(?:er)?\b", r"\brobust\b",
    r"\bexceptional\b", r"\bexceeded\b", r"\boutperformed\b",
    r"\boutstanding\b", r"\bsolid\b", r"\bhealthy\b",
    r"\bexcellent\b", r"\bbeating\s+expectations?\b",
]

# Q&A 회피 패턴
QA_EVASION_PATTERNS: list[tuple[str, str]] = [
    (r"(?:don'?t|do\s+not)\s+(?:break\s+that\s+out|break\s+out\s+that|disclose\s+that)",
     "refusal_to_disclose"),
    (r"(?:not\s+(?:going|able)\s+to\s+(?:comment|provide|give|break)\b)",
     "refusal_to_comment"),
    (r"(?:we'?ll?\s+(?:take\s+that\s+offline|get\s+back\s+to\s+you))",
     "deflection"),
    (r"(?:as\s+(?:we|I)\s+(?:mentioned|said|noted)\s+earlier\b)",
     "circular_reference"),
    (r"(?:(?:competitive|strategic)\s+reasons?\b.{0,30}(?:can'?t|cannot|won'?t|will\s+not)\s+share)",
     "competitive_excuse"),
    (r"(?:I'?m\s+not\s+sure\s+I\s+(?:understand|follow)\s+the\s+question)",
     "question_reframing"),
    (r"(?:going\s+forward\b.{0,50}(?:we'll|we\s+will|we\s+plan)\s+(?:to\s+)?(?:provide|share|disclose))",
     "future_promise_deflection"),
]

# 가이던스 품질 패턴
GUIDANCE_PATTERNS: dict[str, list[str]] = {
    "specific": [
        r"\bwe\s+(?:guide|expect|project|forecast)\s+(?:revenue|earnings|EPS).{0,20}\$[\d\.]+",
        r"\bour\s+(?:guidance|outlook)\s+(?:is|for)\s+(?:Q\d|fiscal|full\s+year).{0,20}\$[\d\.]+",
        r"\brange\s+of\s+\$[\d\.]+\s+(?:to|-)\s+\$[\d\.]+\b",
    ],
    "withdrawn": [
        r"\bnot\s+(?:providing|issuing|giving)\s+(?:guidance|outlook)\b",
        r"\bwithdrawn?\s+(?:our\s+)?(?:guidance|outlook)\b",
        r"\bpause.{0,20}guidance\b",
        r"\bsuspend.{0,20}guidance\b",
    ],
    "widening": [
        r"\bwider\s+(?:range|band)\b",
        r"\bincreased\s+uncertainty\b",
        r"\bbroad(?:er)?\s+range\b",
    ],
}


# ===========================================================================
# 2) 쿼터 분리 헬퍼
# ===========================================================================

def _split_into_quarters(texts: list[str]) -> list[dict[str, Any]]:
    """transcript 텍스트 리스트를 분기별 dict로 변환.

    입력 형식 두 가지:
      A) ["Q1 2024 Earnings Call...", "Q4 2023 Earnings Call..."]  — 분기별 별도 문자열
      B) 단일 긴 문자열에 날짜 헤더 포함 — 자동 분리 시도

    Returns:
        [{"quarter": "Q1 2024", "text": "...", "has_qa": bool}, ...]
        — 최신 순 정렬
    """
    quarters: list[dict[str, Any]] = []

    for i, text in enumerate(texts):
        if not text or not text.strip():
            continue

        # 쿼터/날짜 추출 시도
        qtr_label = _extract_quarter_label(text, fallback=f"Period_{i+1}")

        # Q&A 섹션 포함 여부
        has_qa = bool(re.search(
            r"(?:question[-\s]and[-\s]answer|Q&A|question\s+from|analyst\s+question)",
            text, re.I
        ))

        quarters.append({
            "quarter": qtr_label,
            "text": text,
            "has_qa": has_qa,
            "word_count": len(text.split()),
        })

    return quarters


def _extract_quarter_label(text: str, fallback: str = "Unknown") -> str:
    """텍스트에서 분기 레이블 추출 (예: 'Q3 2024')."""
    # "Q3 2024" 또는 "Third Quarter 2024" 패턴
    m = re.search(
        r"(?:Q[1-4]\s*[\-–]?\s*\d{4}|"
        r"(?:First|Second|Third|Fourth)\s+Quarter\s+\d{4}|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})",
        text[:500], re.I
    )
    if m:
        return m.group().strip()

    # "FY2024 Q3" 패턴
    m2 = re.search(r"FY\d{4}\s*Q[1-4]|Q[1-4]\s*FY\d{4}", text[:500], re.I)
    if m2:
        return m2.group().strip()

    return fallback


# ===========================================================================
# 3) 핵심 카운팅 함수들
# ===========================================================================

def _count_kpis(text: str) -> dict[str, int]:
    """KPI 카테고리별 언급 횟수."""
    counts: dict[str, int] = {}
    text_lower = text.lower()
    for cat, patterns in KPI_VOCAB.items():
        total = 0
        for pat in patterns:
            total += len(re.findall(pat, text_lower, re.I))
        counts[cat] = total
    return counts


def _count_hedging(text: str) -> dict[str, int]:
    """Hedging language 카테고리별 언급 횟수."""
    counts: dict[str, int] = {}
    for cat, patterns in HEDGING_VOCAB.items():
        total = 0
        for pat in patterns:
            total += len(re.findall(pat, text, re.I))
        counts[cat] = total
    counts["total"] = sum(counts.values())
    return counts


def _count_confidence(text: str) -> int:
    """Confidence marker 총 언급 횟수."""
    total = 0
    for pat in CONFIDENCE_MARKERS:
        total += len(re.findall(pat, text, re.I))
    return total


def _count_non_gaap_metrics(text: str) -> int:
    """비-GAAP 지표 종류 수 (중복 제외)."""
    found: set[str] = set()
    patterns = [
        r"non[-\s]?GAAP\s+\w+(?:\s+\w+)?",
        r"adjusted\s+\w+(?:\s+\w+)?(?:\s+\w+)?",
        r"(?:free\s+cash\s+flow|FCF)(?:\s+\w+)?",
        r"core\s+\w+(?:\s+\w+)?",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            term = re.sub(r"\s+", " ", m.group().lower().strip())
            # 너무 일반적인 단어 제외
            if len(term) > 6 and term not in {"adjusted revenue", "non-gaap"}:
                found.add(term[:40])
    return len(found)


def _detect_qa_evasions(text: str, quarter: str) -> list[dict[str, str]]:
    """Q&A 섹션에서 회피 패턴 탐지."""
    evasions: list[dict[str, str]] = []

    # Q&A 섹션만 추출
    qa_m = re.search(
        r"(?:question[-\s]and[-\s]answer|Q&A|QUESTION[-\s]AND[-\s]ANSWER)[^\n]*\n(.*)",
        text, re.I | re.DOTALL
    )
    qa_text = qa_m.group(1) if qa_m else text

    for pat, evasion_type in QA_EVASION_PATTERNS:
        for m in re.finditer(pat, qa_text, re.I):
            # 앞뒤 200자 컨텍스트 추출
            start = max(0, m.start() - 200)
            end   = min(len(qa_text), m.end() + 200)
            context = qa_text[start:end].replace("\n", " ").strip()

            # 어떤 주제에 대한 회피인지 추정
            topic = _infer_evasion_topic(context)

            evasions.append({
                "quarter":          quarter,
                "evasion_type":     evasion_type,
                "question_topic":   topic,
                "response_excerpt": context[:200],
            })

    return evasions


def _infer_evasion_topic(context: str) -> str:
    """회피 응답 컨텍스트에서 질문 주제 추정."""
    topic_patterns = [
        (r"useful\s+li(?:fe|ves)|depreciation|capex|capitaliz",   "capitalization/useful life"),
        (r"customer\s+concentration|largest\s+customer",          "customer concentration"),
        (r"margin|profitability|gross\s+profit",                   "margin/profitability"),
        (r"revenue\s+recognition|deferred|backlog|RPO",           "revenue recognition"),
        (r"SEC|investigation|inquiry|restatement",                 "regulatory/restatement"),
        (r"guidance|outlook|forecast",                             "forward guidance"),
        (r"insider|selling|option|stock",                          "insider transactions"),
        (r"related\s+party|affiliate|invest(?:ment|ed)",           "related party"),
        (r"inventory|channel|sell[-\s]through",                    "inventory/channel"),
    ]
    for pat, label in topic_patterns:
        if re.search(pat, context, re.I):
            return label
    return "unclassified"


def _detect_guidance_quality(text: str) -> str:
    """가이던스 품질: SPECIFIC | WIDENING | WITHDRAWN | ABSENT."""
    for q_type, patterns in GUIDANCE_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text, re.I):
                return q_type.upper()
    return "ABSENT"


# ===========================================================================
# 4) 트렌드 계산 헬퍼
# ===========================================================================

def _compute_trend(values: list[float | int]) -> str:
    """리스트 → INCREASING | STABLE | DECREASING."""
    if len(values) < 2:
        return "INSUFFICIENT_DATA"
    # 단순 선형 기울기
    n = len(values)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((xs[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    den = sum((x - mean_x) ** 2 for x in xs) or 1e-9
    slope = num / den
    threshold = mean_y * 0.05  # 5% 기울기 임계값
    if slope > threshold:
        return "INCREASING"
    if slope < -threshold:
        return "DECREASING"
    return "STABLE"


def _pct_change(old: float, new: float) -> float | None:
    """백분율 변화율."""
    if old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)


# ===========================================================================
# 5) 핵심 공개 API
# ===========================================================================

def analyze_earnings_calls(
    ticker: str,
    transcripts: list[str],
) -> dict[str, Any]:
    """분기별 earnings call/release 텍스트 분석.

    Args:
        ticker:      종목 코드
        transcripts: 분기별 텍스트 리스트 (최신 순 또는 오래된 순; 자동 정렬)
                     8-16개 권장.

    Returns:
        {
          "ticker": str,
          "quarters_analyzed": int,
          "kpi_trends": {...},
          "hedging_trend": {...},
          "confidence_trend": {...},
          "non_gaap_trend": {...},
          "qa_evasions": [...],
          "guidance_quality_latest": str,
          "flags": {...},
          "summary_text": str,
        }
    """
    if not transcripts:
        return _empty_call_result(ticker, "transcripts 없음")

    quarters = _split_into_quarters(transcripts)
    if not quarters:
        return _empty_call_result(ticker, "분기 분리 실패")

    # 분기별 지표 계산
    by_quarter: list[dict[str, Any]] = []
    for q in quarters:
        text = q["text"]
        wc   = max(q["word_count"], 1)

        kpi_counts  = _count_kpis(text)
        hedge_counts = _count_hedging(text)
        conf_count  = _count_confidence(text)
        ng_count    = _count_non_gaap_metrics(text)
        evasions    = _detect_qa_evasions(text, q["quarter"]) if q["has_qa"] else []
        guidance    = _detect_guidance_quality(text)

        hedge_density = hedge_counts["total"] / wc

        by_quarter.append({
            "quarter":        q["quarter"],
            "word_count":     wc,
            "has_qa":         q["has_qa"],
            "kpis":           kpi_counts,
            "hedge_counts":   hedge_counts,
            "hedge_density":  round(hedge_density, 5),
            "confidence":     conf_count,
            "non_gaap_count": ng_count,
            "evasions":       evasions,
            "guidance":       guidance,
        })

    # 시계열 트렌드: 분기 레이블에서 연도+분기 추출하여 오래된 순 정렬
    # 추출 실패 시 원본 입력 순서 유지 (뒤집지 않음)
    def _qtr_sort_key(q: dict) -> tuple:
        label = q.get("quarter", "")
        # "Q3 2023" → (2023, 3)
        m = re.search(r"(\d{4})[^\d]*Q?([1-4])|Q([1-4])[^\d]*(\d{4})", label, re.I)
        if m:
            g = m.groups()
            year = int(g[0] or g[3] or 0)
            qnum = int(g[1] or g[2] or 0)
            return (year, qnum)
        return (0, 0)

    sorted_quarters = sorted(by_quarter, key=_qtr_sort_key)
    # 모두 (0,0)이면 정렬 실패 → 원본 순서 유지
    if all(_qtr_sort_key(q) == (0, 0) for q in sorted_quarters):
        ordered = list(by_quarter)   # 입력 그대로
    else:
        ordered = sorted_quarters    # 오래된 순 (ascending)

    hedge_densities  = [q["hedge_density"] for q in ordered]
    conf_counts      = [q["confidence"] for q in ordered]
    ng_counts        = [q["non_gaap_count"] for q in ordered]
    gaap_counts      = [q["kpis"]["gaap_core"] for q in ordered]
    non_gaap_counts  = [q["kpis"]["non_gaap"] for q in ordered]
    new_kpi_counts   = [q["kpis"]["new_kpis_to_watch"] for q in ordered]

    # KPI 비율 트렌드: Non-GAAP / GAAP 비율
    ng_gaap_ratios = [
        (ng / max(gc, 1)) for ng, gc in zip(non_gaap_counts, gaap_counts)
    ]

    hedge_trend       = _compute_trend(hedge_densities)
    confidence_trend  = _compute_trend(conf_counts)
    non_gaap_trend    = _compute_trend(ng_counts)
    ng_gaap_trend     = _compute_trend(ng_gaap_ratios)

    # Oldest → Latest 변화율
    oldest = ordered[0] if ordered else {}
    latest = ordered[-1] if ordered else {}

    hedge_delta_pct = _pct_change(
        oldest.get("hedge_density", 0),
        latest.get("hedge_density", 0),
    )
    conf_delta_pct = _pct_change(
        oldest.get("confidence", 0),
        latest.get("confidence", 0),
    )

    # Q&A 회피 집계
    all_evasions = [ev for q in ordered for ev in q.get("evasions", [])]
    evasion_by_type: dict[str, int] = defaultdict(int)
    for ev in all_evasions:
        evasion_by_type[ev["evasion_type"]] += 1

    # 신규 KPI 도입 여부
    new_kpi_first_seen_quarter: str | None = None
    for q in ordered:
        if q["kpis"]["new_kpis_to_watch"] > 0:
            new_kpi_first_seen_quarter = q["quarter"]
            break

    kpi_substitution = (
        non_gaap_counts[-1] > gaap_counts[-1]
        and (len(ordered) < 2 or non_gaap_counts[-1] > non_gaap_counts[0])
    ) if len(ordered) >= 1 else False

    # 플래그 결정
    flags: dict[str, bool | str] = {
        "hedge_density_increasing":  hedge_trend == "INCREASING",
        "hedge_delta_over_15pct":    bool(hedge_delta_pct and hedge_delta_pct > 15),
        "confidence_declining":      confidence_trend == "DECREASING",
        "non_gaap_expanding":        non_gaap_trend == "INCREASING",
        "kpi_substitution_detected": kpi_substitution,
        "new_kpis_introduced":       new_kpi_first_seen_quarter is not None,
        "new_kpi_first_quarter":     new_kpi_first_seen_quarter or "N/A",
        "guidance_withdrawn":        latest.get("guidance") == "WITHDRAWN",
        "guidance_widening":         latest.get("guidance") == "WIDENING",
        "qa_evasions_detected":      len(all_evasions) > 0,
        "qa_evasion_count":          len(all_evasions),
    }

    # 요약 텍스트 (LLM 컨텍스트용)
    summary_lines = [
        f"## Agent 5 사전 계산 — {ticker}",
        f"분석 분기: {len(ordered)}개",
        f"\n### KPI 트렌드",
        f"Non-GAAP / GAAP 비율 트렌드: {ng_gaap_trend}",
        f"Non-GAAP 지표 수 트렌드: {non_gaap_trend}",
        f"신규 KPI 도입: {'예 (첫 등장 ' + (new_kpi_first_seen_quarter or 'N/A') + ')' if flags['new_kpis_introduced'] else '없음'}",
        f"\n### Hedging Language 트렌드",
        f"밀도 트렌드: {hedge_trend}",
        f"최신 밀도: {latest.get('hedge_density', 0):.4f}",
        f"누적 변화율: {hedge_delta_pct:+.1f}%" if hedge_delta_pct is not None else "누적 변화율: N/A",
        f"\n### Confidence Marker 트렌드",
        f"트렌드: {confidence_trend}",
        f"최신 카운트: {latest.get('confidence', 'N/A')}",
        f"\n### 가이던스 품질 (최신 분기)",
        f"{latest.get('guidance', 'ABSENT')}",
        f"\n### Q&A 회피 패턴",
        f"총 {len(all_evasions)}건 탐지" if all_evasions else "탐지 없음",
    ]
    if evasion_by_type:
        for t, cnt in sorted(evasion_by_type.items(), key=lambda x: -x[1]):
            summary_lines.append(f"  {t}: {cnt}건")

    summary_lines.append("\n### 🚩 플래그 요약")
    for flag_name, val in flags.items():
        if val and val is not False and val != "N/A":
            summary_lines.append(f"  ⚑ {flag_name}: {val}")

    return {
        "ticker":                 ticker,
        "quarters_analyzed":      len(ordered),
        "by_quarter":             by_quarter,
        "kpi_trends": {
            "non_gaap_trend":        non_gaap_trend,
            "ng_gaap_ratio_trend":   ng_gaap_trend,
            "new_kpi_first_quarter": new_kpi_first_seen_quarter,
            "latest_gaap_count":     latest.get("kpis", {}).get("gaap_core"),
            "latest_non_gaap_count": latest.get("kpis", {}).get("non_gaap"),
            "latest_new_kpi_count":  latest.get("kpis", {}).get("new_kpis_to_watch"),
        },
        "hedging_trend": {
            "trend":             hedge_trend,
            "hedge_delta_pct":   hedge_delta_pct,
            "latest_density":    latest.get("hedge_density"),
            "oldest_density":    oldest.get("hedge_density"),
            "by_category_latest": latest.get("hedge_counts", {}),
        },
        "confidence_trend": {
            "trend":           confidence_trend,
            "conf_delta_pct":  conf_delta_pct,
            "latest_count":    latest.get("confidence"),
        },
        "non_gaap_trend": {
            "trend":              non_gaap_trend,
            "latest_count":       latest.get("non_gaap_count"),
            "oldest_count":       oldest.get("non_gaap_count"),
        },
        "qa_evasions":             all_evasions[:10],  # 최대 10건
        "qa_evasion_by_type":      dict(evasion_by_type),
        "guidance_quality_latest": latest.get("guidance", "ABSENT"),
        "flags":                   flags,
        "summary_text":            "\n".join(summary_lines),
    }


def agent5_precomputed(
    ticker: str,
    transcripts: list[str] | None = None,
    earnings_releases_raw: dict | None = None,
) -> dict[str, Any]:
    """Agent 5 사전 계산 컨텍스트 패키지.

    Args:
        ticker:               종목 코드
        transcripts:          분기별 전체 transcript 텍스트 리스트 (우선)
        earnings_releases_raw: data_sources.sec_earnings_releases() 반환값 (fallback)

    Returns:
        analyze_earnings_calls() 결과 + agent 메타데이터
    """
    texts: list[str] = []

    # 우선순위 1: 사용자 업로드 transcript
    if transcripts:
        texts = [t for t in transcripts if t and t.strip()]

    # 우선순위 2: SEC 8-K earnings releases
    elif earnings_releases_raw:
        raw = earnings_releases_raw
        if isinstance(raw, dict):
            releases = raw.get("releases", raw.get("results", []))
        elif isinstance(raw, list):
            releases = raw
        else:
            releases = []

        for r in releases:
            if isinstance(r, dict):
                text = r.get("text", "") or r.get("content", "") or str(r)
            else:
                text = str(r)
            if text.strip():
                texts.append(text)

    if not texts:
        return _empty_call_result(ticker, "데이터 없음")

    result = analyze_earnings_calls(ticker, texts)
    result["data_source"] = "user_transcripts" if transcripts else "sec_8k_releases"
    return result


def _empty_call_result(ticker: str, reason: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "quarters_analyzed": 0,
        "by_quarter": [],
        "kpi_trends": {},
        "hedging_trend": {},
        "confidence_trend": {},
        "non_gaap_trend": {},
        "qa_evasions": [],
        "qa_evasion_by_type": {},
        "guidance_quality_latest": "ABSENT",
        "flags": {},
        "summary_text": f"데이터 없음: {reason}",
        "data_source": "none",
    }
