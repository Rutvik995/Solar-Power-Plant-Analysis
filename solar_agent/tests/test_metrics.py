"""
test_metrics.py — Unit tests for tools/metrics.py

Design principles:
  - Every test uses synthetic DataFrames with analytically known answers.
  - Tests are independent (no DB, no LLM, no network).
  - Edge cases: NaN, zero denominators, low-irradiance days, standby inverters.
  - Guard behaviours are explicitly tested (e.g., PR = NaN when H ≤ MIN_H).

Run:  pytest tests/test_metrics.py -v
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

# Adjust import path for tests run from the project root
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solar_agent.tools import metrics as m
from solar_agent.config.settings import settings

MIN_H = settings.min_irradiance_kwh_m2
MIN_DC = settings.min_dc_power_kw_for_efficiency
CLIP_FRAC = settings.clipping_threshold_fraction


# ---------------------------------------------------------------------------
# Fixtures — synthetic inverter-day rows
# ---------------------------------------------------------------------------

def _row(
    log_date=date(2025, 7, 1),
    inverter_id=1,
    plant_id=1,
    block_id=1,
    rated_dc_kw=100.0,
    rated_ac_kw=90.0,
    peak_dc_power_kw=80.0,
    peak_ac_power_kw=75.0,
    total_daily_yield_kwh=400.0,
    avg_dc_voltage_v=500.0,
    avg_dc_current_a=100.0,
    total_solar_radiation_kwh_m2=5.0,
    peak_poa_irradiance_w_m2=850.0,
    avg_ambient_temp_c=28.0,
    max_module_temp_c=55.0,
    soiling_ratio=0.97,
    inverter_status_code=0,
) -> dict:
    return dict(
        log_date=log_date,
        inverter_id=inverter_id,
        plant_id=plant_id,
        block_id=block_id,
        rated_dc_kw=rated_dc_kw,
        rated_ac_kw=rated_ac_kw,
        peak_dc_power_kw=peak_dc_power_kw,
        peak_ac_power_kw=peak_ac_power_kw,
        total_daily_yield_kwh=total_daily_yield_kwh,
        avg_dc_voltage_v=avg_dc_voltage_v,
        avg_dc_current_a=avg_dc_current_a,
        total_solar_radiation_kwh_m2=total_solar_radiation_kwh_m2,
        peak_poa_irradiance_w_m2=peak_poa_irradiance_w_m2,
        avg_ambient_temp_c=avg_ambient_temp_c,
        max_module_temp_c=max_module_temp_c,
        soiling_ratio=soiling_ratio,
        inverter_status_code=inverter_status_code,
    )


def _df(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


# ---------------------------------------------------------------------------
# 1. Performance Ratio
# ---------------------------------------------------------------------------

class TestPerformanceRatio:

    def test_basic_pr(self):
        """PR = yield / (rated_dc_kw × H) for a normal day."""
        df = _df(_row(total_daily_yield_kwh=400.0, rated_dc_kw=100.0,
                      total_solar_radiation_kwh_m2=5.0))
        result = m.compute_performance_ratio(df)
        # Expected: 400 / (100 × 5) = 0.80
        assert math.isclose(result.iloc[0]["performance_ratio"], 0.80, rel_tol=1e-6)

    def test_pr_nan_on_low_irradiance(self):
        """PR must be NaN when H ≤ MIN_H (BR-01)."""
        df = _df(_row(total_solar_radiation_kwh_m2=MIN_H))  # exactly at boundary
        result = m.compute_performance_ratio(df)
        assert pd.isna(result.iloc[0]["performance_ratio"]), \
            "PR should be NaN when H == MIN_H (not strictly greater)"

    def test_pr_valid_above_min_h(self):
        """PR is computed when H > MIN_H."""
        df = _df(_row(total_solar_radiation_kwh_m2=MIN_H + 0.01,
                      total_daily_yield_kwh=50.0, rated_dc_kw=100.0))
        result = m.compute_performance_ratio(df)
        assert not pd.isna(result.iloc[0]["performance_ratio"])

    def test_pr_does_not_mutate_input(self):
        """compute_performance_ratio must not modify the original DataFrame."""
        df = _df(_row())
        original_cols = set(df.columns)
        m.compute_performance_ratio(df)
        assert set(df.columns) == original_cols

    def test_pr_zero_rated_dc(self):
        """PR is NaN when rated_dc_kw = 0 (division by zero guard)."""
        df = _df(_row(rated_dc_kw=0.0, total_solar_radiation_kwh_m2=5.0))
        result = m.compute_performance_ratio(df)
        assert pd.isna(result.iloc[0]["performance_ratio"])


class TestAggregatePR:

    def test_aggregate_pr_is_not_average_of_pr(self):
        """
        Block PR must be sum(yields) / sum(rated_dc × H), NOT mean(PR).
        Test with two inverters having different capacities.
        """
        rows = [
            _row(inverter_id=1, rated_dc_kw=100.0,
                 total_daily_yield_kwh=400.0,
                 total_solar_radiation_kwh_m2=5.0),  # PR = 0.80
            _row(inverter_id=2, rated_dc_kw=50.0,
                 total_daily_yield_kwh=175.0,
                 total_solar_radiation_kwh_m2=5.0),  # PR = 0.70
        ]
        df = _df(*rows)
        result = m.aggregate_pr(df, group_cols=["block_id"])
        # Correct: (400+175) / (100*5 + 50*5) = 575 / 750 = 0.7667
        # Wrong (naive avg): (0.80 + 0.70) / 2 = 0.75
        expected = 575 / 750
        assert math.isclose(result.iloc[0]["performance_ratio"], expected, rel_tol=1e-5)

    def test_aggregate_pr_excludes_low_irradiance(self):
        """aggregate_pr must exclude low-irradiance days."""
        rows = [
            _row(inverter_id=1, total_daily_yield_kwh=400.0,
                 total_solar_radiation_kwh_m2=5.0),   # valid
            _row(inverter_id=1, log_date=date(2025, 7, 2),
                 total_daily_yield_kwh=10.0,
                 total_solar_radiation_kwh_m2=0.1),   # low irradiance — exclude
        ]
        df = _df(*rows)
        result = m.aggregate_pr(df, group_cols=["block_id"])
        # Only the valid day is counted: 400 / (100*5) = 0.80
        assert math.isclose(result.iloc[0]["performance_ratio"], 0.80, rel_tol=1e-5)


# ---------------------------------------------------------------------------
# 2. Specific Yield
# ---------------------------------------------------------------------------

class TestSpecificYield:

    def test_basic(self):
        df = _df(_row(total_daily_yield_kwh=400.0, rated_dc_kw=100.0))
        result = m.compute_specific_yield(df)
        assert math.isclose(result.iloc[0]["specific_yield_kwh_kwp"], 4.0, rel_tol=1e-6)

    def test_zero_capacity(self):
        df = _df(_row(rated_dc_kw=0.0))
        result = m.compute_specific_yield(df)
        assert pd.isna(result.iloc[0]["specific_yield_kwh_kwp"])


# ---------------------------------------------------------------------------
# 3. Capacity Factor
# ---------------------------------------------------------------------------

class TestCapacityFactor:

    def test_basic(self):
        """CF = yield / (rated_ac_kw × 24)."""
        df = _df(_row(total_daily_yield_kwh=360.0, rated_ac_kw=90.0))
        result = m.compute_capacity_factor(df)
        # 360 / (90 × 24) = 360 / 2160 = 0.1667
        assert math.isclose(result.iloc[0]["capacity_factor"], 360 / 2160, rel_tol=1e-5)

    def test_zero_ac_rating(self):
        df = _df(_row(rated_ac_kw=0.0))
        result = m.compute_capacity_factor(df)
        assert pd.isna(result.iloc[0]["capacity_factor"])


# ---------------------------------------------------------------------------
# 4. Inverter Efficiency
# ---------------------------------------------------------------------------

class TestInverterEfficiency:

    def test_basic_efficiency(self):
        """Efficiency = peak_ac / peak_dc when dc > MIN_DC."""
        df = _df(_row(peak_ac_power_kw=75.0, peak_dc_power_kw=80.0))
        result = m.compute_inverter_efficiency(df)
        assert math.isclose(result.iloc[0]["inverter_efficiency"], 75.0 / 80.0, rel_tol=1e-6)

    def test_low_dc_excluded(self):
        """Efficiency is NaN when peak_dc ≤ MIN_DC."""
        df = _df(_row(peak_dc_power_kw=MIN_DC - 0.001))
        result = m.compute_inverter_efficiency(df)
        assert pd.isna(result.iloc[0]["inverter_efficiency"])

    def test_sensor_error_clamped(self):
        """Efficiency > 1.0 (e.g. sensor error) should be NaN."""
        df = _df(_row(peak_ac_power_kw=100.0, peak_dc_power_kw=80.0))  # eff = 1.25
        result = m.compute_inverter_efficiency(df)
        assert pd.isna(result.iloc[0]["inverter_efficiency"]), \
            "Efficiency > 1.0 should be clamped to NaN"

    def test_very_low_efficiency_clamped(self):
        """Efficiency < 0.5 (faulty sensor reading) should be NaN."""
        df = _df(_row(peak_ac_power_kw=10.0, peak_dc_power_kw=80.0))  # eff = 0.125
        result = m.compute_inverter_efficiency(df)
        assert pd.isna(result.iloc[0]["inverter_efficiency"])


# ---------------------------------------------------------------------------
# 5. DC/AC Loading & Clipping
# ---------------------------------------------------------------------------

class TestDcAcLoading:

    def test_loading_ratio(self):
        df = _df(_row(rated_dc_kw=120.0, rated_ac_kw=100.0))
        result = m.compute_dc_ac_loading(df)
        assert math.isclose(result.iloc[0]["dc_ac_loading_ratio"], 1.20, rel_tol=1e-6)

    def test_clipping_flag_true(self):
        """Clipping flagged when peak_ac >= CLIP_FRAC × rated_ac."""
        df = _df(_row(peak_ac_power_kw=89.0, rated_ac_kw=90.0))  # 98.9% → clipping
        result = m.compute_dc_ac_loading(df)
        assert result.iloc[0]["is_clipping"] == True

    def test_clipping_flag_false(self):
        """No clipping when peak_ac < CLIP_FRAC × rated_ac."""
        df = _df(_row(peak_ac_power_kw=80.0, rated_ac_kw=90.0))  # 88.9% → no clipping
        result = m.compute_dc_ac_loading(df)
        assert result.iloc[0]["is_clipping"] == False

    def test_clipping_boundary(self):
        """Exactly at CLIP_FRAC → clipping."""
        threshold = CLIP_FRAC * 90.0  # = 88.2
        df = _df(_row(peak_ac_power_kw=threshold, rated_ac_kw=90.0))
        result = m.compute_dc_ac_loading(df)
        assert result.iloc[0]["is_clipping"] == True


# ---------------------------------------------------------------------------
# 6. DC Power Consistency
# ---------------------------------------------------------------------------

class TestDcPowerConsistency:

    def test_consistent(self):
        """avg_dc_voltage × avg_dc_current / 1000 within 20% of peak_dc → OK."""
        # Reconstructed: 500V × 100A / 1000 = 50 kW
        # peak_dc: 60 kW → discrepancy = 10 kW = 16.7% → OK
        df = _df(_row(avg_dc_voltage_v=500.0, avg_dc_current_a=100.0, peak_dc_power_kw=60.0))
        result = m.compute_dc_power_consistency(df)
        assert result.iloc[0]["dc_power_ok"] == True

    def test_inconsistent(self):
        """Reconstructed power wildly off → dc_power_ok = False."""
        # Reconstructed: 500V × 10A / 1000 = 5 kW; peak = 80 kW → huge discrepancy
        df = _df(_row(avg_dc_voltage_v=500.0, avg_dc_current_a=10.0, peak_dc_power_kw=80.0))
        result = m.compute_dc_power_consistency(df)
        assert result.iloc[0]["dc_power_ok"] == False


# ---------------------------------------------------------------------------
# 7. Soiling Loss
# ---------------------------------------------------------------------------

class TestSoilingLoss:

    def test_soiling_loss_pct(self):
        """soiling_ratio=0.97 → 3% loss."""
        df = _df(_row(soiling_ratio=0.97, total_daily_yield_kwh=400.0))
        result = m.compute_soiling_loss(df)
        assert math.isclose(result.iloc[0]["soiling_loss_pct"], 3.0, rel_tol=1e-5)

    def test_clean_panel_no_loss(self):
        """soiling_ratio=1.0 → 0% loss, 0 kWh lost."""
        df = _df(_row(soiling_ratio=1.0, total_daily_yield_kwh=400.0))
        result = m.compute_soiling_loss(df)
        assert math.isclose(result.iloc[0]["soiling_loss_pct"], 0.0, abs_tol=1e-9)
        assert math.isclose(result.iloc[0]["soiling_energy_loss_kwh"], 0.0, abs_tol=1e-9)

    def test_energy_loss(self):
        """soiling_ratio=0.95, yield=400 → loss = 400 × (1/0.95 - 1) = 21.05 kWh."""
        df = _df(_row(soiling_ratio=0.95, total_daily_yield_kwh=400.0))
        result = m.compute_soiling_loss(df)
        expected_loss = 400.0 * (1 / 0.95 - 1)
        assert math.isclose(result.iloc[0]["soiling_energy_loss_kwh"], expected_loss, rel_tol=1e-5)

    def test_invalid_soiling_ratio(self):
        """soiling_ratio = 0 → NaN (guard: avoid division by zero)."""
        df = _df(_row(soiling_ratio=0.0))
        result = m.compute_soiling_loss(df)
        assert pd.isna(result.iloc[0]["soiling_loss_pct"])

    def test_soiling_ratio_above_1_nan(self):
        """soiling_ratio > 1.0 is physically impossible → NaN."""
        df = _df(_row(soiling_ratio=1.05))
        result = m.compute_soiling_loss(df)
        assert pd.isna(result.iloc[0]["soiling_loss_pct"])


# ---------------------------------------------------------------------------
# 8. Peer Deviation Score
# ---------------------------------------------------------------------------

class TestPeerDeviation:

    def _make_block_df(self, prs: list[float]) -> pd.DataFrame:
        """Helper: create a block of inverters on a single day with given PRs."""
        rows = []
        for i, pr in enumerate(prs):
            # Reverse-engineer yield: yield = PR × rated_dc_kw × H
            # Use rated_dc_kw=100, H=5 so yield = PR × 500
            rows.append(_row(
                inverter_id=i + 1,
                block_id=1,
                total_daily_yield_kwh=pr * 100.0 * 5.0,
                rated_dc_kw=100.0,
                total_solar_radiation_kwh_m2=5.0,
            ))
        df = _df(*rows)
        df = m.compute_performance_ratio(df)
        return df

    def test_normal_inverter_score_near_zero(self):
        """An inverter at the median should have a score close to 0."""
        prs = [0.80, 0.80, 0.80, 0.79, 0.81]  # nearly identical
        df = self._make_block_df(prs)
        result = m.compute_peer_deviation(df)
        # Inverter 0 has PR=0.80, which is the median → score ≈ 0
        score = result.iloc[0]["peer_deviation_score"]
        assert abs(score) < 0.5, f"Expected score near 0, got {score}"

    def test_underperforming_inverter_negative_score(self):
        """
        An inverter with PR far below peers should have a strong negative score.

        We use a spread distribution so MAD > 0:
        prs = [0.82, 0.80, 0.78, 0.81, 0.79, 0.20]
        median ≈ 0.795, MAD = 1.4826 × median(|pr - median|) > 0,
        so the outlier at 0.20 gets a large negative z-score.
        """
        prs = [0.82, 0.80, 0.78, 0.81, 0.79, 0.20]  # varied + one bad outlier
        df = self._make_block_df(prs)
        result = m.compute_peer_deviation(df)
        # Last inverter (index 5) has PR=0.20, far from block median ~0.795
        bad_score = result.iloc[5]["peer_deviation_score"]
        assert bad_score < -settings.peer_underperformance_z_threshold, \
            f"Expected score < -{settings.peer_underperformance_z_threshold}, got {bad_score}"

    def test_requires_performance_ratio_column(self):
        """compute_peer_deviation raises ValueError if performance_ratio is missing."""
        df = _df(_row())
        with pytest.raises(ValueError, match="performance_ratio"):
            m.compute_peer_deviation(df)

    def test_single_inverter_block_nan(self):
        """A block with only one inverter has no peers → score should be NaN."""
        prs = [0.80]
        df = self._make_block_df(prs)
        result = m.compute_peer_deviation(df)
        assert pd.isna(result.iloc[0]["peer_deviation_score"])

    def test_all_identical_pr_score_zero(self):
        """If all PRs are identical, MAD = 0 and scores should be 0 (not NaN)."""
        prs = [0.80, 0.80, 0.80]
        df = self._make_block_df(prs)
        result = m.compute_peer_deviation(df)
        for score in result["peer_deviation_score"]:
            assert not pd.isna(score)
            assert score == 0.0

    def test_cross_block_isolation(self):
        """
        Peer deviation is computed within the same (log_date, block_id).
        Inverters in different blocks must not influence each other's scores.
        """
        rows = [
            # Block 1: healthy inverters
            _row(inverter_id=1, block_id=1, total_daily_yield_kwh=0.80*100*5,
                 rated_dc_kw=100.0, total_solar_radiation_kwh_m2=5.0),
            _row(inverter_id=2, block_id=1, total_daily_yield_kwh=0.80*100*5,
                 rated_dc_kw=100.0, total_solar_radiation_kwh_m2=5.0),
            _row(inverter_id=3, block_id=1, total_daily_yield_kwh=0.80*100*5,
                 rated_dc_kw=100.0, total_solar_radiation_kwh_m2=5.0),
            # Block 2: one very underperforming inverter (should NOT affect block 1)
            _row(inverter_id=4, block_id=2, total_daily_yield_kwh=0.20*100*5,
                 rated_dc_kw=100.0, total_solar_radiation_kwh_m2=5.0),
            _row(inverter_id=5, block_id=2, total_daily_yield_kwh=0.80*100*5,
                 rated_dc_kw=100.0, total_solar_radiation_kwh_m2=5.0),
            _row(inverter_id=6, block_id=2, total_daily_yield_kwh=0.80*100*5,
                 rated_dc_kw=100.0, total_solar_radiation_kwh_m2=5.0),
        ]
        df = _df(*rows)
        df = m.compute_performance_ratio(df)
        result = m.compute_peer_deviation(df)

        # Block 1 inverters should all be near 0
        block1 = result[result["block_id"] == 1]
        for score in block1["peer_deviation_score"]:
            assert abs(score) < 1.0, f"Block 1 score should be near 0, got {score}"


# ---------------------------------------------------------------------------
# 9. Expected Yield
# ---------------------------------------------------------------------------

class TestExpectedYield:

    def test_expected_yield(self):
        df = _df(_row(rated_dc_kw=100.0, total_solar_radiation_kwh_m2=5.0))
        result = m.compute_expected_yield(df)
        assert math.isclose(result.iloc[0]["expected_yield_kwh"], 500.0, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# 10. compute_all_kpis (smoke test)
# ---------------------------------------------------------------------------

class TestComputeAllKpis:

    def test_all_kpis_adds_expected_columns(self):
        df = _df(
            _row(inverter_id=1), _row(inverter_id=2, total_daily_yield_kwh=350.0)
        )
        result = m.compute_all_kpis(df)
        expected_cols = [
            "expected_yield_kwh",
            "performance_ratio",
            "specific_yield_kwh_kwp",
            "capacity_factor",
            "inverter_efficiency",
            "dc_ac_loading_ratio",
            "is_clipping",
            "reconstructed_dc_power_kw",
            "soiling_loss_pct",
            "soiling_energy_loss_kwh",
            "peer_deviation_score",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_all_kpis_empty_df(self):
        """compute_all_kpis should not crash on an empty DataFrame."""
        df = pd.DataFrame(columns=_df(_row()).columns)
        result = m.compute_all_kpis(df)
        assert result.empty


# ---------------------------------------------------------------------------
# 11. aggregate_kpis
# ---------------------------------------------------------------------------

class TestAggregateKpis:

    def test_block_level_aggregation(self):
        """Block-level yield is the sum of inverter yields."""
        rows = [
            _row(inverter_id=1, block_id=1, total_daily_yield_kwh=400.0),
            _row(inverter_id=2, block_id=1, total_daily_yield_kwh=380.0),
            _row(inverter_id=3, block_id=2, total_daily_yield_kwh=300.0),
        ]
        df = _df(*rows)
        df = m.compute_all_kpis(df)
        result = m.aggregate_kpis(df, level="block", period="total")

        block1 = result[result["block_id"] == 1]
        assert math.isclose(block1.iloc[0]["total_daily_yield_kwh"], 780.0, rel_tol=1e-5)

        block2 = result[result["block_id"] == 2]
        assert math.isclose(block2.iloc[0]["total_daily_yield_kwh"], 300.0, rel_tol=1e-5)

    def test_plant_level_pr_not_average(self):
        """Plant PR is correctly computed from sums, not averaged."""
        rows = [
            # rated_dc=100, H=5 → expected=500; yield=400 → PR=0.80
            _row(inverter_id=1, block_id=1, rated_dc_kw=100.0,
                 total_daily_yield_kwh=400.0, total_solar_radiation_kwh_m2=5.0),
            # rated_dc=200, H=5 → expected=1000; yield=700 → PR=0.70
            _row(inverter_id=2, block_id=1, rated_dc_kw=200.0,
                 total_daily_yield_kwh=700.0, total_solar_radiation_kwh_m2=5.0),
        ]
        df = _df(*rows)
        df = m.compute_all_kpis(df)
        result = m.aggregate_kpis(df, level="plant", period="total")
        # Correct: 1100 / 1500 = 0.7333
        expected_pr = 1100.0 / 1500.0
        actual_pr = result.iloc[0]["performance_ratio"]
        assert math.isclose(actual_pr, expected_pr, rel_tol=1e-5), \
            f"Expected PR={expected_pr:.4f}, got {actual_pr:.4f}"
