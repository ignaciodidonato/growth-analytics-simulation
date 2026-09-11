-- Schema: plataforma de teleterapia (sociedad de psicologos, EE.UU.)
-- Motor: SQLite. Diferencias con Postgres se anotan donde aplica.

CREATE TABLE channels (
    channel_id      INTEGER PRIMARY KEY,
    channel_name    TEXT NOT NULL UNIQUE,   -- clave tecnica (google_ads, meta, ...)
    channel_label   TEXT NOT NULL,          -- nombre de presentacion (Google Ads, Meta, ...)
    channel_type    TEXT NOT NULL CHECK (channel_type IN ('paid', 'organic', 'owned'))
);

CREATE TABLE campaigns (
    campaign_id     INTEGER PRIMARY KEY,
    channel_id      INTEGER NOT NULL REFERENCES channels(channel_id),
    campaign_name   TEXT NOT NULL,
    ab_test_name    TEXT,
    variant         TEXT CHECK (variant IN ('A', 'B')),
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL CHECK (end_date >= start_date)
);

CREATE INDEX idx_campaigns_channel ON campaigns(channel_id);

CREATE TABLE campaign_daily_metrics (
    campaign_id     INTEGER NOT NULL REFERENCES campaigns(campaign_id),
    date            DATE NOT NULL,
    impressions     INTEGER NOT NULL CHECK (impressions >= 0),
    clicks          INTEGER NOT NULL CHECK (clicks >= 0),
    spend           NUMERIC(10, 2) NOT NULL CHECK (spend >= 0),
    PRIMARY KEY (campaign_id, date)
);

CREATE TABLE plans (
    plan_id             INTEGER PRIMARY KEY,
    plan_name           TEXT NOT NULL UNIQUE,
    sessions_per_month  INTEGER NOT NULL CHECK (sessions_per_month > 0),
    monthly_fee         NUMERIC(10, 2) NOT NULL CHECK (monthly_fee > 0)
);

CREATE TABLE users (
    user_id                     INTEGER PRIMARY KEY,
    acquisition_campaign_id     INTEGER NOT NULL REFERENCES campaigns(campaign_id),
    state                       TEXT NOT NULL,  -- codigo de 2 letras (CA, NY, TX, ...)
    signup_date                 DATE NOT NULL
);

CREATE INDEX idx_users_campaign ON users(acquisition_campaign_id);
CREATE INDEX idx_users_state ON users(state);

CREATE TABLE funnel_events (
    event_id        INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(user_id),
    event_type      TEXT NOT NULL CHECK (event_type IN ('registro', 'activacion', 'suscripcion_paga')),
    event_date      DATE NOT NULL
);

CREATE INDEX idx_funnel_events_user_type ON funnel_events(user_id, event_type);

-- UNIQUE en user_id fuerza la relacion 1:1 (una suscripcion por usuario),
-- coherente con la simplificacion de no modelar upgrade/downgrade de plan.
CREATE TABLE subscriptions (
    subscription_id     INTEGER PRIMARY KEY,
    user_id             INTEGER NOT NULL UNIQUE REFERENCES users(user_id),
    plan_id             INTEGER NOT NULL REFERENCES plans(plan_id),
    start_date          DATE NOT NULL,
    cancel_date         DATE CHECK (cancel_date IS NULL OR cancel_date >= start_date)
);

CREATE INDEX idx_subscriptions_plan ON subscriptions(plan_id);
