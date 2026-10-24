CREATE TABLE work_board (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	key VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (owner_id, key)
);

CREATE TABLE work_column (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	board_id BIGINT NOT NULL, 
	key VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	position INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (board_id, key), 
	FOREIGN KEY(board_id) REFERENCES work_board (id)
);

CREATE TABLE work_item (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	board_id BIGINT NOT NULL, 
	column_id BIGINT NOT NULL, 
	title VARCHAR NOT NULL, 
	description TEXT DEFAULT '' NOT NULL, 
	priority VARCHAR DEFAULT 'normal' NOT NULL, 
	due_date DATE, 
	due_at TIMESTAMP WITH TIME ZONE, 
	timezone VARCHAR DEFAULT 'UTC' NOT NULL, 
	agent_id BIGINT, 
	team_id BIGINT, 
	thread_id BIGINT, 
	position BIGINT NOT NULL, 
	archived BOOLEAN DEFAULT false NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(board_id) REFERENCES work_board (id), 
	FOREIGN KEY(column_id) REFERENCES work_column (id)
);

CREATE INDEX ix_work_item_owner_board ON work_item (owner_id, board_id);
