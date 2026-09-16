# Native-Text PDF Debt Extraction

## 목적

SEC debt exhibit가 PDF라는 이유만으로 binary bytes를 `response.text`로 decode하지 않는다. Native text layer와 좌표 구조가 usable하면 deterministic extraction을 하고, 그렇지 않으면 multimodal source reader로 넘긴다.

```text
PDF bytes
  -> pypdf native text fragments(page/x/y/font/text)
  -> explicit layout structure exists?
       yes -> deterministic table/prose extraction
       no  -> multimodal source reader
```

Repository의 기본 전략은 scanned PDF를 먼저 전면 OCR해 모든 표를 JSON화하는 것이 아니다.

## Native-text safety gate

기본 native-text density gate는 visual/image-only PDF를 text parser가 억지로 해석하는 것을 막는다.

다만 **작지만 명백한 coordinate table**은 전체 문서 text density가 낮아도 안전하게 구조를 읽을 수 있다. 이 경우에만 제한적으로 예외를 둔다.

```text
sparse native text
  + deterministic coordinate-table parser가 실제 candidate 생성
  => table candidate 허용
  => prose extraction은 여전히 금지

sparse native text
  + explicit table candidate 없음
  => VISUAL_EXTRACTION_REQUIRED
```

즉 global threshold를 낮춘 것이 아니다.

## 1. Ordinary row-oriented PDF table

Semantic header의 x 좌표 midpoint를 column boundary로 사용한다.

```text
Instrument | Principal | Maturity | Coupon | CUSIP
```

```text
cluster_status = pdf_layout_row
cluster_basis = [
  same_native_pdf_layout_row,
  coordinate_mapped_columns,
  pypdf_text_matrix
]
```

## 2. Multi-row PDF instrument

한 instrument의 material fields가 바로 다음 y-row로 wrap될 수 있다.

```text
Term Loan B | 800 |        | CUSIP
            |     | 2028-12-15 |
```

첫 행의 explicit identity를 유지한 채 인접 continuation row의 non-name fields만 결합한다.

```text
cluster_status = pdf_layout_row_group
cluster_basis = [
  contiguous_native_pdf_layout_rows,
  shared_instrument_identity,
  coordinate_mapped_columns,
  pypdf_text_matrix
]
```

같은 field가 서로 다른 값으로 충돌하면 선택/합산하지 않고 `null` + warning으로 남긴다.

## 3. Transposed PDF table

```text
Term       | Note A | Note B
Principal  | 500    | 400
Maturity   | 2028   | 2030
Coupon     | 5.25%  | 6.00%
```

첫 x-column에 field labels가 있고 상단 y-row에 instrument titles가 있으면 x-column별 candidate를 만든다.

```text
cluster_status = pdf_layout_column
cluster_basis = [
  transposed_native_pdf_table,
  coordinate_mapped_instrument_columns,
  first_column_field_labels,
  pypdf_text_matrix
]
```

`Senior Secured Notes`처럼 title 내부의 `secured` token이 generic field classifier에 걸리더라도, 전체 문자열이 강한 instrument identity이면 title interpretation을 우선한다.

## PDF footnotes

Native layout row에 `(1)`, `[a]`, `*` 등의 trailing marker가 있으면 numeric parsing 전에 marker를 분리한다.

Page 안에서 유일한 definition을 찾으면 별도 evidence span으로 연결한다.

```text
PDF PAGE FOOTNOTE | page=... | marker=1 | ...
```

Footnote는 자동 adjustment rule이 아니다. Candidate는 linked footnote warning을 갖고 reviewer가 authoritative snapshot 승격 전에 원문을 재확인한다.

## Aggregate row 방지

`Total debt`, `Subtotal`, `Aggregate`는 instrument identity로 사용하지 않는다. Footnote-only row도 data candidate가 아니다.

## Native PDF prose

명확한 table structure가 없는 충분한 native-text page는 y/x coordinate order로 재구성한 뒤 기존 debt-block clustering을 사용한다.

```text
<accession>:<sequence>:p<page>:s<span>
cluster_status = pdf_native_prose | pdf_native_prose_linked
```

Table로 확정된 page는 다시 flattened prose로 중복 해석하지 않는다.

Sparse-native-text 예외로 table을 읽은 경우에도 prose extraction은 하지 않는다.

## Graph / closure integration

동일한 PDF extraction semantics가 다음 경로에 적용된다.

```text
initial SEC packet
source-document graph
locator-less contract graph
foreign contract closure
named-entity contract resolution
```

String-compatible native payload는 reference search를 가능하게 하면서 원본 PDF bytes도 보존한다. Debt extraction은 원본 bytes에서 다시 좌표 candidate를 만든다.

## Scanned / image-only PDF

Native text/좌표가 없거나 deterministic association을 안전하게 만들 수 없으면 candidate를 만들지 않는다.

```text
VISUAL_EXTRACTION_REQUIRED|...|media_type=pdf|...
PDF_NATIVE_TEXT_UNAVAILABLE|...
PDF_NATIVE_TEXT_EXTRACTION_FAILED|...
```

다음 단계는 OCR table pipeline이 아니라 multimodal source reader다.

## Verification boundary

Native PDF candidate도 authoritative debt row가 아니다. 최소 다음을 다시 확인해야 한다.

1. x/y reconstruction이 실제 visual layout과 일치하는가
2. header/field-label orientation이 맞는가
3. continuation row가 같은 instrument인가
4. unit와 footnote가 amount 의미를 바꾸는가
5. CUSIP/ISIN이 올바른 instrument column/row에 붙었는가
6. source refs가 실제 candidate를 지지하는가

## 여전히 deterministic하지 않는 경우

- rotated/skewed text 또는 복잡한 CTM 때문에 좌표가 깨진 PDF
- glyph 단위로 지나치게 잘게 쪼개져 column identity를 안정적으로 복구할 수 없는 PDF
- 시각적 병합관계가 text matrix에 보존되지 않은 표
- 여러 page의 visual hierarchy를 함께 봐야 하는 구조
- chart/image callout을 통해서만 debt term을 이해할 수 있는 source

이 경우는 multimodal 원본 읽기로 남긴다.

핵심은 **좌표 구조가 증거로 충분한 곳까지만 deterministic하게 사용하고, 그 이상은 원본 시각문맥을 다시 읽는 것**이다.
