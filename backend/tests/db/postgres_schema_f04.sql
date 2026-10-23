CREATE TABLE thread (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	title VARCHAR, 
	provider VARCHAR NOT NULL, 
	model VARCHAR, 
	provider_session_id VARCHAR, 
	session_state VARCHAR DEFAULT 'new' NOT NULL, 
	provider_state JSON, 
	workspace_path VARCHAR, 
	agent_id BIGINT, 
	kind VARCHAR DEFAULT 'chat' NOT NULL, 
	job_id BIGINT, 
	room_id BIGINT, 
	last_seq BIGINT DEFAULT '0' NOT NULL, 
	archived_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_thread_owner_listing ON thread (owner_id, archived_at, updated_at);

CREATE UNIQUE INDEX uq_thread_provider_session ON thread (provider, provider_session_id) WHERE provider_session_id IS NOT NULL;

CREATE TABLE turn (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	thread_id BIGINT NOT NULL, 
	seq INTEGER NOT NULL, 
	prompt_text TEXT NOT NULL, 
	status VARCHAR DEFAULT 'running' NOT NULL, 
	error_code VARCHAR, 
	result TEXT, 
	error TEXT, 
	model VARCHAR, 
	usage JSON, 
	owner_token VARCHAR, 
	owner_instance VARCHAR, 
	heartbeat_at TIMESTAMP WITH TIME ZONE, 
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	engine_phase VARCHAR, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_turn_thread_id_seq UNIQUE (thread_id, seq), 
	CONSTRAINT fk_turn_thread_id_thread FOREIGN KEY(thread_id) REFERENCES thread (id)
);

CREATE INDEX ix_turn_status_heartbeat ON turn (status, heartbeat_at);

CREATE UNIQUE INDEX uq_turn_one_running_per_thread ON turn (thread_id) WHERE status = 'running';

CREATE TABLE approval (
	id VARCHAR NOT NULL, 
	thread_id BIGINT NOT NULL, 
	turn_id BIGINT NOT NULL, 
	request_id VARCHAR NOT NULL, 
	tool VARCHAR NOT NULL, 
	input JSON NOT NULL, 
	summary TEXT, 
	kind VARCHAR, 
	status VARCHAR DEFAULT 'pending' NOT NULL, 
	delivered_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	consumed_at TIMESTAMP WITH TIME ZONE, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_approval_thread_id_thread FOREIGN KEY(thread_id) REFERENCES thread (id), 
	CONSTRAINT fk_approval_turn_id_turn FOREIGN KEY(turn_id) REFERENCES turn (id)
);

CREATE INDEX ix_approval_turn_id_status ON approval (turn_id, status);

CREATE TABLE event (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	thread_id BIGINT NOT NULL, 
	turn_id BIGINT, 
	seq BIGINT NOT NULL, 
	type VARCHAR NOT NULL, 
	data JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_event_thread_id_seq UNIQUE (thread_id, seq), 
	CONSTRAINT fk_event_thread_id_thread FOREIGN KEY(thread_id) REFERENCES thread (id), 
	CONSTRAINT fk_event_turn_id_turn FOREIGN KEY(turn_id) REFERENCES turn (id)
);

CREATE INDEX ix_event_turn_id ON event (turn_id);
