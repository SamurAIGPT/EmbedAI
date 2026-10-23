CREATE TABLE team_notice (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	team_id BIGINT NOT NULL, 
	family VARCHAR DEFAULT 'incident' NOT NULL, 
	kind VARCHAR NOT NULL, 
	subject_agent_id BIGINT NOT NULL, 
	subject_thread_id BIGINT NOT NULL, 
	room_id BIGINT, 
	key VARCHAR, 
	detail TEXT, 
	last_request TEXT, 
	last_reply TEXT, 
	replay VARCHAR, 
	count INTEGER DEFAULT '1' NOT NULL, 
	already_notified BOOLEAN DEFAULT false NOT NULL, 
	chipped BOOLEAN DEFAULT false NOT NULL, 
	status VARCHAR DEFAULT 'pending' NOT NULL, 
	lead_turn_id BIGINT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	delivered_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_team_notice_status ON team_notice (owner_id, status);

CREATE INDEX ix_team_notice_thread ON team_notice (owner_id, subject_thread_id, family, created_at);
