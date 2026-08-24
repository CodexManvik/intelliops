"""Tests for IsolationForestCorrelator."""

from datetime import datetime
import random

import pytest

from common.contracts import TelemetryEvent, TelemetryKind
from services.correlation.adapters.isolation_forest_correlator import IsolationForestCorrelator


class TestIsolationForestCorrelator:
    def test_detect_warmup(self):
        correlator = IsolationForestCorrelator(max_samples=5)
        event = TelemetryEvent(
            source="test",
            kind=TelemetryKind.METRIC,
            name="cpu",
            value=0.8,
            ts=datetime.now(),
            fingerprint="test1",
        )
        for i in range(4):
            score = correlator.detect(event)
            assert score == 0.0

    def test_detect_after_warmup_creates_model(self):
        """After warmup, the model should be created for each metric."""
        correlator = IsolationForestCorrelator(max_samples=5)
        
        # Send values with some variation to train the model
        values = [0.45, 0.48, 0.50, 0.52, 0.55, 0.47, 0.49, 0.51, 0.53, 0.50]
        for i, val in enumerate(values):
            event = TelemetryEvent(
                source="test",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=val,
                ts=datetime.now(),
                fingerprint=f"test{i}",
            )
            correlator.detect(event)
        
        # After warmup, there should be a model for the metric
        assert "cpu" in correlator._models
        
        # The model should be an IsolationForest instance
        from sklearn.ensemble import IsolationForest
        assert isinstance(correlator._models["cpu"], IsolationForest)

    def test_score_is_float(self):
        """After model is created, detect should return a float score."""
        correlator = IsolationForestCorrelator(max_samples=5)
        
        # Send enough values to trigger model creation
        for i in range(10):
            event = TelemetryEvent(
                source="test",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=0.5 + (i * 0.02),
                ts=datetime.now(),
                fingerprint=f"test{i}",
            )
            correlator.detect(event)
        
        # Verify the model exists
        assert "cpu" in correlator._models
        
        # Now test that detect returns a float
        new_event = TelemetryEvent(
            source="test",
            kind=TelemetryKind.METRIC,
            name="cpu",
            value=0.60,
            ts=datetime.now(),
            fingerprint="new",
        )
        score = correlator.detect(new_event)
        # Score should be a float (0 is a valid float)
        assert isinstance(score, float)
        # Score should be between 0 and 1
        assert 0.0 <= score <= 1.0

    def test_correlate_creates_situation(self):
        correlator = IsolationForestCorrelator()
        events = [
            TelemetryEvent(
                source="test",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=0.8,
                ts=datetime.now(),
                fingerprint="e1",
            ),
            TelemetryEvent(
                source="test",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=0.9,
                ts=datetime.now(),
                fingerprint="e2",
            ),
        ]
        situation = correlator.correlate(events)
        assert situation.id.startswith("sit-")
        assert len(situation.member_events) == 2
        assert situation.status.value == "detected"

    def test_retrain_updates_reliability(self):
        correlator = IsolationForestCorrelator()
        training_data = [
            {"signature": "sig1", "worked": True},
            {"signature": "sig1", "worked": True},
            {"signature": "sig1", "worked": False},
            {"signature": "sig2", "worked": True},
        ]
        correlator.retrain(training_data)
        assert correlator.reliability("sig1") == 2.0 / 3.0
        assert correlator.reliability("sig2") == 1.0

    def test_snapshot_load_preserves_state(self):
        correlator1 = IsolationForestCorrelator(max_samples=5)
        for i in range(10):
            event = TelemetryEvent(
                source="test",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=0.5 + (i * 0.01),
                ts=datetime.now(),
                fingerprint=f"test{i}",
            )
            correlator1.detect(event)
        snapshot = correlator1.snapshot()
        correlator2 = IsolationForestCorrelator(max_samples=5)
        correlator2.load(snapshot)
        assert len(correlator1._buffers) == len(correlator2._buffers)
        # Both should have the same model type
        assert "cpu" in correlator1._models
        assert "cpu" in correlator2._models

    def test_should_suppress(self):
        correlator = IsolationForestCorrelator()
        correlator._reliability["sig1"] = 0.9
        correlator._reliability["sig2"] = 0.7
        assert correlator.should_suppress("sig1", 0.8) is True
        assert correlator.should_suppress("sig2", 0.8) is False
