-- Metricas clave para el equipo de growth.
-- Motor: SQLite.

-- 1) Costo por registro, mensual por canal.
--    Seguro de trendear mes a mes: en este negocio el registro ocurre el
--    mismo dia del click, sin lag que distorsione la atribucion temporal.
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
ORDER BY ch.channel_name, month;


-- 2) CAC blended por canal, todo el periodo.
--    CAC = gasto total del canal / clientes PAGOS adquiridos por ese canal
--    (no registros). Se calcula sobre todo el rango de 12 meses para evitar
--    el ruido del lag entre spend y conversion a pago.
--    Gasto y clientes pagos se agregan en CTEs separados (cada uno a su
--    propio grano) antes de unirlos por channel_name: si se hace todo en
--    un solo JOIN, cada fila diaria de spend se multiplica por la cantidad
--    de usuarios de esa campania (fan-out) e infla el gasto total.
WITH channel_spend AS (
    SELECT ch.channel_name, SUM(m.spend) AS spend_total
    FROM campaign_daily_metrics m
    JOIN campaigns c ON c.campaign_id = m.campaign_id
    JOIN channels ch ON ch.channel_id = c.channel_id
    GROUP BY ch.channel_name
),
channel_paying_customers AS (
    SELECT ch.channel_name, COUNT(DISTINCT s.user_id) AS clientes_pagos
    FROM subscriptions s
    JOIN users u ON u.user_id = s.user_id
    JOIN campaigns c ON c.campaign_id = u.acquisition_campaign_id
    JOIN channels ch ON ch.channel_id = c.channel_id
    GROUP BY ch.channel_name
)
SELECT
    cs.channel_name,
    ROUND(cs.spend_total, 2) AS spend_total,
    COALESCE(cp.clientes_pagos, 0) AS clientes_pagos,
    ROUND(cs.spend_total * 1.0 / NULLIF(cp.clientes_pagos, 0), 2) AS cac
FROM channel_spend cs
LEFT JOIN channel_paying_customers cp ON cp.channel_name = cs.channel_name
ORDER BY cac;


-- 3) Conversion de funnel por canal: registro -> activacion -> pago.
--    Cada etapa cuenta usuarios distintos que alcanzaron ese evento
--    (no eventos crudos), y las tasas son etapa-a-etapa, no acumuladas
--    contra el total de registros.
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
ORDER BY tasa_registro_a_pago DESC;
