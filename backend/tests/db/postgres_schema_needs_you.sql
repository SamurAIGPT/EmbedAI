CREATE TABLE needs_you_state (
	owner_id VARCHAR NOT NULL, 
	time_zone VARCHAR, 
	last_active_at TIMESTAMP WITH TIME ZONE, 
	card_day VARCHAR, 
	card_thread_id BIGINT, 
	card_hash VARCHAR, 
	card_revision INTEGER DEFAULT '0' NOT NULL, 
	notified JSON NOT NULL, 
	PRIMARY KEY (owner_id)
);
