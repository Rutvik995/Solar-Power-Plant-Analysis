"""
test_dashboard_endpoints.py
Real-DB integration tests for /api/dashboard/*.

All assertions use Plant 1 / Plant 3, 2026-09-01 to 2026-09-26 (26 days).

Tolerances:
  PR:          ±0.0001
  Energy:      ±5 MWh
  Soiling:     ±0.5 pp
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app

CLIENT = TestClient(app)

START = "2026-09-01"
END   = "2026-09-26"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ov(plant_id=None, start=START, end=END, agg="day"):
    params = {"start": start, "end": end, "agg": agg}
    if plant_id:
        params["plant_id"] = str(plant_id)
    r = CLIENT.get("/api/dashboard/overview", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def plant(plant_id, start=START, end=END):
    r = CLIENT.get(f"/api/dashboard/plant/{plant_id}", params={"start": start, "end": end})
    assert r.status_code == 200, r.text
    return r.json()


def heatmap(plant_id, start=START, end=END):
    r = CLIENT.get(f"/api/dashboard/plant/{plant_id}/heatmap", params={"start": start, "end": end})
    assert r.status_code == 200, r.text
    return r.json()


def inverter(inv_id, start=START, end=END):
    r = CLIENT.get(f"/api/dashboard/inverter/{inv_id}", params={"start": start, "end": end})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1. PR accuracy — must match chat tool value within ±0.0001
# ---------------------------------------------------------------------------

def test_plant1_pr():
    data = ov(plant_id=1)
    p1 = next(p for p in data["plants"] if p["plant_id"] == 1)
    assert p1["pr"] is not None, "PR should not be null for Plant 1"
    assert abs(p1["pr"] - 0.8705) < 0.0001, (
        f"Plant 1 PR = {p1['pr']}, expected ~0.8705 ±0.0001"
    )


# ---------------------------------------------------------------------------
# 2. Energy total for Plant 1 ~4 135 MWh (±5 MWh)
# ---------------------------------------------------------------------------

def test_plant1_energy():
    data = ov(plant_id=1)
    p1 = next(p for p in data["plants"] if p["plant_id"] == 1)
    energy = p1["energy_mwh"]
    assert energy is not None
    assert abs(energy - 4135.0) < 5.0, (
        f"Plant 1 energy = {energy} MWh, expected ~4135 ±5"
    )


# ---------------------------------------------------------------------------
# 3. Fleet PR matches plants-table plant_id=1 PR
# ---------------------------------------------------------------------------

def test_fleet_pr_consistent():
    data = ov(plant_id=1)
    # When filtered to a single plant, fleet_pr == that plant's PR
    assert data["fleet_pr"] is not None
    p1 = next(p for p in data["plants"] if p["plant_id"] == 1)
    assert abs(data["fleet_pr"] - p1["pr"]) < 0.0001


# ---------------------------------------------------------------------------
# 4. Soiling loss Plant 3 ~9.7% (±0.5 pp)
# ---------------------------------------------------------------------------

def test_plant3_soiling():
    data = ov(plant_id=3)
    p3 = next(p for p in data["plants"] if p["plant_id"] == 3)
    sl = p3["soiling_loss_pct"]
    assert sl is not None, "Soiling loss should not be null for Plant 3"
    assert abs(sl - 9.7) < 0.5, (
        f"Plant 3 soiling loss = {sl}%, expected ~9.7 ±0.5"
    )


# ---------------------------------------------------------------------------
# 5. Heatmap — Plant 1 has exactly 20 inverters
# ---------------------------------------------------------------------------

def test_plant1_heatmap_inverter_count():
    data = heatmap(1)
    assert len(data["inverters"]) == 20, (
        f"Expected 20 inverters in Plant 1 heatmap, got {len(data['inverters'])}"
    )
    assert len(data["dates"]) > 0
    assert len(data["pr"]) == len(data["inverters"])
    for row in data["pr"]:
        assert len(row) == len(data["dates"])


# ---------------------------------------------------------------------------
# 6. Zero-gen day P1_Block_A_INV_02, 2026-09-16 → null or 0 in heatmap
# ---------------------------------------------------------------------------

def test_zero_gen_in_heatmap():
    data = heatmap(1, start="2026-09-14", end="2026-09-20")
    inv_id = "P1_Block_A_INV_02"
    if inv_id not in data["inverters"]:
        pytest.skip(f"{inv_id} not found in heatmap (may use different ID format)")
    idx = data["inverters"].index(inv_id)
    try:
        date_idx = data["dates"].index("2026-09-16")
    except ValueError:
        pytest.skip("2026-09-16 not in heatmap date range")
    pr_val = data["pr"][idx][date_idx]
    # Should be null (no data) or zero (zero yield → 0 PR)
    assert pr_val is None or pr_val == 0.0, (
        f"Expected null/0 for zero-gen day, got {pr_val}"
    )


# ---------------------------------------------------------------------------
# 7. Zero-gen day shows as flagged in inverter endpoint
# ---------------------------------------------------------------------------

def test_zero_gen_flagged_in_inverter():
    inv_id = "P1_Block_A_INV_02"
    data = inverter(inv_id, start="2026-09-14", end="2026-09-20")
    assert data["summary"]["flagged_days"] >= 1, (
        f"Expected ≥1 flagged day for {inv_id}, got {data['summary']['flagged_days']}"
    )
    # Check the specific date appears in daily output
    dates_with_data = {row["date"]: row for row in data["daily"]}
    if "2026-09-16" in dates_with_data:
        row = dates_with_data["2026-09-16"]
        yield_val = row.get("yield_kwh") or 0
        assert yield_val <= 1.0, f"Expected ~0 yield on zero-gen day, got {yield_val}"


# ---------------------------------------------------------------------------
# 8. Weekly aggregation returns fewer rows than daily
# ---------------------------------------------------------------------------

def test_weekly_fewer_rows_than_daily():
    daily = ov(plant_id=1, agg="day")
    weekly = ov(plant_id=1, agg="week")
    assert len(weekly["daily"]) < len(daily["daily"]), (
        f"Weekly ({len(weekly['daily'])}) should have fewer rows than daily ({len(daily['daily'])})"
    )


# ---------------------------------------------------------------------------
# 9. Empty range returns valid (empty) response
# ---------------------------------------------------------------------------

def test_empty_range_overview():
    # Pass a range before the database min date
    data = ov(plant_id=1, start="2020-01-01", end="2020-01-10")
    # Should return a valid structure, not an error
    assert "kpis" in data
    assert isinstance(data["plants"], list)
    assert isinstance(data["daily"], list)


def test_empty_range_heatmap():
    data = heatmap(1, start="2020-01-01", end="2020-01-10")
    assert isinstance(data["inverters"], list)
    assert isinstance(data["pr"], list)


def test_empty_range_inverter():
    inv_id = "P1_Block_A_INV_02"
    data = inverter(inv_id, start="2020-01-01", end="2020-01-10")
    assert "summary" in data
    assert isinstance(data["daily"], list)


# ---------------------------------------------------------------------------
# 10. KPI structure — every KPI has value/previous/delta/sparkline
# ---------------------------------------------------------------------------

def test_kpi_structure():
    data = ov(plant_id=1)
    kpis = data["kpis"]
    for key in ["energy_mwh", "pr", "specific_yield", "capacity_factor",
                "soiling_loss_pct", "availability_pct", "open_alerts"]:
        assert key in kpis, f"Missing KPI: {key}"
        blk = kpis[key]
        for field in ("value", "previous", "delta", "sparkline"):
            assert field in blk, f"KPI '{key}' missing field '{field}'"


# ---------------------------------------------------------------------------
# 11. Plant detail — blocks + best/worst + treemap populated
# ---------------------------------------------------------------------------

def test_plant1_detail_structure():
    data = plant(1)
    assert len(data["blocks"]) >= 1
    for blk in data["blocks"]:
        assert "pr" in blk
        assert "energy_mwh" in blk
        assert "inverter_count" in blk
    assert len(data["inverters_best"]) <= 5
    assert len(data["inverters_worst"]) <= 5
    assert len(data["treemap"]) >= 1


# ---------------------------------------------------------------------------
# 12. Clamped_note appears when date is out of DB range
# ---------------------------------------------------------------------------

def test_clamped_note_future_end():
    data = ov(plant_id=1, start=START, end="2099-12-31")
    assert data.get("clamped_note") is not None, (
        "clamped_note should be set when end date is after DB max"
    )
    assert "clamped" in data["clamped_note"].lower()


# ---------------------------------------------------------------------------
# 13. Heatmap max_days cap — long range returns ≤60 dates
# ---------------------------------------------------------------------------

def test_heatmap_max_days_cap():
    # Plant 1, very wide range
    data = heatmap(1, start="2026-01-01", end="2099-12-31")
    assert len(data["dates"]) <= 60, (
        f"Heatmap returned {len(data['dates'])} dates, expected ≤60"
    )


# ---------------------------------------------------------------------------
# 14. Peer median PR in inverter endpoint is sensible
# ---------------------------------------------------------------------------

def test_inverter_peer_median_pr():
    inv_id = "P1_Block_A_INV_01"
    data = inverter(inv_id)
    if not data["peer_median_pr"]:
        pytest.skip("No peer_median_pr data (may be different inverter ID format)")
    for entry in data["peer_median_pr"]:
        assert "date" in entry
        assert "peer_median_pr" in entry
        if entry["peer_median_pr"] is not None:
            assert 0 < entry["peer_median_pr"] < 2, (
                f"Implausible peer_median_pr: {entry['peer_median_pr']}"
            )


# ---------------------------------------------------------------------------
# 15. Inverter 404 for unknown ID
# ---------------------------------------------------------------------------

def test_inverter_404():
    r = CLIENT.get("/api/dashboard/inverter/NONEXISTENT_INV_99999",
                   params={"start": START, "end": END})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 16. Data quality section present and sane
# ---------------------------------------------------------------------------

def test_data_quality_section():
    data = ov(plant_id=1)
    dq = data.get("data_quality", [])
    assert isinstance(dq, list)
    if dq:
        row = dq[0]
        for field in ("plant_id", "rows", "missing_days", "null_counts"):
            assert field in row


# ---------------------------------------------------------------------------
# 17. Health band is green / amber / red
# ---------------------------------------------------------------------------

def test_health_values():
    data = ov()
    for p in data["plants"]:
        assert p["health"] in ("green", "amber", "red"), (
            f"plant {p['plant_id']} health={p['health']!r}"
        )


# ---------------------------------------------------------------------------
# 18. Weather non-null test for Plant 1: ambient_c, module_c, irradiance
# ---------------------------------------------------------------------------

def test_plant1_weather_non_null():
    data = plant(1)
    weather = data.get("weather", [])
    assert len(weather) > 0, "Weather list should not be empty for Plant 1"
    for row in weather:
        assert row["ambient_c"] is not None, f"ambient_c is null in {row}"
        assert row["module_c"] is not None, f"module_c is null in {row}"
        assert row["irradiance"] is not None, f"irradiance is null in {row}"


# ---------------------------------------------------------------------------
# 19. Leaderboard: bottom 5 sorted ascending by PR, ties broken by name
# ---------------------------------------------------------------------------

def test_plant1_leaderboard_sorting():
    data = plant(1)
    worst = data.get("inverters_worst", [])
    assert len(worst) <= 5
    for i in range(len(worst) - 1):
        # PR ascending
        assert worst[i]["pr"] <= worst[i + 1]["pr"]
        if worst[i]["pr"] == worst[i + 1]["pr"]:
            # Tie break by name ascending
            assert worst[i]["inverter_id"] <= worst[i + 1]["inverter_id"]


# ---------------------------------------------------------------------------
# 20. Overview previous period metadata
# ---------------------------------------------------------------------------

def test_overview_previous_period():
    data = ov(start="2026-09-01", end="2026-09-26")
    prev = data.get("previous_period")
    assert prev is not None
    assert "days_covered" in prev
    assert "days_expected" in prev
    assert "has_data" in prev

