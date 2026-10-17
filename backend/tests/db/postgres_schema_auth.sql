CREATE TABLE auth_owner (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	password_hash VARCHAR, 
	user_id VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE auth_pairing_code (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	code_hash VARCHAR(64) NOT NULL, 
	label VARCHAR DEFAULT '' NOT NULL, 
	scopes VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	used_at TIMESTAMP WITH TIME ZONE, 
	failed_attempts INTEGER DEFAULT '0' NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_auth_pairing_code_code_hash ON auth_pairing_code (code_hash);

CREATE TABLE auth_session (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	kind VARCHAR NOT NULL, 
	label VARCHAR NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	scopes VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_auth_session_token_hash ON auth_session (token_hash);
