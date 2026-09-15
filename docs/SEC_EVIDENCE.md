# SEC Point-in-Time Evidence Layer

## 목적

Distressed-equity 연구에서 가장 위험한 오류 중 하나는 **나중에 알게 된 사실을 과거 시점의 판단 근거에 섞는 것**이다. 이 모듈은 미국 상장사의 historical replay를 위해 분석일 당시 SEC에 공개되어 있던 자료만 수집하는 최소 evidence layer다.

공식 SEC 자료만 사용한다.

- submissions API: `https://data.sec.gov/submissions/CIK##########.json`
- companyfacts API: `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
- ticker/CIK lookup: `https://www.sec.gov/files/company_tickers.json`
- filing archive: `https://www.sec.gov/Archives/edgar/data/...`

SEC API 문서: <https://www.sec.gov/search-filings/edgar-application-programming-interfaces>

## 실행

```bash
export SEC_USER_AGENT="Research your-email@example.com"

distressed-equity-sec \
  --ticker CVNA \
  --analysis-date 2022-12-31 \
  -o output/cvna_2022-12-31_sec.json \
  --tasks-output output/cvna_2022-12-31_tasks.json
```

CIK를 직접 지정할 수도 있다.

```bash
distressed-equity-sec \
  --cik 1690820 \
  --analysis-date 2022-12-31
```

## 출력

research packet에는 다음이 포함된다.

1. `snapshot`
   - 회사명, ticker, CIK, 분석일
   - 분석일 이전 10-K / 10-Q / 8-K / amendment
   - filing date, report date, accession number, SEC archive URL
   - point-in-time standardized XBRL facts
2. `evidence`
   - 주장
   - source
   - publication date
   - event/period date
   - locator
3. `point_in_time_violations`
   - 분석일 뒤 공개된 자료가 섞였는지 검증
4. `screening_draft`
   - SEC 자료만으로 안전하게 채울 수 있는 후보 초안
   - 채우면 안 되는 값은 명시적으로 `null`
5. `agent_tasks`
   - capital stack
   - historical market data
   - impairment / normalized economics
   를 조사하는 point-in-time agent task manifest

## Filing history

SEC의 현재 submissions JSON에는 최근 filing이 columnar array로 들어 있고, 오래된 filing은 `filings.files`에 별도 JSON 이름과 기간이 제공된다.

Historical cutoff이 오래된 경우 필요한 history JSON만 추가 조회한다. 각 filing은 `filingDate <= analysis_date`인 경우만 남긴다.

## XBRL fact selection

현재 표준 alias를 다음 항목에 사용한다.

- revenue
- operating income
- cash
- operating cash flow
- capex
- current debt
- noncurrent debt
- shares outstanding

선택 규칙:

1. `filed <= analysis_date`
2. `period_end <= analysis_date`
3. 지정한 form에 해당
4. 모든 표준 alias를 함께 비교해 가장 최근 period end를 우선
5. 같은 period면 최신 filing을 우선
6. 같은 context면 alias/unit preference를 tie-breaker로 사용

즉 예전에 사용하던 preferred revenue tag가 남아 있다는 이유로 더 최근 `Revenues` fact를 무시하지 않는다.

## 왜 자동 TTM을 만들지 않는가

SEC `companyfacts`의 duration fact는 같은 period end에 다음이 섞일 수 있다.

- 분기 단독
- 6개월 YTD
- 9개월 YTD
- 연간

따라서 `revenue`, `CFO`, `capex`를 가져왔다고 해서 임의로 TTM이나 정상화 FCF로 바꾸지 않는다.

CFO와 capex가 동일한 start/end/unit을 공유할 때만 `reported CFO - reported capex`를 `raw_fcf_context`로 제공한다. 이것도 owner cash flow가 아니라 조사용 문맥이다.

## Screening prefill 정책

자동 승격하는 값은 제한적이다.

- `shares_outstanding`: standardized point-in-time fact가 있으면 초안에 사용
- `starting_liquidity`: `CashAndCashEquivalentsAtCarryingValue`인 경우에만 cash component로 제안
- positive reported revenue가 있으면 `is_pre_revenue=false`
- positive operating income가 있으면 `has_historical_operating_profit=true`

자동으로 채우지 않는 값:

- share price
- peak market cap
- enterprise value
- net debt
- TTM revenue
- real-customer quality
- revolver availability
- restricted cash
- debt maturity / covenant schedule
- recovery window
- normalized scenarios
- probability range

특히 `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`처럼 restricted cash가 섞일 수 있는 tag는 available liquidity로 바로 승격하지 않는다.

## Capital-stack agent

SEC snapshot 이후 `capital_stack_extractor`가 filing 원문에서 다음을 복원하도록 task manifest를 만든다.

- unrestricted cash / restricted cash
- revolver availability
- debt tranches, coupons, maturities
- collateral and seniority
- covenant / springing maturity
- minimum liquidity and cure mechanics
- leases / preferred / earn-out / TRA / contingent claims
- dilution sources and fully diluted shares

이 결과가 들어오기 전에는 Time-to-Death를 확정하지 않는다.

## Historical market agent

SEC만으로 cutoff-date 주가를 신뢰성 있게 복원할 수 없으므로 별도 과제로 남긴다.

- cutoff-date share price
- current-at-cutoff market cap
- prior peak market cap
- reconciled point-in-time EV
- 당시 bond price/yield 등 distress evidence

후일 주가를 알고 있다는 사실이 판단에 영향을 주지 않도록 cutoff guardrail을 동일하게 적용한다.

## Fair access

SEC는 자동 접근이 공공 서비스에 부담을 주지 않도록 fair-access 정책을 적용한다. 현재 클라이언트는:

- 식별 가능한 `SEC_USER_AGENT`를 필수로 요구
- 기본 최소 요청 간격 0.12초
- 0.1초보다 짧은 설정을 거부
- 필요한 history file만 조회

한다.

정책은 바뀔 수 있으므로 운영 전 SEC 공식 Developer Resources와 Accessing EDGAR 문서를 다시 확인해야 한다.

## 한계

1. 표준 taxonomy만 읽기 때문에 company-specific extension fact는 누락될 수 있다.
2. filing 원문의 debt footnote/covenant 표를 아직 결정론적으로 파싱하지 않는다.
3. SEC는 시장가격 데이터 공급자가 아니므로 가격·bond 정보는 별도 point-in-time source가 필요하다.
4. filing date 단위 cutoff를 사용한다. 일중 historical replay가 필요하면 acceptance datetime을 기준으로 더 엄격한 cutoff가 필요하다.
5. amended filing은 해당 amendment가 실제 공개된 날짜 이후에만 포함된다. 나중의 restatement를 과거 원 filing에 소급하지 않는다.

## 다음 연결

```text
SEC snapshot
    ↓
safe screening draft + evidence ledger
    ↓
agent: capital stack / market / impairment normalization
    ↓
complete ScreeningCandidate
    ↓
universe gates
    ↓
DEEP_DIVE_CANDIDATE / RESEARCH / DROP
    ↓
deterministic full case model
    ↓
Required Probability vs defensible probability range
```
