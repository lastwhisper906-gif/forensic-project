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
        r"\bpipeline\b", r"\bTCV\b", r"\btotal contract value\b",
        r"\bGMV\b", r"\bnormalized\b", r"\badjusted FCF\b",
        r"\bcore operating income\b",
    ],
}

# Hedging language — 증가하면 경보
HEDGING_VOCAB: dict[str, list[str]] = {
    "challenging": [
        r"\bchallenging\b", r"\bheadwind\b", r"\bpressured?\b",
        r"\bdifficult\b", r"\bsoft\b", r"\bweak(?:ness|er)?\b",
        r"\bslowdown\b", r"\bcautious(?:ly)?\b",
    ],
    "timing": [
        r"\blumpy\b", r"\btiming\b", r"\bpush[-\s]?out\b",
        r"\bdelay(?:ed|s)?\b", r"\bslip(?:page)?\b",
        r"\bback[-\s]?end(?:\s+loaded)?\b", r"\buneven\b",
    ],
    "transitory": [
        r"\btransitory\b", r"\btemporary\b", r"\bshort[-\s]?term\b",
        r"\bmomentary\b", r"\bone[-\s]?time\b", r"\bnon[-\s]?recurring\b",
    ],
    "macro": [
        r"\bmacro(?:economic)?\b", r"\buncertain(?:ty)?\b",
        r"\bvolatility\b", r"\binflationary\b", r"\binterest rate\b",
        r"\bmarket condition\b",
    ],
    "uncertainty": [
        r"\bwe\s+believe\b", r"\bwe\s+expect\b", r"\bwe\s+anticipate\b",
        r"\bcannot\s+guarantee\b", r"\bapproximately\b",
        r"\bsubject\s+to\s+change\b", r"\bmay\s+not\b",
        r"\bcould\s+adversely\b", r"\bright\s+direction\b",
        r"\bvisibility\s+(?:is\s+)?limited\b",
    ],
}

# Confidence markers — 감소하면 경보
CONFIDENCE_VOCAB: list[str] = [
    r"\bconfident\b", r"\bstrategically\s+positioned\b",
    r"\bstrong\s+demand\b", r"\brecord\b", r"\bexceptional\b",
    r"\bbest[-\s]ever\b", r"\bclear\s+visibility\b",
    r"\bstrong\s+execution\b",
]

# Q&A 회피 패턴
QA_EVASION_PATTERNS: list[dict[str, Any]] = [
    {
        "type":    "refusal_to_disclose",
        "pattern": re.compile(
            r"we\s+don'?t\s+(?:break|provide|disclose|give|share)\s+(?:that|it|the)?",
            re.I,
        ),
    },
    {
        "type":    "deflection",
        "pattern": re.compile(
            r"(?:take\s+that\s+offline|get\s+back\s+to\s+you|follow[\s-]up\s+later"
            r"|will\s+address\s+separately)",
            re.I,
        ),
    },
    {
        "type":    "question_reframing",
        "pattern": re.compile(
            r"I'?m\s+not\s+sure\s+(?:I\s+)?(?:fully\s+)?(?:understand|follow"
            r"|see|get)\s+(?:the\s+question|what\s+you'?re)",
            re.I,
        ),
    },
    {
        "type":    "circular_reference",
        "pattern": re.compile(
            r"(?:refer\s+you\s+to|as\s+(?:we\s+)?(?:mentioned|noted)\s+in\s+"
            r"(?:our\s+)?(?:press\s+release|10[-\s]?[KQ]|annual\s+report))",
            re.I,
        ),
    },
]

# 가이던스 품질 패턴
GUIDANCE_PATTERNS: dict[str, re.Pattern] = {
    "SPECIFIC":      re.compile(
        r"we\s+(?:expect|guide|target|project|forecast)\s+(?:[^\.\n]{0,40})"
        r"(?:\$[\d\.]+\s*(?:billion|million|B|M)\b|\d+[\.,]\d+%|\d+\s*(?:to|–|-)\s*\d+)",
        re.I,
    ),
    "RANGE_WIDENED": re.compile(
        r"we\s+(?:expect|anticipate)\s+(?:[^\.\n]{0,40})(?:wider|broader|expanded)\s+range",
        re.I,
    ),
    "WITHDRAWN":     re.compile(
        r"(?:not\s+providing|suspending|withdrawing|no\s+longer\s+providing|"
        r"cannot\s+provide)\s+(?:specific\s+)?guidance",
        re.I,
    ),
}

# Q&A 섹션 구분자
_QA_SPLIT_RE = re.compile(
    r"(?:Q&A|Question[-\s]and[-\s]Answer|Questions?\s+and\s+Answers?|"
    r"ANALYST\s+(?:Q&A|QUESTION)|Q\s*&\s*A\s*SESSION|QUESTION[-\s]ANSWER)",
    re.I,
)

# 질문 토픽 추출용 키워드
_TOPIC_KEYWORDS: list[tuple[str, re.Pattern]] = [
    ("revenue recognition",  re.compile(r"revenue\s+recogni[sz]", re.I)),
    ("customer concentration", re.compile(r"customer\s+concentrat|single\s+customer|top\s+customer", re.I)),
    ("guidance",              re.compile(r"\bguidance\b|\boutlook\b|\bforecast\b", re.I)),
    ("margins",               re.compile(r"\bmargin\b|\bgross\s+margin\b|\boperating\s+margin\b", re.I)),
    ("useful life",           re.compile(r"useful\s+life|depreciation\s+(?:period|polic)", re.I)),
    ("accounting change",     re.compile(r"accounting\s+(?:change|polic|method)", re.I)),
    ("capital allocation",    re.compile(r"buyback|dividend|capex|capital\s+alloc", re.I)),
    ("inventory",             re.compile(r"\binventory\b|\bstock\b|\bbacklog\b", re.I)),
    ("competition",           re.compile(r"competit(?:or|ion)|market\s+share", re.I)),
    ("regulation",            re.compile(r"regulat(?:ory|ion)|export\s+control|sanction", re.I)),
]


# ===========================================================================
# 2) 내부 헬퍼
# ===========================================================================

def _count_vocab(text: str, patterns: list[str]) -> int:
    """패턴 리스트에 매칭되는 총 횟수."""
    return sum(len(re.findall(p, text, re.I)) for p in patterns)


def _hedge_density(text: str) -> float:
    """hedge_count / word_count (0이면 0 반환)."""
    words = len(text.split())
    if words == 0:
        return 0.0
    total = 0
    for patterns in HEDGING_VOCAB.values():
        total += _count_vocab(text, patterns)
    return round(total / words, 4)


def _detect_guidance(text: str) -> str:
    """가이던스 품질 분류."""
    if GUIDANCE_PATTERNS["WITHDRAWN"].search(text):
        return "WITHDRAWN"
    if GUIDANCE_PATTERNS["RANGE_WIDENED"].search(text):
        return "RANGE_WIDENED"
    if GUIDANCE_PATTERNS["SPECIFIC"].search(text):
        return "SPECIFIC"
    return "ABSENT"


def _extract_question_topic(question_text: str) -> str:
    """Q&A 질문 토픽 추출."""
    for label, pat in _TOPIC_KEYWORDS:
        if pat.search(question_text):
            return label
    return "general"


def _detect_evasions(qa_text: str, quarter: str) -> list[dict]:
    """Q&A 섹션에서 회피 패턴 탐지."""
    evasions = []
    # 단순 분할 (응답 텍스트 블록별)
    blocks = re.split(r"\n(?=Management:|Response:|A:)", qa_text, flags=re.I)
    for block in blocks:
        for pat_info in QA_EVASION_PATTERNS:
            if pat_info["pattern"].search(block):
                # 근접 context에서 토픽 추출
                topic = _extract_question_topic(block)
                evasions.append({
                    "quarter":      quarter,
                    "evasion_type": pat_info["type"],
                    "question_topic": topic,
                })
                break  # 동일 블록 중복 탐지 방지
    return evasions


def _extract_quarter_label(text: str, idx: int) -> str:
    """텍스트에서 분기 레이블 추출 (Q3 2023 형태)."""
    m = re.search(
        r"(?:Q[1-4]\s+(?:FY)?20\d{2}|(?:FY)?20\d{2}\s+Q[1-4]"
        r"|(?:First|Second|Third|Fourth)\s+Quarter\s+20\d{2})",
        text[:500], re.I,
    )
    if m:
        return m.group(0).strip()
    return f"Period-{idx + 1}"


def _trend_label(values: list[float]) -> str:
    """값 목록의 전반적 추세 (INCREASING / DECREASING / STABLE)."""
    if len(values) < 2:
        return "STABLE"
    first_half = sum(values[: len(values) // 2]) / max(1, len(values) // 2)
    second_half = sum(values[len(values) // 2 :]) / max(1, len(values) - len(values) // 2)
    diff = second_half - first_half
    threshold = max(abs(first_half) * 0.05, 1e-6)
    if diff > threshold:
        return "INCREASING"
    if diff < -threshold:
        return "DECREASING"
    return "STABLE"


def _pct_change(old: float, new: float) -> float | None:
    """백분율 변화율."""
    if old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)


# ===========================================================================
# 3) 핵심 공개 API
# ===========================================================================

def analyze_earnings_calls(
    ticker: str,
    transcripts: list[str],
) -> dict[str, Any]:
    """분기별 transcript 리스트를 분석하여 포렌식 NLP 지표 반환.

    Args:
        ticker:      종목 코드
        transcripts: 분기별 transcript 텍스트 (오래된 것 먼저 또는 최신 먼저 모두 지원)

    Returns:
        분기별 + 추세 + 플래그 dict
    """
    by_quarter: list[dict] = []
    all_new_kpis: set[str] = set()
    new_kpi_first_quarter: str | None = None

    kpi_patterns_by_cat: dict[str, list[str]] = KPI_VOCAB

    for idx, text in enumerate(transcripts):
        if not text or not text.strip():
            continue

        label = _extract_quarter_label(text, idx)

        # KPI 카운트
        kpi_counts: dict[str, int] = {}
        for cat, patterns in kpi_patterns_by_cat.items():
            kpi_counts[cat] = _count_vocab(text, patterns)

        new_kpis_this_q = kpi_counts.get("new_kpis_to_watch", 0)
        if new_kpis_this_q > 0 and new_kpi_first_quarter is None:
            new_kpi_first_quarter = label

        # Hedging
        hd = _hedge_density(text)

        # Confidence
        conf = _count_vocab(text, CONFIDENCE_VOCAB)

        # Non-GAAP count (pattern 수)
        ng_count = kpi_counts.get("non_gaap", 0)

        # Q&A 섹션 분리
        qa_match = _QA_SPLIT_RE.search(text)
        qa_text = text[qa_match.start():] if qa_match else ""
        evasions = _detect_evasions(qa_text, label) if qa_text else []

        # 가이던스
        guidance = _detect_guidance(text)

        by_quarter.append({
            "quarter":       label,
            "kpis":          kpi_counts,
            "hedge_density": hd,
            "confidence":    conf,
            "non_gaap_count": ng_count,
            "evasions":      evasions,
            "guidance":      guidance,
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

    hedge_densities = [q["hedge_density"] for q in ordered]
    conf_counts     = [q["confidence"] for q in ordered]
    ng_counts       = [q["non_gaap_count"] for q in ordered]
    gaap_counts     = [q["kpis"]["gaap_core"] for q in ordered]
    non_gaap_counts = [q["kpis"]["non_gaap"] for q in ordered]
    new_kpi_counts  = [q["kpis"]["new_kpis_to_watch"] for q in ordered]

    hedge_trend = _trend_label(hedge_densities)
    conf_trend  = _trend_label([float(c) for c in conf_counts])
    ng_trend    = _trend_label([float(n) for n in ng_counts])
    gaap_trend  = _trend_label([float(g) for g in gaap_counts])

    oldest = ordered[0] if ordered else {}
    latest = ordered[-1] if ordered else {}

    hedge_delta_pct = _pct_change(
        oldest.get("hedge_density", 0),
        latest.get("hedge_density", 0),
    )
    conf_delta_pct = _pct_change(
        float(oldest.get("confidence", 0)),
        float(latest.get("confidence", 0)),
    )

    # Q&A 회피 집계
    all_evasions = [ev for q in ordered for ev in q.get("evasions", [])]
    evasion_by_type: dict[str, int] = defaultdict(int)
    for ev in all_evasions:
        evasion_by_type[ev["evasion_type"]] += 1

    # KPI 대체 감지: GAAP 감소 + Non-GAAP 증가 동시 발생
    kpi_substitution_detected = bool(
        len(ordered) >= 2
        and gaap_trend == "DECREASING"
        and ng_trend == "INCREASING"
    )

    flags = {
        "hedge_density_increasing":  hedge_trend == "INCREASING",
        "confidence_declining":      conf_trend == "DECREASING",
        "non_gaap_expanding":        ng_trend == "INCREASING",
        "kpi_substitution_detected": kpi_substitution_detected,
        "new_kpis_introduced":       new_kpi_first_quarter is not None,
        "new_kpi_first_quarter":     new_kpi_first_quarter or "N/A",
        "hedge_delta_over_15pct":    bool(hedge_delta_pct and hedge_delta_pct > 15),
        "qa_evasions_detected":      len(all_evasions) > 0,
        "qa_evasion_count":          len(all_evasions),
        "guidance_deteriorated":     latest.get("guidance", "ABSENT") in ("WITHDRAWN", "ABSENT"),
    }

    summary_lines = [
        f"[{ticker}] Earnings Call Language Analysis",
        f"분석 분기: {len(ordered)}개",
        f"Hedging 트렌드: {hedge_trend} | "
        + (f"누적 변화율: {hedge_delta_pct:+.1f}%" if hedge_delta_pct is not None else "누적 변화율: N/A"),
        f"Confidence 트렌드: {conf_trend}",
        f"Non-GAAP 트렌드: {ng_trend}",
        f"최신 가이던스 품질: {latest.get('guidance', 'ABSENT')}",
        "",
        "## Q&A 회피 패턴",
    ]
    for ev in all_evasions[:10]:
        summary_lines.append(
            f"  [{ev['quarter']}] {ev['evasion_type']} — 주제: {ev['question_topic']}"
        )

    if flags.get("kpi_substitution_detected"):
        summary_lines.append(
            "\n⚠️ KPI 대체 탐지: GAAP 지표 감소 + Non-GAAP 지표 증가"
        )
    if new_kpi_first_quarter:
        summary_lines.append(
            f"\n⚠️ 신규 KPI 첫 등장: {new_kpi_first_quarter} — forensic 주의"
        )

    return {
        "ticker":                  ticker,
        "quarters_analyzed":       len(ordered),
        "by_quarter":              ordered,
        "kpi_trends": {
            "gaap_core":  gaap_trend,
            "non_gaap":   ng_trend,
            "new_kpis":   _trend_label([float(n) for n in new_kpi_counts]),
        },
        "hedging_trend": {
            "trend":           hedge_trend,
            "hedge_delta_pct": hedge_delta_pct,
            "densities":       hedge_densities,
        },
        "confidence_trend": {
            "trend":          conf_trend,
            "conf_delta_pct": conf_delta_pct,
            "counts":         conf_counts,
        },
        "non_gaap_trend": {
            "trend":  ng_trend,
            "counts": ng_counts,
        },
        "qa_evasions":             all_evasions,
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
        "ticker":                  ticker,
        "quarters_analyzed":       0,
        "by_quarter":              [],
        "kpi_trends":              {},
        "hedging_trend":           {},
        "confidence_trend":        {},
        "non_gaap_trend":          {},
        "qa_evasions":             [],
        "qa_evasion_by_type":      {},
        "guidance_quality_latest": "ABSENT",
        "flags":                   {},
        "summary_text":            f"데이터 없음: {reason}",
        "data_source":             "none",
    }
