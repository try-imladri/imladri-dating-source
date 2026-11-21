-- Application repository. The separately retained imladri_shadow is migration evidence.
CREATE SCHEMA IF NOT EXISTS imladri_app;
CREATE TABLE IF NOT EXISTS imladri_app.schema_version (
    version integer PRIMARY KEY CHECK (version = 1)
);
INSERT INTO imladri_app.schema_version(version) VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS imladri_app.collections (
    namespace text NOT NULL,
    name text NOT NULL,
    PRIMARY KEY(namespace,name)
);
CREATE TABLE IF NOT EXISTS imladri_app.documents (
    namespace text NOT NULL,
    collection text NOT NULL,
    id_key text NOT NULL CHECK (length(id_key) = 64),
    raw_bson bytea NOT NULL,
    query_document jsonb,
    source_sha256 text NOT NULL CHECK (length(source_sha256) = 64),
    revision bigint NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(namespace, collection, id_key)
);
CREATE INDEX IF NOT EXISTS app_document_query ON imladri_app.documents USING gin(query_document jsonb_path_ops);
CREATE TABLE IF NOT EXISTS imladri_app.indexes (
    namespace text NOT NULL,
    collection text NOT NULL,
    name text NOT NULL,
    definition jsonb NOT NULL,
    PRIMARY KEY(namespace,collection,name)
);
CREATE TABLE IF NOT EXISTS imladri_app.unique_keys (
    namespace text NOT NULL,
    collection text NOT NULL,
    index_name text NOT NULL,
    token text NOT NULL,
    id_key text NOT NULL,
    PRIMARY KEY(namespace,collection,index_name,token),
    FOREIGN KEY(namespace,collection,id_key) REFERENCES imladri_app.documents(namespace,collection,id_key) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS imladri_app.rpc_requests (
    namespace text NOT NULL,
    request_id uuid NOT NULL,
    body_sha256 text NOT NULL CHECK (length(body_sha256) = 64),
    compact_result bytea,
    compact_error bytea,
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(namespace,request_id)
);
CREATE INDEX IF NOT EXISTS app_unique_document ON imladri_app.unique_keys(namespace,collection,id_key);
CREATE INDEX IF NOT EXISTS app_rpc_expiry ON imladri_app.rpc_requests(committed_at);
