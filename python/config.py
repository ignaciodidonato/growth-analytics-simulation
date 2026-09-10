"""Parametros de negocio para la simulacion. Todo lo que es una decision
de modelado (fechas, canales, planes, campanias) vive aca en vez de estar
hardcodeado dentro de la logica de generacion."""

from datetime import date

RANDOM_SEED = 42

START_DATE = date(2024, 1, 1)
END_DATE = date(2024, 12, 31)

CHANNELS = [
    {"channel_name": "google_ads", "channel_type": "paid"},
    {"channel_name": "meta", "channel_type": "paid"},
    {"channel_name": "email", "channel_type": "owned"},
    {"channel_name": "organic", "channel_type": "organic"},
]

PLANS = [
    {"plan_name": "Plan Quincenal", "sessions_per_month": 2, "monthly_fee": 150.00},
    {"plan_name": "Plan Semanal", "sessions_per_month": 4, "monthly_fee": 280.00},
]

# "key" es solo un identificador interno del script para mapear cada
# campania a su funcion de generacion de metricas (ver generate_data.py).
# No se persiste en la base.
CAMPAIGNS = [
    {
        "key": "google_ads_search",
        "channel_name": "google_ads",
        "campaign_name": "Search - Therapy Intent",
        "ab_test_name": None,
        "variant": None,
        "start_date": START_DATE,
        "end_date": END_DATE,
    },
    {
        "key": "meta_prospecting",
        "channel_name": "meta",
        "campaign_name": "Prospecting - Interest Targeting",
        "ab_test_name": None,
        "variant": None,
        "start_date": START_DATE,
        "end_date": END_DATE,
    },
    {
        "key": "email_newsletter",
        "channel_name": "email",
        "campaign_name": "Newsletter - Patient List",
        "ab_test_name": None,
        "variant": None,
        "start_date": START_DATE,
        "end_date": END_DATE,
    },
    {
        "key": "organic_seo",
        "channel_name": "organic",
        "campaign_name": "SEO & Word of Mouth",
        "ab_test_name": None,
        "variant": None,
        "start_date": START_DATE,
        "end_date": END_DATE,
    },
    {
        "key": "meta_ab_test_a",
        "channel_name": "meta",
        "campaign_name": "AB Test - Broad Audience",
        "ab_test_name": "meta_targeting_test",
        "variant": "A",
        "start_date": date(2024, 6, 1),
        "end_date": date(2024, 6, 30),
    },
    {
        "key": "meta_ab_test_b",
        "channel_name": "meta",
        "campaign_name": "AB Test - Lookalike Audience",
        "ab_test_name": "meta_targeting_test",
        "variant": "B",
        "start_date": date(2024, 6, 1),
        "end_date": date(2024, 6, 30),
    },
]

# Probabilidad de que un click se convierta en registro (usuario identificado).
# La diferencia entre meta_ab_test_a y meta_ab_test_b es el efecto real que
# el test A/B deberia detectar mas adelante como estadisticamente significativo.
CLICK_TO_REGISTRO_RATE = {
    "google_ads_search": 0.06,
    "meta_prospecting": 0.03,
    "email_newsletter": 0.12,
    "organic_seo": 0.08,
    "meta_ab_test_a": 0.03,
    "meta_ab_test_b": 0.05,
}

# Los 15 estados con mas poblacion de EE.UU., con pesos aproximados a su
# poblacion relativa. Simplificacion deliberada: no modelamos los 50 estados
# porque el volumen en la mayoria seria demasiado bajo para ser informativo.
US_STATE_WEIGHTS = {
    "CA": 12.0, "TX": 9.0, "FL": 6.5, "NY": 6.0, "PA": 4.0,
    "IL": 3.8, "OH": 3.5, "GA": 3.2, "NC": 3.1, "MI": 3.0,
    "NJ": 2.7, "VA": 2.6, "WA": 2.3, "AZ": 2.2, "MA": 2.1,
}

# Probabilidad de que un usuario registrado llegue a "activacion" (primera
# sesion agendada/realizada). El test A/B usa la misma tasa que la campania
# always-on de Meta: el efecto que estamos probando ya quedo plantado antes,
# en CLICK_TO_REGISTRO_RATE.
ACTIVATION_RATE = {
    "google_ads_search": 0.70,
    "meta_prospecting": 0.55,
    "meta_ab_test_a": 0.55,
    "meta_ab_test_b": 0.55,
    "email_newsletter": 0.80,
    "organic_seo": 0.75,
}

# Probabilidad de que un usuario activado pase a suscripcion paga.
PAID_CONVERSION_RATE = {
    "google_ads_search": 0.45,
    "meta_prospecting": 0.30,
    "meta_ab_test_a": 0.30,
    "meta_ab_test_b": 0.30,
    "email_newsletter": 0.55,
    "organic_seo": 0.50,
}

# Probabilidad de cancelar en cada periodo de 30 dias desde el alta.
MONTHLY_CHURN_RATE = {
    "google_ads_search": 0.05,
    "meta_prospecting": 0.09,
    "meta_ab_test_a": 0.09,
    "meta_ab_test_b": 0.09,
    "email_newsletter": 0.03,
    "organic_seo": 0.04,
}

PLAN_CHOICE_WEIGHTS = {
    "Plan Quincenal": 0.55,
    "Plan Semanal": 0.45,
}
