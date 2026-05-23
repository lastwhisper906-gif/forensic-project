# Claude Code 인계 프롬프트

아래 텍스트를 Claude Code 첫 메시지로 붙여넣으세요.

---

## 📋 복사해서 붙여넣을 프롬프트

```
이 프로젝트는 미국 AI 인프라 주식 포렌식 회계 분석 파이프라인이야.
CLAUDE.md에 전체 컨텍스트가 있으니까 먼저 읽어줘.

현재 완료된 상태:
- agents/data_sources.py: SEC EDGAR 8개 함수 완성 (async/httpx, Group A/B/C)
- agents/api.py: FastAPI 서버 (/chat, /analyze, /jobs, /results, static 서빙)
- agents/chat_handler.py: Claude tool_use 채팅 루프
- agents/db.py: SQLAlchemy Core persistence
- agents/static/index.html: 모바일 채팅 UI
- agents/test_ds.py: 통합 테스트 스크립트
- Dockerfile, docker-compose.yml, railway.toml, .gitignore

아직 없는 것:
- agents/orchestrator.py (핵심 — 6개 서브에이전트 병렬 실행)
- 각 서브에이전트 파일들

다음 작업: [여기에 원하는 작업 입력]
```

---

## 🔧 Claude Code 시작 방법

```bash
# 1. 터미널에서 프로젝트 폴더로 이동
cd C:\Users\Lee\Documents\korean-stock-analyzer

# 2. Claude Code 실행
claude

# 3. 위 프롬프트 붙여넣기 (다음 작업 부분만 바꿔서)
```

---

## 💡 "다음 작업" 예시

orchestrator 만들기:
```
다음 작업: orchestrator.py 작성해줘.
6개 서브에이전트를 asyncio.gather로 병렬 실행하고,
각 에이전트 점수(0-100)를 가중 평균해서 최종 forensic_score 계산.
에이전트별 가중치는 CLAUDE.md의 에이전트 목록 참고.
```

특정 에이전트 구현:
```
다음 작업: Agent 1 (수익인식 에이전트) 구현해줘.
data_sources.extract_financial_timeseries로 XBRL 수치 뽑고,
extract_10k_notes로 revenue recognition 주석 분석.
Beneish M-score와 revenue accrual ratio 계산 포함.
```

테스트 실행 후 수정:
```
다음 작업: python test_ds.py --group A 실행해보고 에러 있으면 고쳐줘.
```

GitHub 푸시:
```
다음 작업: .gitignore 확인하고 GitHub에 올릴 준비 해줘.
민감한 파일(.env, *.db) 빠진 거 확인 후 첫 커밋 메시지 작성해줘.
```
