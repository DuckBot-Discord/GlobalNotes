CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS user_notes (
    id BIGSERIAL,
    user_id BIGINT NOT NULL,
    target_id BIGINT NOT NULL,
    content VARCHAR(2000),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
); 

CREATE TABLE IF NOT EXISTS whitelist(
    entity_id BIGINT PRIMARY KEY,
    is_user BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS user_settings(
    user_id BIGINT PRIMARY KEY,
    notifications_enabled BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS warned(
    user_id BIGINT,
    thread_id BIGINT,
    PRIMARY KEY (user_id, thread_id)
);

CREATE TABLE IF NOT EXISTS user_muted_notes(
    note_id BIGINT REFERENCES user_notes(id) ON DELETE CASCADE,
    user_id BIGINT,
    PRIMARY KEY (note_id, user_id)
);