# tests/test_baseline_codec.py
"""The z-score baseline must survive a snapshot→reload with identical behavior.

This pins river's _from_state contract: Var._from_state(n, m, sig) takes the
VARIANCE as sig, not the raw _S. Storing _S diverges (verified during design).
"""

from river import stats


def _snap(mean: stats.Mean, var: stats.Var) -> dict:
    return {"n": var.mean.n, "mean": mean.get(), "variance": var.get()}


def _load(row: dict) -> tuple[stats.Mean, stats.Var]:
    n = int(row["n"])
    m = stats.Mean._from_state(n, row["mean"])
    v = stats.Var._from_state(n, row["mean"], row["variance"], ddof=1)
    return m, v


def test_snapshot_reload_preserves_zscore():
    orig_m, orig_v = stats.Mean(), stats.Var()
    for x in [10, 12, 11, 13, 9, 14, 8, 11, 12, 10]:
        orig_m.update(x)
        orig_v.update(x)

    new_m, new_v = _load(_snap(orig_m, orig_v))

    # variance reconstructs exactly
    assert abs(new_v.get() - orig_v.get()) < 1e-9
    # the next value's z-score is identical
    test_val = 25.0
    z_orig = abs(test_val - orig_m.get()) / (orig_v.get() ** 0.5)
    z_new = abs(test_val - new_m.get()) / (new_v.get() ** 0.5)
    assert abs(z_orig - z_new) < 1e-9
    # continuing to update stays identical (state, not just a snapshot)
    orig_v.update(test_val)
    new_v.update(test_val)
    assert abs(orig_v.get() - new_v.get()) < 1e-9
