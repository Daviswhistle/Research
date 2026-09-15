# Contract Party Identity

## 목적

Locator-less contract fallback은 원래 `contract kind + exact execution date`로 historical EX-4/EX-10을 찾았다. 하지만 서로 다른 자회사나 facility가 같은 날 동일한 이름의 계약을 체결할 수 있다.

따라서 source 문서가 borrower / issuer / guarantor를 명시하는 경우 party fingerprint를 contract identity에 포함한다.

```text
Credit Agreement
+ 2019-05-03
+ borrower = ABC Borrower LLC
```

은:

```text
Credit Agreement
+ 2019-05-03
+ borrower = XYZ Borrower LLC
```

와 다른 identity다.

## 지원 role

현재 deterministic parser는 법인명이 명시적으로 role과 연결될 때만 party evidence로 인정한다.

- borrower / co-borrower
- issuer
- guarantor / parent guarantor

예:

```text
ABC Borrower LLC, as Borrower
Parent Holdings, Inc., as Parent Guarantor
ABC Issuer LLC (the "Issuer")
```

`the Company`, `the Borrower`처럼 실체 법인명을 확인할 수 없는 generic label은 fingerprint로 쓰지 않는다.

## Hard-filter 규칙

Party는 fuzzy score boost가 아니다.

Source contract mention에 borrower 또는 issuer가 있으면 candidate exhibit도 **같은 role + 같은 normalized legal entity**를 하나 이상 독립적으로 확인해야 한다.

```text
source borrower = ABC Borrower LLC
candidate borrower = XYZ Borrower LLC
→ reject
```

Source에 borrower/issuer는 없고 guarantor만 있으면 guarantor overlap이 필요하다.

Source에 party 정보가 전혀 없으면 이 레이어는 중립이며 기존 `kind + date + restated state` fallback이 그대로 동작한다.

## 법인명 normalization

Fuzzy company matching은 하지 않는다. 다음 정도의 표기 차이만 정규화한다.

```text
ABC Holdings, Inc.
ABC Holdings Incorporated
→ abc holdings inc

Borrower L.L.C.
Borrower LLC
→ borrower llc
```

반대로 이름 token이 실질적으로 다르면 자동 동일시하지 않는다.

## Local context guard

Party parser는 같은 계약 mention의 문장/line에 가까운 role-labelled legal entity만 사용한다.

다음처럼 두 facility가 연속되어 있을 때:

```text
ABC Borrower LLC, as Borrower, entered into a Credit Agreement dated May 3, 2019.
XYZ Borrower LLC, as Borrower, entered into a Credit Agreement dated May 3, 2019.
```

첫 번째 contract identity에 XYZ borrower가 섞이면 안 된다.

다른 문장의 계약명 뒤에서 regex가 잡은 party는 버리는 방향으로 설계했다. False negative를 감수하더라도 잘못된 obligor를 붙이지 않는 것이 우선이다.

## Graph identity key

Source graph의 locator-less identity dedupe key도 party fingerprint를 포함한다.

```text
(source node, contract kind, execution date, party fingerprint)
```

따라서 같은 source filing에 같은 날짜의 두 Credit Agreement가 있어도 borrower가 다르면 별도 graph edge를 가진다.

## Amendment guard와의 관계

Party matching은 기존 안전장치를 대체하지 않는다.

순서는 다음과 같다.

```text
explicit SEC locator
    ↓
form/date/exhibit
    ↓
contract kind + execution date
    ↓
restated/original distinction
    ↓
party hard filter (if source exposes parties)
    ↓
unique candidate only
```

Amendment / waiver / supplement / joinder / consent exhibit가 원계약을 인용하는 false positive 방지도 그대로 유지된다.

## 일부러 하지 않는 것

- 약칭 회사명과 법인 정식명칭의 fuzzy entity resolution
- parent/subsidiary 관계만으로 동일 party라고 추정
- lender / administrative agent가 같다는 이유로 같은 facility라고 추정
- source에 party가 있는데 candidate가 party를 확인하지 못한 경우 date/title만으로 강제 연결
- 다른 CIK의 법인을 자동 추적

이 영역은 cross-CIK entity graph 또는 source-aware verification이 추가되기 전까지 unresolved로 남긴다.
