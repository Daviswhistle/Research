# CRSP / WRDS Equity Export Normalization

## 목적

Historical equity replay가 신뢰할 수 있으려면 ticker가 아니라 **permanent security identity**와 point-in-time name history를 써야 한다.

이 adapter는 licensed CRSP/WRDS-style CSV export를 기존 `CsvMarketProvider` contract로 변환한다.

```text
CRSP stock-name history
  PERMNO + NAMEDT/NAMEENDT
        -> point-in-time security intervals

CRSP market history
  PERMNO + date + price + total-return field
        -> raw close
        -> continuous total-return-equivalent adjusted series

output
  securities.csv
  prices.csv
        -> CsvMarketProvider
        -> historical replay / credit replay / coverage / calibration
```

## 가장 중요한 원칙

### 1. Ticker는 identity가 아니다

Stable key:

```text
security_id = CRSP_PERMNO:<PERMNO>
```

Ticker/company name은 해당 `NAMEDT..NAMEENDT` interval의 display/routing metadata일 뿐이다.

Ticker 변경이나 ticker 재사용 때문에 과거 securities를 이름으로 join하지 않는다.

### 2. Point-in-time membership

Security master output에는:

```text
start_date = NAMEDT
end_date   = NAMEENDT
```

를 기록한다.

Intervals는 inclusive membership으로 처리한다.

같은 PERMNO의 retained intervals가 겹치면 hard fail한다. 어느 name/ticker row가 맞는지 임의 선택하지 않는다.

### 3. Common-equity share code filter

기본:

```bash
--share-codes 10,11
```

이다.

다른 universe를 의도적으로 연구하려면 직접 바꿀 수 있다.

```bash
--share-codes all
```

을 쓰면 share-code filter를 끈다.

기본 common-equity filter를 쓰는데 share-code column을 찾지 못하면 hard fail한다.

## Return series: CFACPR만으로 만들지 않음

Distress drawdown은 split만 보정한 가격보다 **주주 total-return path에 가까운 연속 series**가 더 적절하다.

이 normalizer는 selected return field를 연쇄해 total-return-equivalent index를 만든다.

```math
W_t = W_{t-1}(1+r_t)
```

첫 usable raw price를 각 continuous segment의 scale anchor로 사용한다.

따라서 `adjusted_close`는 literal exchange close가 아니라:

> drawdown 비교를 위한 total-return-equivalent price index scaled to a raw-price anchor

이다.

Raw economic close는 별도로 `close`에 보존된다.

## Return semantics를 자동 추정하지 않음

CRSP product/export generation에 따라 delisting-return handling 계약이 다를 수 있다.

따라서 CLI에서 반드시 하나를 명시한다.

### `legacy`

```bash
--return-semantics legacy
```

Selected return과 optional delisting return이 별도 field라는 계약이다.

둘 다 존재하는 row:

```math
r_{total} = (1+r)(1+r_{delist}) - 1
```

한쪽만 존재하면 존재하는 값을 사용한다.

Legacy mode에서 delisting-return column을 찾지 못하면 metadata warning을 남긴다. 조용히 완전한 delisting coverage라고 주장하지 않는다.

### `already_total`

```bash
--return-semantics already_total
```

Selected return field가 사용자가 의도하는 완성된 total return이라는 계약이다.

이 모드에서는 DLRET를 다시 더하지 않는다.

`--market-delisting-return-column`을 동시에 명시하면 possible double counting으로 hard fail한다.

### 왜 explicit mode인가

Column name만 보고:

```text
RET -> 무조건 legacy
DlyRet -> 무조건 이미 완성
```

처럼 추론하지 않는다.

Licensed export의 공식 data dictionary를 확인한 뒤 사용자가 return semantics를 선택해야 한다.

## Return chain break

Internal return이 missing이면 서로 다른 scale segment를 이어 붙이면 안 된다.

예:

```text
Jan 1  price=10   anchor adjusted=10
Jan 2  price=9    return missing
Jan 3  price=9.9  return=+10%
```

Jan 2에서 chain이 끊긴다.

Normalizer output:

```text
Jan 1 adjusted_close = 10
Jan 2 adjusted_close = blank   <- barrier
Jan 3 adjusted_close = 9.9     <- new segment after Jan 2 anchor
```

기존 `CsvMarketProvider`는 blank adjusted row를 `adjusted=False`로 읽는다.

따라서 replay window가 이 barrier를 가로지르면:

```text
history is not split/dividend adjusted
```

로 후보 생성을 거부한다.

이것이 잘못된 continuity를 만들어내는 것보다 낫다.

## Negative price sign

Normalizer는 selected raw price의 경제적 크기에:

```text
abs(price)
```

를 사용하고, 원본이 negative-signed였는지는:

```text
crsp_prc_was_negative
```

에 보존한다.

따라서 원본 CRSP sign convention을 잃지 않으면서 market price를 음수 경제가치로 오해하지 않는다.

Metadata에도 negative-signed row count가 남는다.

## Shares outstanding

Shares outstanding 단위는 export contract에 따라 명시적으로 확인해야 한다.

그래서 auto-detect하지 않는다.

사용하려면 둘 다 직접 지정한다.

```bash
--market-shares-outstanding-column SHROUT
--shares-outstanding-scale 1000
```

Scale을 지정하지 않고 shares column만 주거나, scale만 주고 column을 안 주면 hard fail한다.

이 경계는 단위 착오로 market cap이 1,000배 틀어지는 문제를 막기 위한 것이다.

## Column aliases / overrides

Convenience aliases를 지원한다.

예:

```text
PERMNO / Permno
NAMEDT / NameDt
NAMEENDT / NameEndDt
PRC / DlyPrc / MthPrc
RET / DlyRet / MthRet
date / DlyCalDt / MthCalDt
```

하지만 둘 이상의 candidate column이 동시에 존재하면 어느 것을 쓸지 추정하지 않고 hard fail한다.

그때는 explicit override를 쓴다.

예:

```bash
--market-date-column DlyCalDt
--market-price-column DlyPrc
--market-return-column DlyRet
```

Stock-name overrides도 동일하다.

## Market export sorting

대규모 CRSP export를 메모리에 모두 적재하지 않기 위해 market file은 streaming으로 읽는다.

따라서 input은 반드시:

```text
PERMNO ascending
then date ascending
```

이어야 한다.

Duplicate / reverse key가 나오면 hard fail한다.

## CLI

현재 module entrypoint:

```bash
python -m distressed_equity.crsp_normalize_cli \
  --stocknames-csv crsp_stocknames.csv \
  --market-csv crsp_daily.csv \
  --securities-output normalized_securities.csv \
  --prices-output normalized_prices.csv \
  --metadata-output crsp_normalization.json \
  --return-semantics legacy
```

Already-total export:

```bash
python -m distressed_equity.crsp_normalize_cli \
  --stocknames-csv names.csv \
  --market-csv market.csv \
  --securities-output securities.csv \
  --prices-output prices.csv \
  --metadata-output metadata.json \
  --return-semantics already_total
```

## Output securities.csv

기존 `CsvMarketProvider` required fields:

```text
security_id
symbol
name
exchange
asset_type
start_date
end_date
status
```

Audit columns:

```text
crsp_permno
crsp_share_code
crsp_exchange_code
```

Exchange code 1/2/3은 convenience display로 NYSE/AMEX/NASDAQ을 사용하고, 다른 code는 `CRSP_EXCHCD_<code>`로 보존한다.

## Output prices.csv

```text
security_id
symbol
date
close
adjusted_close
volume
shares_outstanding
crsp_permno
crsp_prc_was_negative
crsp_return_semantics
```

`volume`은 현재 비워 둔다. CRSP volume unit semantics를 이 adapter가 임의 추정하지 않는다.

## Metadata / audit trail

다음이 남는다.

```text
stocknames_input_rows
security_interval_count
filtered_stocknames_rows
market_input_rows
output_price_rows
market_rows_outside_security_intervals
rows_without_usable_price
negative_price_rows
missing_return_rows
delisting_return_rows_applied
return_chain_break_count
adjusted_output_rows
unadjusted_barrier_rows
resolved_columns
warnings
```

그리고 CLI metadata에는 실제 input/output path와 config가 추가된다.

## Numeric missing values

Blank / alphabetic missing markers는 return missing으로 처리한다.

Numeric sentinel은 자동 추정하지 않는다.

예를 들어 licensed export가 특정 numeric missing code를 쓴다면 명시한다.

```bash
--missing-return-code -66
--missing-return-code -77
```

그렇지 않은 `< -1` return은 hard fail한다.

## Replay 연결

정규화 후 기존 pipeline을 그대로 쓴다.

```bash
python -m distressed_equity.replay_cli \
  --analysis-date 2022-12-31 \
  --provider csv \
  --securities-csv normalized_securities.csv \
  --prices-csv normalized_prices.csv
```

또는 equity+credit population:

```bash
python -m distressed_equity.credit_replay_cli \
  --analysis-date 2022-12-31 \
  --securities-csv normalized_securities.csv \
  --prices-csv normalized_prices.csv \
  --credit-links-csv security_to_bond_membership.csv \
  --bond-observations-csv trace_daily.csv
```

## 아직 자동화하지 않는 것

- CRSP product generation별 return semantics 추정
- CFACPR만으로 dividend-adjusted series 생성
- shares outstanding 단위 추정
- volume 단위 추정
- current ticker/name으로 historical identity backfill
- missing return numeric sentinel 추정
- CRSP ↔ TRACE issuer/debt identity fuzzy join

정확한 data dictionary가 있으면 explicit column/semantics contract를 넣고 재현 가능한 normalized file을 만드는 것이 목적이다.
