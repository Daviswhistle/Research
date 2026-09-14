# Research

개인 연구 프로젝트와 실험적 분석 도구를 모아두는 저장소입니다.

## 공통 Research Candidate Pipeline (초안)

서로 다른 탐색기를 하나의 거대한 점수식으로 합치지 않고 공통 실행 구조 위에 올리기 위한 실험적 골격입니다.

```text
candidate sources
    ↓
hydration
    ↓
hard filters
    ↓
multi-axis scoring
    ↓
selection / diversity reranking
    ↓
research queue
```

현재는 기존 `transformation_scanner`를 adapter로 연결할 수 있으며, Top-K와 dependency-free MMR형 diversity selector를 제공합니다. Carvana형 distressed-equity 탐색 로직은 별도 작업과 충돌하지 않도록 구현하지 않고 후속 이슈에서 추적합니다.

자세한 설계와 의도적으로 제외한 범위는 [`docs/RESEARCH_PIPELINE.md`](docs/RESEARCH_PIPELINE.md)를 참고하세요.

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

### 테스트

```bash
python -m compileall -q transformation_scanner research_pipeline
python -m pytest
```

> 이 탐색기의 점수는 매수 신호가 아닙니다. 사람이 당일 원문과 자본구조를 먼저 읽어야 할 후보를 정렬하는 연구 우선순위입니다.
