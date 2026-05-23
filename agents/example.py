"""사용 예제: 라이브러리로 직접 호출.

병렬성 비교를 위해 동일 종목을 분석하고 소요시간을 출력합니다.
"""

from __future__ import annotations

import asyncio
import time

from orchestrator import analyze_stock


async def demo_one(ticker: str) -> None:
    print(f"\n=== {ticker} 분석 시작 ===")
    t0 = time.perf_counter()
    result = await analyze_stock(ticker)
    dt = time.perf_counter() - t0
    print(f"총 소요: {dt:.2f}s · 시장={result.market}")
    for key, r in result.reports.items():
        flag = "OK" if not r.error else "ERR"
        cost = f"${r.cost_usd:.4f}" if r.cost_usd else "?"
        print(f"  - {key:<14} {flag}  {r.elapsed_sec:.2f}s  {cost}")
        if r.summary:
            print(f"      summary: {r.summary}")


async def main() -> None:
    # 한국 + 미국 한 종목씩
    await demo_one("005930")   # 삼성전자
    await demo_one("AAPL")     # 애플


if __name__ == "__main__":
    asyncio.run(main())
