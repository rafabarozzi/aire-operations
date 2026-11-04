CREATE TABLE organizations (tenant text PRIMARY KEY, policy jsonb NOT NULL, version integer NOT NULL);
CREATE TABLE incidents (tenant text NOT NULL, id uuid NOT NULL, body jsonb NOT NULL,
    PRIMARY KEY (tenant,id));
CREATE TABLE events (tenant text NOT NULL, incident_id uuid NOT NULL, id uuid NOT NULL, version integer NOT NULL,
    body jsonb NOT NULL, PRIMARY KEY (tenant,incident_id,id), UNIQUE (tenant,incident_id,version),
    FOREIGN KEY (tenant,incident_id) REFERENCES incidents);
CREATE TABLE commands (tenant text NOT NULL, actor text NOT NULL, key text NOT NULL,
    digest text NOT NULL, response jsonb NOT NULL, PRIMARY KEY (tenant,actor,key));
CREATE TABLE schedules (tenant text NOT NULL, incident_id uuid NOT NULL, due_at timestamptz NOT NULL,
    pending uuid, escalation_at timestamptz, escalated boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant,incident_id), FOREIGN KEY (tenant,incident_id) REFERENCES incidents);
CREATE INDEX due_jobs ON schedules (due_at);
CREATE TABLE deliveries (tenant text NOT NULL, incident_id uuid NOT NULL, prompt uuid NOT NULL,
    kind text NOT NULL, provider text NOT NULL, room_id text NOT NULL, body jsonb NOT NULL,
    PRIMARY KEY (tenant,incident_id,prompt,kind,provider,room_id), FOREIGN KEY (tenant,incident_id) REFERENCES incidents);
CREATE TABLE publications (tenant text NOT NULL, incident_id uuid NOT NULL,id uuid NOT NULL,
    audience text NOT NULL, body jsonb NOT NULL, PRIMARY KEY (tenant,incident_id,id),
    FOREIGN KEY (tenant,incident_id) REFERENCES incidents);
CREATE TABLE policy_audit (tenant text NOT NULL, version integer NOT NULL, author text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(), body jsonb NOT NULL, PRIMARY KEY (tenant,version));
