"""CLI 진입점 — US Forensic Accounting Pipeline.

사용법:
    # 단일 종목
    python main.py NVDA
    python main.py MSFT

    # 멀티 종목 동시 분석
    python main.py NVDA MSFT META SMCI

    # Excel 저장 (reports/forensic_candidates.xlsx 누적)
    python main.py NVDA MSFT --excel

    # Opus 총괄 스킵 (빠른 실행 / 개발용)
    python main.py NVDA --no-orchestrator

    # JSON 출력
    python main.py NVDA --json

    # 파일로 저장
    python main.py NVDA --out reports/nvda_forensic.md

    # Tier 필터 (1~2만 출력)
    python main.py NVDA MSFT META --tier-max 2

Forensic Score: 0(최악/Short 후보) ~ 100(깨끗한 회계)
Tier:  1=Active Short  2=Monitor  3=Avoid  4=Archive
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from orchestrator import (
    analyze_stocks,
    calculate_forensic_score,
    export_to_excel,
    ForensicResult,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AI Infrastructure Forensic Accounting Pipeline (US Only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "tickers",
        nargs="+",
        help="미국 종목 티커 (복수 가능 — 예: NVDA MSFT META SMCI)",
    )
    p.add_argument(
        "--no-orchestrator",
        action="store_true",
        help="Opus 총괄 에이전트 스킵 (빠른 실행, Excel/Tier 미지원)",
    )
    p.add_argument(
        "--excel",
        action="store_true",
        help="결과를 reports/forensic_candidates.xlsx에 누적 저장",
    )
    p.add_argument(
        "--excel-path",
        type=str,
        default=None,
        help="Excel 저장 경로 지정 (기본: reports/forensic_candidates.xlsx)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="결과를 JSON 형식으로 출력",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="결과를 파일로 저장 (.md / .json)",
    )
    p.add_argument(
        "--tier-max",
        type=int,
        choices=[1, 2, 3, 4],
        default=None,
        help="이 Tier 이하 종목만 리포트 출력 (예: --tier-max 2 → Tier 1+2만 출력)",
    )
    return p.parse_args()


def _print_score_table(results: list[ForensicResult]) -> None:
    """터미널에 Forensic Score 요약 테이블 출력."""
    print("\n" + "=" * 72)
    print(
        f"{'Ticker':<8} {'Score':>6} {'Tier':>5} {'Tier Label':<14} "
        f"{'Accruals':>9} {'Revenue':>8} {'Capex':>6} "
        f"{'10-K':>5} {'NLP':>5} {'Cat':>4}"
    )
    print("-" * 72)

    for res in results:
        sc = calculate_forensic_score(res)
        sub = sc["sub_scores"]

        def _s(key: str) -> str:
            v = sub.get(key, {}).get("score")
            return str(v) if v is not None else "N/A"

        tier_icon = {1: "🔴", 2: "🟡", 3: "🟢", 4: "⚪"}.get(sc["tier"], "❓")

        print(
            f"{res.ticker:<8} {str(sc['forensic_score'] or 'N/A'):>6} "
            f"{tier_icon}{sc['tier']:>3} {sc['tier_label']:<14} "
            f"{_s('accruals_score'):>9} {_s('revenue_quality_score'):>8} "
            f"{_s('capex_score'):>6} {_s('tenk_diff_score'):>5} "
            f"{_s('call_nlp_score'):>5} {_s('catalyst_score'):>4}"
        )
    print("=" * 72 + "\n")


async def main() -> int:
    args = parse_args()
    skip_orch = getattr(args, "no_orchestrator", False)

    mode_parts: list[str] = []
    if not skip_orch:
        mode_parts.append("Opus 총괄")
    if args.excel:
        mode_parts.append("Excel 저장")
    if args.tier_max:
        mode_parts.append(f"Tier≤{args.tier_max} 필터")
    mode_label = " + ".join(mode_parts) if mode_parts else "빠른 실행"

    print(
        f"\n🔬 포렌식 분석 시작: {', '.join(args.tickers)} [{mode_label}]",
        file=sys.stderr,
    )

    results = await analyze_stocks(
        tickers=args.tickers,
        skip_orchestrator=skip_orch,
        save_excel=args.excel,
        excel_path=args.excel_path,
    )

    total_elapsed = sum(r.elapsed_sec for r in results)
    print(
        f"\n✅ 전체 완료 — {len(results)}개 종목 / 누적 {total_elapsed:.1f}s",
        file=sys.stderr,
    )

    # Tier 필터 적용
    filtered = results
    if args.tier_max and not skip_orch:
        filtered = [
            r for r in results
            if calculate_forensic_score(r)["tier"] <= args.tier_max
        ]
        skipped = len(results) - len(filtered)
        if skipped:
            print(
                f"  (Tier > {args.tier_max} {skipped}개 종목 필터링됨)",
                file=sys.stderr,
            )

    # 점수 요약 테이블 (Orchestrator 포함 시)
    if not skip_orch:
        _print_score_table(results)

    # 출력
    if args.json:
        payload = json.dumps(
            [r.to_dict() for r in filtered],
            ensure_ascii=False,
            indent=2,
        )
        if args.out:
            args.out.write_text(payload, encoding="utf-8")
            print(f"[+] 저장: {args.out}", file=sys.stderr)
        else:
            print(payload)
    else:
        if args.out:
            if len(filtered) == 1:
                args.out.write_text(filtered[0].to_markdown(), encoding="utf-8")
            else:
                combined_md = "\n\n---\n\n".join(r.to_markdown() for r in filtered)
                args.out.write_text(combined_md, encoding="utf-8")
            print(f"[+] 저장: {args.out}", file=sys.stderr)
        else:
            for r in filtered:
                print(r.to_markdown())

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
