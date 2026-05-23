"""data_sources.py — SEC EDGAR Data Infrastructure for Forensic Accounting.

미국 주식 포렌식 분석을 위한 SEC EDGAR 데이터 수집 레이어.
한국(DART/네이버) 코드 완전 제거 후 재작성.

모든 I/O 함수는 async/httpx 기반.
Rate limit: SEC 초당 10회 → Semaphore(8) + 0.13s 간격 준수.

8 public functions
==================
Group A — Filing Metadata
  get_company_cik(ticker)              → str          CIK (10자리)
  list_filings(cik, form_type, count)  → list[dict]   filing 목록
  get_filing_index(accession_no, cik)  → dict         filing 내 문서 인덱스

Group B — Filing Text Extraction
  fetch_10k_text(accession_no, cik)    → str          10-K plain text
  extract_10k_sections(text)           → dict         Item 1A/7/8/9A
  extract_10k_notes(text)              → dict         주석 + 위험 키워드 hits

Group C — XBRL Financial Data
  get_company_facts(cik)               → dict         SEC Company Facts 원본
  extract_financial_timeseries(facts)  → dict         재무 시계열

환경변수
--------
  SEC_USER_AGENT  필수  (예: "MyApp/1.0 you@email.com")
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import date
from typing import Any

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

_USER_AGENT: str = os.environ.get(
    "SEC_USER_AGENT", "ForensicPipeline/1.0 research@example.com"
)

_EDGAR_BASE = "https://www.sec.gov"
_DATA_BASE  = "https://data.sec.gov"

_REQUEST_INTERVAL = 0.13   # seconds between requests (~7.5 req/s)
_SEMAPHORE_SIZE   = 8      # 동시 요청 수


def _get_semaphore() -> asyncio.Semaphore:
    """Lazy Semaphore — 실행 중인 event loop에 바인딩."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    key = "_forensic_semaphore"
    if not hasattr(loop, key):
        setattr(loop, key, asyncio.Semaphore(_SEMAPHORE_SIZE))
    return getattr(loop, key)


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "User-Agent": _USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        },
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, connect=10.0),
    )


def _nodash(accession_no: str) -> str:
    """'0001045810-23-000017' → '000104581023000017'"""
    return accession_no.replace("-", "")


async def _get(url: str, client: httpx.AsyncClient, retries: int = 3) -> httpx.Response:
    """Rate-limited GET with exponential backoff."""
    sem = _get_semaphore()
    async with sem:
        for attempt in range(retries):
            try:
                await asyncio.sleep(_REQUEST_INTERVAL)
                resp = await client.get(url)
                if resp.status_code == 429:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError:
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))
            except (httpx.RequestError, httpx.TimeoutException):
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed after {retries} retries: {url}")


# ===========================================================================
# GROUP A: FILING METADATA
# ===========================================================================

async def get_company_cik(ticker: str) -> str:
    """Ticker 심볼 → 10자리 zero-padded CIK 반환.

    SEC EDGAR company_tickers.json 에서 조회.
    https://www.sec.gov/files/company_tickers.json

    Args:
        ticker: 주식 티커 (대소문자 무관, 예: "NVDA", "nvda")

    Returns:
        "0001045810" 형식의 10자리 CIK 문자열

    Raises:
        ValueError: ticker를 SEC에서 찾을 수 없을 때

    Example:
        cik = await get_company_cik("NVDA")
        # → "0001045810"
    """
    url = f"{_EDGAR_BASE}/files/company_tickers.json"
    async with _make_client() as client:
        resp = await _get(url, client)
        data: dict = resp.json()

    ticker_upper = ticker.upper().strip()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            return str(entry["cik_str"]).zfill(10)

    raise ValueError(
        f"Ticker '{ticker}' not found in SEC company_tickers.json. "
        "Check if it's a valid US-listed ticker."
    )


async def list_filings(
    cik: str,
    form_type: str,
    count: int = 10,
) -> list[dict]:
    """CIK와 form type으로 최근 N개 filing 메타데이터 반환.

    SEC Submissions API 사용:
      https://data.sec.gov/submissions/CIK{cik}.json

    오래된 filings는 additional files에서 추가 조회.

    Args:
        cik:       10자리 CIK (예: "0001045810")
        form_type: 폼 타입 정확히 일치 (예: "10-K", "10-Q", "8-K",
                   "DEF 14A", "CORRESP", "UPLOAD", "4")
        count:     최대 반환 개수 (기본 10)

    Returns:
        [
          {
            "accession_no":     "0001045810-23-000017",
            "filing_date":      "2023-02-24",
            "form_type":        "10-K",
            "primary_document": "nvda-20230129.htm",
            "document_url":     "https://www.sec.gov/Archives/.../nvda-20230129.htm",
            "period_of_report": "2023-01-29",
          },
          ...
        ]

    Example:
        filings = await list_filings("0001045810", "10-K", count=3)
        for f in filings:
            print(f["filing_date"], f["document_url"])
    """
    url = f"{_DATA_BASE}/submissions/CIK{cik}.json"
    cik_nodash = cik.lstrip("0") or "0"

    async with _make_client() as client:
        resp = await _get(url, client)
        data: dict = resp.json()

        recent = data.get("filings", {}).get("recent", {})
        results = _parse_submissions_block(recent, cik_nodash, form_type, count)

        # 부족하면 older filing files 에서 추가
        if len(results) < count:
            for file_info in data.get("filings", {}).get("files", []):
                if len(results) >= count:
                    break
                older_url = f"{_DATA_BASE}/submissions/{file_info['name']}"
                try:
                    file_resp = await _get(older_url, client)
                    more = _parse_submissions_block(
                        file_resp.json(), cik_nodash, form_type, count - len(results)
                    )
                    results.extend(more)
                except Exception:
                    pass

    return results[:count]


def _parse_submissions_block(
    block: dict,
    cik_nodash: str,
    form_type: str,
    limit: int,
) -> list[dict]:
    """Submissions API 블록 → 필터링된 filing 리스트."""
    forms        = block.get("form", [])
    accessions   = block.get("accessionNumber", [])
    dates        = block.get("filingDate", [])
    primary_docs = block.get("primaryDocument", [])
    periods      = block.get("reportDate") or block.get("periodOfReport") or []

    results: list[dict] = []
    for i, form in enumerate(forms):
        if form.strip() != form_type.strip():
            continue
        if i >= len(accessions):
            break

        accession_no = accessions[i]
        primary_doc  = primary_docs[i] if i < len(primary_docs) else ""
        nd           = _nodash(accession_no)
        period       = periods[i] if i < len(periods) else ""

        doc_url = (
            f"{_EDGAR_BASE}/Archives/edgar/data/{cik_nodash}/{nd}/{primary_doc}"
            if primary_doc else ""
        )

        results.append({
            "accession_no":     accession_no,
            "filing_date":      dates[i] if i < len(dates) else "",
            "form_type":        form,
            "primary_document": primary_doc,
            "document_url":     doc_url,
            "period_of_report": period,
        })

        if len(results) >= limit:
            break

    return results


async def get_filing_index(accession_no: str, cik: str) -> dict:
    """Filing 내 모든 문서의 인덱스 반환.

    URL: /Archives/edgar/data/{cik}/{nodash}/{nodash}-index.json

    Args:
        accession_no: "0001045810-23-000017" 형식
        cik:          10자리 CIK (예: "0001045810")

    Returns:
        {
          "accession_no":  "0001045810-23-000017",
          "filing_date":   "2023-02-24",
          "form_type":     "10-K",
          "documents": [
            {
              "sequence":    "1",
              "description": "Annual Report",
              "document":    "nvda-20230129.htm",
              "type":        "10-K",
              "size":        "12345678",
              "url":         "https://www.sec.gov/Archives/..."
            },
            ...
          ],
          "exhibit_map": {
            "10-K":    "https://...",
            "EX-21.1": "https://...",
            "EX-23.1": "https://...",
          }
        }

    TODO: -index.json 이 없는 구형 filing은 -index.htm 파싱으로 폴백 필요.

    Example:
        idx = await get_filing_index("0001045810-23-000017", "0001045810")
        for doc in idx["documents"]:
            print(doc["type"], doc["url"])
    """
    cik_nodash = cik.lstrip("0") or "0"
    nd  = _nodash(accession_no)
    url = f"{_EDGAR_BASE}/Archives/edgar/data/{cik_nodash}/{nd}/{nd}-index.json"

    async with _make_client() as client:
        resp = await _get(url, client)
        data: dict = resp.json()

    documents: list[dict] = []
    exhibit_map: dict[str, str] = {}

    for item in data.get("directory", {}).get("item", []):
        doc_name = item.get("name", "")
        doc_type = item.get("type", "")
        doc_url  = (
            f"{_EDGAR_BASE}/Archives/edgar/data/{cik_nodash}/{nd}/{doc_name}"
        )

        documents.append({
            "sequence":    item.get("sequence", ""),
            "description": item.get("description", ""),
            "document":    doc_name,
            "type":        doc_type,
            "size":        item.get("size", ""),
            "url":         doc_url,
        })

        if doc_type:
            exhibit_map[doc_type] = doc_url

    return {
        "accession_no": accession_no,
        "filing_date":  data.get("filing-date", ""),
        "form_type":    data.get("form-type", ""),
        "documents":    documents,
        "exhibit_map":  exhibit_map,
    }


# ===========================================================================
# GROUP B: FILING TEXT EXTRACTION
# ===========================================================================

async def fetch_10k_text(accession_no: str, cik: str) -> str:
    """10-K primary document 다운로드 → plain text 반환.

    전략:
      1. get_filing_index 로 primary document URL 획득
      2. HTML 다운로드 (최대 15MB 응답)
      3. BeautifulSoup(lxml) 으로 태그 제거, 공백 정규화
      4. 최대 2M 문자 반환

    Args:
        accession_no: "0001045810-23-000017" 형식
        cik:          10자리 CIK (예: "0001045810")

    Returns:
        plain text 문자열 (줄바꿈 정규화, 연속 빈 줄 단축)

    Raises:
        ValueError: primary document URL을 찾을 수 없을 때

    TODO: iXBRL inline XBRL 처리 — 현재는 HTML 태그 제거 후 텍스트만 추출.
          수치 컨텍스트 필요 시 arelle 등 XBRL 파서 연동 권장.

    Example:
        text = await fetch_10k_text("0001045810-23-000017", "0001045810")
        print(f"{len(text):,} chars")
        print(text[:300])
    """
    idx = await get_filing_index(accession_no, cik)
    primary_url = ""

    # 1순위: type이 "10-K" 또는 "10-K/A"인 htm
    for doc in idx["documents"]:
        if doc["type"] in ("10-K", "10-K/A") and doc["document"].endswith((".htm", ".html")):
            primary_url = doc["url"]
            break

    # 2순위: sequence == "1"인 htm
    if not primary_url:
        for doc in idx["documents"]:
            if doc["sequence"] == "1" and doc["document"].endswith((".htm", ".html")):
                primary_url = doc["url"]
                break

    # 3순위: 첫 번째 htm
    if not primary_url:
        for doc in idx["documents"]:
            if doc["document"].endswith((".htm", ".html")):
                primary_url = doc["url"]
                break

    if not primary_url:
        raise ValueError(
            f"10-K primary HTML document not found for {accession_no}. "
            f"Documents: {[d['document'] for d in idx['documents']]}"
        )

    async with _make_client() as client:
        resp = await _get(primary_url, client)

    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "head", "meta", "noscript"]):
        tag.decompose()

    raw = soup.get_text(separator="\n")

    # 공백 정규화 — 연속 빈 줄 → 1줄
    lines: list[str] = []
    prev_blank = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
            prev_blank = False
        else:
            if not prev_blank:
                lines.append("")
            prev_blank = True

    return "\n".join(lines)[:2_000_000]


def extract_10k_sections(text: str) -> dict[str, Any]:
    """10-K plain text에서 핵심 4개 Item 섹션 추출.

    타겟 섹션:
      item_1a  — Risk Factors
      item_7   — Management's Discussion and Analysis (MD&A)
      item_8   — Financial Statements and Supplementary Data
      item_9a  — Controls and Procedures

    전략:
      1. "ITEM N" 패턴으로 모든 Item 경계 위치 탐색
      2. TOC(목차) vs 본문 구분 — 동일 Item이 2번 이상 나오면 두 번째 위치 사용
      3. 다음 Item 시작 전까지를 해당 섹션으로 간주
      4. 최대 50,000자/섹션 (LLM 토큰 절약)

    Args:
        text: fetch_10k_text() 반환값 (전체 10-K plain text)

    Returns:
        {
          "item_1a": "ITEM 1A. RISK FACTORS\n...",
          "item_7":  "ITEM 7. MANAGEMENT'S DISCUSSION...",
          "item_8":  "ITEM 8. FINANCIAL STATEMENTS...",
          "item_9a": "ITEM 9A. CONTROLS AND PROCEDURES...",
          "found":   ["item_1a", "item_7", "item_8", "item_9a"],
        }
        미발견 섹션은 빈 문자열 ""로 포함.

    Example:
        sections = extract_10k_sections(text)
        print("MD&A 앞 500자:")
        print(sections["item_7"][:500])
        print("찾은 섹션:", sections["found"])
    """
    ITEM_PATTERNS: list[tuple[str, re.Pattern]] = [
        ("item_1",   re.compile(r"ITEM\s+1(?![A-Z0-9])", re.I)),
        ("item_1a",  re.compile(r"ITEM\s+1A\b", re.I)),
        ("item_1b",  re.compile(r"ITEM\s+1B\b", re.I)),
        ("item_2",   re.compile(r"ITEM\s+2\b", re.I)),
        ("item_3",   re.compile(r"ITEM\s+3\b", re.I)),
        ("item_4",   re.compile(r"ITEM\s+4\b", re.I)),
        ("item_5",   re.compile(r"ITEM\s+5\b", re.I)),
        ("item_6",   re.compile(r"ITEM\s+6\b", re.I)),
        ("item_7",   re.compile(r"ITEM\s+7(?!A)\b", re.I)),
        ("item_7a",  re.compile(r"ITEM\s+7A\b", re.I)),
        ("item_8",   re.compile(r"ITEM\s+8\b", re.I)),
        ("item_9",   re.compile(r"ITEM\s+9(?![A-Z0-9])", re.I)),
        ("item_9a",  re.compile(r"ITEM\s+9A\b", re.I)),
        ("item_9b",  re.compile(r"ITEM\s+9B\b", re.I)),
        ("item_10",  re.compile(r"ITEM\s+10\b", re.I)),
        ("item_11",  re.compile(r"ITEM\s+11\b", re.I)),
        ("item_12",  re.compile(r"ITEM\s+12\b", re.I)),
        ("item_13",  re.compile(r"ITEM\s+13\b", re.I)),
        ("item_14",  re.compile(r"ITEM\s+14\b", re.I)),
        ("item_15",  re.compile(r"ITEM\s+15\b", re.I)),
        ("item_16",  re.compile(r"ITEM\s+16\b", re.I)),
    ]

    TARGET_SECTIONS = {"item_1a", "item_7", "item_8", "item_9a"}
    MAX_CHARS = 50_000

    # 모든 Item 위치 수집
    all_positions: dict[str, list[int]] = {}
    for key, pat in ITEM_PATTERNS:
        for m in pat.finditer(text):
            all_positions.setdefault(key, []).append(m.start())

    # TOC 건너뜀: 2회 이상 등장 시 두 번째 위치 사용
    chosen: list[tuple[str, int]] = []
    for key, positions in all_positions.items():
        chosen_pos = positions[1] if len(positions) >= 2 else positions[0]
        chosen.append((key, chosen_pos))

    chosen.sort(key=lambda x: x[1])

    result: dict[str, Any] = {k: "" for k in TARGET_SECTIONS}
    found: list[str] = []

    for i, (key, start) in enumerate(chosen):
        if key not in TARGET_SECTIONS:
            continue
        end = chosen[i + 1][1] if i + 1 < len(chosen) else len(text)
        content = text[start:end].strip()[:MAX_CHARS]
        result[key] = content
        found.append(key)

    result["found"] = found
    return result


def extract_10k_notes(text: str) -> dict:
    """10-K Financial Statements 주석 섹션 추출 + forensic 키워드 탐지.

    전략 1 — 섹션 타겟팅:
      "Note N" / "NOTE N" 패턴으로 각 주석 분리.
      동일 번호가 2회 이상 (TOC + 본문) 등장하면 두 번째 우선.

    전략 2 — 키워드 트리거 인덱싱 (11개 위험 키워드):
      related party / VIE / bill-and-hold / consignment /
      useful life extended / trade receivable financing/factoring /
      material weakness / restatement / going concern /
      channel stuffing / round-trip transaction

      발견 시 ±200자 컨텍스트와 함께 반환.

    Args:
        text: 10-K plain text
              (extract_10k_sections()["item_8"] 전달 권장 — 더 정확)

    Returns:
        {
          "notes": {
            "Note 1 - Summary of Significant Accounting Policies": "...",
            "Note 2 - Revenue Recognition": "...",
            ...
          },
          "required_sections": {
            "accounting_policies": "Note 1 - Summary of...",  # 또는 None
            "revenue":             "Note 2 - Revenue Recognition",
            "ppe":                 None,
            "related_party":       "Note 14 - Related Party Transactions",
            "commitments":         "Note 13 - Commitments and Contingencies",
            "income_taxes":        "Note 10 - Income Taxes",
            "subsequent_events":   None,
          },
          "keyword_hits": [
            {
              "note":     "Note 14 - Related Party Transactions",
              "keyword":  "related party",
              "context":  "...the Company entered into a transaction with a related party...",
              "position": 1234,
            }
          ],
          "notes_found":       14,
          "keyword_hit_count": 3,
        }

    Example:
        sections = extract_10k_sections(full_text)
        notes_data = extract_10k_notes(sections.get("item_8", full_text))
        for hit in notes_data["keyword_hits"]:
            print(f"[{hit['keyword']}]  {hit['note'][:40]}")
            print(f"  → {hit['context'][:100]}")
    """
    # ------------------------------------------------------------------
    # 전략 1: Note 경계 탐색
    # ------------------------------------------------------------------
    NOTE_PATTERN = re.compile(
        r"(?:^|\n)\s*"
        r"(?:NOTE|Note)\s+(\d{1,2})"
        r"(?:\.|\s*[-—–]\s*|\s+)"
        r"([A-Z][^\n]{3,80})",
        re.MULTILINE,
    )

    raw_hits: list[tuple[int, str, str]] = []
    for m in NOTE_PATTERN.finditer(text):
        num    = m.group(1)
        suffix = m.group(2).strip().rstrip(".")
        raw_hits.append((m.start(), num, f"Note {num} - {suffix}"))

    # 번호별 그룹 — 두 번째 등장(본문) 우선
    by_num: dict[str, list[tuple[int, str]]] = {}
    for pos, num, title in raw_hits:
        by_num.setdefault(num, []).append((pos, title))

    chosen_notes: list[tuple[int, str, str]] = []
    for num, entries in by_num.items():
        pos, title = entries[1] if len(entries) >= 2 else entries[0]
        chosen_notes.append((pos, num, title))

    chosen_notes.sort(key=lambda x: x[0])

    NOTE_MAX = 10_000
    notes_dict: dict[str, str] = {}
    for i, (pos, _num, title) in enumerate(chosen_notes):
        end_pos = chosen_notes[i + 1][0] if i + 1 < len(chosen_notes) else len(text)
        notes_dict[title] = text[pos:end_pos].strip()[:NOTE_MAX]

    # ------------------------------------------------------------------
    # 필수 7개 섹션 매핑
    # ------------------------------------------------------------------
    REQUIRED: dict[str, re.Pattern] = {
        "accounting_policies": re.compile(r"significant accounting polic|summary of accounting", re.I),
        "revenue":             re.compile(r"\brevenue\b(?!.*tax)", re.I),
        "ppe":                 re.compile(r"property.{0,5}plant.{0,5}equipment|fixed assets", re.I),
        "related_party":       re.compile(r"related.{0,5}part(?:y|ies)", re.I),
        "commitments":         re.compile(r"commitments?.{0,20}contingenc", re.I),
        "income_taxes":        re.compile(r"\bincome tax", re.I),
        "subsequent_events":   re.compile(r"subsequent event", re.I),
    }

    required_map: dict[str, str | None] = {k: None for k in REQUIRED}
    for category, pat in REQUIRED.items():
        for title in notes_dict:
            if pat.search(title):
                required_map[category] = title
                break

    # ------------------------------------------------------------------
    # 전략 2: Forensic 키워드 트리거 인덱싱
    # ------------------------------------------------------------------
    FORENSIC_KEYWORDS: list[tuple[str, re.Pattern]] = [
        ("related party",
         re.compile(r"related.{0,5}part(?:y|ies)", re.I)),
        ("VIE / variable interest entity",
         re.compile(r"variable\s+interest\s+entit|\bVIE\b")),
        ("bill-and-hold",
         re.compile(r"bill.{0,5}and.{0,5}hold", re.I)),
        ("consignment",
         re.compile(r"\bconsignment\b", re.I)),
        ("useful life extended",
         re.compile(r"useful.{0,10}li(?:fe|ves).{0,60}(?:revis|extend|increas|lengthen)", re.I)),
        ("trade receivable financing / factoring",
         re.compile(r"trade\s+receivable.{0,30}financ|\bfactoring\b", re.I)),
        ("material weakness",
         re.compile(r"material\s+weakness", re.I)),
        ("restatement",
         re.compile(r"\brestatement\b|\brestated\b", re.I)),
        ("going concern",
         re.compile(r"going\s+concern", re.I)),
        ("channel stuffing",
         re.compile(r"channel\s+stuff", re.I)),
        ("round-trip transaction",
         re.compile(r"round.{0,5}trip\s+transaction", re.I)),
    ]

    CONTEXT = 200
    keyword_hits: list[dict] = []

    for title, content in notes_dict.items():
        for kw_name, kw_pat in FORENSIC_KEYWORDS:
            for m in kw_pat.finditer(content):
                s   = max(0, m.start() - CONTEXT)
                e   = min(len(content), m.end() + CONTEXT)
                ctx = content[s:e].replace("\n", " ").strip()
                keyword_hits.append({
                    "note":     title,
                    "keyword":  kw_name,
                    "context":  ctx,
                    "position": m.start(),
                })

    return {
        "notes":             notes_dict,
        "required_sections": required_map,
        "keyword_hits":      keyword_hits,
        "notes_found":       len(notes_dict),
        "keyword_hit_count": len(keyword_hits),
    }


# ===========================================================================
# GROUP C: XBRL FINANCIAL DATA
# ===========================================================================

async def get_company_facts(cik: str) -> dict:
    """SEC EDGAR Company Facts API 전체 데이터 반환.

    URL: https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json

    Args:
        cik: 10자리 CIK (예: "0001045810")

    Returns:
        SEC Company Facts JSON 원본.
        {
          "cik":        1045810,
          "entityName": "NVIDIA CORP",
          "facts": {
            "us-gaap": {
              "Revenues": {
                "label": "Revenues",
                "units": {
                  "USD": [
                    {"end": "2023-01-29", "val": 26974000000,
                     "form": "10-K", "filed": "2023-02-24", ...},
                    ...
                  ]
                }
              },
              ...
            },
            "dei": { ... }
          }
        }

    TODO: 반환 JSON이 수십 MB 가능 — 반복 호출 시 로컬 캐시 권장.

    Example:
        facts = await get_company_facts("0001045810")
        node  = facts["facts"]["us-gaap"].get("Revenues", {})
        units = node.get("units", {}).get("USD", [])
        annual = [u for u in units if u.get("form") == "10-K"]
        for u in annual[-3:]:
            print(u["end"], u["val"])
    """
    url = f"{_DATA_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
    async with _make_client() as client:
        resp = await _get(url, client)
        return resp.json()


# XBRL concept 후보 매핑 (회사마다 다른 concept 사용 → 순서대로 시도)
_CONCEPT_CANDIDATES: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenueFromContractWithCustomer",
    ],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "net_income": [
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "ProfitLoss",
    ],
    "operating_cf": [
        "NetCashProvidedByUsedInOperatingActivities",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "CapitalExpendituresIncurredButNotYetPaid",
        "PaymentsToAcquireProductiveAssets",
    ],
    "depreciation": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
    "accounts_receivable": [
        "AccountsReceivableNetCurrent",
        "ReceivablesNetCurrent",
        "AccountsAndOtherReceivablesNetCurrent",
    ],
    "inventory": [
        "InventoryNet",
        "InventoryGross",
    ],
    "total_assets": [
        "Assets",
    ],
    "current_liabilities": [
        "LiabilitiesCurrent",
    ],
    "deferred_revenue": [
        "DeferredRevenueCurrent",
        "ContractWithCustomerLiabilityCurrent",
        "DeferredRevenueAndCreditsNoncurrent",
    ],
    "rd_expense": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ],
    "sga_expense": [
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ],
    "total_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermNotesPayable",
    ],
}


def extract_financial_timeseries(
    facts: dict,
    concepts: list[str] | None = None,
    periods: int = 8,
    form_filter: str | None = None,
) -> dict:
    """Company Facts에서 재무 시계열 데이터 추출.

    Args:
        facts:       get_company_facts() 반환값
        concepts:    추출할 지표 목록.
                     None이면 _CONCEPT_CANDIDATES 전체 (18개).
                     친숙한 이름: ["revenue", "net_income", "operating_cf"]
                     XBRL 직접 지정: ["us-gaap/Revenues", "us-gaap/Assets"]
        periods:     최근 N기간 반환 (기본 8)
        form_filter: "10-K" (연간), "10-Q" (분기 단일값, YTD 제외), None (전체)

    Returns:
        {
          "revenue":      {"2023-01-29": 26974000000.0, "2022-01-30": 16675000000.0, ...},
          "net_income":   {"2023-01-29": 4368000000.0, ...},
          "operating_cf": {"2023-01-29": 5641000000.0, ...},
          ...
          "meta": {
            "entity_name":      "NVIDIA CORP",
            "cik":              "1045810",
            "concepts_found":   ["revenue", "net_income", ...],
            "concepts_missing": ["inventory"],
            "form_filter":      "10-K",
            "periods":          8,
          }
        }

    Notes:
        - capex는 절대값 반환 (원본 음수인 경우 있음)
        - 분기 YTD 제거: start→end 기간 60~105일인 항목만 단일 분기 인정
        - 중복 기간: 최신 filed 값 우선

    Example:
        facts = await get_company_facts("0001045810")
        ts = extract_financial_timeseries(
            facts,
            concepts=["revenue", "net_income", "operating_cf", "capex"],
            periods=8,
            form_filter="10-K",
        )
        for dt, val in ts["revenue"].items():
            print(f"  {dt}  Rev=${val/1e9:.2f}B")
    """
    target  = concepts or list(_CONCEPT_CANDIDATES.keys())
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    dei     = facts.get("facts", {}).get("dei", {})

    result: dict[str, Any] = {}
    found:   list[str] = []
    missing: list[str] = []

    for concept_key in target:
        if concept_key.startswith("us-gaap/"):
            candidates = [concept_key[8:]]
        else:
            candidates = _CONCEPT_CANDIDATES.get(concept_key, [concept_key])

        series: dict[str, float] | None = None
        for candidate in candidates:
            node = us_gaap.get(candidate) or dei.get(candidate)
            if not node:
                continue
            units = node.get("units", {})
            unit_data = (
                units.get("USD")
                or units.get("shares")
                or (next(iter(units.values()), None) if units else None)
            )
            if unit_data:
                series = _filter_series(unit_data, form_filter, periods)
                if series:
                    break

        if series:
            if concept_key == "capex":
                series = {k: abs(v) for k, v in series.items()}
            result[concept_key] = series
            found.append(concept_key)
        else:
            result[concept_key] = {}
            missing.append(concept_key)

    result["meta"] = {
        "entity_name":      facts.get("entityName", ""),
        "cik":              str(facts.get("cik", "")),
        "concepts_found":   found,
        "concepts_missing": missing,
        "form_filter":      form_filter,
        "periods":          periods,
    }
    return result


def _filter_series(
    units_list: list[dict],
    form_filter: str | None,
    periods: int,
) -> dict[str, float]:
    """XBRL units list → {period_end_date: value} 최신 N개.

    분기 YTD 제거: start→end duration 60~105일인 항목만 단일 분기.
    중복 기간: 최신 filed 우선.
    """
    MIN_Q_DAYS = 60
    MAX_Q_DAYS = 105

    period_map: dict[str, tuple[float, str]] = {}  # end → (val, filed)

    for entry in units_list:
        form  = entry.get("form", "")
        end   = entry.get("end", "")
        start = entry.get("start", "")
        val   = entry.get("val")
        filed = entry.get("filed", "")

        if val is None or not end:
            continue

        if form_filter == "10-K" and form not in ("10-K", "10-K/A"):
            continue
        if form_filter == "10-Q" and form not in ("10-Q", "10-Q/A"):
            continue

        # 분기 YTD 제거
        if form in ("10-Q", "10-Q/A") and start and end:
            try:
                span = (date.fromisoformat(end) - date.fromisoformat(start)).days
                if not (MIN_Q_DAYS <= span <= MAX_Q_DAYS):
                    continue
            except ValueError:
                pass

        existing = period_map.get(end)
        if not existing or filed > existing[1]:
            period_map[end] = (float(val), filed)

    sorted_pairs = sorted(period_map.items(), key=lambda x: x[0], reverse=True)[:periods]
    return dict(sorted(sorted_pairs, key=lambda x: x[0]))
