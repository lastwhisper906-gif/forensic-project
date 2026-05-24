"""test_session5.py — Session 5 Agent 4 Language Diff 통합 테스트.

NVDA 더미 10-K 텍스트로 전체 파이프라인 검증.

실행:
    cd forensic-project/agents
    python test_session5.py

환경변수:
    ANTHROPIC_API_KEY  필수 (LLM 테스트)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 테스트 1: forensic_engine.py — 기계적 diff 단독 검증 (LLM 없음)
# ---------------------------------------------------------------------------

async def test_mechanical_diff() -> None:
    """forensic_engine.py 기계적 diff 테스트 (LLM 호출 없음)."""
    print(f"\n{'='*60}")
    print("  [Test 1] forensic_engine.py — 기계적 Diff 검증")
    print('='*60)

    from forensic_engine import generate_forensic_diff_report, get_diff_summary_for_llm

    # NVDA 스타일 더미 10-K 섹션
    prior = {
        "ppe_useful_life_note": (
            "We depreciate compute equipment over 3 to 5 years, "
            "buildings over 20 years, and other equipment over 3 to 7 years."
        ),
        "revenue_recognition_note": (
            "Revenue is recognized when control transfers to the customer. "
            "Product revenue: generally at time of shipment."
        ),
        "item_1a_risk_factors": (
            "We depend on TSMC to fabricate our products. "
            "We face competition from AMD and Intel. "
            "Our products are subject to export control regulations."
        ),
        "related_party_transactions": (
            "We have not entered into any material related party transactions."
        ),
    }

    current = {
        "ppe_useful_life_note": (
            "We depreciate compute equipment over 3 to 7 years, "     # 5→7 연장
            "buildings over 20 years, and other equipment over 3 to 7 years. "
            "Effective fiscal 2025, we extended useful lives of data center equipment "
            "from five years to seven years."
        ),
        "revenue_recognition_note": (
            "Revenue is recognized when control transfers to the customer. "
            "Product revenue: generally at time of shipment. "
            "Extended payment terms of up to 180 days may apply to certain customers."  # 신규
        ),
        "item_1a_risk_factors": (
            "We depend on TSMC and Samsung to fabricate our products. "   # Samsung 추가
            # AMD/Intel competition risk 삭제됨
            "Our products are subject to export control regulations. "
            "We have significant customer concentration risk with certain hyperscalers."  # 신규
        ),
        "related_party_transactions": (
            "We have equity investments in certain customers. "    # 크게 변경됨
            "CoreWeave, Inc. is a customer and we hold an equity stake. "
            "Transactions with related parties are conducted at arm's length."
        ),
    }

    report = generate_forensic_diff_report(
        ticker="NVDA",
        sections_current=current,
        sections_prior=prior,
        fy_current="FY2024",
        fy_prior="FY2023",
    )

    print(f"\n  ✓ Diff 생성 완료")
    print(f"  섹션별 변화:")
    for sec_name, sd in report.sections.items():
        print(
            f"    [{sec_name}]  "
            f"added={sd.lines_added}  removed={sd.lines_removed}  "
            f"change_ratio={sd.change_ratio:.3f}  "
            f"risk_signal={sd.has_risk_signal}  "
            f"keywords={len(sd.keyword_hits)}"
        )

    # LLM 입력용 요약 (문자열 반환)
    summary_str = get_diff_summary_for_llm(report, max_chars=8000)
    print(f"\n  LLM 요약 길이: {len(summary_str)}자")
    print(f"\n  [요약 미리보기]\n{summary_str[:500]}")

    # keyword 탐지 결과
    all_kw: list[str] = []
    for sd in report.sections.values():
        all_kw.extend(kw.get("keyword", "") for kw in sd.keyword_hits)
    if all_kw:
        print(f"\n  🔑 탐지 키워드: {list(set(all_kw))}")

    print("\n  ✅ Test 1 완료")


# ---------------------------------------------------------------------------
# 테스트 2: diff_analyzer.py — Stage 1 분류만 (Haiku, 비용 최소)
# ---------------------------------------------------------------------------

async def test_stage1_classify() -> None:
    """diff_analyzer Stage 1 Haiku 분류 테스트."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n⚠ ANTHROPIC_API_KEY 없음 — Stage 1 분류 스킵")
        return

    print(f"\n{'='*60}")
    print("  [Test 2] diff_analyzer — Stage 1 Haiku 분류 (NVDA 더미)")
    print('='*60)

    from forensic_engine import generate_forensic_diff_report, get_diff_summary_for_llm
    from diff_analyzer import analyze_diff_with_llm, _MODEL_HAIKU

    # 같은 더미 데이터 사용
    prior = {
        "ppe_useful_life_note": (
            "Compute equipment: 3 to 5 years. Buildings: 20 years."
        ),
        "item_1a_risk_factors": (
            "We depend on TSMC. We face competition from AMD and Intel. "
            "Products subject to export controls."
        ),
    }
    current = {
        "ppe_useful_life_note": (
            "Compute equipment: 3 to 7 years. Buildings: 20 years. "  # 연장
            "Effective FY2025, extended from 5 to 7 years."
        ),
        "item_1a_risk_factors": (
            "We depend on TSMC and Samsung. "
            "Products subject to export controls. "
            "Significant customer concentration with hyperscalers."   # 경쟁 risk 삭제, 집중도 risk 추가
        ),
    }

    report = generate_forensic_diff_report(
        ticker="NVDA",
        sections_current=current,
        sections_prior=prior,
        fy_current="FY2024",
        fy_prior="FY2023",
    )
    diff_summary = get_diff_summary_for_llm(report)
    diff_summary.update({"fy_current": "FY2024", "fy_prior": "FY2023", "ticker": "NVDA"})

    print("  🤖 Stage 1 Haiku 분류 실행 중 …")
    result = await analyze_diff_with_llm(
        diff_result=diff_summary,
        ticker="NVDA",
        stage1_model=_MODEL_HAIKU,
        stage2_model=_MODEL_HAIKU,   # 비용 절감: Stage 2도 Haiku 사용
    )

    print(f"\n  💰 비용: ${result['total_cost_usd']:.5f}")
    print(f"  HIGH findings: {result['high_count']}  MEDIUM: {result['medium_count']}")
    print(f"\n  Stage 1 분류 결과:")
    for c in result["classifications"]:
        arrow = "→ Stage2" if c["advance_to_stage2"] else ""
        print(
            f"    [{c['severity']:7}] {c['section_name']:<35} "
            f"{c['change_type']:<30} {arrow}"
        )
        print(f"           {c['one_liner']}")

    print("\n  ✅ Test 2 완료")
    return result


# ---------------------------------------------------------------------------
# 테스트 3: render_memo_from_sections (전체 파이프라인, Sonnet Stage 2)
# ---------------------------------------------------------------------------

async def test_full_diff_pipeline() -> None:
    """diff_analyzer 전체 파이프라인 테스트 (Stage 1 Haiku + Stage 2 Sonnet)."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n⚠ ANTHROPIC_API_KEY 없음 — 전체 파이프라인 스킵")
        return

    print(f"\n{'='*60}")
    print("  [Test 3] 전체 파이프라인: render_memo_from_sections (NVDA)")
    print('='*60)

    from diff_analyzer import render_memo_from_sections

    prior = {
        "ppe_useful_life_note": (
            "We depreciate our property, plant and equipment using the straight-line method "
            "over estimated useful lives of 3 to 5 years for compute equipment, "
            "20 years for buildings and 3 to 7 years for other equipment."
        ),
        "revenue_recognition_note": (
            "We recognize revenue when control transfers to the customer. "
            "For product revenue, control is generally transferred at time of shipment."
        ),
        "item_1a_risk_factors": (
            "We depend on TSMC to fabricate our products. "
            "Any disruption could materially harm our business. "
            "We face intense competition from AMD, Intel, and others."
        ),
        "related_party_transactions": (
            "We have not entered into any material related party transactions "
            "that would require disclosure."
        ),
    }

    current = {
        "ppe_useful_life_note": (
            "We depreciate our property, plant and equipment using the straight-line method "
            "over estimated useful lives of 3 to 7 years for compute equipment, "
            "20 years for buildings and 3 to 7 years for other equipment. "
            "Effective beginning of fiscal 2025, we extended the useful lives of "
            "certain data center compute equipment from five years to seven years "
            "based on our assessment of their operational lifespan and the expected "
            "period of future economic benefit."
        ),
        "revenue_recognition_note": (
            "We recognize revenue when control transfers to the customer. "
            "For product revenue, control is generally transferred at time of shipment. "
            "Certain large enterprise and cloud service provider customers may receive "
            "extended payment terms of up to 180 days, which do not represent "
            "a significant financing component."
        ),
        "item_1a_risk_factors": (
            "We depend on TSMC and Samsung to fabricate our products. "
            "Any disruption could materially harm our business. "
            "Our revenue is concentrated among a limited number of large customers, "
            "including cloud service providers and AI companies, and the loss of "
            "any significant customer could adversely affect our results."
        ),
        "related_party_transactions": (
            "We hold equity investments in certain companies that are also our customers, "
            "including CoreWeave, Inc. These companies purchase our products at prices "
            "and on terms consistent with those offered to unrelated customers. "
            "Revenue from related parties was approximately $X billion in fiscal 2024."
        ),
    }

    peer_ctx = {
        "peers": ["AMD", "AVGO", "MRVL"],
        "sector": "L2_Fabless_Semiconductor",
        "notes": "AMD, AVGO also operate server/data center equipment — peer useful life comparison relevant",
    }

    print("  🤖 Stage 1 (Haiku) + Stage 2 (Sonnet) 실행 중 …")
    result = await render_memo_from_sections(
        ticker="NVDA",
        sections_current=current,
        sections_prior=prior,
        fy_current="FY2024",
        fy_prior="FY2023",
        peer_context=peer_ctx,
        use_opus_executive=False,
    )

    print(f"\n  💰 총 비용: ${result['total_cost_usd']:.5f}")
    print(f"  HIGH: {result['high_count']}  MEDIUM: {result['medium_count']}")

    print(f"\n  Stage 1 분류:")
    for c in result["classifications"]:
        arrow = "→ Stage2" if c["advance_to_stage2"] else ""
        print(f"    [{c['severity']:7}] {c['section_name']}: {c['one_liner']} {arrow}")

    print(f"\n  Stage 2 Findings ({len(result['findings'])}개):")
    for f in result["findings"]:
        prio_sym = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(f["priority"], "⚪")
        print(f"\n  FINDING #{f['finding_no']}: {f['finding_title']}")
        print(f"  {prio_sym} Priority: {f['priority']}  Verdict: {f['verdict']}")
        print(f"  Impact: {f['impact_estimate']}")
        if f['kill_criteria'] and f['kill_criteria'] != 'N/A':
            print(f"  Kill: {f['kill_criteria'][:80]}")

    print(f"\n{'─'*60}")
    print("  [Memo Markdown Preview]")
    print(result["memo_markdown"][:2000])

    print("\n  ✅ Test 3 완료")
    return result


# ---------------------------------------------------------------------------
# 테스트 4: compute_diff_memo (orchestrator 통합)
# ---------------------------------------------------------------------------

async def test_orchestrator_diff_integration() -> None:
    """orchestrator.compute_diff_memo (실제 SEC fetch + diff) 통합 테스트.

    실제 SEC 데이터 사용. 비용 발생 주의.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n⚠ ANTHROPIC_API_KEY 없음 — orchestrator 통합 테스트 스킵")
        return

    print(f"\n{'='*60}")
    print("  [Test 4] orchestrator.compute_diff_memo (실제 SEC fetch)")
    print('='*60)

    from orchestrator import compute_diff_memo

    print("  📡 NVDA 10-K 섹션 SEC fetch + Language Diff 분석 …")
    memo = await compute_diff_memo(
        ticker="NVDA",
        peer_context={"peers": ["AMD", "AVGO"], "sector": "L2_Fabless"},
    )

    if memo is None:
        print("  ✗ compute_diff_memo 실패 또는 스킵됨")
        return

    print(f"\n  결과:")
    print(f"    FY: {memo.get('fy_prior')} → {memo.get('fy_current')}")
    print(f"    HIGH: {memo.get('high_count')}  MEDIUM: {memo.get('medium_count')}")
    print(f"    비용: ${memo.get('total_cost_usd', 0):.5f}")
    print(f"    섹션: {len(memo.get('classifications', []))}")

    print("\n  ✅ Test 4 완료")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("=" * 60)
    print("  Session 5 — Agent 4 Language Diff 테스트 스위트")
    print("  대상: NVDA (더미 + 실제)")
    print("=" * 60)

    # Test 1: 기계적 diff (LLM 없음)
    await test_mechanical_diff()

    # Test 2: Stage 1 Haiku 분류 (저비용)
    await test_stage1_classify()

    # Test 3: 전체 파이프라인 (Stage 1 + 2)
    await test_full_diff_pipeline()

    # Test 4: orchestrator 통합 (실제 SEC 데이터)
    await test_orchestrator_diff_integration()

    print("\n✅ 모든 테스트 완료")


if __name__ == "__main__":
    asyncio.run(main())
