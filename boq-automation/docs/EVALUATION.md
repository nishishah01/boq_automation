# Matching evaluation (engine mode: mock)

Component accuracy here is exact Item No. agreement. An item an engineer would accept but did not choose counts as wrong. Scores are heuristic until calibrated.

This is the rule engine alone. Claude has not been evaluated, because no API key was available. Several rows counted as wrong below are defensible: `PIPE INSULATION` was added because the line says "with Insulation", as the brief requires, although these engineers priced insulation into the pipe item and added no child. `ACOUSTIC INSULATION 10 MM RUBBER` and `ALUMINIUM LINEAR GRILL 150 MM` are arguably closer to the line than the generic items chosen. The clear misses are synonym cases such as copper piping for `REFRIGERANT PIPING`.

## Sample set (Highside)

Ground truth written by the developer against real master records; not engineer-verified. The rules were tuned while building this set, so treat this as a regression check, not a measure of accuracy.

| Metric | Value |
|---|---|
| Sales lines | 32 |
| Expected components | 32 |
| Selected components | 32 |
| Correct | 32 |
| Precision | 100.0% |
| Recall | 100.0% |
| Exact Item No. accuracy | 100.0% |
| Parent-description accuracy | 100.0% |
| Cost Sheet Type accuracy | 100.0% |
| False positives | 0 |
| Missed components | 0 |

## Real BOQ holdout (all four cost sheet types)

Engineers' own CO- selections in the supplied BOQ, hidden from the engine. This is the only human-verified set, and it is small: use it as a baseline, not a forecast.

| Metric | Value |
|---|---|
| Sales lines | 15 |
| Expected components | 15 |
| Selected components | 19 |
| Correct | 11 |
| Precision | 57.9% |
| Recall | 73.3% |
| Exact Item No. accuracy | 47.8% |
| Parent-description accuracy | 53.3% |
| Cost Sheet Type accuracy | 100.0% |
| False positives | 8 |
| Missed components | 4 |

False positives:

- 1.3: CO-14644 COPPER PIPING
- 1.3: CO-10081 PIPE INSULATION
- 1.5.1: CO-10081 PIPE INSULATION
- 1.5.2: CO-10081 PIPE INSULATION
- 1.6.1: CO-11812 TABLE TOP STAND 14 HP
- 1.11.1: CO-09603 INSTALLATON OF PLENUM
- 1.11.2: CO-12969 ACOUSTIC INSULATION 10 MM RUBBER
- 1.11.5: CO-17993 ALUMINIUM LINEAR GRILL 150 MM

Missed:

- 1.3: expected CO-03255
- 1.6.1: expected CO-02803
- 1.11.2: expected CO-00134
- 1.11.5: expected CO-11616
