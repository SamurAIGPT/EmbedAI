CREATE TABLE link_code (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	code VARCHAR NOT NULL, 
	user_id VARCHAR, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	claimed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_link_code_code ON link_code (code);

CREATE INDEX ix_link_code_user_id ON link_code (user_id);

CREATE TABLE permission_rule (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	user_id VARCHAR NOT NULL, 
	bucket VARCHAR NOT NULL, 
	connector VARCHAR, 
	tool VARCHAR, 
	decision VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_permission_rule_bucket ON permission_rule (bucket);

CREATE INDEX ix_permission_rule_lookup ON permission_rule (user_id, bucket, connector, tool);

CREATE INDEX ix_permission_rule_user_id ON permission_rule (user_id);

CREATE TABLE schedule (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	user_id VARCHAR NOT NULL, 
	provider VARCHAR NOT NULL, 
	box_id VARCHAR NOT NULL, 
	prompt_text TEXT NOT NULL, 
	cron_expression VARCHAR NOT NULL, 
	timezone VARCHAR NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	next_run_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_run_at TIMESTAMP WITH TIME ZONE, 
	last_error TEXT, 
	consecutive_failures INTEGER NOT NULL, 
	paused_reason TEXT, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_schedule_next_run_at ON schedule (next_run_at);

CREATE INDEX ix_schedule_user_id ON schedule (user_id);

CREATE TABLE user_credential (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	user_id VARCHAR NOT NULL, 
	provider VARCHAR NOT NULL, 
	file_path VARCHAR NOT NULL, 
	ciphertext TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_user_credential_user_provider UNIQUE (user_id, provider)
);

CREATE INDEX ix_user_credential_user_id ON user_credential (user_id);

CREATE TABLE user_sandbox (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	user_id VARCHAR NOT NULL, 
	box_id VARCHAR NOT NULL, 
	state VARCHAR, 
	machine_type VARCHAR, 
	ttl_seconds INTEGER NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (box_id)
);

CREATE INDEX ix_user_sandbox_is_active ON user_sandbox (is_active);

CREATE INDEX ix_user_sandbox_user_id ON user_sandbox (user_id);

CREATE TABLE task (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	user_id VARCHAR NOT NULL, 
	provider VARCHAR NOT NULL, 
	box_id VARCHAR NOT NULL, 
	prompt_text TEXT NOT NULL, 
	status VARCHAR NOT NULL, 
	prompt_id VARCHAR, 
	session_id VARCHAR, 
	schedule_id BIGINT, 
	scheduled_for TIMESTAMP WITH TIME ZONE, 
	result TEXT, 
	reported_at TIMESTAMP WITH TIME ZONE, 
	error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_task_schedule_id_scheduled_for UNIQUE (schedule_id, scheduled_for), 
	CONSTRAINT fk_task_schedule_id_schedule FOREIGN KEY(schedule_id) REFERENCES schedule (id)
);

CREATE INDEX ix_task_schedule_id ON task (schedule_id);

CREATE INDEX ix_task_session_id ON task (session_id);

CREATE INDEX ix_task_status ON task (status);

CREATE INDEX ix_task_user_id ON task (user_id);

CREATE TABLE permission_ask (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	task_id BIGINT NOT NULL, 
	user_id VARCHAR NOT NULL, 
	bucket VARCHAR NOT NULL, 
	connector VARCHAR, 
	tool VARCHAR, 
	status VARCHAR NOT NULL, 
	decision VARCHAR, 
	always BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	answered_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_permission_ask_task_id_task FOREIGN KEY(task_id) REFERENCES task (id)
);

CREATE INDEX ix_permission_ask_status ON permission_ask (status);

CREATE INDEX ix_permission_ask_task_id ON permission_ask (task_id);

CREATE INDEX ix_permission_ask_user_id ON permission_ask (user_id);
