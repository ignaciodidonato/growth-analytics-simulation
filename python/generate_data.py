"""Genera la base SQLite completa a partir de sql/schema.sql y de las
reglas de negocio definidas en config.py.

Cubre todo el pipeline: dimensiones (channels, plans, campaigns),
trafico/costo por canal (campaign_daily_metrics) y el recorrido de cada
usuario por el funnel hasta su suscripcion (users, funnel_events,
subscriptions).
"""

import os
import sqlite3
from datetime import date, timedelta

import numpy as np

from config import (
    ACTIVATION_RATE,
    CAMPAIGNS,
    CHANNELS,
    CLICK_TO_REGISTRO_RATE,
    END_DATE,
    MONTHLY_CHURN_RATE,
    PAID_CONVERSION_RATE,
    PLAN_CHOICE_WEIGHTS,
    PLANS,
    RANDOM_SEED,
    START_DATE,
    US_STATE_WEIGHTS,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "teleterapia.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "sql", "schema.sql")


def create_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def seed_channels(conn):
    rows = [(c["channel_name"], c["channel_type"]) for c in CHANNELS]
    conn.executemany(
        "INSERT INTO channels (channel_name, channel_type) VALUES (?, ?)", rows
    )
    conn.commit()


def seed_plans(conn):
    rows = [(p["plan_name"], p["sessions_per_month"], p["monthly_fee"]) for p in PLANS]
    conn.executemany(
        "INSERT INTO plans (plan_name, sessions_per_month, monthly_fee) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()


def seed_campaigns(conn):
    channel_ids = dict(conn.execute("SELECT channel_name, channel_id FROM channels").fetchall())
    campaign_ids = {}
    for c in CAMPAIGNS:
        cur = conn.execute(
            """INSERT INTO campaigns
               (channel_id, campaign_name, ab_test_name, variant, start_date, end_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                channel_ids[c["channel_name"]],
                c["campaign_name"],
                c["ab_test_name"],
                c["variant"],
                c["start_date"].isoformat(),
                c["end_date"].isoformat(),
            ),
        )
        campaign_ids[c["key"]] = cur.lastrowid
    conn.commit()
    return campaign_ids


def days_in_range(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def month_index(d, start=START_DATE):
    """0 en el primer mes simulado, 11 en el ultimo (para rango de 12 meses)."""
    return (d.year - start.year) * 12 + (d.month - start.month)


def days_in_month(d):
    nxt = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return (nxt - date(d.year, d.month, 1)).days


def noisy(rng, value, pct):
    """Aplica ruido gaussiano relativo (+/- pct aprox.) sin permitir negativos."""
    return max(value * (1 + rng.normal(0, pct)), 0)


# --- economia por canal -----------------------------------------------
# Cada funcion devuelve (impressions, clicks, spend) para un dia dado.
# El mecanismo de "rendimientos decrecientes" de Google Ads esta en el CPC:
# sube ~3.7%/mes mientras el presupuesto casi se duplica en el anio, asi que
# los clicks crecen mucho menos que el gasto.

def google_ads_params(d, rng):
    m = month_index(d)
    monthly_budget = 4000 + m * (9000 - 4000) / 11
    base_cpc = 3.00 * (1.037 ** m)

    daily_budget = noisy(rng, monthly_budget / days_in_month(d), 0.15)
    cpc = noisy(rng, base_cpc, 0.05)
    ctr = noisy(rng, 0.035, 0.10)

    clicks = round(daily_budget / cpc) if cpc > 0 else 0
    impressions = round(clicks / ctr) if ctr > 0 else 0
    spend = round(clicks * cpc, 2)
    return impressions, clicks, spend


def meta_params(d, rng):
    daily_budget = noisy(rng, 3000 / days_in_month(d), 0.15)
    cpc = noisy(rng, 1.20, 0.08)
    ctr = noisy(rng, 0.012, 0.10)

    clicks = round(daily_budget / cpc) if cpc > 0 else 0
    impressions = round(clicks / ctr) if ctr > 0 else 0
    spend = round(clicks * cpc, 2)
    return impressions, clicks, spend


def meta_ab_test_params(d, rng):
    """Mismo costo/click que la campania always-on de Meta a proposito: el
    test compara audiencias (broad vs lookalike), no eficiencia de puja. La
    diferencia real entre variantes se modela mas adelante, en la etapa de
    conversion a usuario."""
    daily_budget = noisy(rng, 50, 0.10)
    cpc = noisy(rng, 1.20, 0.08)
    ctr = noisy(rng, 0.012, 0.10)

    clicks = round(daily_budget / cpc) if cpc > 0 else 0
    impressions = round(clicks / ctr) if ctr > 0 else 0
    spend = round(clicks * cpc, 2)
    return impressions, clicks, spend


def email_params(d, rng):
    m = month_index(d)
    monthly_sent = 4000 + m * (6000 - 4000) / 11
    monthly_cost = 75 + m * (100 - 75) / 11

    daily_sent = noisy(rng, monthly_sent / days_in_month(d), 0.10)
    ctr = noisy(rng, 0.035, 0.10)

    clicks = round(daily_sent * ctr)
    impressions = round(daily_sent)  # "impressions" = emails enviados
    spend = round(monthly_cost / days_in_month(d), 2)
    return impressions, clicks, spend


def organic_params(d, rng):
    """El trafico organico crece mes a mes por acumulacion de SEO/contenido
    y boca en boca: el espejo narrativo del CAC creciente de Google Ads."""
    m = month_index(d)
    monthly_clicks = 300 + m * (550 - 300) / 11
    daily_clicks = noisy(rng, monthly_clicks / days_in_month(d), 0.15)
    ctr_assumed = 0.08  # tasa de click asumida sobre resultados de busqueda

    clicks = round(daily_clicks)
    impressions = round(clicks / ctr_assumed)
    spend = 0.0
    return impressions, clicks, spend


PARAM_FUNCS = {
    "google_ads_search": google_ads_params,
    "meta_prospecting": meta_params,
    "email_newsletter": email_params,
    "organic_seo": organic_params,
    "meta_ab_test_a": meta_ab_test_params,
    "meta_ab_test_b": meta_ab_test_params,
}


def generate_campaign_daily_metrics(conn, campaign_ids, rng):
    rows = []
    for c in CAMPAIGNS:
        campaign_id = campaign_ids[c["key"]]
        param_func = PARAM_FUNCS[c["key"]]
        for d in days_in_range(c["start_date"], c["end_date"]):
            impressions, clicks, spend = param_func(d, rng)
            rows.append((campaign_id, d.isoformat(), impressions, clicks, spend))

    conn.executemany(
        """INSERT INTO campaign_daily_metrics
           (campaign_id, date, impressions, clicks, spend)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def generate_users(conn, campaign_ids, rng):
    """Convierte una fraccion de los clicks diarios de cada campania en
    usuarios registrados (evento 'registro' implicito en signup_date).
    Simplificacion: se asume conversion el mismo dia del click."""
    states = list(US_STATE_WEIGHTS.keys())
    weights = np.array(list(US_STATE_WEIGHTS.values()))
    weights = weights / weights.sum()

    rows = []
    for c in CAMPAIGNS:
        campaign_id = campaign_ids[c["key"]]
        rate = CLICK_TO_REGISTRO_RATE[c["key"]]
        daily_clicks = conn.execute(
            "SELECT date, clicks FROM campaign_daily_metrics WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchall()

        for date_str, clicks in daily_clicks:
            n_new_users = rng.binomial(clicks, rate)
            if n_new_users == 0:
                continue
            assigned_states = rng.choice(states, size=n_new_users, p=weights)
            rows.extend((campaign_id, state, date_str) for state in assigned_states)

    conn.executemany(
        "INSERT INTO users (acquisition_campaign_id, state, signup_date) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def generate_funnel_and_subscriptions(conn, campaign_ids, rng):
    """Recorre a cada usuario registrado a traves del resto del funnel
    (activacion -> suscripcion paga) y, si paga, simula su ciclo de vida
    de suscripcion (plan elegido y churn mes a mes)."""
    id_to_key = {v: k for k, v in campaign_ids.items()}
    plan_ids = dict(conn.execute("SELECT plan_name, plan_id FROM plans").fetchall())
    plan_names = list(PLAN_CHOICE_WEIGHTS.keys())
    plan_weights = np.array([PLAN_CHOICE_WEIGHTS[p] for p in plan_names])
    plan_weights = plan_weights / plan_weights.sum()

    users = conn.execute(
        "SELECT user_id, acquisition_campaign_id, signup_date FROM users"
    ).fetchall()

    funnel_rows = []
    subscription_rows = []

    for user_id, campaign_id, signup_date_str in users:
        key = id_to_key[campaign_id]
        signup_date = date.fromisoformat(signup_date_str)
        funnel_rows.append((user_id, "registro", signup_date.isoformat()))

        if rng.random() >= ACTIVATION_RATE[key]:
            continue
        activation_date = signup_date + timedelta(days=int(rng.integers(1, 15)))
        if activation_date > END_DATE:
            continue
        funnel_rows.append((user_id, "activacion", activation_date.isoformat()))

        if rng.random() >= PAID_CONVERSION_RATE[key]:
            continue
        paid_date = activation_date + timedelta(days=int(rng.integers(0, 8)))
        if paid_date > END_DATE:
            continue
        funnel_rows.append((user_id, "suscripcion_paga", paid_date.isoformat()))

        plan_name = rng.choice(plan_names, p=plan_weights)
        plan_id = plan_ids[plan_name]

        cancel_date = None
        current = paid_date
        churn_rate = MONTHLY_CHURN_RATE[key]
        while True:
            current = current + timedelta(days=30)
            if current > END_DATE:
                break
            if rng.random() < churn_rate:
                cancel_date = current
                break

        subscription_rows.append((
            user_id,
            plan_id,
            paid_date.isoformat(),
            cancel_date.isoformat() if cancel_date else None,
        ))

    conn.executemany(
        "INSERT INTO funnel_events (user_id, event_type, event_date) VALUES (?, ?, ?)",
        funnel_rows,
    )
    conn.executemany(
        """INSERT INTO subscriptions (user_id, plan_id, start_date, cancel_date)
           VALUES (?, ?, ?, ?)""",
        subscription_rows,
    )
    conn.commit()
    return len(subscription_rows)


def main():
    rng = np.random.default_rng(RANDOM_SEED)
    conn = create_database()

    seed_channels(conn)
    seed_plans(conn)
    campaign_ids = seed_campaigns(conn)
    generate_campaign_daily_metrics(conn, campaign_ids, rng)
    n_users = generate_users(conn, campaign_ids, rng)
    n_subscriptions = generate_funnel_and_subscriptions(conn, campaign_ids, rng)

    conn.close()
    print(f"Base generada en {DB_PATH} ({n_users} usuarios, {n_subscriptions} suscripciones)")


if __name__ == "__main__":
    main()
