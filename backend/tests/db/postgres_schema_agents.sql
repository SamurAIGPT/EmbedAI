CREATE TABLE agent (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	title VARCHAR, 
	description TEXT, 
	instructions TEXT DEFAULT '' NOT NULL, 
	provider VARCHAR DEFAULT 'claude' NOT NULL, 
	model VARCHAR, 
	workspace_path VARCHAR, 
	approval_mode VARCHAR DEFAULT 'ask' NOT NULL, 
	pinned BOOLEAN DEFAULT false NOT NULL, 
	archived_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_agent_owner_name_live ON agent (owner_id, name) WHERE archived_at IS NULL;
