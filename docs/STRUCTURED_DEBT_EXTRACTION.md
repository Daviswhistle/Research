# Structured Debt Exhibit Extraction

## 목적

Debt exhibit가 실제로 제공하는 구조를 최대한 보존해 deterministic extraction을 한다. 반대로 구조가 불명확한 scanned/image source를 억지로 OCR table로 바꾸지는 않는다.

```text
HTML table      -> DOM + rowspan/colspan + orientation
native-text PDF -> pypdf text-matrix x/y + orientation
scanned/image   -> multimodal source reader
```

목표는 표를 전부 JSON화하는 것이 아니라 **투자 판단에 필요한 debt facts를 다른 instrument와 섞지 않고 source-backed candidate로 만드는 것**이다.

## HTML table: 세 가지 deterministic shape

### 1. Ordinary row-oriented table

```text
Instrument | Principal | Maturity | Coupon | CUSIP
Note A     | 500       | 2028     | 5.25%  | ...
```

```text
cluster_status = table_row
cluster_basis  = [same_html_table_row, header_mapped_columns]
```

### 2. Multi-row / spanning instrument

한 instrument가 `rowspan` 또는 blank-name continuation row로 여러 행에 걸칠 수 있다.

```text
Term Loan B | 800 | SOFR + 3.25% |          | CUSIP
            |     |              | 2028-12-15 |
```

Parser는 DOM cell origin을 추적해서 다음 경우만 하나로 묶는다.

- 동일 name cell이 `rowspan`으로 이어진 경우
- 첫 행에 explicit instrument identity가 있고 바로 다음 행의 name column이 비어 있으며 material term만 이어지는 경우

```text
cluster_status = table_row_group
cluster_basis  = [
  contiguous_html_table_rows,
  shared_or_inherited_instrument_identity,
  header_mapped_columns
]
```

서로 다른 행에서 같은 field가 상충하면 합산하거나 임의 선택하지 않는다. 예를 들어 principal 500 / 400이 동시에 붙으면 snapshot `principal`은 `null`로 남고 conflict warning이 생성된다.

### 3. Transposed table

```text
Term                  | Note A | Note B
Principal ($ millions)| 500    | 400
Maturity              | 2028   | 2030
Coupon                | 5.25%  | 6.00%
CUSIP                 | ...    | ...
```

첫 column이 field label이고 각 뒤 column이 instrument일 때 column 단위 candidate를 만든다.

```text
cluster_status = table_column
cluster_basis  = [
  transposed_html_table,
  field_labels_in_first_column,
  same_html_table_column
]
```

`Senior Secured Notes`처럼 instrument title 안에 `secured` 등의 semantic token이 있어도 전체 title identity가 더 구체적이면 instrument header로 취급한다.

## Footnote-aware parsing

HTML `<sup>` marker를 숫자와 붙인 채 버리지 않는다.

```html
<td>500<sup>1</sup></td>
```

은 deterministic parsing에서 amount `500`과 marker `1`을 분리한다. 같은 table 또는 문서 주변에 marker의 유일한 definition이 있으면 별도 source span으로 candidate에 연결한다.

```text
TABLE FOOTNOTE | marker=1 | ...
DOCUMENT FOOTNOTE | marker=1 | ...
```

Footnote는 자동으로 숫자를 수정하는 rule이 아니다. Candidate warning에 “linked footnote를 검토해야 한다”는 경계를 남기고 authoritative snapshot 승격 전 reviewer가 evidence를 다시 연다.

Marker definition이 없거나 여러 개로 모호하면 자동 연결하지 않고 unresolved warning으로 남긴다.

## Aggregate / false candidate 방지

다음 행은 instrument로 승격하지 않는다.

```text
Total debt
Subtotal
Aggregate
```

Footnote-only row도 data row가 아니다. `<th scope="row">`를 사용한 실제 instrument body row는 header row로 오인하지 않도록 semantic-header density를 함께 본다.

## Native-text PDF

PDF는 binary bytes와 pypdf text matrix를 사용하며 HTML과 같은 세 종류의 구조를 지원한다.

```text
pdf_layout_row
pdf_layout_row_group
pdf_layout_column
```

자세한 좌표 규칙과 sparse-native-text guard는 [`NATIVE_PDF_DEBT_EXTRACTION.md`](NATIVE_PDF_DEBT_EXTRACTION.md)를 참고한다.

## Supported field semantics

- instrument / debt / security / facility / loan
- principal / face amount / outstanding
- commitment / drawn / available
- maturity / due date
- coupon / interest rate
- benchmark / spread
- CUSIP / ISIN
- seniority / secured / currency

Header에 millions / billions / thousands 단위가 명확하면 amount scale에 반영한다.

## Visual-source boundary

다음은 deterministic table parser가 확정하려 하지 않는다.

- native text가 없는 scanned PDF
- image file / image-heavy HTML
- rotated/skewed 또는 좌표가 실질적으로 깨진 PDF
- 시각적 병합관계가 text matrix에 보존되지 않은 표
- 여러 페이지/별도 exhibit를 인간적 시각문맥으로 합쳐야만 이해되는 구조

이 경우:

```text
VISUAL_EXTRACTION_REQUIRED|...
```

으로 multimodal source reader에 넘긴다. 기본 경로는 전체 OCR/JSON 변환이 아니라:

```text
source understanding
-> material finding
-> exact evidence
-> investment implication
-> 필요한 최소 field만 structure-on-demand
```

이다.

## Verification boundary

Deterministic candidate도 authoritative debt row가 아니다.

- exact source refs를 다시 연다.
- linked footnote를 같이 확인한다.
- ambiguous/conflicting field는 null로 남긴다.
- model confidence나 parser confidence만으로 stable ledger에 넣지 않는다.
- frozen source packet을 사용하는 stable-ledger ingestion은 explicit reviewer verification을 요구한다.

핵심 원칙은 **source 구조가 명확한 곳까지만 deterministic하게 결합하고, 그 경계를 넘는 순간 추측하지 않는 것**이다.
