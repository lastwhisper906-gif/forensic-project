"""catalyst_monitor.py — Catalyst & Personnel Monitor 사전 계산 (Session 6).

LLM 없이 Python으로 SEC 공시 이벤트를 분류하고 우선순위를 계산.
Agent 6 (catalyst) 가 이 결과를 받아 LLM으로 의미 해석.

모니터링 대상:
  1. 8-K Item 4.02 — Non-Reliance (잠재적 회계 재작성)          ★★★★★
  2. 8-K Item 8.01 — Other (SEC inquiry, DOJ, class action)     ★★★★★
  3. 8-K Item 4.01 — Auditor change                            ★★★★
  4. 8-K Item 5.02 — CFO/CEO/CAO departure                     ★★★★
  5. CORRESP/UPLOAD — SEC comment letter                        ★★★
  6. Form 4 — Insider sales cluster                             ★★
  7. DEF 14A — Audit committee 변경                             ★

비용 최적화:
  data_sources.py 함수들은 이미 async로 구현됨.
  catalyst_monitor는 sync 래퍼로 제공하고, orchestrator가 async로 호출.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


# ===========================================================================
# 1) 우선순위 테이블
# ===========================================================================

CATALYST_SEVERITY: dict[str, int] = {
    "8-K Item 4.02 Non-Reliance":          100,
    "8-K Item 8.01 SEC Investigation":      95,
    "8-K Item 8.01 DOJ Investigation":      95,
    "8-K Item 8.01 Class Action":           90,
    "8-K Item 8.01 Internal Investigation": 85,
    "8-K Item 4.01 Auditor Change":         85,
    "8-K Item 5.02 CFO Departure":          80,
    "8-K Item 5.02 CEO Departure":          78,
    "8-K Item 5.02 CAO Departure":          75,
    "8-K Item 5.02 CFO+Auditor Same Year":  95,   # 복합 신호
    "8-K Item 2.06 Material Impairment":    70,
    "CORRESP Active Thread":                65,
    "CORRESP Repeated Issue":               75,
    "Form 4 Cluster 10+ Sales":            60,
    "Form 4 Cluster 5+ Sales":             45,
    "Form 4 CFO/CEO Large Sale":            55,
    "Form 4 Routine 10b5-1":               10,
}

# 8-K Item → 의미 매핑
_8K_ITEM_DESCRIPTIONS: dict[str, str] = {
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "5.02": "Departure/Appointment of Principal Officers",
    "8.01": "Other Events (SEC inquiry, legal proceedings, etc.)",
    "2.06": "Material Impairments",
    "2.02": "Results of Operations / Earnings Release",
    "7.01": "Regulation FD Disclosure",
}

# 고위험 키워드 (8-K Item 8.01 내용 분류용)
_SEC_INQUIRY_KEYWORDS = [
    r"SEC\s+(?:inquiry|investigation|subpoena|enforcement|Division\s+of\s+Enforcement)",
    r"Department\s+of\s+Justice|DOJ\b",
    r"class\s+action|securities\s+fraud\s+(?:lawsuit|litigation)",
    r"whistleblower",
    r"material\s+weakness",
    r"internal\s+investigation",
    r"restatement|restated",
    r"going\s+concern",
]

# CFO/CEO/CAO 역할 키워드
_OFFICER_PATTERNS: dict[str, list[str]] = {
    "CFO": [
        r"Chief\s+Financial\s+Officer",
        r"\bCFO\b",
        r"principal\s+financial\s+officer",
        r"principal\s+accounting\s+officer",
    ],
    "CEO": [
        r"Chief\s+Executive\s+Officer",
        r"\bCEO\b",
        r"principal\s+executive\s+officer",
    ],
    "CAO": [
        r"Chief\s+Accounting\s+Officer",
        r"\bCAO\b",
        r"Controller\b",
        r"Chief\s+Audit\s+Officer",
    ],
    "General_Counsel": [
        r"General\s+Counsel",
        r"Chief\s+Legal\s+Officer",
        r"\bCLO\b",
    ],
}

# 이직 사유 "good vs bad" 분류
_VOLUNTARY_DEPARTURE_PATTERNS = [
    r"to\s+pursue\s+other\s+(?:interests?|opportunities?)",
    r"to\s+spend\s+more\s+time\s+with\s+(?:his|her|their)\s+family",
    r"for\s+personal\s+reasons",
    r"step(?:ping|ped)\s+down",
    r"resign(?:ing|ed|ation)",
]
_PLANNED_DEPARTURE_PATTERNS = [
    r"retirement|retired",
    r"planned\s+(?:transition|succession)",
    r"previously\s+announced",
]


# ===========================================================================
# 2) 8-K 분류 헬퍼
# ===========================================================================

@dataclass
class CatalystEvent:
    """단일 catalyst 이벤트."""
    date:           str              # "YYYY-MM-DD"
    filing_type:    str              # "8-K", "CORRESP", "Form4"
    item:           str | None       # "4.02", "5.02", etc.
    severity:       int              # 0~100
    category:       str              # CATALYST_SEVERITY 키
    summary:        str              # 1~2줄 요약
    url:            str | None = None
    follow_up:      list[str] = field(default_factory=list)
    officer_role:   str | None = None
    is_discretionary: bool = False   # Form 4: 10b5-1 비해당 여부


def _classify_8k_item_801(text: str) -> str:
    """8-K Item 8.01 내용에서 구체적 카테고리 분류."""
    for pat in _SEC_INQUIRY_KEYWORDS:
        if re.search(pat, text, re.I):
            if re.search(r"SEC\b|enforcement|subpoena", pat, re.I):
                return "8-K Item 8.01 SEC Investigation"
            if re.search(r"DOJ|Justice", pat, re.I):
                return "8-K Item 8.01 DOJ Investigation"
            if re.search(r"class\s+action", pat, re.I):
                return "8-K Item 8.01 Class Action"
            if re.search(r"internal", pat, re.I):
                return "8-K Item 8.01 Internal Investigation"
    return "8-K Item 8.01 Other Event"


def _extract_officer_role(text: str) -> str | None:
    """공시 텍스트에서 임원 역할 추출."""
    for role, patterns in _OFFICER_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text, re.I):
                return role
    return None


def _classify_departure_reason(text: str) -> str:
    """이직 사유 분류: VOLUNTARY | PLANNED | UNKNOWN."""
    if any(re.search(p, text, re.I) for p in _VOLUNTARY_DEPARTURE_PATTERNS):
        return "VOLUNTARY_VAGUE"
    if any(re.search(p, text, re.I) for p in _PLANNED_DEPARTURE_PATTERNS):
        return "PLANNED"
    return "UNKNOWN"


def _is_10b5_1_plan(text: str) -> bool:
    """Form 4에서 10b5-1 사전 계획 매도 여부 확인."""
    return bool(re.search(r"10b5[-\s]?1|Rule\s+10b5[-\s]?1", text, re.I))


def _days_ago(date_str: str) -> int | None:
    """날짜 문자열 → 오늘로부터 경과 일수."""
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return (datetime.now() - dt).days
    except (ValueError, TypeError):
        return None


# ===========================================================================
# 3) SEC 8-K 파싱
# ===========================================================================

def _parse_8k_events(
    raw: dict | list | str,
    lookback_days: int,
) -> list[CatalystEvent]:
    """data_sources.sec_8k_items() 반환값 → CatalystEvent 리스트."""
    events: list[CatalystEvent] = []

    if isinstance(raw, str):
        # 문자열인 경우 간단히 처리
        raw = {"items": [{"summary": raw, "item": "8.01", "date": "unknown"}]}

    filings: list[dict] = []
    if isinstance(raw, list):
        filings = raw
    elif isinstance(raw, dict):
        filings = (
            raw.get("filings") or raw.get("items") or
            raw.get("results") or raw.get("events") or []
        )

    for filing in filings[:50]:   # 최대 50개
        if not isinstance(filing, dict):
            continue

        filed_date = filing.get("date") or filing.get("filed") or ""
        # lookback_days 필터
        days_old = _days_ago(filed_date)
        if days_old is not None and days_old > lookback_days:
            continue

        item  = str(filing.get("item", "") or filing.get("item_no", "") or "").strip()
        text  = str(filing.get("text", "") or filing.get("content", "") or
                   filing.get("summary", "") or filing.get("description", ""))
        url   = filing.get("url") or filing.get("link")

        if not item:
            continue

        event = _classify_8k_filing(item, text, filed_date, url)
        if event:
            events.append(event)

    return events


def _classify_8k_filing(
    item: str,
    text: str,
    date: str,
    url: str | None,
) -> CatalystEvent | None:
    """단일 8-K Item → CatalystEvent."""
    item_clean = item.strip().lstrip("0")  # "04.02" → "4.02"

    if item_clean == "4.02":
        return CatalystEvent(
            date=date, filing_type="8-K", item="4.02",
            severity=CATALYST_SEVERITY["8-K Item 4.02 Non-Reliance"],
            category="8-K Item 4.02 Non-Reliance",
            summary=f"Non-Reliance on Previously Issued Financial Statements: {text[:150]}",
            url=url,
            follow_up=["즉시 10-Q/10-K 재작성 여부 확인", "SEC CORRESP 조회"],
        )

    if item_clean == "4.01":
        return CatalystEvent(
            date=date, filing_type="8-K", item="4.01",
            severity=CATALYST_SEVERITY["8-K Item 4.01 Auditor Change"],
            category="8-K Item 4.01 Auditor Change",
            summary=f"Auditor Change: {text[:150]}",
            url=url,
            follow_up=["신규 감사인 평판 조회", "동시 CFO 변경 여부 확인"],
        )

    if item_clean == "5.02":
        role   = _extract_officer_role(text) or "Unknown Officer"
        reason = _classify_departure_reason(text)

        # CFO + CAO 가 가장 위험
        if role == "CFO":
            sev = CATALYST_SEVERITY["8-K Item 5.02 CFO Departure"]
        elif role == "CEO":
            sev = CATALYST_SEVERITY["8-K Item 5.02 CEO Departure"]
        elif role == "CAO":
            sev = CATALYST_SEVERITY["8-K Item 5.02 CAO Departure"]
        else:
            sev = 40

        # 사유가 모호하면 +10
        if reason == "VOLUNTARY_VAGUE":
            sev = min(100, sev + 10)

        return CatalystEvent(
            date=date, filing_type="8-K", item="5.02",
            severity=sev,
            category=f"8-K Item 5.02 {role} Departure",
            summary=f"{role} departure (reason: {reason}): {text[:120]}",
            url=url,
            officer_role=role,
            follow_up=[
                f"다음 10-Q에서 {role} 공백 기간 + 재작성 위험 확인",
                "12개월 내 감사인 변경 여부 교차 확인",
            ],
        )

    if item_clean == "8.01":
        category = _classify_8k_item_801(text)
        sev = CATALYST_SEVERITY.get(category, 60)
        return CatalystEvent(
            date=date, filing_type="8-K", item="8.01",
            severity=sev,
            category=category,
            summary=f"{category}: {text[:150]}",
            url=url,
            follow_up=["SEC EDGAR CORRESP 조회", "관련 법률 동향 추적"],
        )

    if item_clean == "2.06":
        return CatalystEvent(
            date=date, filing_type="8-K", item="2.06",
            severity=CATALYST_SEVERITY.get("8-K Item 2.06 Material Impairment", 70),
            category="8-K Item 2.06 Material Impairment",
            summary=f"Material Impairment: {text[:150]}",
            url=url,
            follow_up=["관련 자산 goodwill 공시 확인", "ROIC 재계산"],
        )

    return None


# ===========================================================================
# 4) CORRESP 파싱
# ===========================================================================

def _parse_corresp(
    raw: dict | list | str,
    lookback_days: int,
) -> list[CatalystEvent]:
    """data_sources.sec_corresp() 반환값 → CatalystEvent 리스트."""
    events: list[CatalystEvent] = []

    if isinstance(raw, str):
        if not raw.strip() or "no active correspondence" in raw.lower():
            return []
        # 문자열이면 active로 간주
        raw = {"letters": [{"date": "unknown", "summary": raw[:200]}]}

    letters: list[dict] = []
    if isinstance(raw, list):
        letters = raw
    elif isinstance(raw, dict):
        letters = (
            raw.get("letters") or raw.get("results") or
            raw.get("corresp") or raw.get("filings") or []
        )

    # 반복 이슈 탐지: 같은 주제에 대해 2개 이상 서신
    topic_counts: dict[str, int] = {}
    for letter in letters[:20]:
        if not isinstance(letter, dict):
            continue
        date = letter.get("date", "unknown")
        days_old = _days_ago(date)
        if days_old is not None and days_old > lookback_days:
            continue

        text = str(letter.get("text", "") or letter.get("summary", "") or letter.get("content", ""))
        url  = letter.get("url")

        # 주제 분류
        topic = _classify_corresp_topic(text)
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

        sev = CATALYST_SEVERITY["CORRESP Active Thread"]
        events.append(CatalystEvent(
            date=date, filing_type="CORRESP", item=None,
            severity=sev,
            category="CORRESP Active Thread",
            summary=f"SEC Comment Letter — Topic: {topic}: {text[:120]}",
            url=url,
            follow_up=["서신 원문 전체 검토", "응답 기한 및 이슈 반복 여부 확인"],
        ))

    # 반복 이슈 → 심각도 상향
    for event in events:
        topic = _classify_corresp_topic(event.summary)
        if topic_counts.get(topic, 0) >= 2:
            event.severity = CATALYST_SEVERITY["CORRESP Repeated Issue"]
            event.category = "CORRESP Repeated Issue"
            event.follow_up.insert(0, "⚠️ SEC가 동일 이슈 반복 질문 = 답변 불만족")

    return events


def _classify_corresp_topic(text: str) -> str:
    """CORRESP 서신 주제 분류."""
    topic_patterns = [
        (r"revenue\s+recognition|ASC\s+606|performance\s+obligation", "revenue_recognition"),
        (r"capitali[sz]ation|useful\s+life|depreciation",              "capitalization"),
        (r"non[-\s]?GAAP|non[-\s]?GAAP\s+measure",                   "non_gaap_metrics"),
        (r"segment|reportable\s+segment",                              "segment_reporting"),
        (r"related\s+party|VIE|variable\s+interest",                   "related_party"),
        (r"goodwill|impairment\s+test",                                "goodwill_impairment"),
        (r"deferred\s+(?:revenue|tax)",                                "deferred_items"),
        (r"accounting\s+(?:policy|change|estimate)",                   "accounting_policy"),
        (r"internal\s+control|material\s+weakness|ICFR",              "internal_controls"),
    ]
    for pat, label in topic_patterns:
        if re.search(pat, text, re.I):
            return label
    return "other"


# ===========================================================================
# 5) Form 4 파싱
# ===========================================================================

def _parse_form4(
    raw: dict | list | str,
    lookback_days: int,
) -> dict[str, Any]:
    """data_sources.sec_form4() 반환값 → insider pattern 분석."""
    sales: list[dict] = []
    buys:  list[dict] = []

    if isinstance(raw, str):
        # 문자열 파싱 시도
        raw = {"transactions": []}

    transactions: list[dict] = []
    if isinstance(raw, list):
        transactions = raw
    elif isinstance(raw, dict):
        transactions = (
            raw.get("transactions") or raw.get("results") or
            raw.get("filings") or raw.get("form4") or []
        )

    for txn in transactions[:100]:
        if not isinstance(txn, dict):
            continue

        date = txn.get("date", "unknown")
        days_old = _days_ago(date)
        if days_old is not None and days_old > lookback_days:
            continue

        txn_type = str(txn.get("type", "") or txn.get("transaction_type", "")).upper()
        shares    = float(txn.get("shares", 0) or txn.get("amount", 0) or 0)
        price     = float(txn.get("price", 0) or 0)
        value     = shares * price if price > 0 else float(txn.get("value", 0) or 0)
        role      = str(txn.get("role", "") or txn.get("title", "") or txn.get("officer", ""))
        text      = str(txn.get("text", "") or txn.get("description", "") or str(txn))
        plan_10b5 = _is_10b5_1_plan(text) or bool(txn.get("is_10b5_1"))

        record = {
            "date": date, "type": txn_type, "shares": shares,
            "value_usd": value, "role": role,
            "is_10b5_1": plan_10b5,
            "is_discretionary": (
                "S" in txn_type and
                not plan_10b5 and
                not re.search(r"\bgift\b|\baward\b|\bgrant\b", text, re.I)
            ),
        }

        if "S" in txn_type or "SALE" in txn_type or txn.get("is_sale"):
            sales.append(record)
        elif "P" in txn_type or "PURCHASE" in txn_type or "BUY" in txn_type or txn.get("is_buy"):
            buys.append(record)

    # 집계
    total_sales_usd   = sum(s["value_usd"] for s in sales)
    disc_sales        = [s for s in sales if s["is_discretionary"]]
    disc_sales_usd    = sum(s["value_usd"] for s in disc_sales)
    cfo_ceo_sales     = [s for s in sales if re.search(
        r"\bCFO\b|\bCEO\b|\bCAO\b"
        r"|\bChief\s+(?:Financial|Executive|Accounting)\s+Officer\b",
        s["role"], re.I,
    )]
    selling_roles     = list({s["role"] for s in disc_sales if s["role"]})

    # 동일 1~2주 내 복수 임원 매도 클러스터 탐지
    cluster_detected = False
    if len(disc_sales) >= 3:
        dates_with_role = [
            (s["date"], s["role"]) for s in disc_sales
            if s["date"] and s["date"] != "unknown"
        ]
        dates_only = [d for d, _ in dates_with_role]
        if len(dates_only) >= 2:
            try:
                parsed_dates = sorted(
                    datetime.strptime(d[:10], "%Y-%m-%d") for d in dates_only
                )
                span = (parsed_dates[-1] - parsed_dates[0]).days
                if span <= 14:
                    cluster_detected = True
            except Exception:
                pass

    # 시그널 레벨
    disc_count = len(disc_sales)
    if disc_count >= 10:
        signal = "ELEVATED"
    elif disc_count >= 5:
        signal = "WARNING"
    elif disc_count >= 1:
        signal = "MONITOR"
    else:
        signal = "NORMAL"

    # 특이 CFO/CEO 매도
    cfo_ceo_flag = bool(cfo_ceo_sales) and (
        sum(s["value_usd"] for s in cfo_ceo_sales) > 1_000_000  # $1M 이상
    )

    return {
        "sell_count_total":       len(sales),
        "sell_count_discretionary": disc_count,
        "total_sales_usd":        round(total_sales_usd, 0),
        "discretionary_sales_usd": round(disc_sales_usd, 0),
        "buy_count":              len(buys),
        "executives_selling":     selling_roles[:6],
        "cfo_ceo_large_sale":     cfo_ceo_flag,
        "cluster_detected":       cluster_detected,
        "signal":                 signal,
        "red_flag":               disc_count >= 5 or cfo_ceo_flag or cluster_detected,
    }


# ===========================================================================
# 6) 핵심 공개 API
# ===========================================================================

def monitor_catalysts(
    ticker: str,
    raw_8k:     dict | list | str | None = None,
    raw_corresp: dict | list | str | None = None,
    raw_form4:  dict | list | str | None = None,
    lookback_days: int = 180,
) -> dict[str, Any]:
    """SEC 공시 데이터 → Catalyst 이벤트 분류 + 우선순위 계산.

    Args:
        ticker:        종목 코드
        raw_8k:        data_sources.sec_8k_items() 반환값
        raw_corresp:   data_sources.sec_corresp() 반환값
        raw_form4:     data_sources.sec_form4() 반환값
        lookback_days: 탐색 기간 (기본 180일)

    Returns:
        {
          "ticker": str,
          "active_catalysts": [...CatalystEvent dicts],
          "insider_pattern": {...},
          "has_active_catalyst": bool,
          "max_severity": int,
          "flags": {...},
          "summary_text": str,
        }
    """
    events: list[CatalystEvent] = []

    # 8-K 이벤트
    if raw_8k is not None:
        events.extend(_parse_8k_events(raw_8k, lookback_days))

    # CORRESP
    if raw_corresp is not None:
        events.extend(_parse_corresp(raw_corresp, lookback_days))

    # Form 4
    insider_pattern: dict[str, Any] = {}
    if raw_form4 is not None:
        insider_pattern = _parse_form4(raw_form4, lookback_days)
        # Form 4 → CatalystEvent 변환
        form4_events = _form4_to_catalyst_events(insider_pattern, ticker)
        events.extend(form4_events)

    # 심각도 기준 정렬
    events.sort(key=lambda e: e.severity, reverse=True)

    # CFO departure + Auditor change 복합 신호 탐지
    cfo_dates = {
        e.date for e in events
        if "CFO" in (e.officer_role or "") and "5.02" in (e.item or "")
    }
    auditor_dates = {
        e.date for e in events
        if "4.01" in (e.item or "")
    }
    compound_signal = bool(cfo_dates and auditor_dates)
    if compound_signal:
        # 복합 이벤트 최상위 추가
        for ev in events:
            if "CFO" in (ev.officer_role or "") and "5.02" in (ev.item or ""):
                ev.severity = CATALYST_SEVERITY["8-K Item 5.02 CFO+Auditor Same Year"]
                ev.category = "8-K Item 5.02 CFO+Auditor Same Year"
                ev.follow_up.insert(0, "⚠️ CFO + 감사인 동시 변경 = CRITICAL 복합 신호")

    max_sev = max((e.severity for e in events), default=0)
    has_active = max_sev >= 45  # CORRESP 이상

    # 플래그
    corresp_active = any(e.filing_type == "CORRESP" for e in events)
    item_402_found = any("4.02" in (e.item or "") for e in events)
    item_802_found = any("8.01" in (e.item or "") for e in events)
    cfo_changed    = bool(cfo_dates)

    flags = {
        "item_402_restatement_risk": item_402_found,
        "item_801_investigation":    item_802_found,
        "cfo_changed":               cfo_changed,
        "auditor_changed":           bool(auditor_dates),
        "compound_signal":           compound_signal,
        "corresp_active":            corresp_active,
        "insider_red_flag":          insider_pattern.get("red_flag", False),
        "insider_signal":            insider_pattern.get("signal", "UNKNOWN"),
        "max_severity":              max_sev,
        "has_active_catalyst":       has_active,
    }

    # Catalyst probability
    if max_sev >= 85 or compound_signal or item_402_found:
        cat_prob = "HIGH"
    elif max_sev >= 60 or (corresp_active and cfo_changed):
        cat_prob = "MEDIUM"
    elif max_sev >= 30:
        cat_prob = "LOW"
    else:
        cat_prob = "NONE"

    flags["catalyst_probability"] = cat_prob

    # 요약 텍스트
    summary_lines = [
        f"## Agent 6 사전 계산 — {ticker}",
        f"분석 기간: 최근 {lookback_days}일",
        f"탐지 이벤트: {len(events)}건  |  최대 심각도: {max_sev}",
        f"Catalyst Probability: {cat_prob}",
        "",
    ]

    if events:
        summary_lines.append("### 이벤트 목록 (심각도 순)")
        for ev in events[:8]:
            summary_lines.append(
                f"  [{ev.severity:3d}] [{ev.date}] {ev.category}"
                + (f" | {ev.summary[:80]}" if ev.summary else "")
            )

    if insider_pattern:
        ip = insider_pattern
        summary_lines += [
            "",
            f"### Insider Pattern ({lookback_days}일)",
            f"재량 매도: {ip.get('sell_count_discretionary', 0)}건  "
            f"(${ip.get('discretionary_sales_usd', 0):,.0f})",
            f"Signal: {ip.get('signal', 'UNKNOWN')}  "
            f"CFO/CEO 대규모 매도: {ip.get('cfo_ceo_large_sale', False)}",
            f"클러스터 탐지: {ip.get('cluster_detected', False)}",
        ]

    summary_lines.append("\n### 🚩 플래그 요약")
    for k, v in flags.items():
        if v and v is not False and v not in ("UNKNOWN", "NONE", "NORMAL", 0):
            summary_lines.append(f"  ⚑ {k}: {v}")

    return {
        "ticker":            ticker,
        "active_catalysts":  [_event_to_dict(e) for e in events[:10]],
        "insider_pattern":   insider_pattern,
        "has_active_catalyst": has_active,
        "max_severity":      max_sev,
        "catalyst_probability": cat_prob,
        "flags":             flags,
        "summary_text":      "\n".join(summary_lines),
    }


def _form4_to_catalyst_events(
    insider_pattern: dict[str, Any],
    ticker: str,
) -> list[CatalystEvent]:
    """insider_pattern dict → CatalystEvent 리스트."""
    events = []
    disc_count = insider_pattern.get("sell_count_discretionary", 0)
    cfo_ceo_flag = insider_pattern.get("cfo_ceo_large_sale", False)
    cluster = insider_pattern.get("cluster_detected", False)

    if disc_count >= 10:
        events.append(CatalystEvent(
            date="recent", filing_type="Form4", item=None,
            severity=CATALYST_SEVERITY["Form 4 Cluster 10+ Sales"],
            category="Form 4 Cluster 10+ Sales",
            summary=f"재량 내부자 매도 {disc_count}건 (${insider_pattern.get('discretionary_sales_usd', 0):,.0f})",
            follow_up=["10b5-1 plan 여부 각각 확인", "매도 패턴 vs 주가 상승 타이밍 비교"],
        ))
    elif disc_count >= 5:
        events.append(CatalystEvent(
            date="recent", filing_type="Form4", item=None,
            severity=CATALYST_SEVERITY["Form 4 Cluster 5+ Sales"],
            category="Form 4 Cluster 5+ Sales",
            summary=f"재량 내부자 매도 {disc_count}건",
            follow_up=["매도 임원 역할 확인", "동기간 긍정적 공시 여부 확인"],
        ))

    if cfo_ceo_flag:
        events.append(CatalystEvent(
            date="recent", filing_type="Form4", item=None,
            severity=CATALYST_SEVERITY["Form 4 CFO/CEO Large Sale"],
            category="Form 4 CFO/CEO Large Sale",
            summary=f"CFO/CEO $1M+ 재량 매도: {insider_pattern.get('executives_selling', [])}",
            follow_up=["10b5-1 여부 확인", "매도 후 어닝스/공시 일정 확인"],
        ))

    if cluster:
        # 클러스터는 별도 이벤트 (중복 집계 의도적)
        events.append(CatalystEvent(
            date="recent", filing_type="Form4", item=None,
            severity=52,
            category="Form 4 Cluster — Multiple Officers 14d Window",
            summary=f"2주 이내 복수 임원 동시 매도: {insider_pattern.get('executives_selling', [])}",
            follow_up=["매도 가격 vs 현재 주가 비교", "해당 기간 내부 정보 유출 가능성 조사"],
        ))

    return events


def agent6_precomputed(
    ticker: str,
    raw_8k:     dict | list | str | None = None,
    raw_corresp: dict | list | str | None = None,
    raw_form4:  dict | list | str | None = None,
    lookback_days: int = 180,
) -> dict[str, Any]:
    """Agent 6 사전 계산 컨텍스트 패키지.

    data_sources 함수들의 반환값을 입력받아 monitor_catalysts() 실행.
    """
    return monitor_catalysts(
        ticker=ticker,
        raw_8k=raw_8k,
        raw_corresp=raw_corresp,
        raw_form4=raw_form4,
        lookback_days=lookback_days,
    )


def _event_to_dict(ev: CatalystEvent) -> dict[str, Any]:
    return {
        "date":           ev.date,
        "filing_type":    ev.filing_type,
        "item":           ev.item,
        "severity":       ev.severity,
        "category":       ev.category,
        "summary":        ev.summary,
        "url":            ev.url,
        "follow_up":      ev.follow_up,
        "officer_role":   ev.officer_role,
        "is_discretionary": ev.is_discretionary,
    }
