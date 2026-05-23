"""Forensic Pipeline 빠른 테스트 스크립트.

NVDA 단일 종목으로 각 데이터 소스를 순서대로 확인.
Orchestrator(Opus) 없이 data_sources.py 레벨만 테스트.

실행:
    cd agents
    python quicktest.py
"""

from __future__ import annotations

import json
import sys
import time
import traceback

# .env 로드
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("[+] .env 로드 완료")
except ImportError:
    print("[!] python-dotenv 없음 — .env 수동 로드 필요")

import data_sources as ds

TICKER = "NVDA"
PASS = "✓"
FAIL = "✗"
WARN = "⚠"

results: list[tuple[str, str, str]] = []  # (status, name, detail)


def test(name: str, fn, *args, **kwargs):
    """함수 실행 후 결과 출력 + 기록."""
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.perf_counter() - t0
        if isinstance(result, dict) and "error" in result:
            results.append((FAIL, name, result["error"]))
            print(f"  {FAIL}  {name:<35} {elapsed:.1f}s  ERROR: {result['error'][:80]}")
        else:
            # 핵심 값 요약
            summary = _summarize(name, result)
            results.append((PASS, name, summary))
            print(f"  {PASS}  {name:<35} {elapsed:.1f}s  {summary}")
        return result
    except Exception as e:
        elapsed = time.perf_counter() - t0
        tb = traceback.format_exc().splitlines()[-1]
        results.append((FAIL, name, tb))
        print(f"  {FAIL}  {name:<35} {elapsed:.1f}s  {tb[:80]}")
        return None


def _summarize(name: str, result) -> str:
    if not isinstance(result, dict):
        return str(result)[:60]
    if name == "sec_get_cik":
        return f"CIK={result}"
    if name == "sec_xbrl_financials":
        fins = result.get("financials", {})
        rev = fins.get("revenue", {})
        ni = fins.get("net_income", {})
        ocf = fins.get("operating_cf", {})
        if rev:
            latest_end = sorted(rev.keys(), reverse=True)[0]
            return (f"FY={latest_end}  "
                    f"Rev=${rev.get(latest_end,0)/1e9:.1f}B  "
                    f"NI=${ni.get(latest_end,0)/1e9:.1f}B  "
                    f"OCF=${ocf.get(latest_end,0)/1e9:.1f}B")
        return "데이터 없음"
    if name == "sec_10k_sections":
        cur = result.get("current", {})
        secs = list(cur.get("sections", {}).keys())
        date = cur.get("filing_date", "?")
        return f"current={date}  sections={secs}"
    if name == "sec_form4":
        return (f"total={result.get('total_form4_count',0)}  "
                f"sells={result.get('sell_related_count',0)}")
    if name == "sec_8k_items":
        return (f"total={result.get('total_8k_count',0)}  "
                f"critical={result.get('critical_event_count',0)}")
    if name == "sec_corresp":
        active = result.get("active_correspondence", False)
        count = result.get("count", 0)
        return f"active={active}  count={count}"
    if name == "sec_earnings_releases":
        return f"found={result.get('releases_found',0)}"
    if name == "sec_xbrl_quarterly":
        q = result.get("quarterly", {})
        rev_q = q.get("revenue", {})
        if rev_q:
            latest = sorted(rev_q.keys(), reverse=True)[0]
            v = rev_q[latest]
            yoy = result.get("revenue_yoy_growth_pct", {})
            g = yoy.get(latest)
            g_str = f"  YoY={g:+.1f}%" if g is not None else ""
            return f"latest={latest}  Rev=${v/1e9:.2f}B{g_str}" if v else f"latest={latest}  Rev=null"
        return f"quarters={len(rev_q)}"
    if name == "sec_10q_sections":
        filings = result.get("filings", [])
        if filings:
            periods = [f.get("period", "?") for f in filings]
            return f"fetched={len(filings)}  periods={periods}"
        return "filings=0"
    if name == "yahoo_overview":
        return (f"price=${result.get('price','?')}  "
                f"mktcap=${result.get('market_cap',0)/1e9:.0f}B")
    return str(result)[:60]


# ---------------------------------------------------------------------------
print(f"\n{'='*60}")
print(f"  Forensic Pipeline 데이터 소스 테스트  |  Ticker: {TICKER}")
print(f"{'='*60}\n")

# Step 1: CIK 조회
print("[Step 1] CIK 조회")
cik = test("sec_get_cik", ds.sec_get_cik, TICKER)
if not cik:
    print("\n  CIK 조회 실패 — SEC 접근 불가 또는 ticker 오류. 이후 테스트 중단.")
    sys.exit(1)
print()

# Step 2: XBRL 재무 데이터 (Agent 1,2,3 핵심)
print("[Step 2] XBRL 재무 데이터 (Agent 1/2/3)")
xbrl = test("sec_xbrl_financials", ds.sec_xbrl_financials, TICKER, 3)
if xbrl and "financials" in xbrl:
    fins = xbrl["financials"]
    print("\n  --- 주요 항목 3년치 ---")
    for key in ["revenue", "net_income", "operating_cf", "capex",
                "depreciation_amortization", "accounts_receivable",
                "total_assets", "deferred_revenue"]:
        series = fins.get(key, {})
        if series:
            vals = "  ".join(
                f"{end[:4]}=${v/1e9:.2f}B" if v else f"{end[:4]}=null"
                for end, v in sorted(series.items(), reverse=True)
            )
            print(f"  {key:<30} {vals}")

    # Sloan Accrual 간단 계산
    rev = fins.get("revenue", {})
    ni = fins.get("net_income", {})
    ocf = fins.get("operating_cf", {})
    ta = fins.get("total_assets", {})
    if rev and ni and ocf and ta:
        ends = sorted(set(ni) & set(ocf) & set(ta), reverse=True)
        if ends:
            e = ends[0]
            sloan = (ni[e] - ocf[e]) / ta[e] if ta[e] else None
            ccr = ocf[e] / ni[e] if ni[e] else None
            print(f"\n  [계산] {e[:4]}  Sloan Accrual={sloan:.4f}  CCR(OCF/NI)={ccr:.3f}" if sloan and ccr else "")
print()

# Step 3: 10-K 섹션 텍스트 (Agent 3,4)
print("[Step 3] 10-K 섹션 추출 (Agent 3/4)")
tenk = test("sec_10k_sections", ds.sec_10k_sections, TICKER,
            True, ["risk_factors", "critical_accounting"], 3000)
if tenk and not isinstance(tenk, dict):
    pass
elif tenk:
    cur = tenk.get("current", {})
    pri = tenk.get("prior", {})
    print(f"\n  current filing: {cur.get('filing_date','?')}  ({cur.get('accession','?')})")
    print(f"  prior  filing: {pri.get('filing_date','?')}")
    for sname, text in cur.get("sections", {}).items():
        preview = text[:120].replace('\n', ' ')
        print(f"\n  [{sname}] (앞 120자)\n  {preview}...")
print()

# Step 4: Form 4 (Agent 6)
print("[Step 4] Form 4 내부자 거래 (Agent 6)")
f4 = test("sec_form4", ds.sec_form4, TICKER, 90)
if f4 and f4.get("filings"):
    print("\n  최근 Form 4 (최대 5건):")
    for filing in f4["filings"][:5]:
        print(f"  {filing['filed']}  {filing['title'][:70]}")
print()

# Step 5: 8-K 이벤트 (Agent 6)
print("[Step 5] 8-K Item 필터 4.02/5.02 (Agent 6)")
eightk = test("sec_8k_items", ds.sec_8k_items, TICKER,
              ["4.02", "5.02", "8.01", "2.02"], 365)
if eightk and eightk.get("critical_events"):
    print("\n  ⚠  Critical Events (4.02/5.02):")
    for ev in eightk["critical_events"]:
        print(f"  {ev['filed']}  {ev['title'][:70]}")
print()

# Step 6: CORRESP (Agent 6)
print("[Step 6] SEC 서신 (CORRESP/UPLOAD, Agent 6)")
corresp = test("sec_corresp", ds.sec_corresp, TICKER, 365)
print()

# Step 7: 어닝스 릴리즈 (Agent 5)
print("[Step 7] 어닝스 릴리즈 8-K Item 2.02 (Agent 5)")
er = test("sec_earnings_releases", ds.sec_earnings_releases, TICKER, 4)
if er and er.get("releases"):
    print("\n  릴리즈 목록:")
    for rel in er["releases"]:
        print(f"  {rel['filed']}  items={rel['items']}  url={rel['full_url'][:60]}...")
    if er["releases"]:
        print(f"\n  [첫 번째 릴리즈 앞 300자]\n")
        print(f"  {er['releases'][0]['text_excerpt'][:300].replace(chr(10), ' ')}")
print()

# Step 8: 분기 XBRL (10-Q) 수치
print("[Step 8] 분기 XBRL (10-Q, 최근 8분기)")
qxbrl = test("sec_xbrl_quarterly", ds.sec_xbrl_quarterly, TICKER, 8)
if qxbrl and "quarterly" in qxbrl:
    qfins = qxbrl["quarterly"]
    rev_q = qfins.get("revenue", {})
    ni_q  = qfins.get("net_income", {})
    ocf_q = qfins.get("operating_cf", {})
    ar_q  = qfins.get("accounts_receivable", {})
    if rev_q:
        print("\n  --- 분기 매출 (최근 8분기) ---")
        for end in sorted(rev_q.keys(), reverse=True)[:8]:
            v    = rev_q.get(end)
            ni_v = ni_q.get(end)
            ocf_v = ocf_q.get(end)
            ar_v  = ar_q.get(end)
            parts = [f"{end}  Rev=${v/1e9:.2f}B" if v else f"{end}  Rev=null"]
            if ni_v:  parts.append(f"NI=${ni_v/1e9:.2f}B")
            if ocf_v: parts.append(f"OCF=${ocf_v/1e9:.2f}B")
            if ar_v:  parts.append(f"AR=${ar_v/1e9:.2f}B")
            print("  " + "  ".join(parts))
    yoy = qxbrl.get("revenue_yoy_growth_pct", {})
    if yoy:
        print("\n  --- 분기 YoY 매출 성장률 ---")
        for end, g in sorted(yoy.items(), reverse=True):
            g_str = f"{g:+.1f}%" if g is not None else "N/A"
            print(f"  {end}  {g_str}")
print()

# Step 9: 10-Q 섹션 텍스트
print("[Step 9] 10-Q 섹션 추출 (최근 2분기)")
tenq = test("sec_10q_sections", ds.sec_10q_sections, TICKER, 2,
            ["mda", "risk_updates", "controls"], 2000)
if tenq and tenq.get("filings"):
    for filing in tenq["filings"]:
        print(f"\n  Period: {filing['period']}  Filed: {filing['filing_date']}")
        for sname, text in filing.get("sections", {}).items():
            preview = text[:100].replace('\n', ' ')
            print(f"    [{sname}] {preview}...")
print()

# Step 10: Yahoo Overview
print("[Step 10] Yahoo Overview (보조)")
yov = test("yahoo_overview", ds.yahoo_overview, TICKER)
print()

# ---------------------------------------------------------------------------
# 최종 요약
# ---------------------------------------------------------------------------
print("=" * 60)
print("  테스트 요약")
print("=" * 60)
passed = sum(1 for s, _, _ in results if s == PASS)
failed = sum(1 for s, _, _ in results if s == FAIL)
print(f"  통과: {passed}/{len(results)}  실패: {failed}")
print()
for status, name, detail in results:
    print(f"  {status}  {name:<35} {detail[:50]}")

if failed == 0:
    print("\n  모든 데이터 소스 정상. main.py 실행 준비 완료.")
    print(f"\n  다음 명령어:")
    print(f"    python main.py {TICKER} --no-orchestrator")
    print(f"    python main.py {TICKER} --out reports/{TICKER.lower()}_forensic.md")
else:
    print(f"\n  {failed}개 실패 — 위 에러 메시지 확인.")
print()
