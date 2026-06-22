-- Slack semantic-search schema.
-- Applied to the slack-search Supabase project (ref: xpzrsljqjhqewquaziul)
-- via the MCP apply_migration call on 2026-06-22. This file exists so the
-- schema is version-controlled alongside the application code.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE public.channels (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    is_private  boolean DEFAULT false,
    added_at    timestamptz DEFAULT now()
);

CREATE TABLE public.messages (
    id           bigserial PRIMARY KEY,
    channel_id   text NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
    slack_ts     text NOT NULL,
    user_id      text,
    username     text,
    body         text NOT NULL,
    embedding    vector(1536),
    permalink    text,
    thread_ts    text,
    indexed_at   timestamptz DEFAULT now(),
    UNIQUE (channel_id, slack_ts)
);

CREATE INDEX idx_messages_channel_ts    ON public.messages (channel_id, slack_ts DESC);
CREATE INDEX idx_messages_indexed_at    ON public.messages (indexed_at);
CREATE INDEX idx_messages_embedding_hnsw ON public.messages USING hnsw (embedding vector_cosine_ops);

CREATE TABLE public.sync_state (
    channel_id       text PRIMARY KEY REFERENCES public.channels(id) ON DELETE CASCADE,
    oldest_synced_ts text,
    newest_synced_ts text,
    last_synced_at   timestamptz DEFAULT now()
);

ALTER TABLE public.channels    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.messages    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sync_state  ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.match_messages(
    query_embedding   vector(1536),
    match_threshold   float DEFAULT 0.4,
    match_count       int   DEFAULT 10,
    after_ts          text  DEFAULT NULL,
    channel_filter    text  DEFAULT NULL
)
RETURNS TABLE (
    id            bigint,
    channel_id    text,
    channel_name  text,
    slack_ts      text,
    username      text,
    body          text,
    permalink     text,
    similarity    float
)
LANGUAGE sql STABLE
AS $$
    SELECT
        m.id,
        m.channel_id,
        c.name AS channel_name,
        m.slack_ts,
        m.username,
        m.body,
        m.permalink,
        1 - (m.embedding <=> query_embedding) AS similarity
    FROM public.messages m
    JOIN public.channels c ON c.id = m.channel_id
    WHERE m.embedding IS NOT NULL
      AND (1 - (m.embedding <=> query_embedding)) >= match_threshold
      AND (after_ts IS NULL OR m.slack_ts >= after_ts)
      AND (channel_filter IS NULL OR m.channel_id = channel_filter)
    ORDER BY m.embedding <=> query_embedding
    LIMIT match_count;
$$;
