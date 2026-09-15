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
  -o output/cvna_2022-12-31_sec.json \
  --tasks-output output/cvna_2022-12-31_tasks.json
```

스냅샷에는 분석일 이전 10-K/10-Q/8-K 목록, SEC archive 링크, 표준 XBRL fact, 안전하게 채울 수 있는 screening draft, agent task와 task별 structured result template이 함께 저장됩니다.

주의할 점:

- `companyfacts`의 표준 taxonomy만으로 회사별 extension fact가 모두 잡히지는 않습니다.
- revenue/CFO/capex 같은 duration fact는 보고된 원기간 그대로이며 자동으로 분기 정상화하지 않습니다.
- SEC ticker/CIK 매핑은 검색 편의를 위한 자료이므로 CIK와 회사명을 결과에 함께 남깁니다.
- 자동접근은 SEC fair-access 정책을 지켜야 하므로 클라이언트는 식별 가능한 User-Agent를 요구하고 10 req/s보다 느리게 제한합니다.

### SEC debt / covenant evidence

표준 XBRL debt 숫자만으로 survival을 판단하지 않습니다. Primary filing의 debt note에서 maturity, revolver, covenant, springing maturity, collateral, interest 관련 문맥을 좁혀 agent audit packet을 만듭니다.

```bash
distressed-equity-sec-debt \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --filing-limit 3 \
  -o output/cvna_capital_stack_packet.json \
  --diff-output output/cvna_capital_stack_diff.json \
  --task-output output/cvna_capital_stack_task.json
```

정규식으로 찾은 금액·연도·금리는 **후보**일 뿐 자동 debt schedule로 승격하지 않습니다. Filing-to-filing diff도 confirmed amendment가 아니라 금액/만기/비율 신호가 바뀐 위치를 알려주는 탐색 도구입니다.

### Stable debt instrument ledger

Filing마다 이름이 조금씩 달라지는 동일 채무를 추적하기 위해 instrument-level stable ID 레이어를 둡니다.

```bash
distressed-equity-debt-ledger debt_instruments.json \
  -o debt_instrument_ledger.json
```

Matching 우선순위는 CUSIP/ISIN 같은 명시 identifier가 가장 높고, 없으면 instrument type, 이름 token, maturity, coupon, seniority/security를 이용합니다. Best/second-best 후보가 비슷하면 **억지로 같은 채무로 합치지 않고 새 stable ID**를 만듭니다.

Ledger는 principal, maturity, pricing, ranking/security, revolver capacity/usage 변화를 instrument 단위로 보여줍니다. 이는 amendment 후보 탐색이며 법률적 동일성이나 amendment 효력을 자동 확정하지 않습니다.

### Covenant EBITDA / headroom

Maintenance covenant는 계약 정의와 실제 계산을 분리합니다. 에이전트가 계약에서 permitted add-back, required deduction, cash-netting rule, springing trigger를 구조화하면 코드가 covenant EBITDA와 headroom을 계산합니다.

```bash
distressed-equity-covenant covenant_input.json \
  -o covenant_result.json
```

지원 항목:

- max net leverage
- max total leverage
- minimum interest coverage
- minimum fixed-charge coverage
- minimum liquidity
- springing revolver trigger
- minimum EBITDA to comply / EBITDA cushion

`disputed_add_backs`가 있으면 단일 ratio만 내지 않습니다. 모든 disputed add-back을 포함한 case, 전부 제외한 보수 case, 각 항목을 개별 제외한 case를 계산해 `covenant_ebitda_range`, `headroom_range`, breach sensitivity를 함께 보여줍니다. `disputed_addbacks_flip_outcome`은 compliance 결론이 계약 해석에 의존한다는 뜻이지 breach 확률을 의미하지 않습니다.

출력의 `agent_result`는 `distressed-equity-ingest`에 그대로 넣을 수 있습니다. Add-back 허용 여부 자체를 코드가 추정하지 않는 것이 핵심입니다.

### Structured agent result ingestion

에이전트 결과를 자유 텍스트 그대로 모델에 넣지 않습니다. 모든 patch는 task별 whitelist, evidence ID, point-in-time cutoff, value validation, conflict detection을 통과해야 합니다.

```bash
distressed-equity-ingest output/cvna_2022-12-31_sec.json \
  --result output/capital_stack_result.json \
  --result output/market_result.json \
  --result output/normalization_result.json \
  -o output/cvna_merged.json
```

Low-confidence patch는 기본적으로 evidence만 남기고 값을 바꾸지 않습니다. 이미 채워진 non-null 값과 다른 제안도 기본적으로 overwrite하지 않습니다. 필수 deterministic field가 모두 채워지면 같은 실행에서 screener까지 자동 실행합니다.

### Resumable one-company orchestration

SEC, debt/covenant packet, filing diff, source-document graph, explicit cross-CIK source/legal-entity graph, optional market snapshot, task template 생성을 한 workspace로 묶을 수 있습니다.

```bash
export SEC_USER_AGENT="Research your-email@example.com"
export ALPHA_VANTAGE_API_KEY="..."  # optional

distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --market-provider alpha-vantage \
  --workspace output/cvna_2022-12-31
```

Legal-name graph는 현재 SEC `submissions` metadata와 cutoff 이전 filing의 complete-submission `<SEC-HEADER>`를 교차검증합니다. `FORMER CONFORMED NAME` / `DATE OF NAME CHANGE`가 submissions history의 누락을 보완할 수 있으며, source 간 경계 불일치는 `boundary_conflict`로 노출합니다. Historical header가 현재 metadata와 충돌하면 미래 current-name leakage를 피하도록 point-in-time header를 보수적으로 우선합니다.

Cross-CIK 탐색은 SEC Archives URL, 명시 CIK, exact accession처럼 **source가 target CIK를 직접 증명하는 경우만** explicit traversal로 취급합니다. 회사명만 있는 외부 borrower/issuer/guarantor는 별도 `named_entity_contract_graph.json`에 candidate/confirmation provenance를 보존합니다. 이 fallback은 SEC `company_tickers.json`뿐 아니라 공식 누적 CIK/name 파일 `Archives/edgar/cik-lookup-data.txt`도 **candidate generation에만** 사용하므로 ticker가 없는 finance subsidiary나 historical filer도 후보가 될 수 있습니다. 실제 CIK resolution은 source-date SEC legal-name 확인과 unique historical contract exhibit 확인이 모두 성공할 때만 허용하며, name-origin 결과를 `resolved_cross_cik`로 가장하지 않습니다. 자세한 규칙은 [`docs/NAMED_ENTITY_CONTRACT_RESOLUTION.md`](docs/NAMED_ENTITY_CONTRACT_RESOLUTION.md)를 참고하세요.

Named-entity resolver가 유일하게 확인한 exhibit가 실제 SEC Archives / CIK / accession locator를 노출하면 기존 explicit cross-CIK와 foreign locator-less closure를 재시작합니다. 그 explicit hop으로 새로 도달한 문서에서 다시 name-only external party를 resolution하며, 이 `named -> explicit -> named` cycle을 global depth, unique-source node budget, semantic dedupe, iteration cap으로 제한된 fixed point까지 반복합니다. 단, 중간에 explicit SEC locator가 없는 `name-only -> name-only` 체인은 자동으로 열지 않습니다.

Agent가 filing별 debt instrument snapshot을 구조화했다면 같은 workspace에서 stable ledger를 함께 만들 수 있습니다.

```bash
distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --workspace output/cvna_2022-12-31 \
  --debt-instruments output/debt_instruments.json
```

Agent result가 준비되면 같은 workspace에서 재개합니다.

```bash
distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --workspace output/cvna_2022-12-31 \
  --debt-instruments output/debt_instruments.json \
  --result output/capital_stack_result.json \
  --result output/market_result.json \
  --result output/normalization_result.json
```

기존 `research_packet.json`은 기본적으로 frozen input으로 재사용합니다. 데이터를 의도적으로 다시 수집하려면 `--refresh`를 사용합니다. Debt ledger는 후속 해석 artifact이므로 frozen packet을 수정하지 않고 `debt_instrument_ledger.json`과 `merged.json`에 별도로 저장됩니다.

### Survivorship-aware historical market replay

현재 살아 있는 종목만 과거로 되감는 오류를 피하기 위해 point-in-time market/universe 계층을 별도로 둡니다.