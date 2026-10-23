CREATE TABLE agent_job (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	root_id BIGINT, 
	parent_id BIGINT, 
	depth INTEGER DEFAULT '0' NOT NULL, 
	origin_kind VARCHAR DEFAULT 'person' NOT NULL, 
	origin_ref VARCHAR, 
	origin_quote TEXT, 
	requester_agent_id BIGINT NOT NULL, 
	requester_thread_id BIGINT NOT NULL, 
	requester_turn_id BIGINT NOT NULL, 
	assignee_agent_id BIGINT NOT NULL, 
	assignee_thread_id BIGINT, 
	assignee_turn_id BIGINT, 
	mode VARCHAR DEFAULT 'work' NOT NULL, 
	brief TEXT NOT NULL, 
	brief_hash VARCHAR NOT NULL, 
	done_when TEXT, 
	status VARCHAR DEFAULT 'queued' NOT NULL, 
	outcome VARCHAR, 
	outcome_detail TEXT, 
	result TEXT, 
	approval_mode VARCHAR DEFAULT 'ask' NOT NULL, 
	executions INTEGER DEFAULT '0' NOT NULL, 
	budget_requests INTEGER DEFAULT '24' NOT NULL, 
	budget_executions INTEGER DEFAULT '48' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	last_activity_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_agent_job_requester_turn ON agent_job (requester_turn_id);

CREATE INDEX ix_agent_job_status ON agent_job (owner_id, status);

CREATE TABLE team (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	lead_agent_id BIGINT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_team_owner ON team (owner_id);

CREATE TABLE team_event (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	team_id BIGINT NOT NULL, 
	kind VARCHAR NOT NULL, 
	agent_id BIGINT, 
	job_id BIGINT, 
	thread_id BIGINT, 
	data JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE agent_job_event (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	job_id BIGINT NOT NULL, 
	kind VARCHAR NOT NULL, 
	detail JSON NOT NULL, 
	at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_agent_job_event_job_id_agent_job FOREIGN KEY(job_id) REFERENCES agent_job (id)
);
