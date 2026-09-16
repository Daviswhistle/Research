# FINRA TRACE Transaction Normalization

## 목적

`CsvBondMarketProvider`는 이미 하루에 하나의 의미 있는 채권 관측치가 들어온다고 가정한다. 그러나 FINRA TRACE Enhanced Historical 원본은 **transaction-level report stream**이다.

그대로 `price_on_or_before`처럼 쓰면 다음 오류가 생길 수 있다.

- 같은 날 여러 execution 중 어떤 값을 daily price로 볼지 암묵적으로 결정함
- inter-dealer 한 거래의 buy/sell 양쪽 report를 두 거래로 셈
- 이후 cancel/correction/reversal된 원거래를 계속 포함함
- weighted-average-price report를 다시 거래량 가중해 double aggregation함
- special price를 ordinary secondary-market price와 섞음
- 일별/월별 파일 overlap으로 같은 report를 두 번 셈

`distressed-equity-trace-normalize`는 이 문제를 **거래 lifecycle → eligibility filter → 명시적 daily aggregation** 순서로 처리한다.

## 입력 범위

현재 parser는 FINRA의 post-2012 **Enhanced Historical Time and Sales CUSIP-bearing pipe-delimited layout**을 대상으로 한다.

CUSIP-bearing 파일을 요구하는 이유는 downstream debt ledger가 CUSIP/ISIN exact identity를 사용하기 때문이다. TRACE Symbol만 있는 원본을 issuer/name fuzzy matching으로 debt instrument에 붙이지 않는다.

중요: repository가 FINRA 원본 데이터 라이선스를 제공하거나 우회하지 않는다. 사용자가 적법하게 접근 가능한 Enhanced Historical export/file을 입력으로 준다는 전제다.

## 처리 순서

```text
raw FINRA reports
    -> parse + unit/date/time validation
    -> exact semantic duplicate collapse
    -> trade lifecycle resolution
       T/R active
       X/C/Y or As-Of R cancel/reverse prior report
    -> market-eligibility filters
    -> group by execution date + CUSIP
    -> explicit VWAP or last-execution policy
    -> normalized daily CSV
    -> CsvBondMarketProvider
```

### 1. Exact duplicate collapse

일별 파일과 월별 파일 등 overlap이 있을 수 있다.

`source_file`/`source_row`만 다른 완전 동일 semantic report는 한 번만 처리한다. `parsed_record_count`는 원본 row 수를 유지하고 `exact_duplicate_record_count`를 별도로 기록한다.

반면 같은 `(Trade Report Date, Reference Number)`가 다른 price/quantity/terms를 갖는 경우에는 hard fail한다. 어느 값이 맞는지 추정하지 않는다.

### 2. Trade lifecycle

활성 report:

```text
T = Trade Report
R = New Correction
```

원거래를 제거하는 report:

```text
X = Trade Cancel
C = Cancelled Correction
Y = Reversal
As Of Indicator = R
```

가능하면 `Prior Trade Report Date + Prior Reference Number`로 target을 찾는다. target locator가 없거나 매치되지 않으면 FINRA reversal matching에 필요한 기본 trade details를 사용한다.

```text
CUSIP
Quantity
Price
Execution Date
Execution Time
Buy/Sell
Contra Party
```

하나만 매치될 때만 제거한다. 여러 개가 매치되면 ambiguity로 실패한다.

### Unresolved correction/reversal

원거래가 입력 범위 밖에 있을 수 있다. 이를 조용히 무시하면 historical price가 오염될 수 있으므로 기본값은 hard fail이다.

```bash
--unresolved-correction-policy error   # default
```

의도적으로 incomplete slice를 연구할 때만:

```bash
--unresolved-correction-policy warn
```

을 쓸 수 있다. 이 경우 metadata와 stderr에 warning이 남는다.

## 기본 eligibility filter

### Disseminated-only

기본:

```bash
--dissemination-policy disseminated
```

`Dissemination Flag=Y`만 포함한다.

이는 특히 inter-dealer 거래에서 buy/sell 양쪽 report를 거래 두 건으로 중복 계산하는 것을 피하기 위한 보수적 기본값이다.

모든 report를 의도적으로 포함하려면:

```bash
--dissemination-policy all
```

을 사용할 수 있지만, output metadata에 inter-dealer two-sided double-count risk warning이 남는다.

### Weighted-average-price report

`Trade Modifier 4=W`는 기본 제외한다.

이미 weighted-average price로 보고된 값을 individual execution과 함께 다시 quantity-weighting하면 의미가 뒤섞일 수 있기 때문이다.

의도적으로 포함하려면:

```bash
--include-weighted-average-price
```

### Special price

`Special Price Indicator=Y`는 기본 제외한다.

의도적으로 포함하려면:

```bash
--include-special-price
```

### Secondary market

기본적으로 `Trading Market Indicator=P1` primary-market report는 제외하고 secondary-market context만 사용한다.

필요하면:

```bash
--include-primary-market
```

으로 opt in한다.

## Daily aggregation policy

Repository는 raw TRACE를 보고 임의로 "closing price"를 정의하지 않는다. 사용자가 policy를 선택한다.

### VWAP

기본:

```bash
--aggregation vwap
```

같은 execution date/CUSIP의 eligible reports에 대해:

```math
VWAP_{price} = \frac{\sum_i Price_i \times Quantity_i}{\sum_i Quantity_i}
```

Yield도 yield가 존재하는 reports에 대해 quantity-weighted average를 계산한다.

Output `volume`은 포함된 report의 uncapped quantity 합이다.

### Last execution

```bash
--aggregation last
```

Execution Time 기준 마지막 eligible report의 price/yield를 사용한다. Daily volume은 여전히 그 날 포함된 모든 eligible report quantity 합이다.

이 값은 "공식 FINRA close"를 주장하지 않는다. **명시적으로 선택한 연구 aggregation policy**다.

## CLI

```bash
distressed-equity-trace-normalize \
  enhanced-time-and-sales-cusip-2022-12.txt \
  enhanced-time-and-sales-cusip-2023-01.txt \
  --aggregation vwap \
  --output trace_daily.csv \
  --metadata-output trace_daily.meta.json
```

출력 CSV:

```text
date
cusip
price_pct_par
yield_pct
volume
source
trade_count
latest_execution_time
aggregation_policy
```

이 CSV는 곧바로 기존 provider에 넣을 수 있다.

```bash
distressed-equity-bond-market \
  --ledger workspace/debt_instrument_ledger.json \
  --analysis-date 2022-12-31 \
  --provider csv \
  --bond-observations-csv trace_daily.csv \
  --output workspace/bond_market.json
```

## Metadata / audit trail

선택적으로 생성되는 metadata JSON에는 다음이 남는다.

```text
inputs
aggregation config
parsed_record_count
exact_duplicate_record_count
active_record_count
included_record_count
daily_observation_count
cancelled_record_count
unresolved_correction_count
warnings
```

따라서 "왜 이 날 가격이 이 값이 됐는가"를 daily row만 보고 끝내지 않고 원본 report stream과 policy까지 역추적할 수 있다.

## 보수적 경계

현재 구현은 다음을 자동으로 추정하지 않는다.

- TRACE Symbol → issuer/debt instrument fuzzy identity
- CUSIP가 없는 historical file의 security master mapping
- unresolved reversal의 target 추정
- bid/ask 또는 evaluated price
- W report를 individual execution으로 복원
- special-price economic meaning을 ordinary trade와 자동 정규화
- CDS/default probability calibration

이 경계를 넘으려면 별도 source-backed adapter가 필요하다.

## 투자 연구에서의 의미

목적은 더 많은 데이터가 아니라 **그 당시 credit market이 실제로 무엇을 가격했는지 왜곡 없이 복원하는 것**이다.

특히 Carvana형 distress 연구에서는 다음 순서가 중요하다.

```text
equity collapse
    + verified capital structure
    + corrected TRACE transaction stream
    + explicit daily aggregation policy
    -> contemporaneous credit stress
    -> survival/recovery model과 비교
```

채권 40~60 cents, 수천 bp spread 같은 값은 매우 강한 signal일 수 있지만, report lifecycle과 거래 중복을 먼저 정리하지 않으면 그 정밀함은 가짜가 된다.
