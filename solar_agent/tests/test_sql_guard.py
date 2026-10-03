"""
test_sql_guard.py — Unit tests for the SQL security guardrail in data_tools.py

Tests:
  - Non-SELECT statements are rejected (INSERT, UPDATE, DELETE, DROP, TRUNCATE, etc.)
  - Valid SELECT and WITH...SELECT statements pass
  - Non-allowlisted tables are rejected
  - Allowlisted tables pass
  - Multi-statement injection attempts are rejected
  - Empty SQL is rejected

Run:  pytest tests/test_sql_guard.py -v
"""

from __future__ import annotations

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solar_agent.tools.data_tools import _validate_sql_security, SecurityError


# ---------------------------------------------------------------------------
# Valid statements — must NOT raise
# ---------------------------------------------------------------------------

class TestValidStatements:

    def test_simple_select(self):
        _validate_sql_security("SELECT * FROM plants")

    def test_select_with_where(self):
        _validate_sql_security(
            "SELECT plant_id, plant_name FROM plants WHERE plant_id = 1"
        )

    def test_select_with_join(self):
        _validate_sql_security(
            """
            SELECT dit.log_date, dit.inverter_id, dit.total_daily_yield_kwh
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            WHERE dit.log_date >= '2025-01-01'
            LIMIT 100
            """
        )

    def test_cte_with_select(self):
        _validate_sql_security(
            """
            WITH summary AS (
                SELECT plant_id, SUM(total_daily_yield_kwh) AS total_yield
                FROM daily_inverter_telemetry
                WHERE log_date >= '2025-01-01'
                GROUP BY plant_id
            )
            SELECT * FROM summary ORDER BY total_yield DESC
            """
        )

    def test_all_allowed_tables(self):
        allowed = [
            "plants",
            "blocks",
            "inverters",
            "daily_weather_telemetry",
            "daily_inverter_telemetry",
        ]
        for table in allowed:
            _validate_sql_security(f"SELECT * FROM {table} LIMIT 1")

    def test_aggregate_query(self):
        _validate_sql_security(
            """
            SELECT block_id, AVG(total_daily_yield_kwh) as avg_yield
            FROM daily_inverter_telemetry
            GROUP BY block_id
            HAVING AVG(total_daily_yield_kwh) > 100
            """
        )

    def test_subquery(self):
        _validate_sql_security(
            """
            SELECT *
            FROM (
                SELECT plant_id, MAX(total_solar_radiation_kwh_m2) AS peak_h
                FROM daily_weather_telemetry
                GROUP BY plant_id
            ) sub
            WHERE peak_h > 5.0
            """
        )


# ---------------------------------------------------------------------------
# DML / DDL statements — must raise SecurityError
# ---------------------------------------------------------------------------

class TestRejectedStatements:

    @pytest.mark.parametrize("sql", [
        "INSERT INTO plants (plant_name) VALUES ('Evil Plant')",
        "UPDATE plants SET plant_name = 'hacked' WHERE plant_id = 1",
        "DELETE FROM daily_inverter_telemetry WHERE log_date < '2020-01-01'",
        "DROP TABLE plants",
        "TRUNCATE daily_inverter_telemetry",
        "ALTER TABLE plants ADD COLUMN hacked TEXT",
        "CREATE TABLE evil (id INT)",
        "GRANT ALL PRIVILEGES ON plants TO attacker",
        "REVOKE SELECT ON plants FROM solar_ro",
    ])
    def test_dml_ddl_rejected(self, sql: str):
        with pytest.raises(SecurityError):
            _validate_sql_security(sql)

    def test_empty_sql_rejected(self):
        with pytest.raises(SecurityError):
            _validate_sql_security("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(SecurityError):
            _validate_sql_security("   \n  ")

    def test_semicolon_injection_dml(self):
        """Multi-statement: SELECT followed by DELETE."""
        sql = "SELECT * FROM plants; DELETE FROM plants"
        # sqlglot parses this as two statements; the DELETE must be caught
        with pytest.raises(SecurityError):
            _validate_sql_security(sql)

    def test_copy_command_rejected(self):
        """PostgreSQL COPY is not a SELECT."""
        with pytest.raises(SecurityError):
            _validate_sql_security("COPY plants TO '/tmp/out.csv'")


# ---------------------------------------------------------------------------
# Table allowlist — non-allowlisted tables must raise SecurityError
# ---------------------------------------------------------------------------

class TestTableAllowlist:

    @pytest.mark.parametrize("table", [
        "pg_user",
        "pg_shadow",
        "information_schema.tables",
        "credentials",
        "audit_log",
        "users",
        "secrets",
    ])
    def test_disallowed_table_rejected(self, table: str):
        with pytest.raises(SecurityError, match="allowlist"):
            _validate_sql_security(f"SELECT * FROM {table}")

    def test_disallowed_table_in_subquery(self):
        """Table name in a subquery must also be checked."""
        with pytest.raises(SecurityError):
            _validate_sql_security(
                "SELECT * FROM (SELECT * FROM pg_user) sub"
            )

    def test_disallowed_table_in_cte(self):
        """Table referenced inside a CTE must also be allowlisted."""
        with pytest.raises(SecurityError):
            _validate_sql_security(
                """
                WITH bad AS (SELECT usename FROM pg_user)
                SELECT * FROM bad
                """
            )


# ---------------------------------------------------------------------------
# WITH DML injection — WITH clause wrapping a DML must be caught
# ---------------------------------------------------------------------------

class TestCTEInjection:

    def test_with_insert_rejected(self):
        """
        A CTE that inserts data (a PostgreSQL-supported pattern) must be rejected.
        """
        sql = """
        WITH evil AS (
            INSERT INTO plants (plant_name) VALUES ('hack') RETURNING plant_id
        )
        SELECT * FROM evil
        """
        with pytest.raises(SecurityError):
            _validate_sql_security(sql)

    def test_with_delete_rejected(self):
        sql = """
        WITH removed AS (
            DELETE FROM daily_inverter_telemetry
            WHERE log_date < '2020-01-01'
            RETURNING inverter_id
        )
        SELECT * FROM removed
        """
        with pytest.raises(SecurityError):
            _validate_sql_security(sql)
