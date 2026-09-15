# Structured Debt Exhibit Extraction

## 목적

Debt exhibit는 prose만 있는 것이 아니다. 다음처럼 표의 행/열 관계 자체가 instrument identity와 term association을 결정하는 경우가 많다.

```text
Instrument                               Principal ($mm)   Maturity       Coupon   CUSIP
5.25% Senior Secured Notes due 2028      500               2028-06-15     5.25%    123456789
6.00% Senior Secured Notes due 2030      400               2030-06-15     6.00%    987654321
```

이 표를 단순 text로 flatten하면 `$500m`과 두 번째 CUSIP을 잘못 연결하는 false association이 생길 수 있다.

따라서 structured extractor의 기본 원칙은 다음과 같다.

```text
HTML table -> row / column 구조 보존
PDF/image   -> text처럼 강제 해석하지 않음
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

Table body는 prose extractor에서 제거한다. 따라서 한 표가 한 번은 row-aware candidate로, 다시 한 번은 flattened prose candidate로 중복 처리되지 않는다.

## Table source span

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

자동 proposal 결합은 **같은 table row 내부에서만** 발생한다.

즉 다음은 허용되지 않는다.

```text
row 1 principal -> row 2 CUSIP
row 1 coupon    -> row 2 maturity
```

각 row candidate의 `source_refs`는 정확히 그 row source span을 가리킨다.

## Multi-row headers

`rowspan` / `colspan`을 grid로 확장하고, 연속된 `<th>` header row를 column별로 결합한다.

예:

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

## Prose coexistence

표 밖의 prose는 기존 distant-span clustering extractor로 계속 처리한다.

```text
HTML
  -> tables: row-aware extractor
  -> outside prose: existing source-span clustering
```

따라서 table 구조를 보존하면서 기존 CUSIP/ISIN, note-title, facility-label, distant-span logic을 잃지 않는다.

## PDF / image safety boundary

현재 dependency-free pipeline은 PDF/image binary를 자동 OCR/PDF parser로 해석하지 않는다.

다음 파일은 text fetch 전에 defer한다.

```text
.pdf
.png
.jpg / .jpeg
.gif
.tif / .tiff
.bmp
.webp
GRAPHIC document type
```

또한 HTML wrapper 안에 image만 있고 visible text가 너무 적은 exhibit도 defer한다.

Frozen packet warning은 machine-readable prefix를 사용한다.

```text
VISUAL_EXTRACTION_REQUIRED|
accession=...|
document=...|
document_type=...|
media_type=pdf|image|image_heavy_html|
url=...|
reason=...
```

중요한 점은 **PDF를 response.text로 decode한 뒤 regex extraction하지 않는 것**이다. Binary/visual document는 후속 visual/PDF extraction stage가 준비될 때까지 unresolved evidence로 남긴다.

## Verification boundary

HTML table row candidate도 authoritative debt row가 아니다.

Verification 단계에서는 다음을 다시 확인해야 한다.

1. header가 실제 해당 column의 의미를 나타내는지
2. header unit이 row value에 적용되는지
3. rowspan/colspan으로 표현된 header가 올바르게 해석됐는지
4. row가 instrument row인지 subtotal/summary row인지
5. candidate field가 정확한 source row와 연결되는지

확인 후에만 stable debt ledger input으로 승격한다.

## 현재 제한

1. Transposed table(행이 metric, 열이 instrument) 자동 해석은 아직 하지 않는다.
2. Footnote marker가 column 의미를 바꾸는 복잡한 표는 자동 판단하지 않는다.
3. PDF의 native text layer / embedded table extraction은 아직 구현하지 않았다.
4. Scanned PDF/image OCR은 아직 구현하지 않았다.
5. Separate exhibits 사이의 table-row evidence를 자동 merge하지 않는다.

현재 목표는 구조를 모르는 상태에서 aggressively 숫자를 뽑는 것보다, **HTML에서 신뢰할 수 있는 row/column association은 보존하고 visual source는 안전하게 unresolved로 남기는 것**이다.
