# 4-에이전트 병렬 주식 분석기 (KR + US)

Claude Agent SDK로 만든 멀티 에이전트 시스템.
**4개 전문 에이전트가 동시에 실행**되어 단일 종목에 대해 약 4배 빠른 종합 분석을 만들어냅니다.

## 에이전트 구성

| Agent | 역할 | 한국 데이터 | 미국 데이터 |
|---|---|---|---|
| **A — Financial** | ROE, PER, PBR, 부채비율, 현금흐름 | DART `fnlttSinglIndx`, 네이버 시세 | Yahoo `info` |
| **B — Dividend**  | 5년 배당 히스토리, 배당성향 추이 | DART 배당지표 (M240000) | Yahoo `dividends` |
| **C — News**      | 최근 30일 공시·뉴스 큐레이션 | DART `list.json`, 네이버 뉴스 | SEC EDGAR, Yahoo News |
| **D — Risk**      | 대주주/소송/감사의견/임원변동 | DART + 키워드 필터 | SEC 8-K(Item 4.02/5.02), 13D/G |

병렬 처리는 `orchestrator.analyze_stock` 안에서 `asyncio.gather`로 처리됩니다.

## 파일 구조

```
data_sources.py    # DART / SEC / 네이버 / Yahoo 헬퍼 (HTTP/스크래핑)
agents.py          # 4개 에이전트 시스템 프롬프트 + MCP 도구 등록
orchestrator.py    # asyncio.gather 4개 동시 실행
main.py            # CLI 진입점
example.py         # 라이브러리 사용 예제
requirements.txt
.env.example
```

## 설치

```bash
pip install -r requirements.txt
cp .env.example .env   # 키 채우기
```

필요한 키:
- `ANTHROPIC_API_KEY` — Claude API
- `DART_API_KEY` — https://opendart.fss.or.kr (무료, 5분 내 발급)
- `SEC_USER_AGENT` — `your_name your_email@example.com` 형식 (SEC 요구사항)

## 사용

### CLI

```bash
# 삼성전자
python main.py 005930

# 애플
python main.py AAPL

# JSON 파일로 저장
python main.py 005930 --json --out samsung.json

# 마크다운 리포트 저장
python main.py AAPL --out apple.md
```

출력 끝에는 각 에이전트가 만든 `## SUMMARY_JSON` 블록이 따라옵니다.
스크립트에서는 `result.reports["A_financial"].summary` 처럼 dict로 접근하세요.

### 라이브러리

```python
import asyncio
from orchestrator import analyze_stock

async def main():
    result = await analyze_stock("005930")   # KR/US 자동 판별
    print(result.to_markdown())
    print(result.reports["B_dividend"].summary)

asyncio.run(main())
```

## 병렬성 동작 원리

```python
# orchestrator.py 핵심
tasks = [run_single_agent(k, ticker, market) for k in agent_keys]
reports = await asyncio.gather(*tasks)   # 4개 동시
```

각 `run_single_agent`은 독립적인 `ClaudeSDKClient` 인스턴스를 띄우고,
`mcp_servers={"stock-data": STOCK_DATA_SERVER}` 로 데이터 소스를 도구로 노출합니다.
LLM 추론과 HTTP 호출이 각 에이전트마다 별도 task에서 진행되므로
**가장 느린 에이전트 시간 ≈ 전체 시간**이 됩니다.

## 확장 아이디어

- 종목 리스트 입력 → `asyncio.gather(*[analyze_stock(t) for t in tickers])`로 다중 종목 동시 분석
- 결과를 SQLite/Postgres에 저장하고 일간 cron으로 변동 추적
- Agent E (밸류에이션) 추가: DCF/멀티플 비교
- Agent F (기술적 분석) 추가: 차트 패턴, RSI/MACD

## 주의사항

- DART/네이버 API는 호출량 제한이 있습니다. 단일 종목 1회 분석에 약 8~12회 호출.
- 네이버 금융 스크래핑은 HTML 변경에 취약합니다 — 셀렉터가 깨지면 `data_sources.naver_quote`를 손볼 것.
- SEC EDGAR는 User-Agent에 연락처가 포함되어야 합니다. 그렇지 않으면 차단됩니다.
- 본 도구는 정보 제공용이며 투자 자문이 아닙니다.
