"""peer_set.py — AI 인프라 섹터 Peer 기업 매핑 모듈.

Agent 1-3 정량 지표 산출 시 Peer-relative z-score 계산에 사용.
Orchestrator가 Peer 데이터를 한 번만 fetch하고 세 에이전트에 공유.

CLAUDE.md Layer 분류 기준 (L1~L9) 적용.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# AI 인프라 섹터별 Peer 매핑
# ---------------------------------------------------------------------------

_PEER_MAP: dict[str, list[str]] = {
    # L2 Fabless (GPU / AI chip)
    "NVDA": ["AMD", "AVGO", "MRVL"],
    "AMD":  ["NVDA", "AVGO", "MRVL"],
    "AVGO": ["NVDA", "AMD", "MRVL"],
    "MRVL": ["NVDA", "AMD", "AVGO"],
    "QCOM": ["NVDA", "AMD", "AVGO"],

    # L3 Foundry
    "INTC": ["AMD", "NVDA", "QCOM"],
    "TSM":  ["INTC", "ASML", "AMAT"],

    # L4 반도체 장비
    "ASML": ["AMAT", "LRCX", "KLAC"],
    "AMAT": ["ASML", "LRCX", "KLAC"],
    "LRCX": ["ASML", "AMAT", "KLAC"],
    "KLAC": ["ASML", "AMAT", "LRCX"],

    # L5 부품/소재
    "ENTG": ["ICHR", "ONTO"],
    "ICHR": ["ENTG", "UCTT"],
    "ONTO": ["ENTG", "CAMT"],

    # L6 ODM/서버
    "SMCI": ["DELL", "HPE"],
    "DELL": ["SMCI", "HPE"],
    "HPE":  ["SMCI", "DELL"],

    # L7 Data Center REIT
    "EQIX": ["DLR", "IRM"],
    "DLR":  ["EQIX", "IRM"],
    "IRM":  ["EQIX", "DLR"],

    # L8 Neocloud
    "CRWV": ["NBIS", "APLD"],
    "NBIS": ["CRWV", "APLD"],
    "APLD": ["CRWV", "NBIS"],

    # L9 Hyperscaler
    "MSFT":  ["GOOGL", "META", "AMZN"],
    "GOOGL": ["MSFT", "META", "AMZN"],
    "META":  ["MSFT", "GOOGL", "AMZN"],
    "AMZN":  ["MSFT", "GOOGL", "META"],
    "ORCL":  ["MSFT", "AMZN", "GOOGL"],
}

# 섹터 그룹 (시가총액 유사 비교용)
_SECTOR_GROUP: dict[str, str] = {
    # Fabless
    "NVDA": "fabless_large", "AMD": "fabless_large",
    "AVGO": "fabless_large", "MRVL": "fabless_mid",
    "QCOM": "fabless_large",
    # Foundry
    "INTC": "foundry_large", "TSM": "foundry_large",
    # Equipment
    "ASML": "equipment_large", "AMAT": "equipment_large",
    "LRCX": "equipment_large", "KLAC": "equipment_large",
    # Parts
    "ENTG": "parts_mid", "ICHR": "parts_small", "ONTO": "parts_small",
    # Server / ODM
    "SMCI": "server_mid", "DELL": "server_large", "HPE": "server_large",
    # DC REIT
    "EQIX": "dc_reit_large", "DLR": "dc_reit_large", "IRM": "dc_reit_large",
    # Neocloud
    "CRWV": "neocloud_large", "NBIS": "neocloud_small", "APLD": "neocloud_small",
    # Hyperscaler
    "MSFT":  "hyperscaler", "GOOGL": "hyperscaler",
    "META":  "hyperscaler", "AMZN":  "hyperscaler",
    "ORCL":  "hyperscaler",
}


async def get_peer_set(ticker: str, sector: str = "") -> list[str]:
    """동일 섹터 + 비슷한 시가총액 5~10개 종목 반환.

    Args:
        ticker: 기준 종목 티커 (예: "NVDA")
        sector: 섹터 힌트 — 없으면 _PEER_MAP 직접 조회

    Returns:
        peer ticker 리스트 (3~8개).
        매핑 없을 경우 섹터 그룹 내 fallback 조회.

    Example:
        peers = await get_peer_set("NVDA")
        # → ["AMD", "AVGO", "MRVL"]
    """
    key = ticker.upper().strip()

    # 1순위: 직접 매핑
    peers = _PEER_MAP.get(key)
    if peers:
        return list(peers)

    # 2순위: 섹터 그룹 내 동종 종목
    own_group = _SECTOR_GROUP.get(key, "")
    if own_group:
        fallback = [
            t for t, g in _SECTOR_GROUP.items()
            if g == own_group and t != key
        ][:6]
        if fallback:
            return fallback

    # 3순위: 섹터 힌트 키워드 매칭
    if sector:
        sector_lower = sector.lower()
        keyword_hits = [
            t for t, g in _SECTOR_GROUP.items()
            if sector_lower in g.lower() and t != key
        ][:5]
        if keyword_hits:
            return keyword_hits

    return []


def get_sector_group(ticker: str) -> str:
    """종목 섹터 그룹 반환. 없으면 'unknown'."""
    return _SECTOR_GROUP.get(ticker.upper().strip(), "unknown")


def get_all_ai_infra_tickers() -> list[str]:
    """모든 AI 인프라 매핑 티커 반환 (스크리너 초기 풀 생성용)."""
    return sorted(set(_PEER_MAP.keys()) | set(_SECTOR_GROUP.keys()))
