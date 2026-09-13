# Historical Market Replay

## 목적

Distressed-equity 연구에서 과거 후보를 재현할 때 가장 위험한 오류 중 하나는 **현재 살아 있는 종목만 과거로 되감는 survivorship bias**다.

이 계층의 목적은 다음을 분리하는 것이다.

1. 분석일 당시 실제 투자 가능했던 security universe
2. 분석일 당시의 raw/as-traded 주가
3. split/dividend-adjusted 가격 경로
4. 상장폐지 이후 결과
5. 기업 펀더멘털과 자본구조

가격 데이터만으로 기업을 매수 후보로 확정하지 않는다. Market replay는 값싼 **candidate-generation layer**이며, 이후 SEC evidence, capital-stack audit, survival analysis와 deterministic valuation을 통과해야 한다.

## provider abstraction

`HistoricalMarketProvider`는 세 가지를 요구한다.

```text
universe(as_of)
price_on_or_before(security, as_of)
adjusted_history(security, start, end)
```

`SecurityIdentity.security_id`는 가능하면 ticker가 아닌 permanent identifier를 사용한다. Ticker는 변경되거나 재사용될 수 있기 때문이다.

## Alpha Vantage provider

`AlphaVantageProvider`는 공식 Alpha Vantage API를 이용한다.

- `LISTING_STATUS`: 특정 과거 날짜의 active/delisted 미국 주식·ETF 목록
- `TIME_SERIES_DAILY`: cutoff 시점 raw/as-traded close
- `TIME_SERIES_WEEKLY_ADJUSTED`: split/dividend-adjusted 장기 경로

공식 문서:

- https://www.alphavantage.co/documentation/

### 왜 historical `active` universe를 사용하는가

분석일이 2022-12-31이면 **2022-12-31 당시 active였던 종목**을 요청한다. 그 종목이 2023년에 상장폐지되었더라도 당시 universe에는 남는다.

현재 active 목록을 가져와 2022년 가격만 붙이는 방식은 금지한다.

### 왜 raw daily + adjusted weekly를 분리하는가

Point-in-time market cap:

```text
raw cutoff close × cutoff shares outstanding
```

후보 생성용 drawdown:

```text
1 - latest adjusted price / prior adjusted peak
```

raw 가격으로 장기 drawdown을 계산하면 stock split만으로 가짜 distress가 생길 수 있다. 반대로 adjusted 가격으로 당시 market cap을 계산하면 실제 거래가격과 어긋날 수 있다.

현재 Alpha Vantage 문서에서 Daily Adjusted는 premium endpoint로 표시되므로, 기본 구현은 exact cutoff 가격에 raw daily를 사용하고 장기 drawdown에는 Weekly Adjusted를 사용한다.

### 제한

Alpha Vantage는 종목별 historical price request가 필요하므로 `bulk_safe=False`다.

따라서 전체 미국 시장 수천 종목을 무제한으로 호출하지 않는다. 기본 CLI는 다음 중 하나를 요구한다.

- `--symbols`
- `--symbols-file`
- 또는 사용자가 명시적으로 `--allow-slow-full-universe`

Alpha Vantage provider는 후보 확인과 소규모 historical replay에 적합하다.

## CSV bulk provider

대규모 replay에는 `CsvMarketProvider`를 사용한다.

### securities CSV

필수:

```text
security_id,symbol,name
```

선택:

```text
exchange,asset_type,start_date,end_date,status
```

예:

```csv
security_id,symbol,name,exchange,asset_type,start_date,end_date,status
10001,AAA,Example Inc,NASDAQ,Stock,2012-01-03,2024-06-11,delisted
```

`end_date`가 2024년이어도 2022 replay에서는 정상적으로 universe에 포함된다.

### prices CSV

필수:

```text
security_id,date,close
```

권장:

```text
adjusted_close,volume,shares_outstanding
```

예:

```csv
security_id,date,close,adjusted_close,volume,shares_outstanding
10001,2022-12-30,3.10,3.10,5400000,105000000
```

`adjusted_close`가 없으면 해당 가격 경로는 adjustment-safe로 인정하지 않으며 기본 replay에서 drawdown 후보를 만들지 않는다.

## CRSP 사용 시

CRSP export를 보유한 경우 `PERMNO`를 `security_id`로 매핑하는 방식이 가장 적합하다.

CRSP는 다음 데이터를 별도로 제공한다.

- permanent security identifier (PERMNO)
- shares history
- delisting date / code
- delisting return
- post-delisting amount/price information

따라서 장기 역사검증에서는 일반적인 ticker-only 데이터보다 훨씬 강하다.

특히 성과측정 시 마지막 거래가격에서 끝내면 상장폐지 후 분배·회수금액을 무시할 수 있다. CRSP의 delisting return은 이 문제를 다루기 위한 별도 필드다.

참고:

- https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-data-descriptions-guide-crspaccess/
- https://www.crsp.org/wp-content/uploads/guides/CRSP10_Year_US_Stock_Database_Guide.pdf

## replay 실행

### Alpha Vantage — targeted replay

```bash
export ALPHA_VANTAGE_API_KEY="..."

distressed-equity-replay \
  --provider alpha-vantage \
  --analysis-date 2022-12-31 \
  --symbols CVNA,UPST,OPEN \
  --min-drawdown 0.60 \
  --markdown-output output/replay.md \
  -o output/replay.json
```

### CSV — bulk replay

```bash
distressed-equity-replay \
  --provider csv \
  --analysis-date 2022-12-31 \
  --securities-csv data/security_master.csv \
  --prices-csv data/prices.csv \
  --min-drawdown 0.60 \
  --markdown-output output/replay.md \
  -o output/replay.json
```

## replay 출력

각 후보에는 최소한 다음이 남는다.

```text
security_id
symbol
analysis_date
raw cutoff price + date
adjusted cutoff price + date
adjusted prior peak + date
adjusted drawdown
provider
```

이 단계의 drawdown은 **candidate-generation proxy**다. 최종 `distress_gate`의 point-in-time peak market cap 또는 EV 재구성을 대체하지 않는다.

## SEC packet과 결합

`apply_market_seed_to_prefill()`은 SEC research packet에 다음 값만 안전하게 추가한다.

- raw cutoff price
- shares가 이미 SEC에서 확인된 경우 cutoff market cap
- adjusted price drawdown
- peak adjusted price/date
- provider/security ID

다음은 그대로 unresolved로 남긴다.

- peak market capitalization
- reconciled EV
- note-level net debt
- revolver/covenant
- normalized economics

## base-rate library

`BaseRateLibrary`는 turnaround probability를 자동 생성하지 않는다. 과거 사례를 **확률 범위의 anchor**로 제공한다.

각 사례는 최소한 다음 날짜를 가진다.

```text
analysis_date
outcome_known_date
```

2022-12-31 historical replay에서는 `outcome_known_date > 2022-12-31`인 사례를 base rate에 사용할 수 없다. 즉 2023~2025 결과를 알고 있는 사례를 2022년 판단에 소급해서 넣지 않는다.

또한 평가 중인 동일 사례는 `exclude_case_ids`로 제외해야 한다.

예시 schema:

```json
{
  "case_id": "case-001",
  "ticker": "XYZ",
  "analysis_date": "2016-01-15",
  "outcome_known_date": "2019-01-15",
  "industry_group": "consumer",
  "impairment_type": "cyclical",
  "leverage_bucket": "high",
  "survived_12m": true,
  "existing_common_survived_12m": true,
  "normalized_within_3y": true,
  "equity_multiple_3y": 4.2
}
```

실행:

```bash
distressed-equity-base-rates cases.jsonl \
  --cutoff 2022-12-31 \
  --impairment-type cyclical \
  --leverage-bucket high
```

## 남아 있는 데이터 문제

1. Alpha Vantage symbol identity는 CRSP PERMNO 같은 영구 ID가 아니다.
2. 역사적 fully diluted share count는 SEC XBRL 한 필드만으로 충분하지 않을 수 있다.
3. peak market cap은 point-in-time shares history가 필요하다.
4. bond price/yield history는 별도 provider가 필요하다.
5. OTC 전환, bankruptcy distribution, relisting까지 정확히 평가하려면 전문 데이터가 유리하다.
6. 전체시장 장기 백테스트에서는 provider licensing과 API 호출비용을 별도로 고려해야 한다.

따라서 현재 권장 구조는 다음이다.

```text
Alpha Vantage → 소규모 역사 검증 / universe sanity check
CSV permanent-ID provider → 대규모 replay
CRSP급 데이터 → 정식 survivorship/delisting 성과 연구
```
