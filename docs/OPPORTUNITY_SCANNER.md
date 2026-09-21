# Opportunity Scanner v0

## Purpose

Do not choose a revenue model such as Bags, token issuance, or trading first and optimize it later.

Money-flow mechanism discovery
→ independent revenue evidence
→ cheap falsifiable experiment
→ preserve separate axes
→ Pareto frontier
→ update with real cash P&L

The scanner chooses the next experiment worth running, not a business winner.

## Facts and hypotheses stay separate

The JSONL input separates:

- facts: observable items such as independent revenue-signal count
- hypotheses: priors for demand, automation fit, repeatability, distribution, upside
- validation: cash cost, human time, feedback delay, pass and kill conditions
- risks: capital, platform-policy, and legal risk
- signals: source URLs and the observed claim

The default selector does not collapse these into one opaque score. It builds Pareto layers over demand strength, evidence quality, automation fit, distribution access, cheap cash validation, feedback speed, and safety.

Within a Pareto frontier it uses an explicit lexicographic tie-break: weakest-link feasibility, direct revenue evidence, cash ease, then feedback speed.

## First scan: 2026-09-21

The seed file contains five mechanisms:

1. AI order-intake plus exception handling
2. E-commerce automation reliability watchdog
3. Polymarket shadow market making
4. Authorized bug-bounty agent
5. Creator-fee token factory

The first scan puts order-intake/exception handling at the front of the immediate validation queue. This is not a claim that it is the globally best business. It means the current evidence is stronger and the hypothesis is cheap enough to falsify.

The important product wedge is not another generic PDF parser. It is an exception-first order-operations layer focused on SKU/pricing/inventory mismatches, missing fields, duplicate orders, approvals, and audit trails.

Polymarket stays in the parallel queue because maker rebates plus separate liquidity rewards can be tested with a zero-capital shadow simulation.

Bug bounty stays in the queue, but only after a historical benchmark on already disclosed vulnerabilities. Live testing must remain inside explicitly authorized scope.

Token issuance stays in the queue, but fee schedules are not proof of profitability. Recent launch cohorts should be measured before batch issuance.

## Run

python -m opportunity_scanner --input examples/opportunities_2026-09-21.jsonl --limit 3 --max-cash 500 --max-hours 24 --output output/opportunity_scanner/latest.md

The default minimum independent revenue-signal count is two.

## Next implementation steps

1. Marketplace evidence ingestion
2. Polymarket shadow-PnL recorder
3. Token launch cohort collector
4. Historical bounty benchmark runner

The final winner is determined by validated economic profit, not by prior scores.


## First experiment implementation

The branch also includes a deterministic exception-first order-intake demo.

An LLM or document parser may extract fields upstream, but the final decision to create a draft order is gated by ordinary code. The demo checks:

- duplicate purchase-order numbers
- unknown or missing SKUs
- invalid quantities
- catalog-price mismatches
- insufficient inventory
- missing customer/currency/line data

Run a clean fixture:

    python -m opportunity_scanner.order_intake_demo --order examples/order_intake/po_clean.json --catalog examples/order_intake/catalog.json

Run the exception fixture:

    python -m opportunity_scanner.order_intake_demo --order examples/order_intake/po_bad.json --catalog examples/order_intake/catalog.json --seen-pos examples/order_intake/seen_po_numbers.json

The bad fixture is expected to exit with code 2 and return needs_review. That is intentional: uncertain or inconsistent orders are quarantined instead of being written into Shopify/ERP.
