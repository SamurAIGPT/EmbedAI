CREATE TABLE room (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	bulletin TEXT DEFAULT '' NOT NULL, 
	default_responder_kind VARCHAR DEFAULT 'member' NOT NULL, 
	default_responder_agent_id BIGINT, 
	thread_id BIGINT NOT NULL, 
	last_read_seq BIGINT DEFAULT '0' NOT NULL, 
	archived_at TIMESTAMP WITH TIME ZONE, 
	deleting_at TIMESTAMP WITH TIME ZONE, 
	created_by VARCHAR DEFAULT 'person' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_room_owner_name_live ON room (owner_id, name) WHERE archived_at IS NULL;

CREATE TABLE room_dispatch (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	room_id BIGINT NOT NULL, 
	message_seq BIGINT NOT NULL, 
	agent_id BIGINT NOT NULL, 
	reason VARCHAR DEFAULT 'default' NOT NULL, 
	chain_depth INTEGER DEFAULT '0' NOT NULL, 
	status VARCHAR DEFAULT 'queued' NOT NULL, 
	turn_id BIGINT, 
	error_code VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_room_dispatch_room_id_room FOREIGN KEY(room_id) REFERENCES room (id)
);

CREATE INDEX ix_room_dispatch_queue ON room_dispatch (room_id, status);

CREATE UNIQUE INDEX uq_room_dispatch_message_agent ON room_dispatch (room_id, message_seq, agent_id);

CREATE TABLE room_member (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	room_id BIGINT NOT NULL, 
	agent_id BIGINT NOT NULL, 
	member_thread_id BIGINT, 
	last_seen_seq BIGINT DEFAULT '0' NOT NULL, 
	held_at TIMESTAMP WITH TIME ZONE, 
	added_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_room_member_room_id_room FOREIGN KEY(room_id) REFERENCES room (id)
);

CREATE UNIQUE INDEX uq_room_member ON room_member (room_id, agent_id);
