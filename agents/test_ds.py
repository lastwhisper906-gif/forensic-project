"""test_ds.py — data_sources.py 8개 함수 통합 테스트.

NVDA (NVIDIA) 로 모든 함수를 순서대로 호출.

실행:
    cd agents
    python test_ds.py

    # 특정 그룹만:
    python test_ds.py --group A
    python test_ds.py --group B
    python test_ds.py --group C
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import traceback

try:
    from dotenv import load_dotenv
    load_dotenv()
    print("[+] .env 로드 완료")
except ImportError:
    print("[!] python-dotenv 없음 — 환경변수 수동 설정 필요")

import data_sources as ds

TICKER = "NVDA"
PASS = "✓"
FAIL = "✗"

# 공유 상태 (async 테스트 간 데이터 전달)
_state: dict = {}

results: list[tuple[str, str, float, str]] = []  # (status, name, elapsed, detail)


def record(status: str, name: str, elapsed: float, detail: str):
    results.append((status, name, elapsed, detail))
    icon = PASS if status == PASS else FAIL
    print(f"  {icon}  {name:<40} {elapsed:.1f}s  {detail[:70]}")


# ---------------------------------------------------------------------------
# GROUP A: Filing Metadata
# ---------------------------------------------------------------------------

async def test_get_company_cik():
    t0 = time.perf_counter()
    try:
        cik = await ds.get_company_cik(TICKER)
        _state["cik"] = cik
        record(PASS, "get_company_cik", time.perf_counter() - t0,
               f"CIK={cik}")
        return True
    except Exception:
        record(FAIL, "get_company_cik", time.perf_counter() - t0,
               traceback.format_exc().splitlines()[-1])
        return False


async def test_list_filings():
    cik = _state.get("cik")
    if not cik:
        record(FAIL, "list_filings", 0.0, "CIK 없음 — 이전 테스트 실패")
        return False

    t0 = time.perf_counter()
    try:
        filings_10k = await ds.list_filings(cik, "10-K", count=3)
        filings_10q = await ds.list_filings(cik, "10-Q", count=4)
        _state["filings_10k"] = filings_10k
        _state["filings_10q"] = filings_10q

        detail = (f"10-K: {len(filings_10k)}건  "
                  f"최신={filings_10k[0]['filing_date'] if filings_10k else 'N/A'}  "
                  f"10-Q: {len(filings_10q)}건")
        record(PASS, "list_filings", time.perf_counter() - t0, detail)

        # 상세 출력
        print()
        print("    [10-K filings]")
        for f in filings_10k[:3]:
            print(f"      {f['filing_date']}  {f['accession_no']}  period={f['period_of_report']}")
        print("    [10-Q filings]")
        for f in filings_10q[:4]:
            print(f"      {f['filing_date']}  {f['accession_no']}  period={f['period_of_report']}")
        print()
        return True
    except Exception:
        record(FAIL, "list_filings", time.perf_counter() - t0,
               traceback.format_exc().splitlines()[-1])
        return False


async def test_get_filing_index():
    filings = _state.get("filings_10k", [])
    cik     = _state.get("cik")
    if not filings or not cik:
        record(FAIL, "get_filing_index", 0.0, "10-K filing 정보 없음")
        return False

    t0 = time.perf_counter()
    accession_no = filings[0]["accession_no"]
    try:
        idx = await ds.get_filing_index(accession_no, cik)
        _state["filing_index"] = idx
        _state["latest_10k_accession"] = accession_no

        n_docs = len(idx["documents"])
        exhibit_types = list(idx["exhibit_map"].keys())[:6]
        detail = (f"문서 {n_docs}개  "
                  f"form_type={idx['form_type']}  "
                  f"exhibits={exhibit_types}")
        record(PASS, "get_filing_index", time.perf_counter() - t0, detail)

        print()
        print("    [Filing 문서 목록 (앞 8개)]")
        for doc in idx["documents"][:8]:
            print(f"      seq={doc['sequence']:>2}  type={doc['type']:<15}  {doc['document']}")
        print()
        return True
    except Exception:
        record(FAIL, "get_filing_index", time.perf_counter() - t0,
               traceback.format_exc().splitlines()[-1])
        return False


# ---------------------------------------------------------------------------
# GROUP B: Filing Text Extraction
# ---------------------------------------------------------------------------

async def test_fetch_10k_text():
    accession_no = _state.get("latest_10k_accession")
    cik          = _state.get("cik")
    if not accession_no or not cik:
        record(FAIL, "fetch_10k_text", 0.0, "accession_no 없음")
        return False

    t0 = time.perf_counter()
    try:
        text = await ds.fetch_10k_text(accession_no, cik)
        _state["text_10k"] = text

        # 간단 검증
        has_item1a = "item 1a" in text.lower() or "item1a" in text.lower()
        has_item7  = "item 7" in text.lower()
        detail = (f"{len(text):,} chars  "
                  f"Item1A={'✓' if has_item1a else '✗'}  "
                  f"Item7={'✓' if has_item7 else '✗'}")
        record(PASS, "fetch_10k_text", time.perf_counter() - t0, detail)

        print()
        print("    [텍스트 앞 400자]")
        print("   ", text[:400].replace("\n", " "))
        print()
        return True
    except Exception:
        record(FAIL, "fetch_10k_text", time.perf_counter() - t0,
               traceback.format_exc().splitlines()[-1])
        return False


def test_extract_10k_sections():
    text = _state.get("text_10k")
    if not text:
        record(FAIL, "extract_10k_sections", 0.0, "text_10k 없음")
        return False

    t0 = time.perf_counter()
    try:
        sections = ds.extract_10k_sections(text)
        found = sections.get("found", [])
        detail = f"found={found}"
        record(PASS, "extract_10k_sections", time.perf_counter() - t0, detail)

        print()
        for key in ["item_1a", "item_7", "item_8", "item_9a"]:
            content = sections.get(key, "")
            if content:
                preview = content[:120].replace("\n", " ")
                print(f"    [{key}] ({len(content):,} chars)")
                print(f"      {preview}...")
            else:
                print(f"    [{key}] ✗ 미발견")
        print()
        _state["sections_10k"] = sections
        return True
    except Exception:
        record(FAIL, "extract_10k_sections", time.perf_counter() - t0,
               traceback.format_exc().splitlines()[-1])
        return False


def test_extract_10k_notes():
    sections = _state.get("sections_10k", {})
    # Item 8이 있으면 그것만, 없으면 전체 텍스트
    source = sections.get("item_8") or _state.get("text_10k", "")
    if not source:
        record(FAIL, "extract_10k_notes", 0.0, "item_8 / text_10k 없음")
        return False

    t0 = time.perf_counter()
    try:
        notes_data = ds.extract_10k_notes(source)
        n_notes = notes_data["notes_found"]
        n_hits  = notes_data["keyword_hit_count"]
        req     = notes_data["required_sections"]
        found_req = [k for k, v in req.items() if v]

        detail = (f"notes={n_notes}  "
                  f"keyword_hits={n_hits}  "
                  f"required_found={found_req}")
        record(PASS, "extract_10k_notes", time.perf_counter() - t0, detail)

        print()
        print("    [발견된 Notes 목록 (앞 10개)]")
        for title in list(notes_data["notes"].keys())[:10]:
            print(f"      {title}")

        print()
        print("    [필수 섹션 매핑]")
        for cat, note_title in req.items():
            status = f"→ {note_title[:50]}" if note_title else "✗ 미발견"
            print(f"      {cat:<25} {status}")

        if notes_data["keyword_hits"]:
            print()
            print(f"    [⚠ Forensic 키워드 Hits ({n_hits}개)]")
            for hit in notes_data["keyword_hits"][:5]:
                print(f"      [{hit['keyword']}]  {hit['note'][:40]}")
                print(f"        → {hit['context'][:80]}...")
        print()
        return True
    except Exception:
        record(FAIL, "extract_10k_notes", time.perf_counter() - t0,
               traceback.format_exc().splitlines()[-1])
        return False


# ---------------------------------------------------------------------------
# GROUP C: XBRL Financial Data
# ---------------------------------------------------------------------------

async def test_get_company_facts():
    cik = _state.get("cik")
    if not cik:
        record(FAIL, "get_company_facts", 0.0, "CIK 없음")
        return False

    t0 = time.perf_counter()
    try:
        facts = await ds.get_company_facts(cik)
        _state["facts"] = facts

        entity   = facts.get("entityName", "?")
        n_gaap   = len(facts.get("facts", {}).get("us-gaap", {}))
        detail   = f"entity={entity}  us-gaap concepts={n_gaap}"
        record(PASS, "get_company_facts", time.perf_counter() - t0, detail)

        # 주요 개념 존재 확인
        gaap = facts["facts"].get("us-gaap", {})
        check_concepts = ["Revenues", "NetIncomeLoss",
                          "NetCashProvidedByUsedInOperatingActivities", "Assets"]
        print()
        print("    [주요 XBRL concept 존재 여부]")
        for c in check_concepts:
            exists = c in gaap
            print(f"      {c:<55} {'✓' if exists else '✗'}")
        print()
        return True
    except Exception:
        record(FAIL, "get_company_facts", time.perf_counter() - t0,
               traceback.format_exc().splitlines()[-1])
        return False


def test_extract_financial_timeseries():
    facts = _state.get("facts")
    if not facts:
        record(FAIL, "extract_financial_timeseries", 0.0, "facts 없음")
        return False

    t0 = time.perf_counter()
    try:
        # 연간 (10-K)
        annual = ds.extract_financial_timeseries(
            facts,
            concepts=["revenue", "net_income", "operating_cf",
                      "capex", "accounts_receivable", "total_assets",
                      "deferred_revenue", "rd_expense"],
            periods=5,
            form_filter="10-K",
        )
        # 분기 (10-Q)
        quarterly = ds.extract_financial_timeseries(
            facts,
            concepts=["revenue", "net_income", "operating_cf"],
            periods=8,
            form_filter="10-Q",
        )

        meta = annual["meta"]
        detail = (f"found={len(meta['concepts_found'])}  "
                  f"missing={meta['concepts_missing']}")
        record(PASS, "extract_financial_timeseries", time.perf_counter() - t0, detail)

        print()
        print("    [연간 재무 시계열 (10-K, 최근 5년)]")
        for concept in ["revenue", "net_income", "operating_cf", "capex"]:
            series = annual.get(concept, {})
            if series:
                vals = "  ".join(
                    f"{dt[:4]}=${v/1e9:.2f}B" for dt, v in sorted(series.items())
                )
                print(f"      {concept:<25} {vals}")
            else:
                print(f"      {concept:<25} ✗ 데이터 없음")

        print()
        print("    [분기 매출 시계열 (10-Q, 최근 8분기)]")
        rev_q = quarterly.get("revenue", {})
        for dt, v in sorted(rev_q.items()):
            print(f"      {dt}  ${v/1e9:.2f}B")

        # Sloan Accrual 계산
        rev  = annual.get("revenue", {})
        ni   = annual.get("net_income", {})
        ocf  = annual.get("operating_cf", {})
        ta   = annual.get("total_assets", {})
        common_dates = sorted(set(ni) & set(ocf) & set(ta), reverse=True)
        if common_dates:
            d = common_dates[0]
            sloan = (ni[d] - ocf[d]) / ta[d] if ta[d] else None
            ccr   = ocf[d] / ni[d] if ni[d] else None
            print()
            print(f"    [Forensic 지표 — {d[:4]}]")
            if sloan is not None:
                print(f"      Sloan Accrual = {sloan:.4f}  (>0.05 주의)")
            if ccr is not None:
                print(f"      CCR (OCF/NI)  = {ccr:.3f}   (<0.8 주의)")
        print()
        return True
    except Exception:
        record(FAIL, "extract_financial_timeseries", time.perf_counter() - t0,
               traceback.format_exc().splitlines()[-1])
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_all(group: str | None):
    print(f"\n{'='*65}")
    print(f"  data_sources.py 통합 테스트  |  Ticker: {TICKER}")
    print(f"{'='*65}\n")

    run_a = group in (None, "A")
    run_b = group in (None, "B")
    run_c = group in (None, "C")

    if run_a:
        print("── GROUP A: Filing Metadata ──────────────────────────────────")
        ok = await test_get_company_cik()
        if ok:
            await test_list_filings()
            await test_get_filing_index()

    if run_b:
        print("── GROUP B: Filing Text Extraction ───────────────────────────")
        ok = await test_fetch_10k_text()
        if ok:
            test_extract_10k_sections()
            test_extract_10k_notes()

    if run_c:
        print("── GROUP C: XBRL Financial Data ──────────────────────────────")
        ok = await test_get_company_facts()
        if ok:
            test_extract_financial_timeseries()

    # 최종 요약
    print("=" * 65)
    print("  테스트 요약")
    print("=" * 65)
    passed = sum(1 for s, *_ in results if s == PASS)
    failed = sum(1 for s, *_ in results if s == FAIL)
    print(f"  통과: {passed}/{len(results)}  실패: {failed}\n")
    for status, name, elapsed, detail in results:
        print(f"  {status}  {name:<40} {elapsed:.1f}s  {detail[:50]}")

    if failed == 0:
        print("\n  ✅ 모든 테스트 통과. orchestrator 실행 준비 완료.")
    else:
        print(f"\n  ❌ {failed}개 실패. 위 에러 메시지 확인.")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="data_sources.py 테스트")
    parser.add_argument(
        "--group",
        choices=["A", "B", "C"],
        default=None,
        help="테스트할 그룹 (미지정 시 전체)",
    )
    args = parser.parse_args()
    asyncio.run(run_all(args.group))
