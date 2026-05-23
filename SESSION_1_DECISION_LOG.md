# Session 1 Decision Log
## Forensic Accounting Pipeline — AI Infrastructure Sector (US Only)

**날짜:** 2026-05-22  
**목적:** 기존 4-agent KR+US 주식 분석기 → AI 인프라 섹터 특화 Forensic Pipeline 재설계  
**프레임워크:** Chanos-Schilit forensic accounting tradition  
**시장:** 미국 전용 (KR 코드 제거)

---

## Part 1: 기존 코드 분석 결과

### 1-A. agents.py — 4개 에이전트 시스템 프롬프트 핵심 요약

| Agent | 역할 핵심 | 핵심 지시문 |
|---|---|---|
| **A — Financial** | ROE/PER/PBR/부채비율/FCF | KR→DART+Naver, US→Yahoo; 두 출처 충돌 시 공식 출처(DART/SEC) 우선 |
| **B — Dividend** | 5년 배당 히스토리/배당성향 | payout ratio 60% 초과 시 지속 가능성 경고; 배당 증가/유지/삭감 추이 |
| **C_quant — Forensic Quant** | Beneish M-Score 8변수 + 발생액 + Owner Earnings | SUMMARY_JSON에 `recommended_note_categories` 포함 → C_notes에 핸드오프 |
| **C_notes — Notes Analyst** | C_quant red_flags를 context로 받아 주석 집중 분석 | KR 전용(DART); 주석 모호성 자체가 경고 신호 |
| **D — Risk** | 공시/뉴스 키워드 스캔; 대주주변경/소송/감사의견/임원변경 | 8-K Item 4.02(회계정정)/5.02(임원변경), SC 13D/G, NT 10-K 주목 |

**특징적 설계 포인트:**
- `_COMMON_OUTPUT_RULE`으로 모든 에이전트에 공통 출력 규칙 부여 (`## SUMMARY_JSON` 필수)
- `AGENT_C_NOTES`는 `AGENT_REGISTRY`에서 제외 → orchestrator가 Phase 2에서 직접 호출 (Sequential handoff 패턴)
- `max_turns=8`로 모든 에이전트 통일 (분석 1사이클 가정)

---

### 1-B. data_sources.py — KR vs US 코드 분리도

**분리 구조: 완전 독립, 함수 단위로 나뉨**

```
# KR 전용 (DART API + Naver 스크래핑)
dart_corp_code()            # 종목코드 → DART corp_code 매핑
dart_financials()           # DART 주요 재무지표 (fnlttSinglIndx)
dart_recent_disclosures()   # DART 공시 목록 (list.json)
dart_dividend_history()     # DART 배당지표 (M240000)
dart_financial_statements() # DART 재무제표 3년치 (fnlttSinglAcnt) — Beneish용
dart_notes_text()           # DART 사업보고서 ZIP → 주석 키워드 추출
naver_quote()               # 네이버 금융 스크래핑 (현재가/PER/PBR)
naver_news()                # 네이버 뉴스 헤드라인

# US 전용 (yfinance + SEC EDGAR)
yahoo_overview()            # yfinance .info (PE/PB/ROE/FCF 등)
yahoo_dividend_history()    # yfinance .dividends
yahoo_financial_statements()# yfinance .financials/.cashflow/.balance_sheet — Beneish용
sec_recent_filings()        # SEC EDGAR browse-edgar atom feed
yahoo_news()                # yfinance .news

# 공통
detect_market()             # 6자리 숫자=KR, 알파벳=US
```

**제거 범위 (신규 파이프라인):** `dart_*` 함수 5개 전부 + `naver_*` 함수 2개  
**유지 범위:** `yahoo_*` 3개 + `sec_recent_filings()` + `detect_market()` (또는 US 고정으로 단순화)

**추가 필요한 데이터 소스:**
- SEC EDGAR XBRL API → 재무제표 구조화 데이터 (yfinance보다 신뢰성↑)
- SEC EDGAR Full-Text → 10-K 전문 텍스트 (N vs N-1 diff용)
- SEC Form 4 → 임원 내부자 거래
- SEC CORRESP/UPLOAD 타입 → SEC 서신 왕래
- 어닝스 콜 트랜스크립트 → 별도 소스 필요 (옵션: FinancialModelingPrep, Motley Fool 스크래핑)

---

### 1-C. orchestrator.py — 재사용 가능한 병렬 구조

**재사용 확정:**

```python
# 1. AgentReport 데이터클래스 — 그대로 사용
@dataclass
class AgentReport:
    agent: str
    text: str
    summary: dict[str, Any]
    elapsed_sec: float
    cost_usd: float | None
    error: str | None

# 2. _extract_summary_json() — regex 기반 SUMMARY_JSON 파싱 — 그대로 사용
_SUMMARY_RE = re.compile(r"##\s*SUMMARY_JSON\s*\n+\s*(\{.*\})", re.DOTALL)

# 3. run_single_agent() 핵심 구조 — 거의 그대로 사용
async with ClaudeSDKClient(options=options) as client:
    await client.query(user_prompt)
    async for message in client.receive_response():
        ...

# 4. asyncio.gather 병렬 실행 패턴 — 그대로 사용
tasks = [run_single_agent(k, ticker, market) for k in agent_keys]
reports = await asyncio.gather(*tasks)

# 5. run_orchestrator() + anthropic.AsyncAnthropic 직접 호출 패턴 — 그대로 사용
# 6. export_to_excel() — 컬럼 구조만 조정하여 재사용
# 7. AnalysisResult.to_markdown() — 구조 변경 후 재사용
```

**수정/제거:**
- `Phase 2 (C_notes handoff)` → 신규 파이프라인에서는 6개 에이전트 모두 독립 병렬 가능 (KR 주석 추출 의존성 없음)
- `extra_context` 전달 패턴 → Agent 6 (Catalyst)만 선택적으로 Agent 1~3의 red_flags 받는 방식으로 변형 가능

---

### 1-D. main.py CLI — 유지 vs 폐기

| 인자 | 판단 | 이유 |
|---|---|---|
| `tickers` (positional) | **유지** | 핵심 입력 |
| `--deep` | **유지** | 심층 분석 모드 유용 |
| `--no-orchestrator` | **유지** | 빠른 실행 / 개발 디버깅 |
| `--json` | **유지** | 파이프라인 통합 시 유용 |
| `--out` | **유지** | 리포트 파일 저장 |
| `--excel` / `--excel-path` | **유지** | 후보 누적 관리 |
| `--market` | **폐기** | US 전용으로 고정; 불필요 |

**추가 고려 인자:**
- `--tier-filter [1,2,3,4]` → 특정 Tier 이하만 리포트 출력
- `--since YYYY` → N-1 10-K diff 기준연도 지정

---

## Part 2: 새 6-Agent 아키텍처 검토

### 2-A. 6개 분할 적절성 평가

**전체 판단: 적절함. 단, 아래 2가지 고려 권장.**

**통합 검토 → 유지 권장:**
- Agent 1 (Accruals) + Agent 2 (Revenue Quality)는 데이터 소스가 겹치나, forensic framework가 다름
  - Agent 1: Sloan (balance sheet 기반 발생액) → 현금흐름 조작
  - Agent 2: Channel stuffing/DSO divergence → 매출 타이밍 조작
  - 같은 재무제표를 보지만 다른 렌즈. **분리 유지 권장**

- Agent 4 (10-K Diff) + Agent 5 (Earnings Call)은 모두 NLP 기반이나 데이터 소스가 완전히 다름
  - Agent 4: SEC EDGAR 10-K 전문 텍스트
  - Agent 5: 어닝스 콜 트랜스크립트 (별도 소스)
  - **분리 유지 권장**

**분리 검토 → Agent 3 세분화 옵션:**

> **옵션 A: 현행 유지 (Agent 3 = Capex/Depreciation + Useful Life + Capitalized R&D)**
> - 장점: 6개 에이전트로 깔끔, 병렬 오버헤드 최소
> - 단점: AI 인프라 핵심 이슈(서버 감가상각 life extension)가 다른 항목과 묻힐 수 있음

> **옵션 B: Agent 3을 3A(Capex/Dep Ratio) + 3B(Useful Life Narrative) 로 분리 → 7 agents**
> - 장점: Useful Life 변경은 주석+MD&A 분석이 필요 → 별도 NLP 에이전트가 더 깊이 파고들 수 있음
> - 단점: 에이전트 7개로 복잡도↑, 초기 파이프라인에는 과도

**권장: 옵션 A (6개 유지). Useful Life 트래킹은 Agent 3 시스템 프롬프트에 10-K MD&A 인용 의무화로 보강.**

---

### 2-B. 각 Agent 시스템 프롬프트 핵심 메시지 (한 줄 제안)

```
Agent 1 — Accruals & Cash Flow Quality
"Expose earnings manipulation by decomposing the spread between 
 reported net income and operating cash flow via Sloan accruals, 
 CFO/NI conversion ratio, and off-balance-sheet factoring detection 
 under SAB 11 indicators."

Agent 2 — Revenue Quality
"Identify premature or fictitious revenue recognition by tracking 
 DSO trend, AR-vs-Revenue growth divergence, deferred revenue / RPO 
 trajectory, and channel inventory proxy signals in US AI infrastructure 
 companies."

Agent 3 — Capitalization & Useful Life
"Detect earnings inflation through capitalization abuse by monitoring 
 Capex/Depreciation ratios, server and network equipment useful life 
 extension patterns across fiscal years, and capitalized internal-use 
 software / R&D as a share of total operating costs."

Agent 4 — 10-K Language Diff
"Surface accounting quality erosion by systematically diffing N-1 vs N 
 10-K filings for tone shifts in Risk Factors, MD&A, and Critical 
 Accounting Estimates, with emphasis on related-party transaction 
 changes and qualifying language added or removed."

Agent 5 — Earnings Call Language
"Detect management credibility deterioration by tracking KPI substitution 
 patterns, hedging-language frequency escalation, and the proliferation 
 of non-GAAP metrics across sequential earnings call transcripts."

Agent 6 — Catalyst & Personnel Monitor
"Identify high-probability short catalysts by monitoring SEC event filings 
 (8-K Items 4.02 / 5.02 / 8.01), clustered insider Form 4 sell 
 transactions, SEC CORRESP/UPLOAD correspondence chains, and 
 CFO/auditor turnover timing."
```

---

### 2-C. asyncio.gather 병렬 처리 구조 재사용 가능성

**결론: 그대로 재사용 가능. 오히려 구조가 단순해짐.**

기존 파이프라인은 KR 주석 추출 때문에 Phase 1 → Phase 2 (C_notes sequential handoff) → Phase 3 (Orchestrator) 3단계가 필요했음.

신규 파이프라인은 6개 에이전트 모두 미국 EDGAR/Yahoo 데이터 독립 접근 → 순차 의존성 없음:

```python
# 신규 파이프라인 — Phase 구조 단순화
# Phase 1: 6개 에이전트 전부 병렬 실행
phase1_keys = ["accruals", "revenue", "capex", "tenk_diff", "call_nlp", "catalyst"]
tasks = [run_single_agent(k, ticker, "US") for k in phase1_keys]
reports = await asyncio.gather(*tasks)   # 기존 코드 그대로

# Phase 2: Opus Orchestrator 총괄 (기존 run_orchestrator() 구조 재사용)
orch_report = await run_orchestrator(interim)
```

**선택적 Phase 2.5 (옵션):** Agent 6이 Agent 1~3의 `top_red_flags`를 context로 받아 보강 탐색하는 sequential handoff를 추가할 수 있음. 하지만 초기 파이프라인에서는 생략 권장 (복잡도 대비 효과 불확실).

**변경 없이 재사용 가능한 것들:**
- `AgentReport` / `AnalysisResult` 데이터클래스
- `run_single_agent()` 함수 (agent_key, ticker, market 인자 그대로)
- `_extract_summary_json()` 유틸리티
- `asyncio.gather` 패턴
- `run_orchestrator()` 함수 (프롬프트 텍스트만 교체)
- `export_to_excel()` (컬럼 스키마 조정)
- `main.py` CLI 구조 전체

---

## Part 3: 출력 형식 설계

### 3-A. Forensic Score 정의

**0 ~ 100점. 점수가 낮을수록 회계 품질이 의심스러움 (Short 후보에 가까움).**

> 기존 시스템(높을수록 좋은 투자처)과 반전: Forensic pipeline의 목적이 short 후보 발굴이므로 낮은 점수 = 더 위험 = 관심 대상. 오케스트레이터 프롬프트에 명시 필요.

**Sub-score 구조 (각 에이전트 → 가중치 합산):**

| Agent | Sub-score 이름 | 기본 가중치 |
|---|---|---|
| Agent 1 | `accruals_score` (0~100) | 20% |
| Agent 2 | `revenue_quality_score` (0~100) | 20% |
| Agent 3 | `capex_score` (0~100) | 15% |
| Agent 4 | `tenk_diff_score` (0~100) | 20% |
| Agent 5 | `call_nlp_score` (0~100) | 10% |
| Agent 6 | `catalyst_score` (0~100) | 15% |

> 가중치는 Orchestrator가 섹터/개별 기업 특성에 따라 최종 조정. 기본값은 위 비율.

---

### 3-B. Tier 분류

| Tier | 이름 | Forensic Score 범위 | 의미 |
|---|---|---|---|
| **Tier 1** | Active Short | 0 ~ 30 | 복수의 hard red flag; 즉각 short 검토 |
| **Tier 2** | Monitor | 31 ~ 55 | 단서 포착; 다음 10-Q/어닝스 콜 집중 추적 |
| **Tier 3** | Avoid | 56 ~ 70 | Yellow flag; 신규 포지션 개시 자제 |
| **Tier 4** | Archive | 71 ~ 100 | 현재 forensic 신호 없음; 분기별 재검토 |

---

### 3-C. SUMMARY_JSON 스키마 (최종 Orchestrator 출력)

```json
{
  "ticker": "NVDA",
  "company_name": "NVIDIA Corporation",
  "analysis_date": "2026-05-22",
  "forensic_score": 42,
  "tier": 2,
  "tier_label": "Monitor",
  "sub_scores": {
    "accruals_score":       {"score": 35, "weight": 0.20, "weighted": 7.0},
    "revenue_quality_score":{"score": 50, "weight": 0.20, "weighted": 10.0},
    "capex_score":          {"score": 40, "weight": 0.15, "weighted": 6.0},
    "tenk_diff_score":      {"score": 45, "weight": 0.20, "weighted": 9.0},
    "call_nlp_score":       {"score": 60, "weight": 0.10, "weighted": 6.0},
    "catalyst_score":       {"score": 40, "weight": 0.15, "weighted": 6.0}
  },
  "red_flags": [
    {
      "agent": "accruals",
      "severity": "HIGH",
      "flag": "3-year consecutive NI > OCF; Sloan accrual ratio +0.08 (above 0.05 threshold)",
      "evidence": "FY2024 NI $29.8B vs OCF $17.6B; 3-year TATA avg +0.062"
    },
    {
      "agent": "capex",
      "severity": "MEDIUM",
      "flag": "Server useful life extended from 4yr to 5yr (FY2023 10-K MD&A)",
      "evidence": "Depreciation/Capex ratio fell from 0.82 to 0.61 post-extension"
    }
  ],
  "next_action": {
    "priority": "HIGH",
    "actions": [
      "Pull FY2025 10-K when filed; verify useful life disclosure in Note 2",
      "Monitor Q2 2026 earnings call for further KPI substitution (RPO vs Backlog)",
      "Check Form 4 filings for insider sell clusters in next 30 days"
    ]
  },
  "orchestrator_narrative": "...",
  "confidence": "MEDIUM"
}
```

---

### 3-D. 각 에이전트 SUMMARY_JSON 스키마

**Agent 1 — Accruals:**
```json
{
  "sloan_accrual_ratio": 0.062,
  "cfo_ni_ratio": 0.59,
  "ni_vs_ocf_divergence_3yr": "PERSISTENT",
  "factoring_signal": "DETECTED|NONE|UNCERTAIN",
  "accruals_score": 35,
  "red_flags": ["3-year NI > OCF", "AR securitization disclosure added FY2023"]
}
```

**Agent 2 — Revenue Quality:**
```json
{
  "dso_trend": "+12 days YoY",
  "ar_vs_rev_growth_spread": "+8.3%p",
  "deferred_revenue_trajectory": "DECLINING",
  "rpo_disclosure_consistency": "INCONSISTENT",
  "channel_inventory_signal": "ELEVATED|NORMAL|UNKNOWN",
  "revenue_quality_score": 50,
  "red_flags": ["DSO expanding 3 consecutive quarters"]
}
```

**Agent 3 — Capex/Depreciation:**
```json
{
  "capex_dep_ratio": 2.1,
  "useful_life_years_current": 5,
  "useful_life_years_prior": 4,
  "useful_life_changed": true,
  "capitalized_rd_pct_opex": 0.18,
  "capex_score": 40,
  "red_flags": ["Useful life extended by 1yr adding est. $4.2B to FY2023 NI"]
}
```

**Agent 4 — 10-K Language Diff:**
```json
{
  "new_risk_factors": ["Data center concentration risk added", "Customer concentration >10% removed"],
  "cae_changes": ["Revenue recognition threshold lowered from 'reasonably certain' to 'probable'"],
  "related_party_delta": "INCREASED",
  "tone_shift": "MORE_HEDGED|LESS_HEDGED|STABLE",
  "tenk_diff_score": 45,
  "red_flags": ["Critical accounting estimate for useful life modified mid-cycle"]
}
```

**Agent 5 — Earnings Call Language:**
```json
{
  "kpi_substitution_detected": true,
  "dropped_kpis": ["GPU unit shipments"],
  "added_kpis": ["Sovereign AI deals", "NIM pipeline"],
  "hedging_language_frequency_delta": "+23%",
  "non_gaap_metric_count": 7,
  "call_nlp_score": 60,
  "red_flags": ["GPU unit volume KPI silently dropped after Q2 2025"]
}
```

**Agent 6 — Catalyst Monitor:**
```json
{
  "sec_8k_alerts": [
    {"item": "5.02", "date": "2026-04-15", "desc": "CFO departure announced"}
  ],
  "form4_insider_sells": {
    "last_90d_count": 8,
    "total_value_usd": 142000000,
    "signal": "ELEVATED"
  },
  "sec_corresp_active": false,
  "auditor_change": false,
  "cfo_change_within_12m": true,
  "catalyst_score": 40,
  "red_flags": ["CFO departure 6mo post-audit; Form 4 cluster $142M past 90d"]
}
```

---

## Part 4: 합의된 아키텍처 요약

```
┌─────────────────────────────────────────────────────────────┐
│           AI Infrastructure Forensic Pipeline               │
│                   US Only / Short-Hunting                   │
└─────────────────────────────────────────────────────────────┘

Phase 1: asyncio.gather (6개 병렬)
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Agent 1  │ │ Agent 2  │ │ Agent 3  │
│ Accruals │ │ Revenue  │ │ Capex/   │
│ & CF     │ │ Quality  │ │ Dep Life │
└──────────┘ └──────────┘ └──────────┘
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Agent 4  │ │ Agent 5  │ │ Agent 6  │
│ 10-K     │ │ Earnings │ │ Catalyst │
│ Diff     │ │ Call NLP │ │ Monitor  │
└──────────┘ └──────────┘ └──────────┘
         │
         ▼
Phase 2: Opus Orchestrator
  - 6개 sub-score 가중 합산
  - Forensic Score (0~100, 낮을수록 위험)
  - Tier 분류 (1~4)
  - Red flag 종합 + Next Action 생성
         │
         ▼
Output: .md Report + candidates.xlsx (누적)
```

**데이터 소스 맵:**
```
Agent 1, 2, 3  → yfinance (.financials/.cashflow/.balance_sheet)
                  + SEC EDGAR XBRL (구조화 재무 데이터)
Agent 4        → SEC EDGAR Full-Text (10-K 원문 2년치)
Agent 5        → Earnings call transcripts (신규 소스 필요)
Agent 6        → SEC EDGAR (8-K atom, Form 4, CORRESP type)
Orchestrator   → Anthropic API direct (claude-opus-4-6)
```

**MCP Server 구조 변경:**
- `STOCK_DATA_SERVER` 유지, KR 도구 제거
- 신규 추가: `sec_10k_fulltext()`, `sec_form4()`, `sec_corresp()`, `earnings_transcript()`
- 서버 이름 변경 권장: `"stock-data"` → `"forensic-data"`

---

## Part 5: Session 2 작업 계획

### Session 2: data_sources.py 재작성 (US Forensic 전용)

**우선순위 1 — 신규 데이터 소스 구현:**
1. `sec_10k_fulltext(ticker, year)` → EDGAR full-text search API 또는 직접 파일 다운로드
2. `sec_form4(ticker, days=90)` → EDGAR insider transactions
3. `sec_8k_items(ticker, items=["4.02","5.02","8.01"])` → 8-K 특정 Item 필터링
4. `sec_corresp(ticker)` → SEC 서신 왕래 탐지
5. `earnings_transcript(ticker, quarters=4)` → 트랜스크립트 소스 결정 필요

**우선순위 2 — 기존 US 소스 강화:**
- `yahoo_financial_statements()` → SEC EDGAR XBRL로 교체 또는 보강 (yfinance 신뢰도 한계)
- `sec_recent_filings()` → `sec_8k_items()` 전용 함수로 분리

**결정 미결 사항 (Session 2에서 결론):**
- 어닝스 콜 트랜스크립트 소스: **옵션 A** = FinancialModelingPrep API (유료, $29/mo) vs **옵션 B** = Motley Fool/Seeking Alpha 스크래핑 (무료, 법적 그레이존)
- 10-K diff 방식: **옵션 A** = EDGAR full-text search API (`efts.sec.gov`) vs **옵션 B** = 직접 10-K ZIP 다운로드 + BeautifulSoup (기존 `dart_notes_text()` 패턴 재사용)

### Session 3: agents.py 재작성 (6 agents)
- 6개 에이전트 시스템 프롬프트 완성 (이번 세션 2-B 초안 기반)
- MCP 도구 allowed_tools 재설계
- `AGENT_REGISTRY` 업데이트

### Session 4: orchestrator.py 수정
- Phase 구조 단순화 (2-phase: parallel + orchestrator)
- Forensic Score 계산 로직 (`calculate_forensic_score()`)
- Tier 분류 함수 (`classify_tier()`)
- `AnalysisResult.to_markdown()` 출력 형식 재작성

### Session 5: 통합 테스트
- 파일럿 종목 3개로 E2E 실행 (예: MSFT, META, SMCI — 수준 차이 큰 종목 혼합)
- Sub-score 캘리브레이션 (Tier 1/2 비율이 전체 유니버스의 15~25%가 목표)
- 리포트 출력 품질 검증

---

## 부록: 검토 중인 AI 인프라 섹터 유니버스 (초안)

**Hyperscaler Core:**
MSFT, GOOGL, AMZN, META

**AI Chip / Hardware:**
NVDA, AMD, INTC, MRVL, AVGO

**Adjacent Infrastructure:**
SMCI (서버), VRT (전력), EATON, NUE (강재)  
EQIX, DLR, AMT (데이터센터 REIT)

**Networking:**
CSCO, ANET, JNPR

> 총 ~20개 종목. Tier 1/2 후보를 분기별로 5~8개 내외로 추리는 것이 목표.

---

*Session 1 완료. 다음 작업: Session 2 — data_sources.py 재작성.*
