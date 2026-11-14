# ApiScope Empirical Evaluation Report

**Experiment Executed At:** 2026-11-13 09:44:23 UTC  
**Target Systems:**  
1. `sample_app` (Ecommerce reference target with 12 planted architectural bugs)  
2. `StockPile` (Clean enterprise asset & surplus management baseline API)  

---

## 1. Executive Summary

This empirical evaluation benchmark compares **four API testing and diagnostic strategies**
across 12 planted bugs (`BUG-01` through `BUG-12`) representing security vulnerabilities,
contract deviations, boundary errors, state machine violations, latency drifts,
swallowed exceptions, and zombie endpoints.

| Metric | Strategy 1: Handwritten Probes | Strategy 2: Rule-Based (M5) | Strategy 3: Rule + AI (M5+M12) | Strategy 4: Full ApiScope Combined |
|---|---|---|---|---|
| **Total Tests Executed** | 16 | 70 | 74 | 90 |
| **Duration (ms)** | 189.86 ms | 462.64 ms | 527.32 ms | 763.08 ms |
| **Spec Endpoint Coverage** | 86.67% (13/15) | 100.0% (15/15) | 100.0% (15/15) | 100.0% (15/15) |
| **Planted Bugs Detected** | **10 / 12** | **7 / 12** | **10 / 12** | **12 / 12** |
| **Bug Detection Rate** | **83.33%** | **58.33%** | **83.33%** | **100.0%** |
| **False Positives** | **0** | **0** | **0** | **0** |

---

## 2. Per-Bug Detection Matrix (`BUG-01` to `BUG-12`)

| Bug ID | Vulnerability / Defect Description | S1: Hand | S2: Rule | S3: Rule+AI | S4: Combined |
|---|---|:---:|:---:|:---:|:---:|
| `BUG-01` | DELETE /products/{id} without auth returns 204 instead of 401 | [x] | [x] | [x] | [x] |
| `BUG-02` | IDOR: User 1 accesses User 2 profile (HTTP 200 instead of 403) | [x] | [ ] | [x] | [x] |
| `BUG-03` | Single-use coupon reused on repeat redemption | [x] | [ ] | [x] | [x] |
| `BUG-04` | Negative order quantity accepted (boundary failure) | [x] | [x] | [x] | [x] |
| `BUG-05` | Shipped order cancelled illegally (state transition failure) | [x] | [ ] | [x] | [x] |
| `BUG-06` | Order creation returns 200 OK instead of 201 Created | [x] | [x] | [x] | [x] |
| `BUG-07` | Missing required price on product creation accepted | [x] | [x] | [x] | [x] |
| `BUG-08` | Latency drift / artificial delay on /products/recommendations | [x] | [x] | [x] | [x] |
| `BUG-09` | User profile leaks internal_auth_hash (contract schema leakage) | [x] | [x] | [x] | [x] |
| `BUG-10` | Swallowed billing exception on /orders/{id}/sync-invoice | [x] | [x] | [x] | [x] |
| `BUG-11` | Undocumented endpoint /debug/internal-metrics in active traffic | [ ] | [ ] | [ ] | [x] |
| `BUG-12` | Unused /coupons/legacy-archive and deprecated /products/v1-catalog | [ ] | [ ] | [ ] | [x] |

---

## 3. Analysis & Key Findings

1. **Rule-Based Generation (Strategy 2)**:
   - Automatically achieves full OpenAPI spec coverage without manual test scripting.
   - Reliably detects boundary violations (`BUG-04`), unauthenticated operations (`BUG-01`),
     status code mismatches (`BUG-06`), missing required body fields (`BUG-07`), and schema leakage (`BUG-09`).
   - Misses semantic state invariants (`BUG-05`), IDOR permissions (`BUG-02`), and single-use logic (`BUG-03`).

2. **Project-Aware AI Generation (Strategy 3)**:
   - Synthesizes domain-aware test scenarios guided by the declarative `ProjectProfile` YAML.
   - Uncovers IDOR on user profiles (`BUG-02`), coupon reuse (`BUG-03`), and shipped order cancellation (`BUG-05`),
     elevating automated detection to **83.33%**.

3. **Full ApiScope Combined Pipeline (Strategy 4)**:
   - Combines dynamic test suite execution (M4, M5, M12) with runtime analytical observability:
     * **Zombie API Detector (M9)** flags the undocumented `/debug/internal-metrics` endpoint (`BUG-11`)
       and unused `/coupons/legacy-archive` / deprecated `/products/v1-catalog` endpoints (`BUG-12`).
     * **Silent Failure Detector (M10)** correlates `app.log` errors via `X-Correlation-ID` (`BUG-10`)
       and diagnoses latency drift (`BUG-08`).
   - Reaches **100.0% (12/12) planted bug detection** with **0 false positives**.

---

## 4. Module Health Verdicts (`sample_app`)

- **Module `coupons`**: `UNHEALTHY`
- **Module `e-commerce`**: `UNHEALTHY`
- **Module `health`**: `HEALTHY WITH WARNINGS`
- **Module `orders`**: `INCOMPLETE`
- **Module `products`**: `INCOMPLETE`
- **Module `users`**: `UNHEALTHY`

---

## 5. Clean Target Specificity Verification (`StockPile`)

To verify that ApiScope's diagnostic engines do not generate false positives against compliant software:
- **System**: StockPile Enterprise Asset & Surplus Management
- **Endpoints Evaluated**: 6
- **Automated Tests Executed**: 13
- **False Positives Detected**: **0**
- **Clean Module Verdicts**:
- **Module `assets`**: `HEALTHY`
- **Module `health`**: `HEALTHY`
- **Module `surplus`**: `HEALTHY`

**Conclusion:** ApiScope exhibits high specificity (0% false positive rate) on conforming APIs
while providing 100% sensitivity on planted security and contract defects.
