app_name = "migration"
app_title = "Migration"
app_publisher = "Sena"
app_description = "AI-powered data migration agent-app"
app_email = "hello@senaagents.com"
app_license = "MIT"

after_migrate = "migration.setup.ensure_integration_source_types"

talk_to_your_data_sources = [
	{
		"source_key": "migration_integrations",
		"title": "Migration Sources",
		"subtitle": "Normalized records from connected external systems",
		"agent_name": "Migration",
		"source_registry_item": "agent/migration",
		"required_app": "migration",
		"implementation_status": "Ready",
		"sort_order": 30,
		"source_kind": "external_integration",
		"adapter": "integration_record",
		"capabilities": {
			"schema_discovery": True,
			"preview": True,
			"filters": True,
			"raw_sql": False,
		},
	},
]

# Whitelisted API methods
# accessible via /api/method/migration.agentapp_migration.migration.api.<method>
