# Debt Instrument Evidence Clustering

## 목적

하나의 SEC debt exhibit에서 동일 instrument의 핵심 조건이 서로 멀리 떨어져 등장할 수 있다.

예:

```text
page/section A: 5.25% Senior Secured Notes due 2028
...
page/section F: aggregate principal amount $500 million
...
page/section K: maturity date June 15, 2028
...
page/section Q: CUSIP 123456789
```

기존 extractor는 block별로 `(name, instrument_type)`가 같으면 뒤의 block을 중복으로 보고 버릴 수 있었다. 그 결과 title/coupon은 남지만 principal, maturity, CUSIP 같은 distant evidence가 사라질 수 있었다.

현재 구현은 먼저 debt-relevant source span을 보존한 뒤 **같은 SEC exhibit 안에서만** instrument evidence cluster를 만든다.

핵심 원칙:

```text
proximity alone != same instrument
```

자동 결합은 문서상 거리가 아니라 명시 identifier와 안정적 instrument identity evidence를 우선한다.

## Scope boundary

자동 clustering의 범위는 한 `FilingDocument` 내부다.

허용:

```text
same accession
+ same exhibit URL
+ distant source spans
=> evidence clustering 가능
```

현재 자동으로 하지 않음:

```text
EX-4.1 span
+ EX-10.2 span
=> cross-document auto merge
```

서로 다른 exhibit 간 동일 instrument 여부는 이후 verification / stable debt ledger 단계에서 source-backed snapshot으로 판단한다. 이 경계는 recall보다 false merge 방지를 우선한다.

## Source span discovery

Extractor는 먼저 debt-relevant block을 source span으로 보존한다.

Trigger에는 다음이 포함된다.

- note / facility / indenture / credit agreement language
- aggregate principal / commitment / maturity / pricing language
- SOFR / lien / security language
- **CUSIP / ISIN standalone labels**

CUSIP 또는 ISIN만 떨어져 있는 문단도 이제 cluster evidence에서 사라지지 않는다.

## Hard split rules

다음 명시 evidence가 충돌하면 같은 cluster로 합치지 않는다.

### Explicit identifiers

```text
CUSIP A != CUSIP B
ISIN A  != ISIN B
```

명시 identifier 충돌은 hard split이다.

### Maturity

두 span 모두 high-confidence maturity를 제공하면서 값이 다르면 split한다.

```text
maturity_date A != maturity_date B
maturity_year A != maturity_year B
```

### Note series

명시 note title 또는 note coupon이 서로 충돌하면 split한다.

예:

```text
5.25% Senior Secured Notes due 2028
6.00% Senior Secured Notes due 2030
```

은 같은 generic `Indenture` exhibit 안에 있어도 별도 candidate다.

### Instrument family

특정 instrument family가 충돌하면 split한다.

```text
notes != revolving credit facility
notes != term loan
revolver != term loan
```

`credit_facility`는 generic family로 취급한다. Generic credit evidence는 **하나의** specific credit family를 보조할 수 있지만, 이미 revolver가 확인된 cluster와 term loan 사이를 연결하는 bridge가 될 수 없다.

```text
credit + revolver       -> compatible
credit + term loan      -> compatible
credit + revolver + term loan -> invalid / split
credit + notes          -> split
```

## Positive link evidence

Hard conflict가 없는 경우 다음 evidence를 높은 순서로 사용한다.

| Basis | Relative strength |
| --- | ---: |
| same explicit CUSIP / ISIN | 100 |
| same explicit note title | 95 |
| specific document note identity | 90 |
| unique explicit note identity in document | 85 |
| same explicit facility label | 80 |
| specific document facility identity | 75 |
| same note coupon + maturity | 75 |
| unique explicit facility identity in document | 70 |

숫자는 investment score가 아니라 deterministic clustering precedence다.

## Specific document identity

SEC filing index의 exhibit description 자체가 특정 series를 명확히 지정할 수 있다.

예:

```text
Indenture relating to 5.25% Senior Secured Notes due 2028
```

이 경우 같은 exhibit 안의 distant principal / maturity / CUSIP span이 다른 instrument와 충돌하지 않는다면 해당 note identity에 연결할 수 있다.

반대로 description이 단순히:

```text
Indenture
Credit Agreement
```

처럼 generic하면 그것만으로 여러 note series 또는 revolver/term loan을 하나로 합치지 않는다.

## Unique identity inside one exhibit

Exhibit description이 generic이어도 문서 전체에서 명시 note title 또는 facility label이 정확히 하나뿐이면 그 identity를 보조 anchor로 사용할 수 있다.

하지만 둘 이상의 competing identity가 존재하면 unique-anchor rule은 사용하지 않는다.

## Ambiguous bridge guard

한 generic span이 동일 강도로 두 conflicting instrument cluster에 붙을 수 있는 경우 임의 winner를 선택하지 않는다.

```text
              generic span
              /          \
      note series A    note series B
```

A와 B가 explicit identifier / maturity / coupon 등으로 서로 충돌하면 generic span의 동률 link를 차단한다.

이 원칙은 ambiguous contract matching에서 winner를 억지로 고르지 않는 전체 파이프라인 철학과 동일하다.

## Cluster-level revalidation

Pairwise link가 각각 허용되어도 union 후 cluster 전체가 inconsistent해질 수 있다.

따라서 실제 union 직전에 현재 cluster A와 cluster B의 모든 proposals를 다시 비교한다.

특히 generic `credit` evidence가 순차 merge 과정에서 revolver와 term loan 사이의 conflict를 가리지 못하도록 cluster-level family consistency를 재검증한다.

## Output provenance

`DebtInstrumentSourceCandidate`는 다음 cluster provenance를 보존한다.

```text
cluster_status
cluster_basis
source_span_ids
proposals
warnings
snapshot_template
```

### `cluster_status`

```text
single_span
linked_spans
```

### `cluster_basis`

실제 결합에 사용된 deterministic basis 목록이다.

예:

```json
[
  "same_explicit_note_title",
  "specific_document_note_identity"
]
```

## Snapshot template semantics

Cluster의 모든 span은 `snapshot_template.source_refs`에 들어간다.

```text
source_refs = [span A, span F, span K, span Q]
```

High-confidence field는 cluster 안에서 값이 **유일할 때만** 자동 prefill한다.

```text
one principal value -> prefill
one CUSIP value     -> prefill
```

반대로 cluster 안에 같은 field의 high-confidence 값이 둘 이상 남아 있으면 값을 선택하지 않는다.

```text
principal = null
warning = conflicting high-confidence principal proposals
```

Clustering 자체는 authoritative debt row를 만들지 않는다.

## Verification boundary

Multi-span candidate에는 명시 warning이 붙는다.

```text
clustered N source spans within one SEC exhibit;
verify the shared instrument identity before ledger ingestion
```

`debt_instrument_verifier` task도 다음을 요구한다.

- cluster가 실제로 하나의 legal instrument인지 확인
- 각 retained field를 해당 `source_refs`와 대조
- ambiguous identity면 분리하거나 unresolved 처리
- verified snapshot만 stable debt ledger로 전달

따라서 흐름은:

```text
raw exhibit
  -> debt-relevant spans
  -> conservative document-local cluster
  -> candidate template
  -> source verification
  -> stable debt instrument ledger
```

이다.

## Tests

Regression coverage는 다음 failure mode를 포함한다.

1. 멀리 떨어진 note title / principal / maturity / CUSIP가 specific document identity 아래 결합됨.
2. 같은 generic indenture 안의 두 note series가 섞이지 않음.
3. specific document title이 같아도 conflicting CUSIP은 hard split.
4. generic credit agreement에서 revolver와 term loan이 합쳐지지 않음.
5. standalone CUSIP paragraph가 discovery 단계에서 보존됨.
6. generic credit family가 revolver와 term loan 사이 bridge가 되지 못함.
7. generic credit evidence가 notes로 bridge되지 않음.

## Known limitations / next boundary

- Cross-document evidence clustering은 의도적으로 자동화하지 않는다.
- HTML-to-text가 표/열 구조를 잃으면 table-heavy exhibit의 instrument association은 여전히 약할 수 있다.
- PDF/image-only exhibit는 별도 extraction path가 필요하다.
- Amendment / exchange / extinguishment가 같은 legal instrument lineage인지 여부는 source-span clustering과 별개 문제다.
- Source fragment scan은 bounded이며 매우 긴 exhibit에서 뒤쪽 evidence가 탐색 한도 밖일 수 있다.

다음 높은 가치의 correctness 과제는 **PDF/image/table-heavy debt exhibit extraction을 구조적으로 보강하는 것**이다.
