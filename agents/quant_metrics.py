"""quant_metrics.py — Agent 1-3 정량 지표 사전 계산 (Python-first 레이어).

LLM 호출 전에 Python으로 지표를 계산하여 에이전트 context로 전달.
에이전트는 이 숫자를 해석하고, 10-K 주석에서 텍스트 증거를 찾는 역할만 담당.

제공 지표
---------
Agent 1 (Accruals & Cash Flow Quality)
  - Sloan Accruals Ratio  (8분기/연간 시계열 + peer z-score)
  - Cash Conversion Ratio (TTM + 4기간 추세)
  - OCF Composition       (운전자본 변동 비중)

Agent 2 (Revenue Quality)
  - DSO Change YoY        (peer z-score 포함)
  - AR Growth vs Rev Growth + Beneish DSRI
  - Deferred Revenue / RPO 추이

Agent 3 (Capitalization & Useful Life)
  - Capex / Depreciation Ratio (8기간 + peer z-score)
  - Capitalized R&D / Total R&D 비율
  - Beneish AQI (Asset Quality Index)
  - EPS 영향 추정 (Useful Life 변경 시)
"""

from __future__ import annotations

import statistics
from typing import Any


# ===========================================================================
# 공통 유틸리티
# ===========================================================================

def _sorted_desc(ts: dict[str, float]) -> list[tuple[str, float]]:
    """시계열 dict → (period, value) 내림차순 정렬."""
    return sorted(ts.items(), key=lambda x: x[0], reverse=True)


def peer_z_score(value: float, peer_values: list[float]) -> float | None:
    """값의 Peer-relative z-score 계산.

    모집단 = [value] + peer_values.
    데이터 2개 미만 → None.

    Returns:
        z-score (양수 = 평균 위, 음수 = 평균 아래).
        Sloan Accruals 등에서 양수가 높을수록 risk.
    """
    if not peer_values or len(peer_values) < 2:
        return None
    all_vals = [value] + list(peer_values)
    try:
        mean  = statistics.mean(all_vals)
        stdev = statistics.stdev(all_vals)
    except statistics.StatisticsError:
        return None
    if stdev == 0:
        return 0.0
    return round((value - mean) / stdev, 3)


def _common_periods(
    *time_series: dict[str, float],
    top_n: int = 8,
) -> list[str]:
    """여러 시계열의 공통 기간 (최신순 top_n)."""
    if not time_series:
        return []
    common = set(time_series[0].keys())
    for ts in time_series[1:]:
        common &= set(ts.keys())
    return sorted(common, reverse=True)[:top_n]


def _get_peer_latest_value(
    peer_ts_map: dict[str, dict[str, Any]],
    *concept_keys: str,
) -> list[float]:
    """peer_ts_map에서 concept_keys 순서로 첫 번째로 찾은 시계열의 최신값 리스트 반환."""
    vals: list[float] = []
    for peer_ts in peer_ts_map.values():
        for key in concept_keys:
            ts = peer_ts.get(key, {})
            if ts:
                latest_val = sorted(ts.items(), reverse=True)
                if latest_val:
                    vals.append(float(latest_val[0][1]))
                    break
    return vals


# ===========================================================================
# Agent 1: Accruals & Cash Flow Quality
# ===========================================================================

def compute_sloan_accruals(
    ts: dict[str, Any],
    peer_ts_map: dict[str, dict[str, Any]] | None = None,
    periods: int = 8,
) -> dict:
    """Sloan Accruals Ratio = (Net Income - Operating CFO) / Avg Total Assets.

    Schilit 4th ed. 기준: > +0.10 = ALARM, +0.05~0.10 = WARNING.

    Returns:
        {
          "series": [{"period", "net_income", "operating_cf", "avg_assets",
                      "accruals_ratio", "flag"}],
          "latest": {"value": float, "z_score": float|None, "flag": bool},
          "trend":  "DETERIORATING|STABLE|IMPROVING",
          "data_quality": "FULL|PARTIAL|MISSING",
        }
    """
    ni_ts     = ts.get("net_income", {})
    cfo_ts    = ts.get("operating_cf", {})
    assets_ts = ts.get("total_assets", {})

    periods_common = _common_periods(ni_ts, cfo_ts, assets_ts, top_n=periods)
    if not periods_common:
        return {"series": [], "latest": None, "trend": "MISSING", "data_quality": "MISSING"}

    assets_sorted_asc = sorted(assets_ts.keys())

    series: list[dict] = []
    for period in periods_common:
        ni  = float(ni_ts.get(period, 0))
        cfo = float(cfo_ts.get(period, 0))
        ta  = float(assets_ts.get(period, 0))

        # Avg Total Assets = (현재 + 직전) / 2
        idx = assets_sorted_asc.index(period) if period in assets_sorted_asc else -1
        if idx > 0:
            prev_ta = float(assets_ts.get(assets_sorted_asc[idx - 1], ta))
            avg_ta  = (ta + prev_ta) / 2
        else:
            avg_ta = ta

        if avg_ta == 0:
            continue

        ratio = (ni - cfo) / avg_ta
        flag  = ratio > 0.08 or ratio < -0.10   # 양방향 이상치

        series.append({
            "period":            period,
            "net_income":        round(ni, 0),
            "operating_cf":      round(cfo, 0),
            "avg_total_assets":  round(avg_ta, 0),
            "accruals_ratio":    round(ratio, 4),
            "flag":              flag,
        })

    if not series:
        return {"series": [], "latest": None, "trend": "MISSING", "data_quality": "MISSING"}

    latest_val = series[0]["accruals_ratio"]

    # Peer z-score
    z = None
    if peer_ts_map:
        peer_ratios: list[float] = []
        for p_ts in peer_ts_map.values():
            p_ni  = p_ts.get("net_income", {})
            p_cfo = p_ts.get("operating_cf", {})
            p_ta  = p_ts.get("total_assets", {})
            common = _common_periods(p_ni, p_cfo, p_ta, top_n=1)
            if common:
                p = common[0]
                denom = float(p_ta.get(p, 0))
                if denom:
                    peer_ratios.append(
                        (float(p_ni.get(p, 0)) - float(p_cfo.get(p, 0))) / denom
                    )
        z = peer_z_score(latest_val, peer_ratios)

    # 트렌드 (최신 3기간)
    recent = [s["accruals_ratio"] for s in series[:3]]
    trend = "STABLE"
    if len(recent) >= 2:
        if recent[0] > recent[-1] + 0.03:
            trend = "DETERIORATING"
        elif recent[0] < recent[-1] - 0.03:
            trend = "IMPROVING"

    return {
        "series":       series,
        "latest":       {"value": latest_val, "z_score": z, "flag": series[0]["flag"]},
        "trend":        trend,
        "data_quality": "FULL" if len(series) >= 3 else "PARTIAL",
    }


def compute_cash_conversion(
    ts: dict[str, Any],
    peer_ts_map: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """Cash Conversion Ratio (CCR) = Operating CFO / Net Income.

    연속 4분기 < 1.0 = red flag.

    Returns:
        {
          "series": [{"period", "cfo", "net_income", "ccr", "flag"}],
          "latest": {"value": float, "flag": bool},
          "quarters_below_1": int,
          "trend": "IMPROVING|STABLE|DETERIORATING",
          "z_score": float|None,
        }
    """
    cfo_ts = ts.get("operating_cf", {})
    ni_ts  = ts.get("net_income", {})

    common = _common_periods(cfo_ts, ni_ts, top_n=8)

    series: list[dict] = []
    for period in common:
        cfo = float(cfo_ts.get(period, 0))
        ni  = float(ni_ts.get(period, 0))
        if ni == 0:
            continue
        ccr  = cfo / ni
        flag = ccr < 1.0
        series.append({
            "period":     period,
            "cfo":        round(cfo, 0),
            "net_income": round(ni, 0),
            "ccr":        round(ccr, 3),
            "flag":       flag,
        })

    if not series:
        return {"series": [], "latest": None, "quarters_below_1": 0, "trend": "MISSING"}

    latest_ccr     = series[0]["ccr"]
    quarters_below = sum(1 for s in series if s["flag"])

    # Peer z-score
    z = None
    if peer_ts_map:
        peer_ccrs: list[float] = []
        for p_ts in peer_ts_map.values():
            p_cfo = p_ts.get("operating_cf", {})
            p_ni  = p_ts.get("net_income", {})
            c     = _common_periods(p_cfo, p_ni, top_n=1)
            if c:
                p_ni_val = float(p_ni.get(c[0], 0))
                if p_ni_val:
                    peer_ccrs.append(float(p_cfo.get(c[0], 0)) / p_ni_val)
        z = peer_z_score(latest_ccr, peer_ccrs)

    trend = "STABLE"
    if len(series) >= 2:
        delta = series[0]["ccr"] - series[-1]["ccr"]
        if delta < -0.1:
            trend = "DETERIORATING"
        elif delta > 0.1:
            trend = "IMPROVING"

    return {
        "series":           series,
        "latest":           {"value": latest_ccr, "flag": latest_ccr < 1.0},
        "quarters_below_1": quarters_below,
        "trend":            trend,
        "z_score":          z,
    }


def compute_ocf_composition(ts: dict[str, Any]) -> dict:
    """OCF Composition: 운전자본 변동 비중 분석.

    OCF ≈ NI + D&A + Δ운전자본 + 기타
    Δ운전자본 proxy = CFO - NI - D&A
    비중이 30% 초과 시 flag.

    Returns:
        {
          "series": [{"period", "cfo", "ni", "da", "delta_wc_proxy",
                      "working_capital_pct", "flag"}],
          "latest": {"working_capital_pct": float, "flag": bool},
        }
    """
    cfo_ts = ts.get("operating_cf", {})
    ni_ts  = ts.get("net_income", {})
    da_ts  = ts.get("depreciation", {})

    common = _common_periods(cfo_ts, ni_ts, da_ts, top_n=5)

    series: list[dict] = []
    for period in common:
        cfo = float(cfo_ts.get(period, 0))
        ni  = float(ni_ts.get(period, 0))
        da  = float(da_ts.get(period, 0))
        if cfo == 0:
            continue
        delta_wc = cfo - ni - da
        wc_pct   = delta_wc / cfo
        series.append({
            "period":               period,
            "cfo":                  round(cfo, 0),
            "net_income":           round(ni, 0),
            "depreciation":         round(da, 0),
            "delta_wc_proxy":       round(delta_wc, 0),
            "working_capital_pct":  round(wc_pct, 3),
            "flag":                 wc_pct > 0.30,
        })

    if not series:
        return {"series": [], "latest": None}

    return {
        "series": series,
        "latest": {
            "working_capital_pct": series[0]["working_capital_pct"],
            "flag":                series[0]["flag"],
        },
    }


def agent1_precomputed(
    ts: dict[str, Any],
    peer_ts_map: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """Agent 1 전용 사전 계산 패키지 (orchestrator → agent 전달용)."""
    return {
        "agent":         "A1_AccrualsCashFlow",
        "ticker":        ts.get("ticker", ""),
        "entity_name":   ts.get("meta", {}).get("entity_name", ""),
        "sloan_accruals": compute_sloan_accruals(ts, peer_ts_map),
        "cash_conversion": compute_cash_conversion(ts, peer_ts_map),
        "ocf_composition": compute_ocf_composition(ts),
        "peer_tickers":  list(peer_ts_map.keys()) if peer_ts_map else [],
    }


# ===========================================================================
# Agent 2: Revenue Quality
# ===========================================================================

def compute_dso(
    ts: dict[str, Any],
    peer_ts_map: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """Days Sales Outstanding = AR / Revenue × 365.

    YoY +7일 이상 = WARNING, +15일 이상 = ALARM.

    Returns:
        {
          "series": [{"period", "ar", "revenue", "dso", "flag"}],
          "latest": {"value": float, "z_score": float|None},
          "yoy_change_days": float|None,
          "trend": "EXPANDING|STABLE|CONTRACTING",
        }
    """
    ar_ts  = ts.get("accounts_receivable", {})
    rev_ts = ts.get("revenue", {})

    common = _common_periods(ar_ts, rev_ts, top_n=6)

    series: list[dict] = []
    for period in common:
        ar  = float(ar_ts.get(period, 0))
        rev = float(rev_ts.get(period, 0))
        if rev == 0:
            continue
        dso = ar / rev * 365
        series.append({
            "period":  period,
            "ar":      round(ar, 0),
            "revenue": round(rev, 0),
            "dso":     round(dso, 1),
            "flag":    False,
        })

    if not series:
        return {"series": [], "latest": None, "yoy_change_days": None, "trend": "MISSING"}

    yoy_change: float | None = None
    if len(series) >= 2:
        yoy_change = series[0]["dso"] - series[1]["dso"]
        series[0]["flag"] = yoy_change > 7

    # Peer z-score
    z = None
    if peer_ts_map:
        peer_dsos: list[float] = []
        for p_ts in peer_ts_map.values():
            p_ar  = p_ts.get("accounts_receivable", {})
            p_rev = p_ts.get("revenue", {})
            c     = _common_periods(p_ar, p_rev, top_n=1)
            if c:
                p_rev_val = float(p_rev.get(c[0], 0))
                if p_rev_val:
                    peer_dsos.append(float(p_ar.get(c[0], 0)) / p_rev_val * 365)
        z = peer_z_score(series[0]["dso"], peer_dsos)

    trend = "STABLE"
    if len(series) >= 2:
        spread = series[0]["dso"] - series[-1]["dso"]
        if spread > 5:
            trend = "EXPANDING"
        elif spread < -5:
            trend = "CONTRACTING"

    return {
        "series":          series,
        "latest":          {"value": series[0]["dso"], "z_score": z},
        "yoy_change_days": round(yoy_change, 1) if yoy_change is not None else None,
        "trend":           trend,
    }


def compute_ar_revenue_spread(ts: dict[str, Any]) -> dict:
    """AR Growth vs Revenue Growth 괴리율 (channel stuffing 지표).

    Spread > +10%p = WARNING, > +20%p = ALARM.
    Beneish DSRI > 1.465 = manipulator threshold.

    Returns:
        {
          "series": [{"period", "ar_growth", "rev_growth", "spread",
                      "dsri", "flag"}],
          "latest": {"spread": float, "flag": bool, "dsri": float|None},
        }
    """
    ar_ts  = ts.get("accounts_receivable", {})
    rev_ts = ts.get("revenue", {})

    periods_desc = sorted(set(ar_ts.keys()) & set(rev_ts.keys()), reverse=True)[:7]

    series: list[dict] = []
    for i in range(len(periods_desc) - 1):
        curr = periods_desc[i]
        prev = periods_desc[i + 1]

        ar_c  = float(ar_ts.get(curr, 0))
        ar_p  = float(ar_ts.get(prev, 0))
        rev_c = float(rev_ts.get(curr, 0))
        rev_p = float(rev_ts.get(prev, 0))

        if ar_p == 0 or rev_p == 0:
            continue

        ar_g    = (ar_c - ar_p) / ar_p
        rev_g   = (rev_c - rev_p) / rev_p
        spread  = ar_g - rev_g
        dsri    = (ar_c / rev_c) / (ar_p / rev_p) if rev_c and rev_p else None
        flag    = spread > 0.10 or (dsri is not None and dsri > 1.465)

        series.append({
            "period":     curr,
            "ar_growth":  round(ar_g, 4),
            "rev_growth": round(rev_g, 4),
            "spread":     round(spread, 4),
            "dsri":       round(dsri, 3) if dsri is not None else None,
            "flag":       flag,
        })

    if not series:
        return {"series": [], "latest": None}

    return {
        "series": series,
        "latest": {
            "spread": series[0]["spread"],
            "flag":   series[0]["flag"],
            "dsri":   series[0]["dsri"],
        },
    }


def compute_deferred_revenue_trajectory(ts: dict[str, Any]) -> dict:
    """Deferred Revenue / Revenue 비율 추이.

    DR 감소 + Revenue 증가 → pull-forward risk.

    Returns:
        {
          "series": [{"period", "deferred_rev", "revenue", "dr_rev_ratio"}],
          "latest": {"value": float, "dr_rev_ratio": float, "trend": str, "flag": bool},
        }
    """
    dr_ts  = ts.get("deferred_revenue", {})
    rev_ts = ts.get("revenue", {})

    if not dr_ts:
        return {"series": [], "latest": {"trend": "UNKNOWN", "flag": False, "value": None, "dr_rev_ratio": None}}

    common = _common_periods(dr_ts, rev_ts, top_n=5)
    series: list[dict] = []
    for period in common:
        dr  = float(dr_ts.get(period, 0))
        rev = float(rev_ts.get(period, 0))
        ratio = dr / rev if rev > 0 else 0
        series.append({
            "period":       period,
            "deferred_rev": round(dr, 0),
            "revenue":      round(rev, 0),
            "dr_rev_ratio": round(ratio, 4),
        })

    if not series:
        return {"series": [], "latest": {"trend": "UNKNOWN", "flag": False, "value": None, "dr_rev_ratio": None}}

    trend = "STABLE"
    flag  = False
    if len(series) >= 2:
        dr_delta  = series[0]["deferred_rev"] - series[-1]["deferred_rev"]
        rev_delta = series[0]["revenue"]      - series[-1]["revenue"]
        if dr_delta < 0 and rev_delta > 0:
            trend = "DECLINING"
            flag  = True
        elif dr_delta > 0:
            trend = "GROWING"

    return {
        "series": series,
        "latest": {
            "value":        series[0]["deferred_rev"],
            "dr_rev_ratio": series[0]["dr_rev_ratio"],
            "trend":        trend,
            "flag":         flag,
        },
    }


def compute_gross_margin_index(ts: dict[str, Any]) -> dict:
    """Beneish Gross Margin Index (GMI) = GM_{t-1} / GM_t.

    GMI > 1.193 → WARNING (마진 악화 → 조작 압력).
    """
    gp_ts  = ts.get("gross_profit", {})
    rev_ts = ts.get("revenue", {})

    common = _common_periods(gp_ts, rev_ts, top_n=4)
    if len(common) < 2:
        return {"gmi": None, "flag": False}

    curr = common[0]
    prev = common[1]

    rev_c = float(rev_ts.get(curr, 0))
    rev_p = float(rev_ts.get(prev, 0))
    gp_c  = float(gp_ts.get(curr, 0))
    gp_p  = float(gp_ts.get(prev, 0))

    if rev_c == 0 or rev_p == 0:
        return {"gmi": None, "flag": False}

    gm_curr = gp_c / rev_c
    gm_prev = gp_p / rev_p

    if gm_curr == 0:
        return {"gmi": None, "flag": False}

    gmi = gm_prev / gm_curr
    return {
        "gmi":           round(gmi, 3),
        "flag":          gmi > 1.193,
        "gm_current":    round(gm_curr, 4),
        "gm_prior":      round(gm_prev, 4),
        "current_period": curr,
        "prior_period":   prev,
    }


def agent2_precomputed(
    ts: dict[str, Any],
    peer_ts_map: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """Agent 2 전용 사전 계산 패키지."""
    return {
        "agent":              "A2_RevenueQuality",
        "ticker":             ts.get("ticker", ""),
        "entity_name":        ts.get("meta", {}).get("entity_name", ""),
        "dso":                compute_dso(ts, peer_ts_map),
        "ar_revenue_spread":  compute_ar_revenue_spread(ts),
        "deferred_rev":       compute_deferred_revenue_trajectory(ts),
        "gmi":                compute_gross_margin_index(ts),
        "peer_tickers":       list(peer_ts_map.keys()) if peer_ts_map else [],
    }


# ===========================================================================
# Agent 3: Capitalization & Useful Life
# ===========================================================================

def compute_capex_dep_ratio(
    ts: dict[str, Any],
    peer_ts_map: dict[str, dict[str, Any]] | None = None,
    periods: int = 8,
) -> dict:
    """Capex / Depreciation Ratio = CapEx / D&A.

    > 3.0 = 대규모 자산 확장 (성장 여부 확인 필요).
    Ratio 상승 + revenue 정체 = 감가상각 억제 의심.

    Returns:
        {
          "series": [{"period", "capex", "depreciation", "ratio", "flag"}],
          "latest": {"value": float, "z_score": float|None, "flag": bool},
          "trend":  "RISING|STABLE|FALLING",
        }
    """
    capex_ts = ts.get("capex", {})
    da_ts    = ts.get("depreciation", {})

    common = _common_periods(capex_ts, da_ts, top_n=periods)

    series: list[dict] = []
    for period in common:
        capex = float(capex_ts.get(period, 0))
        da    = float(da_ts.get(period, 0))
        if da == 0:
            continue
        ratio = capex / da
        flag  = ratio > 3.0
        series.append({
            "period":       period,
            "capex":        round(capex, 0),
            "depreciation": round(da, 0),
            "ratio":        round(ratio, 3),
            "flag":         flag,
        })

    if not series:
        return {"series": [], "latest": None, "trend": "MISSING"}

    latest_ratio = series[0]["ratio"]

    # Peer z-score
    z = None
    if peer_ts_map:
        peer_ratios: list[float] = []
        for p_ts in peer_ts_map.values():
            p_cx = p_ts.get("capex", {})
            p_da = p_ts.get("depreciation", {})
            c    = _common_periods(p_cx, p_da, top_n=1)
            if c:
                p_da_val = float(p_da.get(c[0], 0))
                if p_da_val:
                    peer_ratios.append(float(p_cx.get(c[0], 0)) / p_da_val)
        z = peer_z_score(latest_ratio, peer_ratios)

    trend = "STABLE"
    if len(series) >= 2:
        delta = series[0]["ratio"] - series[-1]["ratio"]
        if delta > 0.3:
            trend = "RISING"
        elif delta < -0.3:
            trend = "FALLING"

    return {
        "series": series,
        "latest": {"value": latest_ratio, "z_score": z, "flag": series[0]["flag"]},
        "trend":  trend,
    }


def compute_cap_rd_ratio(ts: dict[str, Any]) -> dict:
    """Capitalized R&D (Internal-Use Software) / Total R&D.

    > 30% = WARNING (aggressive capitalization).
    급증 + 신제품 출시 둔화 = ALARM (WorldCom 패턴).

    Returns:
        {
          "series": [{"period", "capitalized_rd", "total_rd", "ratio", "flag"}],
          "latest": {"value": float|None, "flag": bool},
        }
    """
    cap_ts = ts.get("capitalized_software", {})
    rd_ts  = ts.get("rd_expense", {})

    if not cap_ts:
        return {"series": [], "latest": {"value": None, "flag": False}}

    common = _common_periods(cap_ts, rd_ts, top_n=5)

    series: list[dict] = []
    for period in common:
        cap_rd = float(cap_ts.get(period, 0))
        total  = float(rd_ts.get(period, 0))
        if total == 0:
            continue
        ratio = cap_rd / total
        series.append({
            "period":          period,
            "capitalized_rd":  round(cap_rd, 0),
            "total_rd":        round(total, 0),
            "ratio":           round(ratio, 4),
            "flag":            ratio > 0.30,
        })

    if not series:
        return {"series": [], "latest": {"value": None, "flag": False}}

    return {
        "series": series,
        "latest": {"value": series[0]["ratio"], "flag": series[0]["flag"]},
    }


def compute_beneish_aqi(ts: dict[str, Any]) -> dict:
    """Beneish Asset Quality Index (AQI).

    AQI = (1 - (CA + PPE) / TA)_t / (1 - (CA + PPE) / TA)_{t-1}
    AQI > 1.254 → WARNING (비유동 자산 비중 급증).

    근사: CA 없으면 ppe_net만 사용.
    """
    assets_ts = ts.get("total_assets", {})
    ppe_ts    = ts.get("ppe_net", {})
    # 현재 자산은 XBRL에서 직접 없을 수도 있음 → PPE proxy만 사용

    common = _common_periods(assets_ts, ppe_ts, top_n=4)
    if len(common) < 2:
        return {"aqi": None, "flag": False}

    curr = common[0]
    prev = common[1]
    ta_c  = float(assets_ts.get(curr, 0))
    ta_p  = float(assets_ts.get(prev, 0))
    ppe_c = float(ppe_ts.get(curr, 0))
    ppe_p = float(ppe_ts.get(prev, 0))

    if ta_c == 0 or ta_p == 0:
        return {"aqi": None, "flag": False}

    nca_c = 1 - ppe_c / ta_c
    nca_p = 1 - ppe_p / ta_p

    if nca_p == 0:
        return {"aqi": None, "flag": False}

    aqi = nca_c / nca_p
    return {
        "aqi":            round(aqi, 3),
        "flag":           aqi > 1.254,
        "current_period": curr,
        "prior_period":   prev,
    }


def estimate_useful_life_eps_impact(
    ppe_net: float,
    old_life_years: float,
    new_life_years: float,
    tax_rate: float = 0.21,
    shares_outstanding: float | None = None,
) -> dict:
    """Useful Life 연장 시 EPS 영향 추정.

    절약된 D&A = PPE_net × (1/old_life - 1/new_life)
    EPS 영향   = 절약된 D&A × (1 - 세율) / 발행주식수

    Args:
        ppe_net:            순 PP&E (USD)
        old_life_years:     기존 내용연수 (년)
        new_life_years:     변경 후 내용연수 (년)
        tax_rate:           법인세율 (기본 21%)
        shares_outstanding: 발행주식수 (None이면 per-share 계산 생략)

    Returns:
        {
          "delta_depreciation_usd": float,   # 감소된 D&A (비용 절약)
          "after_tax_eps_impact":   float|None,
          "annualized_savings_usd": float,
          "flag":                   bool,     # EPS 5% 이상 부풀기 flag
        }
    """
    if old_life_years <= 0 or new_life_years <= 0 or new_life_years <= old_life_years:
        return {
            "delta_depreciation_usd": 0.0,
            "after_tax_eps_impact":   None,
            "annualized_savings_usd": 0.0,
            "flag":                   False,
        }

    delta_dep = ppe_net * (1 / old_life_years - 1 / new_life_years)
    after_tax = delta_dep * (1 - tax_rate)
    eps_impact = after_tax / shares_outstanding if shares_outstanding else None

    return {
        "delta_depreciation_usd": round(delta_dep, 0),
        "after_tax_savings_usd":  round(after_tax, 0),
        "after_tax_eps_impact":   round(eps_impact, 4) if eps_impact is not None else None,
        "annualized_savings_usd": round(after_tax, 0),
        "flag":                   True if delta_dep > 0 else False,
    }


def agent3_precomputed(
    ts: dict[str, Any],
    peer_ts_map: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """Agent 3 전용 사전 계산 패키지."""
    ppe_net_ts  = ts.get("ppe_net", {})
    shares_ts   = ts.get("shares_outstanding", {})

    # 최신 ppe_net, shares
    latest_ppe    = sorted(ppe_net_ts.items(), reverse=True)[0][1] if ppe_net_ts else 0
    latest_shares = sorted(shares_ts.items(), reverse=True)[0][1] if shares_ts else None

    return {
        "agent":           "A3_CapexUsefulLife",
        "ticker":          ts.get("ticker", ""),
        "entity_name":     ts.get("meta", {}).get("entity_name", ""),
        "capex_dep_ratio": compute_capex_dep_ratio(ts, peer_ts_map),
        "cap_rd_ratio":    compute_cap_rd_ratio(ts),
        "aqi":             compute_beneish_aqi(ts),
        "ppe_net_latest":  round(float(latest_ppe), 0),
        "shares_outstanding": float(latest_shares) if latest_shares else None,
        "peer_tickers":    list(peer_ts_map.keys()) if peer_ts_map else [],
        # EPS 영향은 LLM이 10-K에서 내용연수를 추출한 후 이 함수를 가이드로 사용
        "eps_impact_helper": {
            "note": "10-K PP&E note에서 old_life_years / new_life_years 추출 후 계산",
            "formula": "PPE_net × (1/old_life - 1/new_life) × (1 - 0.21) / shares_outstanding",
            "ppe_net_usd": round(float(latest_ppe), 0),
            "shares_outstanding": float(latest_shares) if latest_shares else None,
        },
    }
