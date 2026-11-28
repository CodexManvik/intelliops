# Detector benchmark

Five seeded, labelled streams scored by all three detectors. The streams are in
[`testdata/benchmark.json`](../testdata/benchmark.json) and
`crates/correlation/tests/benchmark.rs` runs them:

```bash
cargo test -p intelliops-correlation --test benchmark -- --nocapture
```

The online z-score and robust detectors flag exactly the events the Python
versions did. The isolation forest's trees differ from scikit-learn's, and the
test holds it within a small tolerance of the original.

| Stream | Detector | Precision | Recall | False-positive rate |
|---|---|---|---|---|
| normal_noise | z-score | — | — | 0.004 |
| normal_noise | robust | — | — | 0.003 |
| normal_noise | trained | — | — | 0.014 |
| seasonal_cycle | z-score | — | — | 0.014 |
| seasonal_cycle | robust | — | — | 0.018 |
| seasonal_cycle | trained | — | — | 0.007 |
| point_anomaly | z-score | 1.000 | 1.000 | 0.000 |
| point_anomaly | robust | 0.750 | 1.000 | 0.004 |
| point_anomaly | trained | 0.667 | 1.000 | 0.006 |
| sustained_anomaly | z-score | 0.976 | 0.410 | 0.001 |
| sustained_anomaly | robust | 0.482 | 0.530 | 0.063 |
| sustained_anomaly | trained | 0.491 | 0.550 | 0.063 |
| correlation_break | z-score | 0.950 | 0.380 | 0.001 |
| correlation_break | robust | 0.448 | 0.640 | 0.053 |
| correlation_break | trained | 0.467 | 0.640 | 0.049 |

(Values for the original Python detectors; the trained row is scikit-learn's.)

## Reading it

- The robust detector finds more of a sustained shift and a broken correlation,
  at a much higher false-positive rate. It is a trade-off, not an upgrade.
- On the seasonal stream the robust detector does **not** beat the plain
  z-score (0.018 against 0.014). An earlier version of this page claimed it was
  about seven times better; the current results do not show that.
- These are synthetic streams, each isolating one failure mode. They are not a
  claim about noise reduction on real traffic.
