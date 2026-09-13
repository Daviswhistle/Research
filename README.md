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

회계·산술은 결정론적 코드가 수행하고, 에이전트는 survival audit, temporary vs permanent impairment, 정상화 economics 반증, probability range calibration, 모델 이중계산 검증만 맡도록 분리했습니다. 에이전트가 임의의 성공확률을 만들어내지 않는 것이 핵심 원칙입니다.

```bash
python -m distressed_equity examples/distressed_equity_case.json
# 또는

distressed-equity examples/distressed_equity_case.json -o output/example.md
```

자세한 설계는 [`docs/DISTRESSED_EQUITY.md`](docs/DISTRESSED_EQUITY.md)를 참고하세요.

### 테스트

```bash
python -m compileall -q transformation_scanner distressed_equity
python -m pytest
```

> 두 도구의 결과는 매수·매도 신호가 아닙니다. 사람이 원문, 자본구조, 회계처리와 반증 근거를 먼저 검토할 후보를 정렬하는 연구 도구입니다.
