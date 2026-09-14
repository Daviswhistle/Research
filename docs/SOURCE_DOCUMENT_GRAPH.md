# SEC Source Document Graph Resolver

## 목적

최근 10-Q/10-K가 원 계약서를 직접 첨부하지 않고 과거 8-K의 Exhibit 10.1 등을 **incorporation by reference**로 가리키는 경우가 많다.

`source_graph` 레이어는 이 참조를 historical cutoff 안에서 따라가서 원계약/과거 amendment exhibit를 debt-instrument source packet에 추가한다.

```text
current 10-Q / exhibit
    ↓ reference text
"incorporated by reference to Exhibit 10.1
 to Form 8-K filed on May 15, 2020"
    ↓
point-in-time SEC filing universe
    ↓
2020-05-15 Form 8-K
    ↓ filing index
EX-10.1 Credit Agreement
    ↓
source span extraction
    ↓
DebtInstrumentSnapshot verification
```

이 resolver는 법률적 계약관계를 확정하는 도구가 아니다. SEC에서 실제로 어떤 문서가 어떤 과거 filing/exhibit를 가리켰는지를 재구성하는 source-navigation layer다.

## Reference parsing

현재 자동으로 인식하는 locator는 다음과 같다.

- SEC accession number
- SEC Archives URL의 accession
- `Form 8-K / 10-K / 10-Q / S-1 / S-3 / S-4`
- filing date
- `Exhibit 10.1`, `Exhibit 4.2` 등 exhibit 번호
- `incorporated by reference`, `previously filed`, `filed as exhibit`, `reference is made to` 문맥

정확한 accession이 있으면 이를 우선한다.

accession이 없으면 `filing date + form` 조합이 historical filing universe에서 하나의 filing으로만 해소될 때만 자동 연결한다.

후보가 여러 개면 추측하지 않고 `unresolved`로 남긴다.

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

과거 filing과 특정 exhibit까지 SEC source에서 재확인했다.

### `filing_resolved`

filing은 식별했지만 특정 exhibit를 하나로 좁히지 못했다.

### `unresolved`

form/date/accession 정보가 부족하거나 후보가 여러 개다.

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

이 guard가 historical replay에서 accidental hindsight를 막는다.

## 재귀

해결한 과거 exhibit 자체가 다시 더 오래된 원계약을 incorporation by reference할 수 있으므로 resolver는 재귀적으로 동작한다.

기본값:

```text
max_depth = 3
max_nodes = 80
```

무한 loop나 지나친 SEC request를 막기 위한 상한이다.

`source_document_graph.json`에서 unresolved edge를 확인한 뒤 필요하면 옵션을 늘릴 수 있다.

## 실행

### SEC instrument extractor

```bash
distressed-equity-sec-instruments \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  --reference-depth 3 \
  --reference-max-nodes 80 \
  -o output/debt_instrument_sources.json \
  --graph-output output/source_document_graph.json \
  --template-output output/debt_instrument_template.json
```

참조 해석을 끄려면:

```bash
--no-resolve-references
```

을 사용한다.

### Research workspace

`distressed-equity-research`는 기본적으로 resolver를 실행하고 frozen `research_packet.json` 안에 graph를 저장한다.

별도 artifact:

```text
source_document_graph.json
```

도 생성된다.

따라서 나중에 agent result가 들어와도 당시 어떤 source graph를 사용했는지가 바뀌지 않는다.

## 확장된 instrument packet

resolved exhibit는 기존 `debt_instrument_sources.json`에 추가되고 동일한 field-level provenance extractor를 거친다.

즉 별도 신뢰 경로를 만들지 않는다.

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

원래 recent-window exhibit와 incorporation으로 찾은 old exhibit 모두 같은 provenance 검사를 통과해야 한다.

## 일부러 자동화하지 않는 것

- 같은 날짜/같은 form의 여러 filing 중 임의 선택
- exhibit 번호가 없는 reference에서 가장 비슷한 EX-10을 임의 선택
- 다른 issuer/subsidiary CIK로의 cross-issuer reference 자동 추적
- 계약 제목/계약일만으로 SEC filing을 전시장 검색해 강제 매칭
- 법률적 successor agreement / novation / extinguishment 판정

이 경우 graph edge를 unresolved 상태로 남기는 것이 잘못된 계약을 붙이는 것보다 낫다.

## 다음 확장 후보

1. 계약 제목 + execution date를 이용한 conservative historical index search
2. cross-CIK guarantor/subsidiary graph
3. distant source-span clustering
4. PDF/image/table-heavy exhibit parser

현재 단계의 목표는 **recent filing window 밖의 원계약을 놓치는 survivorship-style source bias를 줄이면서, 시간 정보와 provenance를 보존하는 것**이다.
