"""test_session6.py — Session 6 Agent 5-6 통합 테스트.

NVDA 더미/실제 데이터로 전체 파이프라인 검증.

실행:
    cd forensic-project/agents
    python test_session6.py

환경변수:
    ANTHROPIC_API_KEY  LLM 테스트 필요
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 테스트 1: call_metrics.py — Python 사전 계산 (LLM 없음)
# ---------------------------------------------------------------------------

def test_call_metrics_dummy() -> None:
    """더미 transcript 4개로 call_metrics.py 검증."""
    from call_metrics import analyze_earnings_calls, agent5_precomputed

    print(f"\n{'='*60}")
    print("  [Test 1] call_metrics.py — 더미 transcript NLP 검증")
    print('='*60)

    # NVDA 스타일 더미 transcript 4분기
    transcripts = [
        # Q4 2022 (오래된 것)
        """Q4 2022 Earnings Call - NVIDIA Corporation

        Revenue for Q4 2022 was $6.05 billion. Net income was $1.41 billion.
        Operating income was $1.56 billion. Earnings per share $0.57.
        Free cash flow was $2.15 billion.
        Record data center revenue. Strong GPU demand from hyperscalers.

        Q&A Section:
        Analyst: Can you provide GPU unit shipments by segment?
        Management: We don't break out GPU unit shipments. What I can tell you
        is that our data center business continues to see robust demand.
        """,

        # Q1 2023
        """Q1 2023 Earnings Call - NVIDIA Corporation

        Revenue $7.19 billion. Net income $2.04 billion.
        Non-GAAP EPS $1.09. Adjusted EBITDA approximately $2.8 billion.
        Free cash flow $2.64 billion. Adjusted operating margin 53%.
        We believe demand remains strong.

        Q&A Section:
        Analyst: Can you discuss the useful life of your data center equipment?
        Management: We don't provide that level of detail for competitive reasons.
        We review our useful life assumptions annually as part of standard practice.
        """,

        # Q2 2023
        """Q2 2023 Earnings Call - NVIDIA Corporation

        Revenue $13.51 billion, record quarter. Non-GAAP EPS $2.70.
        Adjusted EBITDA $8.83 billion. Core earnings $4.50 billion.
        We expect continued strength. We anticipate approximately $16 billion next quarter.
        We believe the AI demand environment remains exceptional.
        Momentum is strong across all customer verticals.

        Q&A Section:
        Analyst: Are you seeing any customer concentration risk with large cloud customers?
        Management: I'm not sure I fully understand the question in terms of what you're
        looking for. What we can say is that we work closely with all our partners.
        """,

        # Q3 2023 (최신, hedging 증가)
        """Q3 2023 Earnings Call - NVIDIA Corporation

        Revenue approximately $18.1 billion. Non-GAAP EPS $4.02.
        Adjusted EBITDA, adjusted FCF, adjusted gross margin, core operating income,
        normalized revenue run-rate, ARR bookings pipeline engagement.
        We believe, we expect, we anticipate, we cannot guarantee, approximately,
        subject to change, may not, could adversely, challenging macro environment.
        Timing of certain customer deliveries may be lumpy.
        We are not providing specific guidance for the upcoming quarter due to
        macroeconomic uncertainty.

        Q&A Section:
        Analyst: Can you break out the revenue contribution from CoreWeave?
        Management: We don't break that out for individual customers.
        Analyst: Are you concerned about customer concentration?
        Management: I'm not sure I follow the question exactly. We have a diverse
        customer base and we'll take that offline and get back to you.
        """,
    ]

    result = analyze_earnings_calls("NVDA", transcripts)

    print(f"\n  분기 분석: {result['quarters_analyzed']}개")
    print(f"  Hedging 트렌드: {result['hedging_trend'].get('trend')}")
    print(f"  Hedging 변화율: {result['hedging_trend'].get('hedge_delta_pct')}%")
    print(f"  Confidence 트렌드: {result['confidence_trend'].get('trend')}")
    print(f"  Non-GAAP 트렌드: {result['non_gaap_trend'].get('trend')}")
    print(f"  KPI 대체 탐지: {result['flags'].get('kpi_substitution_detected')}")
    print(f"  신규 KPI 첫 등장: {result['flags'].get('new_kpi_first_quarter')}")
    print(f"  가이던스 품질: {result['guidance_quality_latest']}")
    print(f"\n  Q&A 회피 패턴 ({len(result['qa_evasions'])}건):")
    for ev in result['qa_evasions']:
        print(f"    [{ev['quarter']}] {ev['evasion_type']} — 주제: {ev['question_topic']}")

    print(f"\n  🚩 플래그:")
    for k, v in result['flags'].items():
        if v and v is not False and v != 'N/A':
            print(f"    {k}: {v}")

    print("\n  분기별 요약:")
    print("  ┌──────────────┬──────┬──────────┬──────────┬──────────┬──────────┐")
    print("  │ Quarter      │ GAAP │ Non-GAAP │ New KPIs │ Hedging  │ Conf.    │")
    print("  ├──────────────┼──────┼──────────┼──────────┼──────────┼──────────┤")
    for q in result['by_quarter']:
        kpis = q.get('kpis', {})
        print(
            f"  │ {q['quarter'][:14]:14} "
            f"│ {kpis.get('gaap_core', 0):4d} "
            f"│ {kpis.get('non_gaap', 0):8d} "
            f"│ {kpis.get('new_kpis_to_watch', 0):8d} "
            f"│ {q.get('hedge_density', 0):.4f}   "
            f"│ {q.get('confidence', 0):8d} │"
        )
    print("  └──────────────┴──────┴──────────┴──────────┴──────────┴──────────┘")

    print("\n  ✅ Test 1 완료")


# ---------------------------------------------------------------------------
# 테스트 2: catalyst_monitor.py — Python 사전 계산 (LLM 없음)
# ---------------------------------------------------------------------------

def test_catalyst_monitor_dummy() -> None:
    """더미 SEC 데이터로 catalyst_monitor.py 검증."""
    from catalyst_monitor import monitor_catalysts

    print(f"\n{'='*60}")
    print("  [Test 2] catalyst_monitor.py — 더미 이벤트 분류 검증")
    print('='*60)

    # 더미 8-K 이벤트
    dummy_8k = {
        "filings": [
            {
                "date": "2024-11-15",
                "item": "5.02",
                "text": (
                    "Chief Financial Officer Jane Smith notified the company "
                    "of her resignation effective December 31, 2024, "
                    "to pursue other opportunities."
                ),
            },
            {
                "date": "2024-10-01",
                "item": "8.01",
                "text": (
                    "The Company received a subpoena from the SEC Division of "
                    "Enforcement requesting documents related to revenue recognition "
                    "practices for certain customer arrangements."
                ),
            },
            {
                "date": "2024-09-15",
                "item": "4.01",
                "text": (
                    "PricewaterhouseCoopers LLP resigned as the Company's "
                    "independent registered public accounting firm. "
                    "The Company has engaged Deloitte & Touche LLP."
                ),
            },
        ]
    }

    # 더미 CORRESP
    dummy_corresp = {
        "letters": [
            {
                "date": "2024-08-01",
                "summary": (
                    "SEC Staff questions regarding revenue recognition timing "
                    "under ASC 606 for cloud service arrangements."
                ),
            },
            {
                "date": "2024-09-20",
                "summary": (
                    "Follow-up SEC Staff questions regarding revenue recognition "
                    "timing — same issue raised in August correspondence."
                ),
            },
        ]
    }

    # 더미 Form 4
    dummy_form4 = {
        "transactions": [
            {
                "date": "2024-11-10",
                "type": "S",
                "shares": 50000,
                "price": 500.0,
                "role": "Chief Financial Officer",
                "is_10b5_1": False,
            },
            {
                "date": "2024-11-12",
                "type": "S",
                "shares": 30000,
                "price": 495.0,
                "role": "Chief Executive Officer",
                "is_10b5_1": False,
            },
            {
                "date": "2024-11-13",
                "type": "S",
                "shares": 20000,
                "price": 490.0,
                "role": "Chief Accounting Officer",
                "is_10b5_1": False,
            },
            {
                "date": "2024-11-10",
                "type": "S",
                "shares": 10000,
                "price": 500.0,
                "role": "General Counsel",
                "is_10b5_1": False,
            },
            {
                "date": "2024-11-15",
                "type": "S",
                "shares": 5000,
                "price": 485.0,
                "role": "SVP Finance",
                "is_10b5_1": False,
            },
            {
                "date": "2024-10-01",
                "type": "S",
                "shares": 15000,
                "price": 510.0,
                "role": "EVP Operations",
                "is_10b5_1": True,  # 10b5-1 계획
            },
        ]
    }

    result = monitor_catalysts(
        ticker="TESTCO",
        raw_8k=dummy_8k,
        raw_corresp=dummy_corresp,
        raw_form4=dummy_form4,
        lookback_days=730,   # 더미 데이터가 2024년 기준 (현재 2026년)
    )

    print(f"\n  Catalyst Probability: {result['catalyst_probability']}")
    print(f"  Max Severity: {result['max_severity']}")
    print(f"  Has Active Catalyst: {result['has_active_catalyst']}")

    print(f"\n  이벤트 목록 ({len(result['active_catalysts'])}건):")
    for ev in result['active_catalysts']:
        print(
            f"    [{ev['severity']:3d}] [{ev['date']}] "
            f"{ev['category'][:50]}"
        )
        if ev['follow_up']:
            print(f"           → {ev['follow_up'][0]}")

    ip = result['insider_pattern']
    print(f"\n  Insider Pattern:")
    print(f"    재량 매도: {ip.get('sell_count_discretionary')}건  "
          f"(${ip.get('discretionary_sales_usd', 0):,.0f})")
    print(f"    Signal: {ip.get('signal')}")
    print(f"    CFO/CEO 대규모: {ip.get('cfo_ceo_large_sale')}")
    print(f"    클러스터: {ip.get('cluster_detected')}")
    print(f"    매도 임원: {ip.get('executives_selling')}")

    print(f"\n  🚩 플래그:")
    for k, v in result['flags'].items():
        if v and v is not False and v not in (0, "NONE", "NORMAL", "UNKNOWN"):
            print(f"    {k}: {v}")

    print("\n  ✅ Test 2 완료")


# ---------------------------------------------------------------------------
# 테스트 3: 실제 SEC 데이터 (NVDA) catalyst 사전 계산
# ---------------------------------------------------------------------------

async def test_real_catalyst_fetch() -> None:
    """실제 SEC EDGAR 데이터로 NVDA catalyst 계산."""
    print(f"\n{'='*60}")
    print("  [Test 3] 실제 SEC 데이터 NVDA Catalyst 사전 계산")
    print('='*60)

    import data_sources as ds
    from catalyst_monitor import agent6_precomputed

    print("  📡 SEC 8-K / CORRESP / Form4 병렬 fetch …")
    raw_8k, raw_corresp, raw_form4 = await asyncio.gather(
        ds.sec_8k_items("NVDA", items=["4.02","4.01","5.02","8.01","2.06"], days=365),
        ds.sec_corresp("NVDA", days=365),
        ds.sec_form4("NVDA", days=90),
    )

    result = agent6_precomputed(
        ticker="NVDA",
        raw_8k=raw_8k,
        raw_corresp=raw_corresp,
        raw_form4=raw_form4,
        lookback_days=365,
    )

    print(f"\n  Catalyst Probability: {result['catalyst_probability']}")
    print(f"  Max Severity: {result['max_severity']}")
    print(f"  Active Catalysts: {len(result['active_catalysts'])}건")
    print(f"  Insider Signal: {result['insider_pattern'].get('signal', 'N/A')}")

    print(f"\n  [요약]\n{result['summary_text'][:600]}")
    print("\n  ✅ Test 3 완료")


# ---------------------------------------------------------------------------
# 테스트 4: 실제 SEC 8-K 기반 Agent 5 사전 계산 (NVDA)
# ---------------------------------------------------------------------------

async def test_real_call_metrics_fetch() -> None:
    """실제 SEC 8-K earnings release로 NVDA Agent 5 사전 계산."""
    print(f"\n{'='*60}")
    print("  [Test 4] 실제 SEC 8-K Earnings Release — NVDA Agent 5")
    print('='*60)

    import data_sources as ds
    from call_metrics import agent5_precomputed

    print("  📡 SEC 8-K Earnings Release fetch (6분기) …")
    raw_earnings = await ds.sec_earnings_releases("NVDA", quarters=6)

    # 진단 출력
    releases_found = raw_earnings.get("releases_found", 0)
    print(f"  SEC 릴리즈 발견: {releases_found}건")
    for r in raw_earnings.get("releases", [])[:2]:
        text_len = len(r.get("text", ""))
        print(f"    [{r.get('filing_date')}] items={r.get('items')} text_len={text_len}")

    result = agent5_precomputed(
        ticker="NVDA",
        earnings_releases_raw=raw_earnings,
    )

    print(f"\n  분기 분석: {result['quarters_analyzed']}개")
    print(f"  Hedging 트렌드: {result['hedging_trend'].get('trend')}")
    print(f"  Non-GAAP 트렌드: {result['non_gaap_trend'].get('trend')}")
    print(f"  가이던스 품질: {result['guidance_quality_latest']}")
    print(f"  Q&A 회피: {len(result['qa_evasions'])}건")

    print(f"\n  [요약 (앞 500자)]\n{result['summary_text'][:500]}")
    print("\n  ✅ Test 4 완료")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("=" * 60)
    print("  Session 6 — Agent 5-6 테스트 스위트")
    print("  대상: NVDA (더미 + 실제)")
    print("=" * 60)

    # Test 1: call_metrics 더미 (LLM 없음)
    test_call_metrics_dummy()

    # Test 2: catalyst_monitor 더미 (LLM 없음)
    test_catalyst_monitor_dummy()

    # Test 3: 실제 SEC 데이터 catalyst (LLM 없음)
    await test_real_catalyst_fetch()

    # Test 4: 실제 SEC 8-K earnings release (LLM 없음)
    await test_real_call_metrics_fetch()

    print("\n✅ 모든 테스트 완료")


if __name__ == "__main__":
    asyncio.run(main())
