CREATE TABLE agent (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	title VARCHAR, 
	description TEXT, 
	instructions TEXT DEFAULT '' NOT NULL, 
	provider VARCHAR DEFAULT 'claude' NOT NULL, 
	model VARCHAR, 
	fallback_provider VARCHAR, 
	fallback_model VARCHAR, 
	workspace_path VARCHAR, 
	approval_mode VARCHAR DEFAULT 'ask' NOT NULL, 
	memory_enabled BOOLEAN DEFAULT true NOT NULL, 
	recall_enabled BOOLEAN DEFAULT true NOT NULL, 
	memory_upkeep BOOLEAN DEFAULT true NOT NULL, 
	compaction_summary BOOLEAN DEFAULT true NOT NULL, 
	pinned BOOLEAN DEFAULT false NOT NULL, 
	team_id BIGINT, 
	delegation_enabled BOOLEAN DEFAULT true NOT NULL, 
	created_by_agent_id BIGINT, 
	instructions_reviewed_at TIMESTAMP WITH TIME ZONE, 
	archived_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_agent_owner_name_live ON agent (owner_id, name) WHERE archived_at IS NULL;
