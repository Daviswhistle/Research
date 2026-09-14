# Distressed Equity Research Orchestration

`distressed-equity-research`는 한 회사에 대한 point-in-time 연구 workspace를 생성하고, 이후 구조화 agent result를 받아 deterministic screen까지 재개하는 resumable orchestration 명령이다.

특정 LLM SDK를 저장소에 고정하지 않는다. Agent execution은 davis-agent-kit이나 다른 harness가 맡고, 이 저장소는 **task contract / evidence validation / accounting math**를 소유한다.

## 1차 실행: research workspace 생성

```bash
export SEC_USER_AGENT="Research your-email@example.com"
export ALPHA_VANTAGE_API_KEY="..."  # optional

distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --market-provider alpha-vantage \
  --workspace output/cvna_2022-12-31
```

생성물:

```text
output/cvna_2022-12-31/
├── research_packet.json
├── capital_stack.json
├── capital_stack_diff.json
├── debt_instrument_sources.json
├── debt_instrument_task.json
├── debt_instrument_template.json
├── market_snapshot.json       # market provider 사용 시
├── tasks.json
└── summary.md
```

`research_packet.json`은 agent-result ingestion의 canonical input이다.

## Frozen packet rule

같은 workspace를 다시 실행하면 기본적으로 기존 `research_packet.json`을 재사용한다. SEC나 market provider를 다시 호출하지 않는다.

이 규칙은 historical replay의 재현성을 위한 것이다. Agent result가 며칠 뒤 도착하더라도 처음 연구를 시작했을 때의 point-in-time input이 바뀌면 안 된다.

데이터를 의도적으로 다시 수집할 때만:

```bash
--refresh
```

를 사용한다. 요청한 `analysis_date`와 frozen packet의 cutoff가 다르면 재사용을 거부한다.

## SEC debt instrument source 단계

Workspace 생성 시 recent point-in-time filings의 EDGAR filing index를 읽고 EX-4/EX-10 계열 debt-relevant exhibit를 찾는다.

```text
filing index
  ↓
indenture / credit agreement / amendment candidate
  ↓
exact exhibit text span
  ↓
field-level proposal
  ↓
debt_instrument_template.json
```

Template에는 명시적으로 연결된 coupon, maturity, principal, CUSIP/ISIN, commitment, SOFR spread 같은 high-confidence field만 prefill한다. 이것도 최종 debt schedule은 아니다.

`debt_instrument_task.json`의 검증 단계에서 중복 candidate를 합치거나 제거하고, retained field마다 `source_refs`를 유지한다.

## Agent 단계

`tasks.json`의 각 screening task에는:

- objective
- guardrails
- required output
- structured `result_template`

가 들어간다.

에이전트는 result template을 채워 JSON으로 반환해야 한다.

Debt instrument verification은 screening candidate patch가 아니라 stable debt ledger 입력을 만드는 별도 task라서 `debt_instrument_task.json`으로 분리한다.

Capital-stack agent가 maintenance covenant를 찾았다면 ratio를 자유문장으로 계산하지 말고 계약 정의와 입력값을 source-backed covenant model로 정리한 뒤:

```bash
distressed-equity-covenant covenant_input.json -o covenant_result.json
```

을 사용한다. 출력의 `agent_result`는 ingestion에 바로 사용할 수 있다.

`disputed_add_backs`가 있으면 claimed/conservative/개별 제외 sensitivity가 같이 계산된다. 결과가 `disputed_addbacks_flip_outcome`이면 compliance 결론이 해당 add-back 해석에 의존한다는 뜻이다.

## Debt instrument identity 단계

검증한 filing별 debt instrument snapshot을 stable-ID ledger로 넘긴다.

```bash
distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --workspace output/cvna_2022-12-31 \
  --debt-instruments output/cvna_2022-12-31/debt_instrument_template.json
```

생성물:

```text
debt_instrument_ledger.json
```

Workspace 경로에서는 각 snapshot을 frozen `debt_instrument_sources.json`과 대조한다.

- `source_refs` 필수
- 실제 span ID인지 확인
- `source_accession` 일치 확인
- cited exhibit 공개일보다 snapshot 날짜가 빠르지 않은지 확인
- workspace cutoff 이후 snapshot인지 확인

따라서 출처 없는 숫자를 별도 JSON에 넣어 stable ledger로 우회하는 경로를 막는다.

CUSIP/ISIN이 있으면 이를 우선 사용하고, 없으면 이름/type/maturity/coupon/seniority/security를 이용한 보수적 heuristic matching을 사용한다. Match가 애매하면 자동으로 합치지 않는다.

이 ledger는 후속 해석 artifact이므로 frozen `research_packet.json`에 쓰지 않는다. Agent-result ingestion과 함께 실행하면 `merged.json`에도 ledger가 첨부된다.

## 2차 실행: validated result ingest + screen

```bash
distressed-equity-research \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --workspace output/cvna_2022-12-31 \
  --debt-instruments output/cvna_2022-12-31/debt_instrument_template.json \
  --result output/capital_stack_result.json \
  --result output/market_result.json \
  --result output/normalization_result.json
```

추가 생성물:

```text
merged.json
```

필수 deterministic screening field가 모두 채워졌다면 `merged.json`에 `screening_result`도 포함된다.

## 왜 두 번 실행하는가

외부 agent harness가 병렬로 움직일 수 있고, 각 조사 결과를 사람이 검토하고 다시 넣을 수도 있기 때문이다.

중요한 것은 실행 도구가 아니라 상태 경계다.

```text
raw evidence
    ↓
research packet (frozen)
    ↓
agent result proposal
    ↓
point-in-time + evidence + permission validation
    ↓
conflict-safe patch
    ↓
deterministic screen
```

agent output 자체는 source of truth가 아니다.

## Market layer

`--market-provider alpha-vantage`는 targeted replay용이다.

- cutoff raw price를 screening draft의 current price에 채운다.
- adjusted history의 drawdown은 metadata에 proxy로만 저장한다.
- peak market cap을 현재 share count × 과거 peak adjusted price로 만들어내지 않는다.

따라서 최종 distress gate에 필요한 peak market cap/EV는 historical market agent가 별도로 재구성해야 한다.

## Capital-stack diff

workspace의 `capital_stack_diff.json`은 최근 point-in-time filings 사이에서:

- 금액 signal 변화
- maturity/year signal 변화
- rate/ratio signal 변화
- category 등장/소멸

을 표시한다.

이 정보는 agent가 amendment나 refinancing modification을 찾기 위한 우선순위일 뿐이다. 확정 계약조건은 source filing/credit agreement에서 검증한다.

## 아직 자동화하지 않는 것

- LLM provider/API 호출 자체
- ambiguous credit-agreement legal interpretation
- permitted EBITDA add-back 여부의 자동 추정
- future refinancing success
- point success probability
- peak market cap을 price proxy로 대체하는 계산
- ambiguous debt instrument identity의 강제 matching
- incorporation-by-reference로 멀리 떨어진 과거 exhibit의 무제한 재귀 추적

이 경계를 유지해야 historical replay가 결과를 알고 난 뒤의 hindsight로 오염되지 않는다.
