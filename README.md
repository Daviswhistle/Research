# Research

개인 연구 프로젝트와 실험적 분석 도구를 모아두는 저장소입니다.

## DART 변신기업 탐색기

가격이 오른 종목을 뒤쫓는 대신, **공시 전후로 상장사의 경제적 성격이 달라지는 순간**을 조기에 찾는 연구 도구입니다.

대표적으로 다음 사건을 추적합니다.

- 경영권 인수, 영업양수, 합병·분할·주식교환
- 사업목적 추가, 신규사업 진출, 대규모 시설투자
- CB·BW·유상증자·차입 등 변신 거래의 자금조달
- 인수 이후 공급계약·공동개발·수주 등 실행 증거
- 최대주주·대표이사 변경과 사업 매각

기회와 위험을 한 점수로 상쇄하지 않습니다.

- `attention_score`: 회사가 실제로 얼마나 크게 변하는가
- `financing_risk_score`: 희석·차입·거버넌스 위험이 얼마나 큰가

### 설치

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e ".[test]"
```

### 실행

```bash
export DART_API_KEY="OpenDART_인증키"

python -m transformation_scanner \
  --start 20251201 \
  --end 20251231 \
  --include-documents
```

또는 설치된 명령을 사용합니다.

```bash
transformation-scanner --start 20251201 --end 20251231
```

기본 산출물은 다음 위치에 저장됩니다.

```text
output/transformation_scanner/
├── state.sqlite3
├── <run_id>_candidates.jsonl
└── <run_id>_report.md
```

과거 기간을 상태 저장 없이 다시 평가하려면 다음 옵션을 사용합니다.

```bash
python -m transformation_scanner \
  --start 20251201 \
  --end 20251231 \
  --include-documents \
  --no-state \
  --no-persist
```

자세한 설계, 점수 해석과 한계는 [`docs/TRANSFORMATION_SCANNER.md`](docs/TRANSFORMATION_SCANNER.md)를 참고하세요.

## Distressed Equity Convexity Engine

2022년 Carvana 같은 표면적 패턴을 복제하는 대신, **시장 암시 실패확률이 현실의 실패확률보다 높고 생존·정상화 시 기존 보통주 payoff가 큰 기업**을 검증하기 위한 연구 엔진입니다.

핵심 출력은 다음 다섯 가지입니다.

1. `Time to Death`
2. `Base-case Equity Multiple`
3. `Required Probability`
4. `Critical Assumption Count`
5. `Common Equity Capture Ratio`

회계·산술은 결정론적 코드가 수행하고, 에이전트는 survival audit, temporary vs permanent impairment, 정상화 economics 반증, probability range calibration, 모델 이중계산 검증만 맡습니다. 에이전트가 임의의 성공확률을 만들어내지 않는 것이 핵심 원칙입니다.

단일 기업 정밀 분석:

```bash
python -m distressed_equity examples/distressed_equity_case.json
# 또는

distressed-equity examples/distressed_equity_case.json -o output/example.md
```

시장/후보군 1차 스크리닝:

```bash
distressed-equity-screen examples/distressed_equity_universe.jsonl \
  -o output/universe.md \
  --json-output output/universe.json \
  --tasks-output output/research_tasks.json
```

Universe scanner는 후보를 하나의 종합점수로 줄이지 않습니다. `distress / real business / survival / base payoff / assumption burden` gate를 별도로 보여주고 `DEEP_DIVE_CANDIDATE / RESEARCH / DROP`으로 연구 우선순위를 정합니다. Recovery 전에 refinancing이나 covenant 문제가 있으면 자동 생존으로 가정하지 않고 `RESEARCH`로 남깁니다.

### SEC point-in-time evidence

미국 상장사는 SEC의 `submissions`와 XBRL `companyfacts` API를 이용해 **분석일 당시 실제로 공개되어 있던 자료만** 스냅샷으로 만들 수 있습니다.

```bash
export SEC_USER_AGENT="Research your-email@example.com"

distressed-equity-sec \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  -o output/cvna_2022-12-31_sec.json
```

스냅샷에는 분석일 이전 10-K/10-Q/8-K 목록, SEC archive 링크, 그리고 표준 XBRL에서 찾은 revenue, operating income, cash, CFO, capex, debt, shares가 source/date/period와 함께 저장됩니다. 분석일 뒤 제출된 행은 제외합니다.

주의할 점:

- `companyfacts`의 표준 taxonomy만으로 회사별 extension fact가 모두 잡히지는 않습니다.
- revenue/CFO/capex 같은 duration fact는 보고된 원기간 그대로이며 자동으로 분기 정상화하지 않습니다.
- SEC ticker/CIK 매핑은 검색 편의를 위한 자료이므로 CIK와 회사명을 결과에 함께 남깁니다.
- 자동접근은 SEC fair-access 정책을 지켜야 하므로 클라이언트는 식별 가능한 User-Agent를 요구하고 10 req/s보다 느리게 제한합니다.

### Survivorship-aware historical market replay

현재 살아 있는 종목만 과거로 되감는 오류를 피하기 위해 point-in-time market/universe 계층을 별도로 둡니다.

소규모 역사검증은 Alpha Vantage의 historical `LISTING_STATUS`, raw daily price, weekly adjusted price를 사용합니다.

```bash
export ALPHA_VANTAGE_API_KEY="..."

distressed-equity-replay \
  --provider alpha-vantage \
  --analysis-date 2022-12-31 \
  --symbols CVNA,UPST,OPEN \
  --min-drawdown 0.60 \
  --markdown-output output/replay.md \
  -o output/replay.json
```

전체시장/장기 replay는 permanent security ID를 가진 bulk export를 권장합니다.

```bash
distressed-equity-replay \
  --provider csv \
  --analysis-date 2022-12-31 \
  --securities-csv data/security_master.csv \
  --prices-csv data/prices.csv \
  -o output/replay.json
```

raw price는 cutoff market-cap 재구성에, adjusted price는 split 때문에 생기는 가짜 drawdown을 막는 후보 생성용으로 분리합니다. 이 price drawdown은 최종 peak-market-cap/EV distress gate를 대체하지 않습니다.

### Point-in-time base rates

과거 distress 사례의 결과를 확률 anchor로 쓸 때도 look-ahead를 막습니다.

```bash
distressed-equity-base-rates cases.jsonl \
  --cutoff 2022-12-31 \
  --impairment-type cyclical \
  --leverage-bucket high
```

각 사례에는 `analysis_date`뿐 아니라 `outcome_known_date`를 저장합니다. cutoff 이후에야 결과가 알려진 사례는 당시 base rate에 들어갈 수 없고, 현재 평가 중인 동일 사례도 제외할 수 있습니다.

자세한 설계:

- [`docs/DISTRESSED_EQUITY.md`](docs/DISTRESSED_EQUITY.md)
- [`docs/SEC_EVIDENCE.md`](docs/SEC_EVIDENCE.md)
- [`docs/MARKET_REPLAY.md`](docs/MARKET_REPLAY.md)

### 테스트

```bash
python -m compileall -q transformation_scanner distressed_equity
python -m pytest
```

> 두 도구의 결과는 매수·매도 신호가 아닙니다. 사람이 원문, 자본구조, 회계처리와 반증 근거를 먼저 검토할 후보를 정렬하는 연구 도구입니다.
