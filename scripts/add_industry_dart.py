# -*- coding: utf-8 -*-
"""
DART API로 업종 컬럼 추가
실행: python scripts/add_industry_dart.py
"""
import requests, zipfile, io, xml.etree.ElementTree as ET
import pandas as pd, time
from pathlib import Path

DART_API_KEY = "5e67214e2ada4027d32b8fd208cd9025a07b5be5"

BASE     = Path(__file__).parent.parent
CSV_PATH = BASE / "data" / "company_list.csv"

# ── 1. corpCode.xml 다운로드 ──────────────────────────────────────
print("[1] DART corpCode 다운로드...")
r = requests.get(f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_API_KEY}", timeout=30)
with zipfile.ZipFile(io.BytesIO(r.content)) as z:
    with z.open("CORPCODE.xml") as f:
        root = ET.parse(f).getroot()

code_map = {}
for item in root.findall("list"):
    sc = item.findtext("stock_code", "").strip()
    cc = item.findtext("corp_code", "").strip()
    if sc:
        code_map[sc] = cc
print(f"    corp_code {len(code_map)}개 확보")

# ── 2. CSV 로드 ───────────────────────────────────────────────────
df = pd.read_csv(CSV_PATH, dtype=str)
print(f"[2] 기업 수: {len(df)}개")

# ── 3. 업종 수집 ──────────────────────────────────────────────────
print("[3] 업종 수집 중 (약 5~10분 소요)...")
industry = {}
total = len(df)
for i, row in df.iterrows():
    sc = str(row["종목코드"]).zfill(6)
    cc = code_map.get(sc)
    if not cc:
        continue
    try:
        res = requests.get(
            "https://opendart.fss.or.kr/api/company.json",
            params={"crtfc_key": DART_API_KEY, "corp_code": cc},
            timeout=10
        ).json()
        if res.get("status") == "000":
            industry[sc] = res.get("induty_code", "")
    except Exception:
        pass
    if (i + 1) % 200 == 0:
        print(f"    {i+1}/{total} 완료")
    time.sleep(0.08)

# ── 4. 저장 ───────────────────────────────────────────────────────
df["업종"] = df["종목코드"].apply(lambda x: industry.get(str(x).zfill(6), ""))
df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
print(f"\n[완료] 업종 추가 저장: {CSV_PATH}")
print(df[df["업종"] != ""][["회사명","종목코드","시장구분","업종"]].head(10).to_string(index=False))
