# -*- coding: utf-8 -*-
"""
KOSPI/KOSDAQ 전체 기업 재무제표 5년치 수집
─────────────────────────────────────────
데이터 소스 : DART OpenAPI (opendart.fss.or.kr)
입력        : data/company_list.csv
출력        : data/financials/raw/      ← 기업별 CSV (종목코드.csv)
             data/financials_combined.csv ← 전체 통합본

특징
 - 진행상황 자동저장 (data/financials/.progress.json)
 - 중단 후 재실행 시 완료된 기업 스킵
 - 재시도 로직 (최대 3회)
 - 연결/별도 재무제표 자동 선택
"""

import requests, zipfile, io, xml.etree.ElementTree as ET
import pandas as pd
import json, time, sys
from pathlib import Path
from datetime import datetime

# ════════════════════════════════════════
# ★ 설정 (여기만 수정)
# ════════════════════════════════════════
DART_API_KEY = "5e67214e2ada4027d32b8fd208cd9025a07b5be5"

CURRENT_YEAR  = datetime.now().year
TARGET_YEARS  = list(range(CURRENT_YEAR - 5, CURRENT_YEAR))  # 최근 5년치 사업보고서
DELAY_SEC     = 0.08      # API 호출 간격 (초)
BATCH_SAVE    = 50        # N개 기업마다 중간 저장

# 재무제표 구분: CFS=연결, OFS=별도 (연결 없으면 별도로 자동 fallback)
FS_PREFER = "CFS"

# 추출할 계정 키워드 (DART 반환 account_nm 기준)
ACCOUNT_KEYS = {
    "매출액":           "revenue",
    "영업이익":         "operating_income",
    "당기순이익":       "net_income",
    "자산총계":         "total_assets",
    "부채총계":         "total_liabilities",
    "자본총계":         "total_equity",
    "영업활동현금흐름":  "cfo",
    "투자활동현금흐름":  "cfi",
    "재무활동현금흐름":  "cff",
}

# ════════════════════════════════════════
# 경로 설정 (스크립트 위치 기준 자동 계산)
# ════════════════════════════════════════
BASE          = Path(__file__).parent.parent   # korean-stock-analyzer/
RAW_DIR       = BASE / "data" / "financials" / "raw"
PROGRESS_FILE = BASE / "data" / "financials" / ".progress.json"
COMBINED_OUT  = BASE / "data" / "financials_combined.csv"
CORP_CACHE    = BASE / "data" / "corp_code_map.json"
COMPANY_CSV   = BASE / "data" / "company_list.csv"

RAW_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)

# ════════════════════════════════════════
# STEP 1 : DART corp_code 매핑 테이블
# ════════════════════════════════════════
def load_corp_code_map():
    if CORP_CACHE.exists():
        print("[1] corp_code 캐시 로드 중...")
        with open(CORP_CACHE, encoding="utf-8") as f:
            return json.load(f)

    print("[1] DART에서 corpCode.xml 다운로드 중...")
    r = requests.get(
        f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_API_KEY}",
        timeout=30
    )
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        with z.open("CORPCODE.xml") as f:
            root = ET.parse(f).getroot()

    code_map = {}
    for item in root.findall("list"):
        sc = item.findtext("stock_code", "").strip()
        cc = item.findtext("corp_code", "").strip()
        if sc:
            code_map[sc.zfill(6)] = cc

    with open(CORP_CACHE, "w", encoding="utf-8") as f:
        json.dump(code_map, f, ensure_ascii=False)
    print(f"    -> 상장사 {len(code_map)}개 corp_code 확보 & 캐시 저장")
    return code_map

# ════════════════════════════════════════
# STEP 2 : 단일 기업, 단일 연도 재무제표
# ════════════════════════════════════════
def fetch_one(corp_code, year, fs_div="CFS", retry=3):
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
    params = {
        "crtfc_key":  DART_API_KEY,
        "corp_code":  corp_code,
        "bsns_year":  str(year),
        "reprt_code": "11011",   # 사업보고서
        "fs_div":     fs_div,
    }
    for attempt in range(retry):
        try:
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            status = data.get("status", "")
            if status == "000":
                return data.get("list", [])
            if status == "013":   # 연결 없음 → 별도로 재시도
                return None
            return []
        except Exception:
            if attempt < retry - 1:
                time.sleep(1)
    return []

# ════════════════════════════════════════
# STEP 3 : 계정 리스트 → 핵심 지표 딕셔너리
# ════════════════════════════════════════
def parse_accounts(account_list):
    result = {}
    for row in account_list:
        nm = row.get("account_nm", "")
        for kor, eng in ACCOUNT_KEYS.items():
            if kor in nm and eng not in result:
                raw = row.get("thstrm_amount", "").replace(",", "").strip()
                try:
                    result[eng] = int(raw) if raw else None
                except ValueError:
                    result[eng] = None
    return result

# ════════════════════════════════════════
# STEP 4 : 진행상황 로드 / 저장
# ════════════════════════════════════════
def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return set(json.load(f).get("done", []))
    return set()

def save_progress(done_set):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"done": list(done_set), "updated": datetime.now().isoformat()}, f)

# ════════════════════════════════════════
# MAIN
# ════════════════════════════════════════
def main():
    companies = pd.read_csv(COMPANY_CSV, dtype=str).fillna("")
    total = len(companies)
    print(f"[0] 대상 기업: {total:,}개  /  수집 연도: {TARGET_YEARS}")

    corp_map = load_corp_code_map()

    done = load_progress()
    print(f"[2] 이미 완료된 기업: {len(done)}개 (스킵)\n")

    all_rows = []
    newly_done = 0

    for idx, row in companies.iterrows():
        sc     = str(row["종목코드"]).zfill(6)
        name   = row["회사명"]
        market = row["시장구분"]

        if sc in done:
            continue

        corp_code = corp_map.get(sc)
        if not corp_code:
            done.add(sc)
            continue

        company_rows = []
        for year in TARGET_YEARS:
            accounts = fetch_one(corp_code, year, FS_PREFER)
            if accounts is None:
                accounts = fetch_one(corp_code, year, "OFS") or []

            metrics = parse_accounts(accounts)
            if metrics:
                metrics.update({
                    "종목코드": sc,
                    "회사명":   name,
                    "시장구분": market,
                    "연도":     year,
                    "fs_div":   FS_PREFER if accounts else "OFS",
                })
                company_rows.append(metrics)
            time.sleep(DELAY_SEC)

        if company_rows:
            pd.DataFrame(company_rows).to_csv(
                RAW_DIR / f"{sc}.csv", index=False, encoding="utf-8-sig"
            )
            all_rows.extend(company_rows)

        done.add(sc)
        newly_done += 1

        pct = (len(done) / total) * 100
        sys.stdout.write(f"\r  진행: {len(done):,}/{total:,} ({pct:.1f}%)  [{name}]     ")
        sys.stdout.flush()

        if newly_done % BATCH_SAVE == 0:
            save_progress(done)
            _save_combined(all_rows)

    save_progress(done)
    _save_combined(all_rows)
    print(f"\n\n[완료] {COMBINED_OUT}  |  총 수집 행: {len(all_rows):,}개")
    if all_rows:
        df = pd.DataFrame(all_rows)
        print(df[["회사명","연도","revenue","operating_income","net_income"]].head(15).to_string(index=False))

def _save_combined(rows):
    if not rows:
        return
    cols = ["종목코드","회사명","시장구분","연도","fs_div",
            "revenue","operating_income","net_income",
            "total_assets","total_liabilities","total_equity",
            "cfo","cfi","cff"]
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df[cols].to_csv(COMBINED_OUT, index=False, encoding="utf-8-sig")

if __name__ == "__main__":
    main()
