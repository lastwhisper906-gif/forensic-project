"""test_session4.py — Session 4 Agent 1-3 통합 테스트.

NVDA, AMD 두 종목으로 사전 계산 메트릭 검증 + 에이전트 출력 비교.

실행:
    cd forensic-project/agents
    python test_session4.py

환경변수:
    ANTHROPIC_API_KEY  필수
    SEC_USER_AGENT     권장 (기본값 사용 가능)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 테스트 1: quant_metrics.py — 사전 계산 레이어 단독 검증
# ---------------------------------------------------------------------------

async def test_precomputed_metrics(ticker: str) -> None:
    """XBRL fetch + 정량 메트릭 계산만 실행 (LLM 호출 없음)."""
    print(f"\n{'='*60}")
    print(f"[Test 1] 정량 메트릭 사전 계산 검증: {ticker}")
    print('='*60)

    import data_sources as ds
    from peer_set import get_peer_set, get_sector_group
    from quant_metrics import agent1_precomputed, agent2_precomputed, agent3_precomputed

    sector = get_sector_group(ticker)
    peers  = await get_peer_set(ticker, sector)
    print(f"  Sector: {sector}")
    print(f"  Peers:  {peers}")

    # 기준 종목 XBRL
    print(f"\n  📡 {ticker} XBRL fetch (5년) …")
    ts = await ds.sec_xbrl_financials(ticker, years=5)
    entity = ts.get("meta", {}).get("entity_name", ticker)
    print(f"  ✓ {entity} — 수집 완료")
    print(f"    available concepts: {ts.get('meta', {}).get('concepts_found', [])}")

    # Peer XBRL 병렬 fetch
    print(f"\n  📡 Peer XBRL fetch: {peers} …")
    peer_tasks = {p: ds.sec_xbrl_financials(p, years=3) for p in peers}
    peer_results = await asyncio.gather(*peer_tasks.values(), return_exceptions=True)
    peer_ts_map = {
        p: r for p, r in zip(peer_tasks.keys(), peer_results)
        if isinstance(r, dict)
    }
    print(f"  ✓ Peer 수집: {len(peer_ts_map)}/{len(peers)}")

    # ----- Agent 1: Sloan Accruals -----
    print(f"\n  📊 Agent 1 — Accruals & Cash Flow Quality")
    a1 = agent1_precomputed(ts, peer_ts_map)

    sloan = a1["sloan_accruals"]
    if sloan.get("latest"):
        s = sloan["latest"]
        print(f"    Sloan Accruals (최신): {s['value']:.4f}  z-score: {s['z_score']}  flag: {s['flag']}")
        print(f"    Trend: {sloan.get('trend')}  Data Quality: {sloan.get('data_quality')}")
    else:
        print("    Sloan: 데이터 없음")

    ccr = a1["cash_conversion"]
    if ccr.get("latest"):
        c = ccr["latest"]
        print(f"    CCR (최신): {c['value']:.3f}  flag: {c['flag']}")
        print(f"    Quarters below 1.0: {ccr['quarters_below_1']}  Trend: {ccr['trend']}")
    else:
        print("    CCR: 데이터 없음")

    ocf = a1["ocf_composition"]
    if ocf.get("latest"):
        print(f"    OCF WC%: {ocf['latest']['working_capital_pct']:.3f}  flag: {ocf['latest']['flag']}")

    # ----- Agent 2: Revenue Quality -----
    print(f"\n  📊 Agent 2 — Revenue Quality")
    a2 = agent2_precomputed(ts, peer_ts_map)

    dso = a2["dso"]
    if dso.get("latest"):
        d = dso["latest"]
        print(f"    DSO (최신): {d['value']:.1f}일  z-score: {d['z_score']}")
        print(f"    YoY change: {dso.get('yoy_change_days')}일  Trend: {dso.get('trend')}")
    else:
        print("    DSO: 데이터 없음")

    ar_spread = a2["ar_revenue_spread"]
    if ar_spread.get("latest"):
        x = ar_spread["latest"]
        print(f"    AR/Rev Spread: {x['spread']:.4f}  DSRI: {x['dsri']}  flag: {x['flag']}")

    dr = a2["deferred_rev"]
    if dr.get("latest"):
        print(f"    Deferred Rev Trend: {dr['latest']['trend']}  flag: {dr['latest']['flag']}")

    gmi = a2["gmi"]
    print(f"    Beneish GMI: {gmi.get('gmi')}  flag: {gmi.get('flag')}")

    # ----- Agent 3: Capex / Useful Life -----
    print(f"\n  📊 Agent 3 — Capitalization & Useful Life")
    a3 = agent3_precomputed(ts, peer_ts_map)

    cdr = a3["capex_dep_ratio"]
    if cdr.get("latest"):
        r = cdr["latest"]
        print(f"    Capex/Dep (최신): {r['value']:.3f}  z-score: {r['z_score']}  flag: {r['flag']}")
        print(f"    Trend: {cdr.get('trend')}")
    else:
        print("    Capex/Dep: 데이터 없음")

    cap_rd = a3["cap_rd_ratio"]
    if cap_rd.get("latest") and cap_rd["latest"]["value"] is not None:
        print(f"    Cap R&D/Total R&D: {cap_rd['latest']['value']:.4f}  flag: {cap_rd['latest']['flag']}")
    else:
        print("    Cap R&D: 데이터 없음 (대부분 hyperscaler에서 별도 공시 없음)")

    aqi = a3["aqi"]
    print(f"    Beneish AQI: {aqi.get('aqi')}  flag: {aqi.get('flag')}")

    print(f"\n  ✅ {ticker} 사전 계산 완료")
    return a1, a2, a3


# ---------------------------------------------------------------------------
# 테스트 2: NVDA vs AMD 사전 계산 지표 비교
# ---------------------------------------------------------------------------

async def compare_nvda_amd() -> None:
    """NVDA와 AMD 정량 지표 나란히 비교."""
    print(f"\n{'='*60}")
    print("  [Test 2] NVDA vs AMD 정량 지표 비교")
    print('='*60)

    import data_sources as ds
    from peer_set import get_peer_set
    from quant_metrics import (
        compute_sloan_accruals, compute_cash_conversion,
        compute_dso, compute_capex_dep_ratio,
    )

    # 두 종목 동시 fetch
    print("  📡 NVDA, AMD XBRL fetch (5년) …")
    nvda_ts, amd_ts = await asyncio.gather(
        ds.sec_xbrl_financials("NVDA", years=5),
        ds.sec_xbrl_financials("AMD",  years=5),
    )
    print("  ✓ Fetch 완료")

    # NVDA의 peer map에 AMD 포함 (cross-comparison)
    nvda_peer_map = {"AMD": amd_ts}
    amd_peer_map  = {"NVDA": nvda_ts}

    # --- 비교표 출력 ---
    print("\n  ┌──────────────────────────┬────────────────┬────────────────┐")
    print("  │ 지표                     │      NVDA      │      AMD       │")
    print("  ├──────────────────────────┼────────────────┼────────────────┤")

    def fmt(val, decimals=3):
        if val is None:
            return "   N/A   "
        return f"{val:>10.{decimals}f}  "

    # Sloan Accruals
    n_sloan = compute_sloan_accruals(nvda_ts, nvda_peer_map)
    a_sloan = compute_sloan_accruals(amd_ts,  amd_peer_map)
    nv = n_sloan.get("latest") or {}
    av = a_sloan.get("latest") or {}
    print(f"  │ Sloan Accruals           │ {fmt(nv.get('value'), 4)} │ {fmt(av.get('value'), 4)} │")
    print(f"  │   z-score (peer)         │ {fmt(nv.get('z_score'), 3)} │ {fmt(av.get('z_score'), 3)} │")
    print(f"  │   trend                  │ {n_sloan.get('trend','?'):^14} │ {a_sloan.get('trend','?'):^14} │")

    # CCR
    n_ccr = compute_cash_conversion(nvda_ts, nvda_peer_map)
    a_ccr = compute_cash_conversion(amd_ts,  amd_peer_map)
    nc = n_ccr.get("latest") or {}
    ac = a_ccr.get("latest") or {}
    print(f"  │ Cash Conversion Ratio    │ {fmt(nc.get('value'), 3)} │ {fmt(ac.get('value'), 3)} │")
    print(f"  │   quarters_below_1       │ {str(n_ccr.get('quarters_below_1','?')):^14} │ {str(a_ccr.get('quarters_below_1','?')):^14} │")

    # DSO
    n_dso = compute_dso(nvda_ts, nvda_peer_map)
    a_dso = compute_dso(amd_ts,  amd_peer_map)
    nd = n_dso.get("latest") or {}
    ad = a_dso.get("latest") or {}
    print(f"  │ DSO (days)               │ {fmt(nd.get('value'), 1)} │ {fmt(ad.get('value'), 1)} │")
    print(f"  │   YoY change (days)      │ {fmt(n_dso.get('yoy_change_days'), 1)} │ {fmt(a_dso.get('yoy_change_days'), 1)} │")

    # Capex/Dep
    n_cd = compute_capex_dep_ratio(nvda_ts, nvda_peer_map)
    a_cd = compute_capex_dep_ratio(amd_ts,  amd_peer_map)
    nr = n_cd.get("latest") or {}
    ar = a_cd.get("latest") or {}
    print(f"  │ Capex/Dep Ratio          │ {fmt(nr.get('value'), 3)} │ {fmt(ar.get('value'), 3)} │")
    print(f"  │   trend                  │ {n_cd.get('trend','?'):^14} │ {a_cd.get('trend','?'):^14} │")

    print("  └──────────────────────────┴────────────────┴────────────────┘")

    # Red flag 집계
    def count_flags(d):
        count = 0
        for v in d.values():
            if isinstance(v, dict):
                if v.get("flag") is True:
                    count += 1
                lat = v.get("latest")
                if isinstance(lat, dict) and lat.get("flag") is True:
                    count += 1
        return count

    from quant_metrics import agent1_precomputed, agent2_precomputed, agent3_precomputed

    n_a1 = agent1_precomputed(nvda_ts, nvda_peer_map)
    n_a2 = agent2_precomputed(nvda_ts, nvda_peer_map)
    n_a3 = agent3_precomputed(nvda_ts, nvda_peer_map)
    a_a1 = agent1_precomputed(amd_ts,  amd_peer_map)
    a_a2 = agent2_precomputed(amd_ts,  amd_peer_map)
    a_a3 = agent3_precomputed(amd_ts,  amd_peer_map)

    n_flags = count_flags(n_a1) + count_flags(n_a2) + count_flags(n_a3)
    a_flags = count_flags(a_a1) + count_flags(a_a2) + count_flags(a_a3)

    print(f"\n  🚩 Python 단계 Flag 집계:")
    print(f"    NVDA: {n_flags}개 flag")
    print(f"    AMD:  {a_flags}개 flag")


# ---------------------------------------------------------------------------
# 테스트 3: 단일 에이전트 실행 (LLM 포함, 비용 주의)
# ---------------------------------------------------------------------------

async def test_single_agent_run(ticker: str = "NVDA", agent_key: str = "accruals") -> None:
    """단일 에이전트 실행 테스트 (LLM 호출 포함).

    ANTHROPIC_API_KEY 필요.
    """
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        print(f"\n⚠ ANTHROPIC_API_KEY 없음 — LLM 에이전트 테스트 스킵")
        return

    print(f"\n{'='*60}")
    print(f"  [Test 3] 단일 에이전트 실행: {ticker} / {agent_key}")
    print('='*60)

    from orchestrator import (
        fetch_single_xbrl, fetch_peer_xbrl_batch, compute_precomputed_context,
        run_single_agent,
    )
    from peer_set import get_peer_set, get_sector_group

    sector = get_sector_group(ticker)
    peers  = await get_peer_set(ticker, sector)

    print(f"  📡 XBRL fetch …")
    ts         = await fetch_single_xbrl(ticker, years=5)
    peer_ts    = await fetch_peer_xbrl_batch(ticker, peers, years=3)
    precomputed = compute_precomputed_context(agent_key, ts, peer_ts) if ts else None

    print(f"  🤖 {agent_key} 에이전트 실행 …")
    report = await run_single_agent(agent_key, ticker, precomputed=precomputed)

    if report.error:
        print(f"  ✗ 에러: {report.error}")
    else:
        print(f"  ✓ 완료 {report.elapsed_sec:.1f}s")
        print(f"  Summary JSON:\n{json.dumps(report.summary, ensure_ascii=False, indent=2)}")

        # narrative 출력
        if report.summary.get("narrative"):
            print(f"\n  Narrative:\n{report.summary['narrative'][:400]}…")


# ---------------------------------------------------------------------------
# 테스트 4: analyze_stocks (NVDA + AMD 전체 파이프라인, skip_orchestrator)
# ---------------------------------------------------------------------------

async def test_full_pipeline_dry_run() -> None:
    """NVDA + AMD 전체 파이프라인 — skip_orchestrator=True (빠른 실행).

    ANTHROPIC_API_KEY 필요.
    """
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        print(f"\n⚠ ANTHROPIC_API_KEY 없음 — 전체 파이프라인 테스트 스킵")
        return

    print(f"\n{'='*60}")
    print("  [Test 4] 전체 파이프라인 (skip_orchestrator=True)")
    print('='*60)

    from orchestrator import analyze_stocks, calculate_forensic_score

    results = await analyze_stocks(
        ["NVDA", "AMD"],
        skip_orchestrator=True,
    )

    for res in results:
        sc = calculate_forensic_score(res)
        print(f"\n  [{res.ticker}] Forensic Score: {sc['forensic_score']} / Tier: {sc['tier_label']}")
        for k, v in sc["sub_scores"].items():
            score = v.get("score", "N/A")
            err   = v.get("error", "")
            print(f"    {k:<30} score={score}  {err}")

        if sc["red_flags"]:
            print(f"\n  🚩 Red Flags ({len(sc['red_flags'])}개):")
            for f in sc["red_flags"][:5]:
                print(f"    [{f['agent']}] {str(f['flag'])[:80]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("=" * 60)
    print("  Session 4 — Agent 1-3 테스트 스위트")
    print("  대상: NVDA, AMD")
    print("=" * 60)

    # Test 1: 정량 메트릭 단독 검증 (LLM 없음)
    await test_precomputed_metrics("NVDA")
    await test_precomputed_metrics("AMD")

    # Test 2: NVDA vs AMD 비교표
    await compare_nvda_amd()

    # Test 3: 단일 에이전트 LLM 실행 (ANTHROPIC_API_KEY 있을 때만)
    await test_single_agent_run("NVDA", "accruals")

    # Test 4: 전체 파이프라인 (ANTHROPIC_API_KEY 있을 때만)
    await test_full_pipeline_dry_run()

    print("\n✅ 모든 테스트 완료")


if __name__ == "__main__":
    asyncio.run(main())
