CREATE TABLE notification (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	body TEXT, 
	routine_id BIGINT, 
	run_id BIGINT, 
	thread_id BIGINT, 
	read_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_notification_owner_id_created_at ON notification (owner_id, created_at);

CREATE INDEX ix_notification_owner_id_read_at ON notification (owner_id, read_at);

CREATE TABLE routine (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	agent_id BIGINT NOT NULL, 
	name VARCHAR NOT NULL, 
	prompt TEXT NOT NULL, 
	schedule_kind VARCHAR NOT NULL, 
	schedule_spec JSON NOT NULL, 
	timezone VARCHAR DEFAULT 'UTC' NOT NULL, 
	paused BOOLEAN DEFAULT false NOT NULL, 
	paused_reason TEXT, 
	next_run_at TIMESTAMP WITH TIME ZONE, 
	run_limit_minutes INTEGER DEFAULT '30' NOT NULL, 
	approval_mode VARCHAR DEFAULT 'ask' NOT NULL, 
	approval_timeout_minutes INTEGER DEFAULT '30' NOT NULL, 
	catch_up_minutes INTEGER DEFAULT '60' NOT NULL, 
	retry_limit INTEGER DEFAULT '0' NOT NULL, 
	retry_delay_minutes INTEGER DEFAULT '5' NOT NULL, 
	webhook_secret_hash VARCHAR, 
	last_run_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_routine_paused_next_run_at ON routine (paused, next_run_at);

CREATE TABLE routine_run (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	routine_id BIGINT NOT NULL, 
	trigger VARCHAR NOT NULL, 
	state VARCHAR DEFAULT 'queued' NOT NULL, 
	scheduled_for TIMESTAMP WITH TIME ZONE, 
	prompt TEXT, 
	thread_id BIGINT, 
	turn_id BIGINT, 
	result TEXT, 
	error TEXT, 
	waiting_since TIMESTAMP WITH TIME ZONE, 
	waited_seconds INTEGER DEFAULT '0' NOT NULL, 
	attempt INTEGER DEFAULT '1' NOT NULL, 
	retry_of BIGINT, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_routine_run_routine_id_routine FOREIGN KEY(routine_id) REFERENCES routine (id)
);

CREATE INDEX ix_routine_run_routine_id_created_at ON routine_run (routine_id, created_at);

CREATE INDEX ix_routine_run_state ON routine_run (state);

CREATE UNIQUE INDEX uq_routine_run_occurrence ON routine_run (routine_id, scheduled_for) WHERE trigger = 'schedule';

CREATE UNIQUE INDEX uq_routine_run_retry_of ON routine_run (retry_of);
