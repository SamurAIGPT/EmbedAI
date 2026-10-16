CREATE TABLE app_setting (
	key VARCHAR NOT NULL, 
	value_json JSON, 
	version INTEGER NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_by VARCHAR NOT NULL, 
	PRIMARY KEY (key)
);

CREATE TABLE setting_audit (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	key VARCHAR NOT NULL, 
	old_value_json JSON, 
	new_value_json JSON, 
	at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	actor VARCHAR NOT NULL, 
	PRIMARY KEY (id)
);
