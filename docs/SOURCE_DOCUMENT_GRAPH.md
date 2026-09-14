# SEC Source Document Graph Resolver

## 목적

최근 10-Q/10-K가 원 계약서를 직접 첨부하지 않고 과거 8-K의 Exhibit 10.1 등을 **incorporation by reference**로 가리키거나, SEC locator 없이 `Credit Agreement dated May 3, 2019`처럼 계약명과 체결일만 언급하는 경우가 있다.

`source_graph` + `contract_identity` 레이어는 이 참조를 historical cutoff 안에서 따라가서 원계약/과거 exhibit를 debt-instrument source packet에 추가한다.

```text
current 10-Q / exhibit
    ↓
explicit SEC locator가 있으면 우선 사용
    ↓
없으면 contract kind + exact execution date fallback
    ↓
point-in-time SEC filing universe
    ↓
historical filing index / EX-4 / EX-10
    ↓
unique source exhibit only
    ↓
source span extraction
    ↓
DebtInstrumentSnapshot verification
```

이 resolver는 법률적 계약관계를 확정하는 도구가 아니다. SEC에서 실제로 어떤 문서가 어떤 과거 filing/exhibit와 연결되는지를 보수적으로 재구성하는 source-navigation layer다.

## Resolution order

강한 locator를 항상 먼저 사용한다.

1. exact SEC accession / SEC Archives accession
2. unique filing date + form
3. exact exhibit number / document filename
4. **fallback:** canonical contract kind + exact execution date

Fallback은 앞선 locator로 target을 해소할 수 없거나, source 문서에 locator 없이 계약 identity만 존재할 때 사용한다.

## Explicit reference parsing

현재 자동으로 인식하는 locator는 다음과 같다.

- SEC accession number
- SEC Archives URL의 accession
- `Form 8-K / 10-K / 10-Q / S-1 / S-3 / S-4`
- filing date
- `Exhibit 10.1`, `Exhibit 4.2` 등 exhibit 번호
- `incorporated by reference`, `previously filed`, `filed as exhibit`, `reference is made to` 문맥

정확한 accession이 있으면 이를 우선한다.

accession이 없으면 `filing date + form` 조합이 historical filing universe에서 하나의 filing으로만 해소될 때만 자동 연결한다.

후보가 여러 개면 추측하지 않는다.

## Contract identity fallback

SEC locator가 없더라도 다음처럼 계약 identity가 명시될 수 있다.

```text
Credit Agreement dated May 3, 2019
Amended and Restated Credit Agreement dated as of May 3, 2019
Indenture dated June 12, 2020
```

현재 canonical contract kind 예시는 다음과 같다.

- credit agreement
- revolving credit agreement
- term loan agreement
- loan agreement
- indenture
- note purchase agreement
- security agreement
- guaranty / guarantee agreement

Resolver는 계약 체결일을 **filing date가 아니라 contract identity evidence**로 취급한다.

Historical search 범위 기본값:

```text
max_contract_search_filings = 40
execution date - 30 days
execution date + 550 days
```

단, target filing은 항상:

```text
target filing date <= source document filing date
```

여야 한다.

각 후보 filing의 EX-4 / EX-10 원문에서 **같은 canonical contract kind + 정확히 같은 execution date**가 다시 확인되어야 후보가 된다.

### 유일 후보만 자동 연결

한 exhibit만 일치하면:

```text
resolved_by_contract_identity
```

로 연결한다.

둘 이상이면:

```text
ambiguous_contract_identity
```

로 남기고 자동 선택하지 않는다.

하나도 없으면:

```text
unresolved_contract_identity
```

로 남긴다.

## Amendment false-positive 방지

단순히 원계약명+날짜가 들어 있다고 원계약으로 보지 않는다.

예:

```text
Amendment No. 1 to Credit Agreement dated May 3, 2019
```

이 문서는 원계약 날짜를 인용하지만 원계약 자체가 아니다.

따라서 원 `Credit Agreement`를 찾을 때 SEC document description/filename에 다음 성격이 명시된 exhibit는 기본 제외한다.

- amendment
- waiver
- supplement
- joinder
- consent

또한 `Amended and Restated Credit Agreement`와 plain `Credit Agreement`는 같은 canonical kind라도 restated identity를 구분한다.

즉 plain agreement가 restated agreement로, 또는 그 반대로 조용히 승격되지 않는다.

## Self-link 방지

원계약 exhibit 자체에는 당연히 자기 계약명과 execution date가 적혀 있다.

Reverse search 결과가 source document 자기 자신뿐이면 reference edge를 만들지 않는다.

이 guard가 다음과 같은 의미 없는 graph noise를 막는다.

```text
Credit Agreement exhibit
    ↓ self identity
same Credit Agreement exhibit
```

## Graph node

두 종류의 node를 사용한다.

### filing primary

```text
filing:<accession>:primary
```

해당 filing의 primary document다.

### exhibit

```text
exhibit:<accession>:<sequence>:<filename>
```

filing index에서 재확인된 exhibit document다.

## Edge status

### `resolved`

explicit SEC locator로 과거 filing과 특정 exhibit까지 재확인했다.

### `resolved_by_contract_identity`

SEC locator는 없었지만 contract kind + exact execution date가 historical exhibit에서 유일하게 확인됐다.

### `filing_resolved`

filing은 식별했지만 특정 exhibit를 하나로 좁히지 못했다.

### `unresolved`

explicit locator 정보가 부족하거나 후보가 여러 개다.

### `unresolved_contract_identity`

계약명+날짜는 인식했지만 historical exhibit를 유일하게 찾지 못했다.

### `ambiguous_contract_identity`

동일 contract kind + execution date가 둘 이상의 SEC exhibit에서 확인됐다.

이 상태에서는 자동 선택하지 않는다.

### `blocked_cutoff`

참조된 filing 날짜가 historical analysis cutoff 이후다.

### `blocked_temporal`

analysis cutoff 안에는 있지만 **참조하는 source document보다 나중에 제출된 filing**이다.

예:

```text
source filing: 2021-01-01
matched target: 2022-01-01
analysis cutoff: 2023-12-31
```

cutoff만 보면 허용되지만 2021년 문서가 2022년 filing을 참조할 수 없으므로 연결을 거부한다.

### `blocked_depth`

계약 identity는 해소됐지만 configured graph depth를 넘는다.

## 재귀

해결한 과거 exhibit 자체가 다시 더 오래된 원계약을 가리킬 수 있으므로 resolver는 재귀적으로 동작한다.

기본값:

```text
max_depth = 3
max_nodes = 80
```

Locator-less contract identity로 찾은 과거 exhibit도 다시 기존 explicit-reference resolver를 통과한다.

무한 loop나 지나친 SEC request를 막기 위한 상한이다.

## 실행

### SEC instrument extractor

```bash
distressed-equity-sec-instruments \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --reference-depth 3 \
  --reference-max-nodes 80 \
  --contract-search-max-filings 40 \
  --contract-days-before-execution 30 \
  --contract-days-after-execution 550 \
  -o output/debt_instrument_sources.json \
  --graph-output output/source_document_graph.json \
  --template-output output/debt_instrument_template.json
```

전체 reference resolution을 끄려면:

```bash
--no-resolve-references
```

Contract identity fallback만 끄려면:

```bash
--no-contract-identity-fallback
```

을 사용한다.

### Research workspace

`distressed-equity-research`도 같은 resolver를 기본 실행하고 frozen `research_packet.json` 안에 graph를 저장한다.

별도 artifact:

```text
source_document_graph.json
```

도 생성된다.

따라서 나중에 agent result가 들어와도 당시 어떤 source graph를 사용했는지가 바뀌지 않는다.

## 확장된 instrument packet

resolved exhibit는 기존 `debt_instrument_sources.json`에 추가되고 동일한 field-level provenance extractor를 거친다.

별도 신뢰 경로를 만들지 않는다.

```text
resolved old exhibit
    ↓
InstrumentSourceSpan
    ↓
field proposals
    ↓
verification template
    ↓
source_refs validation
    ↓
stable debt ledger
```

recent-window exhibit, explicit reference로 찾은 exhibit, contract identity fallback으로 찾은 exhibit 모두 같은 provenance 검사를 통과해야 한다.

## 일부러 자동화하지 않는 것

- 같은 identity 후보가 둘 이상일 때 arbitrary ranking
- amendment/waiver/supplement를 원계약으로 강제 매칭
- 다른 issuer/subsidiary CIK로의 cross-issuer reference 자동 추적
- 법률적 successor agreement / novation / extinguishment 판정
- 계약 parties가 달라졌는데 같은 title/date라는 이유만으로 동일 계약으로 간주

이 경우 unresolved/ambiguous 상태로 남기는 것이 잘못된 계약을 붙이는 것보다 낫다.

## 다음 확장 후보

1. borrower / guarantor / party names까지 contract identity에 포함
2. cross-CIK guarantor/subsidiary graph
3. distant source-span clustering
4. PDF/image/table-heavy exhibit parser
5. exact amendment / exchange / extinguishment linkage

현재 단계의 목표는 **recent filing window 밖의 원계약을 놓치는 source bias를 줄이면서, 시간 정보와 provenance를 보존하고 false-positive contract linkage를 최소화하는 것**이다.
