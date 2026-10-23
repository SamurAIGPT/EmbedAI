CREATE TABLE computer (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	owner_id VARCHAR NOT NULL, 
	agent_id BIGINT NOT NULL, 
	provider VARCHAR DEFAULT 'docker' NOT NULL, 
	kind VARCHAR DEFAULT 'shell' NOT NULL, 
	image VARCHAR NOT NULL, 
	image_id VARCHAR, 
	cpu_millis INTEGER DEFAULT '1000' NOT NULL, 
	memory_mb INTEGER DEFAULT '1024' NOT NULL, 
	pids_limit INTEGER DEFAULT '256' NOT NULL, 
	network VARCHAR DEFAULT 'none' NOT NULL, 
	idle_minutes INTEGER DEFAULT '30' NOT NULL, 
	auto_start BOOLEAN DEFAULT true NOT NULL, 
	state VARCHAR DEFAULT 'missing' NOT NULL, 
	last_error TEXT, 
	workspace_path VARCHAR, 
	container_id VARCHAR, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_computer_agent_id ON computer (agent_id);
