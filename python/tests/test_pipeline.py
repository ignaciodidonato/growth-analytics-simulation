"""Invariantes del pipeline sobre la base ya generada (correr despues de
generate_data.py y analysis.py)."""
import os
import sqlite3

import pytest

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "teleterapia.db")


@pytest.fixture(scope="module")
def conn():
    if not os.path.exists(DB_PATH):
        pytest.skip("base no generada: correr generate_data.py y analysis.py")
    c = sqlite3.connect(DB_PATH)
    yield c
    c.close()


def scalar(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_rates_are_proportions(conn):
    for table, cols in {
        "funnel_conversion": ["tasa_registro_a_activacion", "tasa_activacion_a_pago", "tasa_registro_a_pago"],
        "channel_metrics": ["churn_mensual"],
        "ab_test_results": ["conversion_rate", "p_value"],
        "state_channel_metrics": ["tasa_registro_a_pago"],
    }.items():
        for col in cols:
            assert scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE {col} < 0 OR {col} > 1") == 0, (table, col)


def test_paying_customers_consistent_across_marts(conn):
    total_subs = scalar(conn, "SELECT COUNT(*) FROM subscriptions")
    assert scalar(conn, "SELECT SUM(clientes_pagos) FROM channel_metrics") == total_subs
    assert scalar(conn, "SELECT SUM(pago) FROM funnel_conversion") == total_subs
    assert scalar(conn, "SELECT SUM(clientes_pagos) FROM state_channel_metrics") == total_subs


def test_signups_consistent_across_marts(conn):
    total_users = scalar(conn, "SELECT COUNT(*) FROM users")
    assert scalar(conn, "SELECT SUM(registros) FROM channel_metrics") == total_users
    assert scalar(conn, "SELECT SUM(registros) FROM monthly_channel_metrics") == total_users
    assert scalar(conn, "SELECT SUM(registros) FROM state_channel_metrics") == total_users


def test_allocated_spend_matches_total_spend(conn):
    total_spend = scalar(conn, "SELECT SUM(spend) FROM campaign_daily_metrics")
    allocated = scalar(conn, "SELECT SUM(spend_asignado) FROM state_channel_metrics")
    assert abs(total_spend - allocated) < 1.0


def test_ab_test_has_two_variants_with_one_shared_p_value(conn):
    rows = conn.execute("SELECT variant, p_value FROM ab_test_results ORDER BY variant").fetchall()
    assert [r[0] for r in rows] == ["A", "B"]
    assert rows[0][1] == rows[1][1]


def test_every_subscription_belongs_to_a_paid_funnel_event(conn):
    orphans = scalar(conn, """
        SELECT COUNT(*) FROM subscriptions s
        WHERE NOT EXISTS (
            SELECT 1 FROM funnel_events fe
            WHERE fe.user_id = s.user_id AND fe.event_type = 'suscripcion_paga'
        )
    """)
    assert orphans == 0
