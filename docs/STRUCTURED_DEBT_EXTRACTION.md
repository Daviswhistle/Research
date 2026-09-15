# Structured Debt Exhibit Extraction

## 목적

Debt exhibit는 prose만 있는 것이 아니다. 표의 행/열 관계 자체가 instrument identity와 term association을 결정하는 경우가 많다.

```text
Instrument                               Principal ($mm)   Maturity       Coupon   CUSIP
5.25% Senior Secured Notes due 2028      500               2028-06-15     5.25%    123456789
6.00% Senior Secured Notes due 2030      400               2030-06-15     6.00%    987654321
```

이를 단순 text로 flatten하면 `$500m`과 두 번째 CUSIP을 잘못 연결하는 false association이 생길 수 있다.

현재 구조화 경로는 다음처럼 나뉜다.

```text
HTML table      -> DOM row / column 구조 보존
native-text PDF -> text-matrix x/y 좌표 구조 보존
scanned/image   -> 자동 추출하지 않고 visual/OCR 경계로 보류
```

## HTML table pipeline

`structured_debt_extraction.py`는 `sec_instruments`의 기존 prose extractor 앞에 구조 보존 레이어를 설치한다.

```text
SEC exhibit HTML
    -> top-level table parser
    -> rowspan / colspan expansion
    -> header rows 결정
    -> normalized column mapping
    -> one data row = one table source span
    -> same-row deterministic proposals
    -> remaining non-table prose만 기존 extractor로 전달
```

Table body는 prose extractor에서 제거한다. 따라서 한 표가 row-aware candidate와 flattened prose candidate로 중복 처리되지 않는다.

## HTML table source span

한 debt row는 다음과 같은 source span ID를 가진다.

```text
<accession>:<sequence>:t<table_index>:r<row_index>
```

`InstrumentSourceSpan.text`에는 구조화된 row provenance를 보존한다.

```text
TABLE ROW |
Instrument=5.25% Senior Secured Notes due 2028 |
Principal Amount ($ in millions)=500 |
Maturity=June 15, 2028 |
CUSIP=123456789
```

Candidate는 다음 provenance를 가진다.

```text
cluster_status = table_row
cluster_basis = [same_html_table_row, header_mapped_columns]
source_span_ids = [that exact row]
```

## Supported column semantics

현재 deterministic mapping은 다음 계열을 지원한다.

- instrument / debt / security / notes / facility / loan / description
- principal / face amount
- outstanding (medium-confidence principal candidate)
- commitment / facility size / capacity
- drawn / borrowed
- available / availability
- maturity / due date
- coupon / interest rate
- benchmark / reference rate
- spread / margin
- CUSIP / ISIN
- seniority / ranking
- secured / security / collateral
- currency

### Units

Header가 단위를 제공하면 cell numeric value에 적용한다.

```text
Principal ($ in millions) = 500
=> principal = 500,000,000
```

`billions`, `millions`, `thousands`를 지원한다.

### Maturity

다음 형태를 deterministic하게 읽는다.

```text
June 15, 2028
2028-06-15
06/15/2028
2028
```

날짜가 없고 year만 있으면 `maturity_year`만 채운다.

## Row association safety

자동 proposal 결합은 **같은 structural row 내부에서만** 발생한다.

```text
row 1 principal -> row 2 CUSIP    # 금지
row 1 coupon    -> row 2 maturity # 금지
```

각 row candidate의 `source_refs`는 정확히 그 row source span을 가리킨다.

## Multi-row HTML headers

`rowspan` / `colspan`을 grid로 확장하고, 연속된 `<th>` header row를 column별로 결합한다.

```text
Instrument | Terms colspan=2 | CUSIP
           | Principal       | Maturity
```

은 내부적으로:

```text
Instrument
Terms / Principal
Terms / Maturity
CUSIP
```

처럼 column identity를 유지한다.

## Native-text PDF pipeline

PDF는 `response.text`로 decode하지 않는다. Binary bytes를 `pypdf`로 읽고 native text objects의 page/x/y/font-size를 보존한다.

```text
PDF bytes
  -> text matrix fragments
  -> y-axis visual rows
  -> x-axis header anchors
  -> midpoint column boundaries
  -> same-layout-row candidate
```

PDF table source ref:

```text
<accession>:<sequence>:p<page>:r<row>
```

Candidate provenance:

```text
cluster_status = pdf_layout_row
cluster_basis = [same_native_pdf_layout_row, coordinate_mapped_columns, pypdf_text_matrix]
```

Table이 아닌 native PDF prose는 page-local coordinate order로 재구성하고 기존 debt block/clustering logic을 적용한다.

```text
<accession>:<sequence>:p<page>:s<span>
cluster_status = pdf_native_prose | pdf_native_prose_linked
```

자세한 규칙은 [`NATIVE_PDF_DEBT_EXTRACTION.md`](NATIVE_PDF_DEBT_EXTRACTION.md)를 참고한다.

## Graph / closure consistency

Native PDF 지원은 initial SEC packet에만 적용되지 않는다.

Package bootstrap에서 downstream 모듈 import 전에 document-aware helper를 설치하므로 다음 경로가 동일한 PDF semantics를 쓴다.

```text
initial SEC packet
source-document graph
locator-less contract graph
foreign closure
named-entity resolution
```

Native PDF source는 string-compatible payload로 source-reference/contract parser에 전달되지만, 그 payload는 원본 PDF bytes를 함께 보존한다. Debt candidate 추출 단계에서는 원본 bytes로 coordinate layout을 다시 구축한다.

## PDF / image safety boundary

`.pdf`는 먼저 native-text probe를 수행한다.

Native text가 충분하면:

```text
PDF_NATIVE_TEXT_EXTRACTED|...
```

Native text가 없거나 너무 sparse하면:

```text
VISUAL_EXTRACTION_REQUIRED|...|media_type=pdf|...
PDF_NATIVE_TEXT_UNAVAILABLE|...
```

Invalid/failed PDF parsing은:

```text
PDF_NATIVE_TEXT_EXTRACTION_FAILED|...
```

으로 남긴다.

다음 image format은 여전히 자동 OCR하지 않는다.

```text
.png
.jpg / .jpeg
.gif
.tif / .tiff
.bmp
.webp
GRAPHIC document type
```

또한 HTML wrapper 안에 image만 있고 visible text가 너무 적은 exhibit도 defer한다.

중요한 점은 **PDF/image bytes를 일반 string으로 decode한 뒤 regex extraction하지 않는 것**이다.

## Verification boundary

HTML/PDF structured candidate도 authoritative debt row가 아니다.

Verification 단계에서는 다음을 다시 확인해야 한다.

1. header가 실제 해당 column의 의미를 나타내는지
2. header unit이 row value에 적용되는지
3. HTML rowspan/colspan 또는 PDF x/y reconstruction이 올바른지
4. wrapped/multi-line PDF cell이 다른 row로 잘못 분리되지 않았는지
5. row가 instrument row인지 subtotal/summary row인지
6. candidate field가 정확한 source row/page와 연결되는지

확인 후에만 stable debt ledger input으로 승격한다.

## 현재 제한

1. Transposed table(행이 metric, 열이 instrument) 자동 해석은 아직 하지 않는다.
2. Footnote marker가 column 의미를 바꾸는 복잡한 표는 자동 판단하지 않는다.
3. PDF multi-row/spanning header는 HTML rowspan/colspan만큼 강하게 재구성하지 않는다.
4. Rotated/skewed PDF text는 좌표 reconstruction이 부정확할 수 있다.
5. Scanned PDF/image OCR은 아직 구현하지 않았다.
6. Separate exhibits 사이의 structured evidence를 자동 merge하지 않는다.

현재 목표는 aggressively 숫자를 뽑는 것보다, **source가 제공하는 구조 evidence가 충분할 때만 row/column association을 보존해 자동화하고 그렇지 않으면 unresolved로 남기는 것**이다.
