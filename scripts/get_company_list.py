# -*- coding: utf-8 -*-
"""
KOSPI + KOSDAQ 전체 상장사 목록 수집
FinanceDataReader -> data/company_list.csv
"""
import pandas as pd
import FinanceDataReader as fdr
from pathlib import Path

BASE = Path(__file__).parent.parent   # korean-stock-analyzer/
OUT  = BASE / "data" / "company_list.csv"

print("[*] KOSPI 종목 수집 중...")
kospi  = fdr.StockListing("KOSPI")
kospi["시장구분"] = "KOSPI"
print(f"    -> {len(kospi)}개")

print("[*] KOSDAQ 종목 수집 중...")
kosdaq = fdr.StockListing("KOSDAQ")
kosdaq["시장구분"] = "KOSDAQ"
print(f"    -> {len(kosdaq)}개")

df = pd.concat([kospi, kosdaq], ignore_index=True)

# 컬럼 정리
col_map = {}
for c in df.columns:
    cl = c.lower().strip()
    if cl in ("name", "corp_name", "회사명", "종목명", "name_kr"):
        col_map[c] = "회사명"
    elif cl in ("code", "symbol", "종목코드", "ticker", "stock_code"):
        col_map[c] = "종목코드"
    elif cl in ("dept", "업종", "sector", "industry", "induty"):
        col_map[c] = "업종"

df = df.rename(columns=col_map)
for need in ["회사명", "종목코드", "업종"]:
    if need not in df.columns:
        df[need] = ""

out_cols = ["회사명", "종목코드", "시장구분", "업종"]
df = df[out_cols].dropna(subset=["종목코드"])

OUT.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(OUT, index=False, encoding="utf-8-sig")

print(f"\n[완료] {OUT} 저장")
print(f"       총 {len(df):,}개 기업 (KOSPI {len(df[df.시장구분=='KOSPI'])} + KOSDAQ {len(df[df.시장구분=='KOSDAQ'])})\n")
print(df.head(10).to_string(index=False))
