# -*- coding: utf-8 -*-
"""
Korean Stock Screener
─────────────────────
financial.db 기반으로 조건 필터링 → data/candidates.xlsx 저장

실행: python scripts/screener.py
조건 바꾸려면 CRITERIA 딕셔너리만 수정
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent.parent
DB   = BASE / "data" / "financial.db"
OUT  = BASE / "data" / "candidates.xlsx"

# ════════════════════════════════════════════════════════════════
# ★ 스크리닝 기준 (여기만 수정)
# ════════════════════════════════════════════════════════════════
CRITERIA = {
    "시가총액_최소_억":    300,    # 시총 300억 이상
    "PER_최대":           30,     # PER 30 이하
    "PBR_최대":            5,     # PBR 5 이하
    "부채비율_최대":      200,    # 부채비율 200% 이하
    "영업이익_양수":      True,   # 영업이익 > 0
    "매출성장률_최소":   None,    # 매출성장률 하한 (None = 미적용)
    "ROE_최소":          None,    # ROE 하한 (None = 미적용)
    "배당수익률_최소":   None,    # 배당수익률 % 하한 (None = 미적용)
    "연속배당증가_최소":  None,    # 연속 배당 증가 연수 (None = 미적용)
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def run():
    log(f"DB 로드: {DB}")
    conn = sqlite3.connect(DB)

    # ── 데이터 로드 ──────────────────────────────────────────────
    # 최신 연도 재무 지표
    met = pd.read_sql("""
        SELECT 종목코드, 회사명, 시장구분, 연도,
               revenue, operating_income, net_income,
               total_assets, total_liabilities, total_equity,
               ROE, 영업이익률, 부채비율, 매출성장률_3Y, EPS_성장률
        FROM metrics
        WHERE 연도 = (SELECT MAX(연도) FROM metrics)
    """, conn)

    # 주가 정보
    price = pd.read_sql("""
        SELECT 종목코드, 현재주가, 시가총액, 배당수익률,
               [52주최고], [52주최저], ticker
        FROM price_info
    """, conn)

    # 배당 요약
    div = pd.read_sql("""
        SELECT 종목코드, 연속증가연수, 배당컷이력, 최근배당금
        FROM dividend_summary
    """, conn)

    # 업종 비교
    ind = pd.read_sql("""
        SELECT 종목코드, 업종그룹,
               ROE_vs_업종, 영업이익률_vs_업종,
               부채비율_vs_업종, PER_vs_업종, PBR_vs_업종
        FROM industry_comparison
        WHERE 연도 = (SELECT MAX(연도) FROM industry_comparison)
    """, conn)

    conn.close()

    # ── 합치기 ──────────────────────────────────────────────────
    df = met.merge(price, on="종목코드", how="left")
    df = df.merge(div,   on="종목코드", how="left")
    df = df.merge(ind,   on="종목코드", how="left")

    # 수치형 정리
    for c in ["ROE","영업이익률","부채비율","매출성장률_3Y","현재주가","시가총액","배당수익률"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # PER / PBR 직접 계산 (yfinance 미제공)
    df["PER_calc"] = np.where(
        df["net_income"].notna() & (df["net_income"] > 0),
        (df["시가총액"] / df["net_income"]).round(2), np.nan
    )
    df["PBR_calc"] = np.where(
        df["total_equity"].notna() & (df["total_equity"] > 0),
        (df["시가총액"] / df["total_equity"]).round(2), np.nan
    )
    df["시가총액_억"] = (df["시가총액"].fillna(0) / 1e8).round(0)
    df["배당수익률_%"] = (df["배당수익률"].fillna(0) * 100).round(2)

    total = len(df)
    log(f"전체 기업: {total:,}개")

    # ── 필터 적용 ────────────────────────────────────────────────
    mask = pd.Series([True] * len(df), index=df.index)

    # 주가 존재
    mask &= df["현재주가"].notna() & (df["현재주가"] > 0)

    # 시가총액
    min_cap = CRITERIA["시가총액_최소_억"] * 1e8
    mask &= df["시가총액"].fillna(0) >= min_cap

    # PER
    if CRITERIA["PER_최대"]:
        mask &= df["PER_calc"].notna() & (df["PER_calc"] > 0) & (df["PER_calc"] <= CRITERIA["PER_최대"])

    # PBR
    if CRITERIA["PBR_최대"]:
        mask &= df["PBR_calc"].notna() & (df["PBR_calc"] > 0) & (df["PBR_calc"] <= CRITERIA["PBR_최대"])

    # 부채비율
    if CRITERIA["부채비율_최대"]:
        mask &= df["부채비율"].fillna(9999) <= CRITERIA["부채비율_최대"]

    # 영업이익 양수
    if CRITERIA["영업이익_양수"]:
        mask &= df["operating_income"].fillna(-1) > 0

    # 매출성장률
    if CRITERIA["매출성장률_최소"] is not None:
        mask &= df["매출성장률_3Y"].fillna(-999) >= CRITERIA["매출성장률_최소"]

    # ROE
    if CRITERIA["ROE_최소"] is not None:
        mask &= df["ROE"].fillna(-999) >= CRITERIA["ROE_최소"]

    # 배당수익률
    if CRITERIA["배당수익률_최소"] is not None:
        mask &= df["배당수익률_%"] >= CRITERIA["배당수익률_최소"]

    # 연속 배당 증가
    if CRITERIA["연속배당증가_최소"] is not None:
        mask &= df["연속증가연수"].fillna(0) >= CRITERIA["연속배당증가_최소"]

    candidates = df[mask].copy().sort_values("시가총액_억", ascending=False)
    log(f"스크리닝 결과: {total:,}개 → {len(candidates)}개 통과")

    # ── 조건별 탈락 현황 ─────────────────────────────────────────
    print("\n[조건별 통과 현황]")
    print(f"  주가 있음          : {(df['현재주가'].notna() & (df['현재주가']>0)).sum():>5,}개")
    print(f"  시총 {CRITERIA['시가총액_최소_억']}억+           : {(df['시가총액'].fillna(0)>=min_cap).sum():>5,}개")
    print(f"  PER 0~{CRITERIA['PER_최대']}           : {(df['PER_calc'].notna()&(df['PER_calc']>0)&(df['PER_calc']<=CRITERIA['PER_최대'])).sum():>5,}개")
    print(f"  PBR 0~{CRITERIA['PBR_최대']}            : {(df['PBR_calc'].notna()&(df['PBR_calc']>0)&(df['PBR_calc']<=CRITERIA['PBR_최대'])).sum():>5,}개")
    print(f"  부채비율 {CRITERIA['부채비율_최대']}% 이하   : {(df['부채비율'].fillna(9999)<=CRITERIA['부채비율_최대']).sum():>5,}개")
    print(f"  영업이익 양수      : {(df['operating_income'].fillna(-1)>0).sum():>5,}개")

    # ── 엑셀 저장 (시트 3개) ─────────────────────────────────────
    writer = pd.ExcelWriter(OUT, engine="openpyxl")

    # 시트1: 전체 통과 기업
    out_cols = [
        "종목코드","회사명","시장구분","업종그룹",
        "현재주가","시가총액_억","PER_calc","PBR_calc",
        "ROE","영업이익률","부채비율","매출성장률_3Y","EPS_성장률",
        "배당수익률_%","연속증가연수","배당컷이력",
        "ROE_vs_업종","PER_vs_업종","PBR_vs_업종",
        "52주최고","52주최저","ticker","연도"
    ]
    out_cols = [c for c in out_cols if c in candidates.columns]
    candidates[out_cols].rename(columns={
        "PER_calc": "PER", "PBR_calc": "PBR", "배당수익률_%": "배당수익률"
    }).to_excel(writer, sheet_name="통과기업", index=False)

    # 시트2: 배당 우량주 (연속증가 3년+)
    div_stars = candidates[candidates["연속증가연수"].fillna(0) >= 3].sort_values(
        "연속증가연수", ascending=False
    )
    if not div_stars.empty:
        div_stars[out_cols].rename(columns={"PER_calc":"PER","PBR_calc":"PBR","배당수익률_%":"배당수익률"}).to_excel(
            writer, sheet_name="배당우량주", index=False
        )

    # 시트3: 업종 대비 ROE 상위 (ROE_vs_업종 상위 50)
    roe_top = candidates[candidates["ROE_vs_업종"].notna()].sort_values(
        "ROE_vs_업종", ascending=False
    ).head(50)
    if not roe_top.empty:
        roe_top[out_cols].rename(columns={"PER_calc":"PER","PBR_calc":"PBR","배당수익률_%":"배당수익률"}).to_excel(
            writer, sheet_name="업종ROE상위50", index=False
        )

    writer.close()
    log(f"저장 완료: {OUT}")
    log(f"  시트1 '통과기업'     : {len(candidates)}개")
    log(f"  시트2 '배당우량주'   : {len(div_stars)}개 (연속증가 3년+)")
    log(f"  시트3 '업종ROE상위50': {len(roe_top)}개")

    # 상위 20개 미리보기
    print("\n[상위 20개 — 시총 기준]")
    print(candidates[["회사명","시장구분","시가총액_억","PER_calc","PBR_calc","ROE","영업이익률","배당수익률_%"]]
          .head(20).rename(columns={"PER_calc":"PER","PBR_calc":"PBR","배당수익률_%":"배당%"})
          .to_string(index=False))

if __name__ == "__main__":
    run()
