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

        DROP TABLE IF EXISTS ab_test_summary;
        CREATE TABLE ab_test_summary (
            ab_test_name                    TEXT PRIMARY KEY,
            lift_relativo                   REAL,
            diff_abs                        REAL,
            ci_low                          REAL,
            ci_high                         REAL,
            clicks_necesarios_por_variante  INTEGER,
            multiplo_muestra                REAL,
            dias_necesarios                 INTEGER
        );

        DROP TABLE IF EXISTS ab_test_daily;
        CREATE TABLE ab_test_daily (
            variant             TEXT,
            date                TEXT,
            clicks              INTEGER,
            registros           INTEGER,
            clicks_acum         INTEGER,
            registros_acum      INTEGER,
            conversion_acum     REAL,
            PRIMARY KEY (variant, date)
        );

        DROP TABLE IF EXISTS monthly_channel_metrics;
        CREATE TABLE monthly_channel_metrics (
            channel_name        TEXT,
            month                TEXT,
            spend                REAL,
            registros            INTEGER,
            costo_por_registro   REAL
        );

        DROP TABLE IF EXISTS funnel_conversion;
        CREATE TABLE funnel_conversion (
            channel_name                  TEXT PRIMARY KEY,
            registro                      INTEGER,
            activacion                    INTEGER,
            pago                          INTEGER,
            tasa_registro_a_activacion    REAL,
            tasa_activacion_a_pago        REAL,
            tasa_registro_a_pago          REAL
        );

        DROP TABLE IF EXISTS state_channel_metrics;
        CREATE TABLE state_channel_metrics (
            state                 TEXT,
            state_name            TEXT,
            channel_name          TEXT,
            registros             INTEGER,
            activaciones          INTEGER,
            clientes_pagos        INTEGER,
            tasa_registro_a_pago  REAL,
            spend_asignado        REAL,
            cac_estimado          REAL,
            PRIMARY KEY (state, channel_name)
        );

        DROP TABLE IF EXISTS state_metrics;
        CREATE TABLE state_metrics (
            state                 TEXT PRIMARY KEY,
            state_name            TEXT,
            registros             INTEGER,
            activaciones          INTEGER,
            clientes_pagos        INTEGER,
            tasa_registro_a_pago  REAL,
            spend_asignado        REAL,
            cac_estimado          REAL
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


def compute_monthly_channel_metrics(conn):
    """Costo por registro, mes a mes y por canal (misma logica que la query 1
    de sql/metrics_queries.sql, persistida aca para alimentar Power BI sin
    que el reporte tenga que recalcularla)."""
    rows = conn.execute(
        """
        SELECT
            ch.channel_name,
            strftime('%Y-%m', m.date) AS month,
            ROUND(SUM(m.spend), 2) AS spend,
            COUNT(DISTINCT u.user_id) AS registros,
            ROUND(SUM(m.spend) * 1.0 / NULLIF(COUNT(DISTINCT u.user_id), 0), 2) AS costo_por_registro
        FROM campaign_daily_metrics m
        JOIN campaigns c ON c.campaign_id = m.campaign_id
        JOIN channels ch ON ch.channel_id = c.channel_id
        LEFT JOIN users u
            ON u.acquisition_campaign_id = c.campaign_id
            AND u.signup_date = m.date
        GROUP BY ch.channel_name, month
        ORDER BY ch.channel_name, month
        """
    ).fetchall()

    conn.executemany(
        """INSERT INTO monthly_channel_metrics
           (channel_name, month, spend, registros, costo_por_registro)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return rows


def compute_funnel_conversion(conn):
    """Conversion etapa a etapa del funnel por canal (misma logica que la
    query 3 de sql/metrics_queries.sql)."""
    rows = conn.execute(
        """
        WITH stage_counts AS (
            SELECT
                ch.channel_name,
                SUM(CASE WHEN fe.event_type = 'registro' THEN 1 ELSE 0 END) AS registro,
                SUM(CASE WHEN fe.event_type = 'activacion' THEN 1 ELSE 0 END) AS activacion,
                SUM(CASE WHEN fe.event_type = 'suscripcion_paga' THEN 1 ELSE 0 END) AS pago
            FROM funnel_events fe
            JOIN users u ON u.user_id = fe.user_id
            JOIN campaigns c ON c.campaign_id = u.acquisition_campaign_id
            JOIN channels ch ON ch.channel_id = c.channel_id
            GROUP BY ch.channel_name
        )
        SELECT
            channel_name,
            registro,
            activacion,
            pago,
            ROUND(activacion * 1.0 / NULLIF(registro, 0), 3) AS tasa_registro_a_activacion,
            ROUND(pago * 1.0 / NULLIF(activacion, 0), 3) AS tasa_activacion_a_pago,
            ROUND(pago * 1.0 / NULLIF(registro, 0), 3) AS tasa_registro_a_pago
        FROM stage_counts
        ORDER BY tasa_registro_a_pago DESC
        """
    ).fetchall()

    conn.executemany(
        """INSERT INTO funnel_conversion
           (channel_name, registro, activacion, pago,
            tasa_registro_a_activacion, tasa_activacion_a_pago, tasa_registro_a_pago)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return rows


US_STATE_NAMES = {
    "CA": "California", "TX": "Texas", "FL": "Florida", "NY": "New York",
    "PA": "Pennsylvania", "IL": "Illinois", "OH": "Ohio", "GA": "Georgia",
    "NC": "North Carolina", "MI": "Michigan", "NJ": "New Jersey",
    "VA": "Virginia", "WA": "Washington", "AZ": "Arizona", "MA": "Massachusetts",
}


def compute_state_metrics(conn):
    """Funnel y costo por estado y por estado x canal.

    El gasto de una campania no viene desglosado por estado (Google/Meta lo
    reportan a nivel campania), asi que se ASIGNA a cada estado en proporcion
    a los registros que la campania consiguio ahi. Es la aproximacion estandar
    y hay que declararla como tal: el CAC por estado es estimado."""
    spend_by_campaign = dict(conn.execute(
        "SELECT campaign_id, SUM(spend) FROM campaign_daily_metrics GROUP BY campaign_id"
    ).fetchall())

    rows = conn.execute(
        """
        SELECT u.state, ch.channel_name, u.acquisition_campaign_id,
               MAX(CASE WHEN fe.event_type = 'activacion' THEN 1 ELSE 0 END),
               MAX(CASE WHEN fe.event_type = 'suscripcion_paga' THEN 1 ELSE 0 END)
        FROM users u
        JOIN campaigns c ON c.campaign_id = u.acquisition_campaign_id
        JOIN channels ch ON ch.channel_id = c.channel_id
        LEFT JOIN funnel_events fe ON fe.user_id = u.user_id
        GROUP BY u.user_id
        """
    ).fetchall()

    reg_by_campaign = {}
    reg_by_state_campaign = {}
    agg = {}  # (state, channel) -> [registros, activaciones, pagos]
    for state, channel, campaign_id, activated, paid in rows:
        reg_by_campaign[campaign_id] = reg_by_campaign.get(campaign_id, 0) + 1
        reg_by_state_campaign[(state, campaign_id)] = reg_by_state_campaign.get((state, campaign_id), 0) + 1
        a = agg.setdefault((state, channel), [0, 0, 0])
        a[0] += 1
        a[1] += activated
        a[2] += paid

    campaign_channel = dict(conn.execute(
        "SELECT c.campaign_id, ch.channel_name FROM campaigns c JOIN channels ch ON ch.channel_id = c.channel_id"
    ).fetchall())
    spend_by_state_channel = {}
    for (state, campaign_id), n in reg_by_state_campaign.items():
        share = n / reg_by_campaign[campaign_id]
        key = (state, campaign_channel[campaign_id])
        spend_by_state_channel[key] = spend_by_state_channel.get(key, 0.0) + spend_by_campaign[campaign_id] * share

    def metrics(registros, activaciones, pagos, spend):
        tasa = round(pagos / registros, 3) if registros else None
        cac = round(spend / pagos, 2) if pagos else None
        return registros, activaciones, pagos, tasa, round(spend, 2), cac

    sc_rows = []
    by_state = {}
    for (state, channel), (r, a, p) in sorted(agg.items()):
        spend = spend_by_state_channel.get((state, channel), 0.0)
        sc_rows.append((state, US_STATE_NAMES[state], channel, *metrics(r, a, p, spend)))
        s = by_state.setdefault(state, [0, 0, 0, 0.0])
        s[0] += r; s[1] += a; s[2] += p; s[3] += spend

    s_rows = [(state, US_STATE_NAMES[state], *metrics(*vals)) for state, vals in sorted(by_state.items())]

    conn.executemany(
        """INSERT INTO state_channel_metrics
           (state, state_name, channel_name, registros, activaciones, clientes_pagos,
            tasa_registro_a_pago, spend_asignado, cac_estimado)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        sc_rows,
    )
    conn.executemany(
        """INSERT INTO state_metrics
           (state, state_name, registros, activaciones, clientes_pagos,
            tasa_registro_a_pago, spend_asignado, cac_estimado)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        s_rows,
    )
    conn.commit()
    return s_rows, sc_rows


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


def required_sample_per_variant(p1, p2, alpha=0.05, power=0.80):
    """Clicks por variante para detectar la diferencia p2 - p1 con un test z
    bilateral de dos proporciones (formula clasica de tamanio de muestra)."""
    z_alpha = norm.ppf(1 - alpha / 2)
    z_beta = norm.ppf(power)
    p_bar = (p1 + p2) / 2
    num = z_alpha * math.sqrt(2 * p_bar * (1 - p_bar)) + z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return math.ceil(num ** 2 / (p2 - p1) ** 2)


def compute_ab_test_summary(conn):
    """Una fila por test: lift observado, intervalo de confianza de la
    diferencia y muestra necesaria para confirmar ese lift con 80% de potencia."""
    rows = conn.execute(
        "SELECT ab_test_name, variant, clicks, registros FROM ab_test_results ORDER BY variant"
    ).fetchall()
    name = rows[0][0]
    (_, _, n_a, x_a), (_, _, n_b, x_b) = rows
    p_a, p_b = x_a / n_a, x_b / n_b
    diff = p_b - p_a
    se_unpooled = math.sqrt(p_a * (1 - p_a) / n_a + p_b * (1 - p_b) / n_b)
    ci_low, ci_high = diff - 1.96 * se_unpooled, diff + 1.96 * se_unpooled
    n_required = required_sample_per_variant(p_a, p_b)

    days_observed = conn.execute(
        """SELECT COUNT(DISTINCT m.date) FROM campaign_daily_metrics m
           JOIN campaigns c ON c.campaign_id = m.campaign_id WHERE c.ab_test_name = ?""",
        (name,),
    ).fetchone()[0]
    clicks_per_day_per_variant = (n_a + n_b) / 2 / days_observed
    days_required = math.ceil(n_required / clicks_per_day_per_variant)

    row = (
        name,
        round(p_b / p_a - 1, 4),
        round(diff, 4),
        round(ci_low, 4),
        round(ci_high, 4),
        n_required,
        round(n_required / ((n_a + n_b) / 2), 1),
        days_required,
    )
    conn.execute(
        """INSERT INTO ab_test_summary
           (ab_test_name, lift_relativo, diff_abs, ci_low, ci_high,
            clicks_necesarios_por_variante, multiplo_muestra, dias_necesarios)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        row,
    )
    conn.commit()
    return row


def compute_ab_test_daily(conn):
    """Conversion acumulada por variante, dia a dia: el grafico de
    monitoreo tipico de un test A/B."""
    daily = conn.execute(
        """
        SELECT c.variant, m.date, m.clicks,
               (SELECT COUNT(*) FROM users u
                WHERE u.acquisition_campaign_id = c.campaign_id AND u.signup_date = m.date) AS registros
        FROM campaign_daily_metrics m
        JOIN campaigns c ON c.campaign_id = m.campaign_id
        WHERE c.ab_test_name IS NOT NULL
        ORDER BY c.variant, m.date
        """
    ).fetchall()

    rows = []
    cum = {}
    for variant, date_str, clicks, registros in daily:
        c, r = cum.get(variant, (0, 0))
        c, r = c + clicks, r + registros
        cum[variant] = (c, r)
        rows.append((variant, date_str, clicks, registros, c, r, round(r / c, 4) if c else None))

    conn.executemany(
        """INSERT INTO ab_test_daily
           (variant, date, clicks, registros, clicks_acum, registros_acum, conversion_acum)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
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

    print("\n--- ab_test_summary ---")
    print(compute_ab_test_summary(conn))
    daily_rows = compute_ab_test_daily(conn)
    print(f"--- ab_test_daily: {len(daily_rows)} filas, ultimo dia: {daily_rows[len(daily_rows) // 2 - 1]} / {daily_rows[-1]}")

    monthly_rows = compute_monthly_channel_metrics(conn)
    print("\n--- monthly_channel_metrics ---")
    for row in monthly_rows:
        print(row)

    funnel_rows = compute_funnel_conversion(conn)
    print("\n--- funnel_conversion ---")
    for row in funnel_rows:
        print(row)

    state_rows, state_channel_rows = compute_state_metrics(conn)
    print("\n--- state_metrics ---")
    for row in sorted(state_rows, key=lambda r: -(r[5] or 0)):
        print(row)
    print("\n--- state_channel_metrics (solo estados con efecto plantado) ---")
    for row in state_channel_rows:
        if row[0] in ("TX", "FL", "NY", "MA", "CA"):
            print(row)

    conn.close()


if __name__ == "__main__":
    main()
