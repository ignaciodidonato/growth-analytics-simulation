"""Calcula metricas de negocio (CAC, ARPU, churn, LTV) y la significancia
del test A/B, y persiste los resultados como tablas 'mart' en la misma
base SQLite para que Power BI las lea directamente, sin tener que
recalcular logica de negocio en DAX.

Las tasas de churn y conversion se ESTIMAN desde los datos generados, no
se leen de config.py: un analista real nunca tiene acceso a los
parametros "verdaderos" de generacion, solo a lo observado.
"""

import math
import os
import sqlite3
from datetime import date

from scipy.stats import norm

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "teleterapia.db")
END_DATE = date(2024, 12, 31)
PERIOD_DAYS = 30  # ventana de riesgo de churn, coherente con generate_data.py


def create_output_tables(conn):
    conn.executescript(
        """
        DROP TABLE IF EXISTS channel_metrics;
        CREATE TABLE channel_metrics (
            channel_name    TEXT PRIMARY KEY,
            spend_total     REAL,
            registros       INTEGER,
            clientes_pagos  INTEGER,
            cac             REAL,
            arpu_mensual    REAL,
            churn_mensual   REAL,
            vida_util_meses REAL,
            ltv             REAL,
            ltv_cac_ratio   REAL
        );

        DROP TABLE IF EXISTS ab_test_results;
        CREATE TABLE ab_test_results (
            ab_test_name        TEXT,
            variant             TEXT,
            clicks              INTEGER,
            registros           INTEGER,
            conversion_rate     REAL,
            z_stat              REAL,
            p_value             REAL,
            significativo_95    INTEGER
        );
        """
    )
    conn.commit()


def compute_channel_metrics(conn):
    spend_by_channel = dict(
        conn.execute(
            """
            SELECT ch.channel_name, SUM(m.spend)
            FROM campaign_daily_metrics m
            JOIN campaigns c ON c.campaign_id = m.campaign_id
            JOIN channels ch ON ch.channel_id = c.channel_id
            GROUP BY ch.channel_name
            """
        ).fetchall()
    )

    registros_by_channel = dict(
        conn.execute(
            """
            SELECT ch.channel_name, COUNT(*)
            FROM users u
            JOIN campaigns c ON c.campaign_id = u.acquisition_campaign_id
            JOIN channels ch ON ch.channel_id = c.channel_id
            GROUP BY ch.channel_name
            """
        ).fetchall()
    )

    # Una fila por suscripcion, con el canal de adquisicion del usuario y la
    # tarifa mensual de su plan, para poder estimar ARPU y churn por canal.
    subs = conn.execute(
        """
        SELECT ch.channel_name, s.start_date, s.cancel_date, p.monthly_fee
        FROM subscriptions s
        JOIN users u ON u.user_id = s.user_id
        JOIN campaigns c ON c.campaign_id = u.acquisition_campaign_id
        JOIN channels ch ON ch.channel_id = c.channel_id
        JOIN plans p ON p.plan_id = s.plan_id
        """
    ).fetchall()

    by_channel = {}
    for channel_name, start_str, cancel_str, fee in subs:
        by_channel.setdefault(channel_name, []).append((start_str, cancel_str, fee))

    rows = []
    for channel_name, spend_total in spend_by_channel.items():
        entries = by_channel.get(channel_name, [])
        clientes_pagos = len(entries)
        registros = registros_by_channel.get(channel_name, 0)
        cac = round(spend_total / clientes_pagos, 2) if clientes_pagos else None

        if clientes_pagos == 0:
            rows.append((channel_name, spend_total, registros, 0, cac, None, None, None, None, None))
            continue

        arpu = sum(fee for _, _, fee in entries) / clientes_pagos

        # Estimacion de churn por metodo de "persona-periodo": cada
        # suscripcion aporta N periodos de 30 dias en riesgo, y 1 si el
        # ultimo periodo termino en cancelacion (0 si sigue activa =
        # censurada). churn_mensual = eventos totales / periodos en riesgo.
        total_periods = 0
        total_events = 0
        for start_str, cancel_str, _ in entries:
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(cancel_str) if cancel_str else END_DATE
            periods = max(math.ceil((end - start).days / PERIOD_DAYS), 1)
            total_periods += periods
            total_events += 1 if cancel_str else 0

        churn_mensual = total_events / total_periods
        vida_util_meses = 1 / churn_mensual if churn_mensual > 0 else None
        ltv = arpu * vida_util_meses if vida_util_meses else None
        ltv_cac_ratio = round(ltv / cac, 2) if (ltv and cac) else None

        rows.append((
            channel_name,
            round(spend_total, 2),
            registros,
            clientes_pagos,
            cac,
            round(arpu, 2),
            round(churn_mensual, 4),
            round(vida_util_meses, 1) if vida_util_meses else None,
            round(ltv, 2) if ltv else None,
            ltv_cac_ratio,
        ))

    conn.executemany(
        """INSERT INTO channel_metrics
           (channel_name, spend_total, registros, clientes_pagos, cac,
            arpu_mensual, churn_mensual, vida_util_meses, ltv, ltv_cac_ratio)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return rows


def two_proportion_z_test(x1, n1, x2, n2):
    p1, p2 = x1 / n1, x2 / n2
    p_pool = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se
    p_value = 2 * norm.sf(abs(z))
    return p1, p2, z, p_value


def compute_ab_test(conn):
    data = conn.execute(
        """
        SELECT c.ab_test_name, c.variant,
               SUM(m.clicks) AS clicks,
               (SELECT COUNT(*) FROM users u WHERE u.acquisition_campaign_id = c.campaign_id) AS registros
        FROM campaign_daily_metrics m
        JOIN campaigns c ON c.campaign_id = m.campaign_id
        WHERE c.ab_test_name IS NOT NULL
        GROUP BY c.campaign_id
        ORDER BY c.variant
        """
    ).fetchall()

    variants = {variant: (clicks, registros) for _, variant, clicks, registros in data}
    (clicks_a, reg_a), (clicks_b, reg_b) = variants["A"], variants["B"]

    p_a, p_b, z, p_value = two_proportion_z_test(reg_a, clicks_a, reg_b, clicks_b)
    ab_test_name = data[0][0]

    rows = [
        (ab_test_name, "A", clicks_a, reg_a, round(p_a, 4), round(z, 3), round(p_value, 4), int(p_value < 0.05)),
        (ab_test_name, "B", clicks_b, reg_b, round(p_b, 4), round(z, 3), round(p_value, 4), int(p_value < 0.05)),
    ]
    conn.executemany(
        """INSERT INTO ab_test_results
           (ab_test_name, variant, clicks, registros, conversion_rate, z_stat, p_value, significativo_95)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return rows


def main():
    conn = sqlite3.connect(DB_PATH)
    create_output_tables(conn)

    channel_rows = compute_channel_metrics(conn)
    print("--- channel_metrics ---")
    for row in sorted(channel_rows, key=lambda r: (r[9] is None, -(r[9] or 0))):
        print(row)

    ab_rows = compute_ab_test(conn)
    print("\n--- ab_test_results ---")
    for row in ab_rows:
        print(row)

    conn.close()


if __name__ == "__main__":
    main()
