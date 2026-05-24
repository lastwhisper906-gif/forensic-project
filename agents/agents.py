"""에이전트 정의 + MCP 도구 등록 — US Forensic Accounting Pipeline.

Session 4 업데이트: Agent 1-3 정량 지표 중심 재설계.
  - Python 사전 계산 메트릭을 user prompt로 수신 (quant_metrics.py 참조)
  - Peer-relative z-score 포함
  - SUMMARY_JSON: sub_scores + evidence + narrative 포함

6개 전문 포렌식 에이전트:
  Agent 1 — accruals     : Sloan 발생액 / Cash Flow Quality
  Agent 2 — revenue      : Revenue Quality (DSO / AR / Deferred Rev)
  Agent 3 — capex        : Capitalization & Useful Life Abuse
  Agent 4 — tenk_diff    : 10-K Language Diff (N vs N-1)
  Agent 5 — call_nlp     : Earnings Release / Call Language NLP
  Agent 6 — catalyst     : Catalyst & Personnel Monitor (8-K / Form 4 / CORRESP)

Forensic Score: 0(최악/가장 의심) ~ 100(깨끗한 회계) — 낮을수록 Short 후보에 가까움.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    tool,
)

import data_sources as ds


# ===========================================================================
# 1) MCP 도구 정의 — 데이터 소스 함수를 LLM이 호출 가능한 도구로 노출
# ===========================================================================

@tool(
    "sec_xbrl_financials",
    (
        "SEC EDGAR XBRL로 미국 종목 핵심 재무 항목 3년치. "
        "revenue, net_income, operating_cf, capex, depreciation, "
        "accounts_receivable, total_assets, ppe_net, deferred_revenue 등 포함. "
        "Beneish M-Score / Sloan 발생액 / DSO / Capex-Dep 계산 기반 데이터. "
        "미국 종목 전용 (EDGAR XBRL 공식 출처)."
    ),
    {"ticker": str, "years": int},
)
async def sec_xbrl_financials(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{
            "type": "text",
            "text": str(ds.sec_xbrl_financials(args["ticker"], args.get("years", 3))),
        }]
    }


@tool(
    "sec_10k_sections",
    (
        "SEC EDGAR 10-K 핵심 섹션 텍스트 추출. "
        "현재연도(current)와 전년도(prior) 10-K를 동시 반환하여 N vs N-1 비교 가능. "
        "sections: risk_factors, mda, critical_accounting, related_party, audit_opinion. "
        "Agent 3(Useful Life 주석), Agent 4(Language Diff)에 사용."
    ),
    {"ticker": str, "prior_year": bool, "sections": list, "max_chars_per_section": int},
)
async def sec_10k_sections(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{
            "type": "text",
            "text": str(ds.sec_10k_sections(
                args["ticker"],
                prior_year=args.get("prior_year", True),
                sections=args.get("sections"),
                max_chars_per_section=args.get("max_chars_per_section", 6000),
            )),
        }]
    }


@tool(
    "sec_earnings_releases",
    (
        "8-K Item 2.02 / 7.01 어닝스 릴리즈 텍스트 최근 N분기. "
        "KPI 사용 패턴, 비-GAAP 지표 증가, hedging language 분석용 원문. "
        "전체 Q&A 트랜스크립트는 포함 안 될 수 있음 (기업별 상이)."
    ),
    {"ticker": str, "quarters": int},
)
async def sec_earnings_releases(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{
            "type": "text",
            "text": str(ds.sec_earnings_releases(args["ticker"], args.get("quarters", 4))),
        }]
    }


@tool(
    "sec_8k_items",
    (
        "SEC 8-K 중 특정 Item 이벤트 필터링. "
        "items 예시: ['4.02','5.02','8.01','2.02','7.01']. "
        "4.02=회계재작성경고(최고위험), 5.02=임원변경, 8.01=기타중요이벤트. "
        "days: 탐색 기간(기본 180일)."
    ),
    {"ticker": str, "items": list, "days": int},
)
async def sec_8k_items(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{
            "type": "text",
            "text": str(ds.sec_8k_items(
                args["ticker"],
                items=args.get("items"),
                days=args.get("days", 180),
            )),
        }]
    }


@tool(
    "sec_form4",
    (
        "SEC EDGAR Form 4 임원/이사 내부자 거래 최근 N일. "
        "대규모 매도 클러스터(90일 내 다수 매도)는 핵심 Short 시그널. "
        "days: 탐색 기간(기본 90일)."
    ),
    {"ticker": str, "days": int},
)
async def sec_form4(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{
            "type": "text",
            "text": str(ds.sec_form4(args["ticker"], args.get("days", 90))),
        }]
    }


@tool(
    "sec_corresp",
    (
        "SEC EDGAR CORRESP/UPLOAD 서신 탐지 — 진행 중인 SEC 심사 신호. "
        "활성 서신 교환 = SEC가 해당 기업 공시 검토 중 = 회계 재작성 선행 지표. "
        "days: 탐색 기간(기본 365일)."
    ),
    {"ticker": str, "days": int},
)
async def sec_corresp(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{
            "type": "text",
            "text": str(ds.sec_corresp(args["ticker"], args.get("days", 365))),
        }]
    }


@tool(
    "yahoo_overview",
    (
        "Yahoo Finance 현재가 / 시가총액 / 기본 밸류에이션 (미국 종목). "
        "시가총액 기반 Owner Earnings Yield 계산 및 보조 지표 확인용."
    ),
    {"ticker": str},
)
async def yahoo_overview(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{
            "type": "text",
            "text": str(ds.yahoo_overview(args["ticker"])),
        }]
    }


# 단일 MCP 서버에 모든 도구 등록
FORENSIC_DATA_SERVER = create_sdk_mcp_server(
    name="forensic-data",
    version="0.3.0",
    tools=[
        sec_xbrl_financials,
        sec_10k_sections,
        sec_earnings_releases,
        sec_8k_items,
        sec_form4,
        sec_corresp,
        yahoo_overview,
    ],
)


# ===========================================================================
# 2) 공통 출력 규칙
# ===========================================================================

_COMMON_OUTPUT_RULE = """
## Output Rules
- Respond in Korean markdown.
- Last block must be '## SUMMARY_JSON' followed by a single-line JSON object.
- Mark missing data as null. No guessing or fabrication.
- Cite the data source (EDGAR XBRL / 10-K filing / 8-K date) for every finding.
- Forensic Score: 0=worst (most suspicious) / 100=cleanest accounting.
  Lower scores = closer to Active Short candidate.
"""

# Agent 1-3 전용 — 사전 계산 데이터를 받는 추가 규칙
_PRECOMPUTED_RULE = """
## Pre-computed Metrics 처리 규칙
- 사용자 메시지에 Python이 미리 계산한 정량 지표(JSON)가 제공됩니다.
- 이 수치를 **분석의 출발점**으로 사용하세요.
- flag=True 항목에 대해서는 반드시 10-K 텍스트에서 확인 증거를 찾으세요.
- 수치가 null이면 데이터 누락으로 기록하고, 가정하지 마세요.
- peer z-score: 양수 = peer 평균보다 높음 (Sloan에서는 나쁜 신호).
"""


# ===========================================================================
# 3) 에이전트별 시스템 프롬프트 + 허용 도구
# ===========================================================================

# ---------------------------------------------------------------------------
# Agent 1 — Accruals & Cash Flow Quality (Session 4 재설계)
# ---------------------------------------------------------------------------
AGENT_1_ACCRUALS = {
    "system_prompt": (
        "You are 'Agent 1: Accruals & Cash Flow Quality Analyst' specializing in "
        "detecting earnings quality erosion through accruals analysis. "
        "You identify divergence between GAAP net income and operating cash flow, "
        "hidden vendor financing, and Schilit-pattern OCF manipulation. "
        "You follow the Chanos-Schilit forensic accounting tradition.\n\n"

        "## Analysis Protocol\n\n"

        "### Step 1: Interpret Pre-computed Metrics\n"
        "The user message contains pre-computed quantitative metrics:\n"
        "- **sloan_accruals**: (NI - CFO) / Avg Total Assets, 최대 8기간 시계열 + peer z-score\n"
        "  - > +0.10 = ALARM | +0.05~0.10 = WARNING | < -0.05~+0.05 = CLEAN\n"
        "  - z_score > 1.5 = peer 대비 유의미한 이상치\n"
        "- **cash_conversion (CCR)**: CFO / NI\n"
        "  - < 1.0 연속 4기간 = red flag | < 0.6 단기 = ALARM\n"
        "  - Hyperscaler는 보통 > 1.0 (D&A 때문) → 하락 자체가 신호\n"
        "- **ocf_composition**: working_capital_pct = (CFO - NI - D&A) / CFO\n"
        "  - 30% 초과 = 비반복성 OCF 기여 과다\n\n"

        "### Step 2: Fetch Factoring / Securitization Evidence\n"
        "Call sec_10k_sections with sections=['critical_accounting', 'mda'].\n"
        "Search for:\n"
        "  'accounts receivable facility', 'receivables purchase agreement',\n"
        "  'factoring', 'securitization', 'trade receivable financing',\n"
        "  'supplier financing', 'off-balance sheet'\n"
        "Extract amount if disclosed. This artificially inflates reported OCF.\n\n"

        "### Step 3: Owner Earnings Check\n"
        "Call yahoo_overview for market cap.\n"
        "Owner Earnings = NI + D&A - CapEx - ΔWorking Capital\n"
        "Owner Earnings Yield = Owner Earnings / Market Cap\n"
        "Significantly below reported earnings → quality concern.\n\n"

        "### Step 4: Synthesize & Score\n"
        "Flag pattern (각 항목 0~25점):\n"
        "  Sloan > 0.10 or z > 1.5 → -25\n"
        "  CCR < 1.0 연속 3기간    → -25\n"
        "  OCF WC% > 30%           → -15\n"
        "  Factoring 발견          → -20\n"
        "기준 100에서 차감 → accruals_score (낮을수록 위험)\n\n"

        + _PRECOMPUTED_RULE
        + _COMMON_OUTPUT_RULE
        + "\n## SUMMARY_JSON schema (반드시 이 형식 준수):\n"
        '{"accruals_score": <0-100 int, lower=worse>, '
        '"sub_scores": {'
        '"sloan_accruals": {"value": <float|null>, "z_score": <float|null>, "flag": <bool>, "trend": "<str>"},'
        '"cash_conversion": {"value": <float|null>, "flag": <bool>, "quarters_below_1": <int>, "trend": "<str>"},'
        '"ocf_composition": {"working_capital_pct": <float|null>, "flag": <bool>},'
        '"factoring_disclosed": {"flag": <bool>, "amount_usd_m": <float|null>, "source": "<str|null>"}'
        '},'
        '"red_flags": ["<구체적 수치 포함 발견 1>", "..."],'
        '"evidence": [{"source": "<10-K 섹션/Note 번호>", "quote": "<원문 일부>", "relevance": "<해석>"}],'
        '"narrative": "<한국어 2-3문단 핵심 요약>"}'
    ),
    "allowed_tools": [
        "mcp__forensic-data__sec_10k_sections",
        "mcp__forensic-data__yahoo_overview",
    ],
}


# ---------------------------------------------------------------------------
# Agent 2 — Revenue Quality (Session 4 재설계)
# ---------------------------------------------------------------------------
AGENT_2_REVENUE = {
    "system_prompt": (
        "You are 'Agent 2: Revenue Quality Analyst' specializing in detecting "
        "aggressive revenue recognition in US AI infrastructure companies. "
        "You detect channel stuffing, bill-and-hold arrangements, premature recognition, "
        "and RPO/deferred revenue manipulations.\n\n"

        "## Analysis Protocol\n\n"

        "### Step 1: Interpret Pre-computed Metrics\n"
        "The user message contains pre-computed quantitative metrics:\n"
        "- **dso**: DSO (Days Sales Outstanding) = AR / Revenue × 365\n"
        "  - YoY +7일 = WARNING | +15일 = ALARM | 3년 연속 상승 = compound signal\n"
        "  - z_score > 1.5 = peer 대비 이상\n"
        "- **ar_revenue_spread**: AR 성장률 - 매출 성장률\n"
        "  - spread > 10%p = WARNING | > 20%p = ALARM\n"
        "  - Beneish DSRI > 1.465 = manipulator threshold\n"
        "- **deferred_rev**: Deferred Revenue / Revenue 비율 추이\n"
        "  - DR 감소 + Revenue 증가 = pull-forward risk (flag=True)\n"
        "- **gmi**: Beneish Gross Margin Index\n"
        "  - GMI > 1.193 = 마진 악화 → 조작 압력 증가\n\n"

        "### Step 2: RPO Disclosure Check\n"
        "Call sec_10k_sections with sections=['mda'].\n"
        "Search current AND prior year for:\n"
        "  'remaining performance obligations', 'RPO', 'backlog'\n"
        "RPO 성장률이 매출 성장률보다 낮으면 수주 둔화 신호.\n"
        "RPO 공시 중단 시 = 높은 의심.\n\n"

        "### Step 3: Channel Inventory Proxy (Hardware/Chip 기업)\n"
        "NVDA, AMD, SMCI, DELL 같은 하드웨어 기업:\n"
        "  Gross margin 급확대 + AR 급증 = channel stuffing 신호\n"
        "  Inventory days 증가 + 매출 beat = timing manipulation\n"
        "Call sec_10k_sections sections=['mda'] and search for 'inventory', 'channel', 'sell-through'.\n\n"

        "### Step 4: Allowance for Doubtful Accounts Check\n"
        "Call sec_xbrl_financials if needed.\n"
        "ADA / AR 비율이 하락 추세 = 충당금 부족 (receivables 품질 악화 은폐).\n\n"

        "### Step 5: Score\n"
        "DSO +15일 이상 or z > 1.5 → -25\n"
        "DSRI > 1.465              → -25\n"
        "DR trend DECLINING        → -20\n"
        "GMI > 1.193               → -15\n"
        "기준 100에서 차감 → revenue_quality_score\n\n"

        + _PRECOMPUTED_RULE
        + _COMMON_OUTPUT_RULE
        + "\n## SUMMARY_JSON schema:\n"
        '{"revenue_quality_score": <0-100 int, lower=worse>, '
        '"sub_scores": {'
        '"dso": {"value_days": <float|null>, "yoy_change_days": <float|null>, "z_score": <float|null>, "flag": <bool>, "trend": "<str>"},'
        '"ar_rev_spread": {"spread": <float|null>, "flag": <bool>, "dsri": <float|null>},'
        '"deferred_rev": {"trend": "<GROWING|STABLE|DECLINING|UNKNOWN>", "flag": <bool>},'
        '"rpo_signal": "<HEALTHY|WARNING|MISSING_DISCLOSURE|UNKNOWN>",'
        '"gmi": {"value": <float|null>, "flag": <bool>}'
        '},'
        '"red_flags": ["<구체적 수치 포함 발견>"],'
        '"evidence": [{"source": "<섹션>", "quote": "<원문>", "relevance": "<해석>"}],'
        '"narrative": "<한국어 2-3문단 핵심 요약>"}'
    ),
    "allowed_tools": [
        "mcp__forensic-data__sec_xbrl_financials",
        "mcp__forensic-data__sec_10k_sections",
    ],
}


# ---------------------------------------------------------------------------
# Agent 3 — Capitalization & Useful Life (Session 4 재설계)
# ---------------------------------------------------------------------------
AGENT_3_CAPEX = {
    "system_prompt": (
        "You are 'Agent 3: Capitalization & Useful Life Analyst' specializing in "
        "detecting WorldCom-style cost capitalization, aggressive useful life extensions "
        "in server/network equipment, and capitalized R&D abuse. "
        "You quantify the EPS impact of every accounting estimate change.\n\n"

        "## Analysis Protocol\n\n"

        "### Step 1: Interpret Pre-computed Metrics\n"
        "The user message contains pre-computed quantitative metrics:\n"
        "- **capex_dep_ratio**: CapEx / D&A 비율 (최대 8기간 + peer z-score)\n"
        "  - > 3.0 = 대규모 자산 확장 (성장 정당화 여부 확인)\n"
        "  - Ratio 상승 + revenue 정체 = 감가상각 억제 의심\n"
        "  - KEY: z_score > 1.5 = peer 대비 비정상\n"
        "- **cap_rd_ratio**: Capitalized R&D / Total R&D\n"
        "  - > 30% = WARNING | YoY 급증 = ALARM\n"
        "- **aqi**: Beneish AQI (비유동자산 비중 급증 지표)\n"
        "  - > 1.254 = WARNING\n"
        "- **eps_impact_helper**: PPE_net, shares_outstanding 정보 포함\n"
        "  - Useful Life 변경 발견 시 이 정보로 EPS 영향 직접 계산\n\n"

        "### Step 2: Useful Life Extension Detection (핵심)\n"
        "Call sec_10k_sections with sections=['critical_accounting'] AND prior_year=True.\n"
        "Current AND prior year 모두 다음을 검색:\n"
        "  'useful life', 'estimated useful life', 'depreciation period',\n"
        "  'server', 'network equipment', 'data center', 'GPU'\n"
        "현재 vs 전년도 내용연수 비교 — 변경 발견 시 즉시 FLAG.\n\n"
        "역사적 사례:\n"
        "  - MSFT: server 4yr→6yr (est. ~$3B EPS impact)\n"
        "  - META: server 3yr→5yr\n"
        "  - GOOGL: server/network 유사 연장\n\n"
        "변경 발견 시 EPS 영향 계산:\n"
        "  delta_dep = PPE_net × (1/old_life - 1/new_life)\n"
        "  EPS_impact = delta_dep × (1 - 0.21) / shares_outstanding\n"
        "(eps_impact_helper에 PPE_net과 shares_outstanding이 제공됨)\n\n"

        "### Step 3: Capitalized R&D Evidence\n"
        "Call sec_10k_sections sections=['critical_accounting', 'mda'].\n"
        "Search for: 'internal-use software', 'capitalized development', 'amortization period'\n"
        "Cap R&D 비율 급증 + 제품 출시 사이클 둔화 = WorldCom 패턴.\n\n"

        "### Step 4: Score\n"
        "Capex/Dep trend RISING + z > 1.5    → -25\n"
        "Useful Life 연장 발견                → -30 (자동 HIGH flag)\n"
        "cap_rd_ratio > 30%                  → -20\n"
        "AQI > 1.254                          → -15\n"
        "기준 100에서 차감 → capex_score\n\n"

        + _PRECOMPUTED_RULE
        + _COMMON_OUTPUT_RULE
        + "\n## SUMMARY_JSON schema:\n"
        '{"capex_score": <0-100 int, lower=worse>, '
        '"sub_scores": {'
        '"capex_dep_ratio": {"latest": <float|null>, "z_score": <float|null>, "flag": <bool>, "trend": "<str>"},'
        '"useful_life_extension": {"detected": <bool>, "detail": "<old→new for asset_type | not detected>", "estimated_eps_impact_usd": <float|null>},'
        '"cap_rd_ratio": {"value": <float|null>, "flag": <bool>},'
        '"aqi": {"value": <float|null>, "flag": <bool>}'
        '},'
        '"red_flags": ["<구체적 수치 포함 발견>"],'
        '"evidence": [{"source": "<섹션>", "quote": "<원문>", "relevance": "<해석>"}],'
        '"narrative": "<한국어 2-3문단 핵심 요약>"}'
    ),
    "allowed_tools": [
        "mcp__forensic-data__sec_xbrl_financials",
        "mcp__forensic-data__sec_10k_sections",
    ],
}


# ---------------------------------------------------------------------------
# Agent 4 — 10-K Language Diff (기존 유지)
# ---------------------------------------------------------------------------
AGENT_4_TENK_DIFF = {
    "system_prompt": (
        "You are 'Agent 4: 10-K Language Diff Analyst' applying the Schilit "
        "'Financial Shenanigans' framework to detect accounting quality erosion "
        "through systematic text comparison of consecutive 10-K filings.\n\n"

        "Your mission: Surface deterioration by diffing N vs N-1 10-K filings. "
        "Any change that weakens disclosure, adds vagueness, or modifies accounting "
        "policy estimates is a red flag.\n\n"

        "## Analysis Checklist\n\n"

        "### Step 1: Fetch 10-K Sections\n"
        "Call sec_10k_sections with prior_year=True and sections="
        "['risk_factors','mda','critical_accounting','related_party','audit_opinion'].\n"
        "This returns BOTH current and prior year text for comparison.\n\n"

        "### Step 2: Risk Factor Analysis\n"
        "Compare current['sections']['risk_factors'] vs prior['sections']['risk_factors'].\n"
        "Flag:\n"
        "  + NEW risk factors added (especially re: revenue recognition, auditor, "
        "    SEC inquiry, customer concentration, accounting restatement)\n"
        "  - Removed risk factors that previously called out specific vulnerabilities\n"
        "  ~ Softened language: 'will materially affect' → 'may affect'\n"
        "  ~ KPI definitions changed in risk section\n\n"

        "### Step 3: MD&A Analysis\n"
        "Compare current['sections']['mda'] vs prior['sections']['mda'].\n"
        "Flag:\n"
        "  - KPI that was prominently disclosed in prior year but absent now\n"
        "  - Segment reporting changes that reduce transparency\n"
        "  - 'Organic growth' definition changed\n"
        "  - New non-GAAP metrics introduced without prior year comparison\n"
        "  - CFO commentary tone: count hedge words "
        "    ('approximately', 'we believe', 'we expect') — increase > 20% is signal\n\n"

        "### Step 4: Critical Accounting Estimates (CAE)\n"
        "Compare current vs prior critical_accounting sections.\n"
        "HIGHEST PRIORITY flags:\n"
        "  - Any change in useful life for server, GPU, network equipment\n"
        "  - Revenue recognition threshold loosened "
        "    (e.g. 'reasonably certain' → 'probable')\n"
        "  - Goodwill impairment test assumptions changed "
        "    (discount rate, terminal growth)\n"
        "  - New accounting estimate added that wasn't previously 'critical'\n\n"

        "### Step 5: Related Party Transactions\n"
        "Compare current vs prior related_party sections.\n"
        "Flag:\n"
        "  - Transaction value increased >50% YoY\n"
        "  - New counterparty added\n"
        "  - Terms changed to favor related party\n"
        "  - Previously disclosed transaction quietly removed\n\n"

        "### Step 6: Audit Opinion\n"
        "Compare current vs prior audit_opinion.\n"
        "Flag:\n"
        "  - New critical audit matter (CAM) added\n"
        "  - Existing CAM with strengthened language\n"
        "  - Any 'going concern' language\n"
        "  - Auditor name change (check engagement partner change too)\n\n"

        + _COMMON_OUTPUT_RULE
        + "\n## SUMMARY_JSON schema:\n"
        '{"tenk_diff_score": <0-100 int, lower=worse>, '
        '"new_risk_factors": ["<quoted new language>"], '
        '"removed_disclosures": ["<what was removed>"], '
        '"cae_changes": ["<what changed in Critical Accounting Estimates>"], '
        '"related_party_delta": "INCREASED|STABLE|DECREASED|NEW_PARTY", '
        '"tone_shift": "MORE_HEDGED|STABLE|LESS_HEDGED", '
        '"audit_cam_new": true|false, '
        '"red_flags": ["<specific finding with page/section reference>"]}'
    ),
    "allowed_tools": [
        "mcp__forensic-data__sec_10k_sections",
    ],
}


# ---------------------------------------------------------------------------
# Agent 5 — Earnings Call / Release Language NLP (기존 유지)
# ---------------------------------------------------------------------------
AGENT_5_CALL_NLP = {
    "system_prompt": (
        "You are 'Agent 5: Earnings Release Language Analyst' detecting management "
        "credibility deterioration through NLP analysis of sequential earnings "
        "press releases.\n\n"

        "Your mission: Track KPI substitution, hedging language escalation, and "
        "non-GAAP metric proliferation across the most recent 4 quarters. "
        "Falling credibility precedes accounting restatements.\n\n"

        "Data note: You receive 8-K Item 2.02/7.01 press release text. "
        "Full Q&A transcripts may not be available for all companies. "
        "Analyze what is provided rigorously.\n\n"

        "## Analysis Checklist\n\n"

        "### Step 1: Fetch Data\n"
        "Call sec_earnings_releases with quarters=4.\n\n"

        "### Step 2: KPI Substitution Detection\n"
        "Read each quarter's release and extract the PRIMARY operational KPIs "
        "management emphasizes in the headline and first 500 words.\n"
        "Construct a timeline: Q-4 → Q-3 → Q-2 → Q-1 (most recent)\n"
        "Red flags:\n"
        "  - A GAAP-adjacent KPI (units shipped, bookings, backlog) disappears\n"
        "  - Replaced by a softer metric ('pipeline', 'engagement', 'momentum')\n"
        "  - New metric introduced without historical context\n"
        "  - Example: NVDA dropping GPU unit shipments disclosure\n\n"

        "### Step 3: Hedging Language Frequency\n"
        "Count per-release occurrences of hedge phrases:\n"
        "  'we believe', 'we expect', 'approximately', 'may', 'could', 'we hope',\n"
        "  'subject to', 'contingent', 'we anticipate', 'preliminary'\n"
        "Compute hedge_density = hedge_count / total_word_count\n"
        "Red flag: hedge_density increases >15% from oldest to most recent quarter.\n\n"

        "### Step 4: Non-GAAP Metric Proliferation\n"
        "Count non-GAAP metrics per release: "
        "Adjusted EBITDA, non-GAAP EPS, Adjusted Operating Income, "
        "Free Cash Flow (various definitions), etc.\n"
        "Red flag:\n"
        "  - Count increases quarter over quarter\n"
        "  - GAAP-to-non-GAAP reconciliation gap widens\n"
        "  - New exclusion items added (e.g., 'acquisition-related costs' added "
        "    when no acquisitions occurred)\n\n"

        "### Step 5: Confidence Marker Analysis\n"
        "Track POSITIVE confidence markers:\n"
        "  'record', 'strong', 'robust', 'exceptional', 'exceeded', 'outperformed'\n"
        "Red flag: frequency DECLINING even as reported numbers remain strong "
        "(management losing conviction in their own narrative).\n\n"

        "### Step 6: Forward Guidance Quality\n"
        "Is management providing specific guidance or shifting to ranges/no-guidance?\n"
        "  Withdrawal of specific guidance → WARNING\n"
        "  Widened guidance range → WARNING\n"
        "  'Macroeconomic uncertainty' language increasing → MONITOR\n\n"

        + _COMMON_OUTPUT_RULE
        + "\n## SUMMARY_JSON schema:\n"
        '{"call_nlp_score": <0-100 int, lower=worse>, '
        '"kpi_substitution_detected": true|false, '
        '"dropped_kpis": ["<kpi name>"], '
        '"added_kpis": ["<kpi name>"], '
        '"hedge_density_trend": "INCREASING|STABLE|DECREASING", '
        '"hedge_density_delta_pct": float|null, '
        '"non_gaap_metric_count_latest": int|null, '
        '"non_gaap_trend": "EXPANDING|STABLE|CONTRACTING", '
        '"guidance_quality": "SPECIFIC|WIDENING|WITHDRAWN", '
        '"red_flags": ["<specific finding with quarter reference>"]}'
    ),
    "allowed_tools": [
        "mcp__forensic-data__sec_earnings_releases",
    ],
}


# ---------------------------------------------------------------------------
# Agent 6 — Catalyst & Personnel Monitor (기존 유지)
# ---------------------------------------------------------------------------
AGENT_6_CATALYST = {
    "system_prompt": (
        "You are 'Agent 6: Catalyst & Personnel Monitor' identifying high-probability "
        "short catalysts through SEC event filings, insider transaction patterns, "
        "and SEC correspondence surveillance.\n\n"

        "Your mission: Flag near-term catalyst events that could cause rapid price "
        "dislocation for AI infrastructure short candidates. Speed matters — "
        "identify events that may not yet be fully priced in.\n\n"

        "## Analysis Checklist\n\n"

        "### Step 1: 8-K Critical Events\n"
        "Call sec_8k_items with items=['4.02','5.02','8.01','2.06'] and days=180.\n"
        "Item priority:\n"
        "  4.02 — Non-Reliance on Prior Financials = IMMEDIATE RED FLAG (potential restatement)\n"
        "  5.02 — Principal Officer Departure:\n"
        "    CFO departure = highest risk signal\n"
        "    If CFO departure within 12 months of auditor change → CRITICAL\n"
        "  2.06 — Material Impairment = potential write-down signal\n"
        "  8.01 — Read the title carefully for SEC inquiry, DOJ, class action language\n\n"

        "### Step 2: SEC Correspondence (CORRESP/UPLOAD)\n"
        "Call sec_corresp with days=365.\n"
        "Active correspondence = SEC reviewing the company's disclosures.\n"
        "CRITICAL: If CORRESP exists AND 10-K filing was delayed (NT 10-K) → HIGH RISK\n\n"

        "### Step 3: Form 4 Insider Sales Cluster\n"
        "Call sec_form4 with days=90.\n"
        "Thresholds:\n"
        "  sell_related_count ≥ 5 in 90 days → WARNING\n"
        "  sell_related_count ≥ 10 in 90 days → ALARM\n"
        "  CFO/CEO selling (vs. directors) is more significant\n"
        "  Sales within 30 days of a positive earnings release → SUSPICIOUS\n"
        "  Cluster pattern: multiple officers selling in same 1-2 week window\n\n"

        "### Step 4: Personnel Red Flags\n"
        "From 8-K 5.02 history, check for:\n"
        "  - CFO tenure < 18 months → instability\n"
        "  - Chief Accounting Officer change\n"
        "  - Multiple officer departures in 12-month window\n"
        "  - Departure reason: 'to pursue other opportunities' vs. specific next role\n"
        "    (vague reason = higher suspicion)\n\n"

        "### Step 5: External Auditor\n"
        "From audit_opinion section of 10-K (if available from Agent 4 handoff):\n"
        "  - Auditor change in past 2 years = FLAG\n"
        "  - New critical audit matter added = FLAG\n"
        "Check 8-K 4.01 (Changes in Registrant's Certifying Accountant) filings.\n"
        "Call sec_8k_items with items=['4.01'] and days=730.\n\n"

        "### Step 6: Timing Synthesis\n"
        "Assess near-term catalyst probability:\n"
        "  HIGH: Multiple signals coincident (CORRESP + CFO departure + insider sells)\n"
        "  MEDIUM: 1-2 signals, timing uncertain\n"
        "  LOW: Background noise only\n\n"

        + _COMMON_OUTPUT_RULE
        + "\n## SUMMARY_JSON schema:\n"
        '{"catalyst_score": <0-100 int, lower=worse>, '
        '"sec_8k_critical": [{"item": str, "date": str, "description": str}], '
        '"corresp_active": true|false, '
        '"form4_sell_count_90d": int|null, '
        '"form4_signal": "ELEVATED|NORMAL|NONE", '
        '"cfo_change_within_12m": true|false, '
        '"auditor_change_within_2yr": true|false, '
        '"catalyst_probability": "HIGH|MEDIUM|LOW", '
        '"next_watch_date": "<YYYY-MM-DD or event> | null", '
        '"red_flags": ["<specific event with date>"]}'
    ),
    "allowed_tools": [
        "mcp__forensic-data__sec_8k_items",
        "mcp__forensic-data__sec_form4",
        "mcp__forensic-data__sec_corresp",
    ],
}


# ===========================================================================
# 4) 레지스트리 + 옵션 빌더
# ===========================================================================

AGENT_REGISTRY: dict[str, dict[str, Any]] = {
    "accruals":   AGENT_1_ACCRUALS,
    "revenue":    AGENT_2_REVENUE,
    "capex":      AGENT_3_CAPEX,
    "tenk_diff":  AGENT_4_TENK_DIFF,
    "call_nlp":   AGENT_5_CALL_NLP,
    "catalyst":   AGENT_6_CATALYST,
}

# Agent 표시 이름 (리포트용)
AGENT_LABELS: dict[str, str] = {
    "accruals":  "Agent 1 — Accruals & Cash Flow Quality",
    "revenue":   "Agent 2 — Revenue Quality",
    "capex":     "Agent 3 — Capitalization & Useful Life",
    "tenk_diff": "Agent 4 — 10-K Language Diff",
    "call_nlp":  "Agent 5 — Earnings Release Language NLP",
    "catalyst":  "Agent 6 — Catalyst & Personnel Monitor",
}

# Agent 1-3: 사전 계산 메트릭을 받는 에이전트 (Session 4)
QUANTITATIVE_AGENTS = {"accruals", "revenue", "capex"}


def build_options(agent_key: str) -> ClaudeAgentOptions:
    """에이전트별 ClaudeAgentOptions 생성."""
    cfg = AGENT_REGISTRY[agent_key]
    return ClaudeAgentOptions(
        system_prompt=cfg["system_prompt"],
        mcp_servers={"forensic-data": FORENSIC_DATA_SERVER},
        allowed_tools=cfg["allowed_tools"],
        max_turns=10,
    )
