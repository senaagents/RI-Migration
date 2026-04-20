const API_BASE = '/api/method/agentapp_migration.agentapp_migration.api'
let csrfToken = ''

function getCSRF() {
  // Try reading from cookie
  const match = document.cookie.match(/csrf_token=([^;]+)/)
  if (match) return match[1]
  // Try reading from Frappe global (when loaded inside Frappe desk)
  if (window.frappe?.csrf_token) return window.frappe.csrf_token
  return ''
}

async function getCSRFToken() {
  const existing = getCSRF()
  if (existing) return existing
  if (csrfToken) return csrfToken

  const res = await fetch(`${API_BASE}.get_csrf_token`, {
    method: 'GET',
    credentials: 'same-origin',
  })
  const data = await res.json()
  if (!res.ok || data.exc || data.exception || data.exc_type) {
    throw new Error(extractFrappeError(data) || `get_csrf_token failed with HTTP ${res.status}`)
  }
  csrfToken = data.message || ''
  return csrfToken
}

async function callAPI(method, params = {}, options = {}) {
  const httpMethod = options.httpMethod || 'POST'
  const headers = { 'Content-Type': 'application/json' }
  const fetchOptions = {
    method: httpMethod,
    headers,
    credentials: 'same-origin',
  }
  if (httpMethod !== 'GET') {
    headers['X-Frappe-CSRF-Token'] = await getCSRFToken()
    fetchOptions.body = JSON.stringify(params)
  }

  const res = await fetch(`${API_BASE}.${method}`, {
    ...fetchOptions,
  })
  const data = await res.json()
  if (!res.ok || data.exc || data.exception || data.exc_type) {
    throw new Error(extractFrappeError(data) || `${method} failed with HTTP ${res.status}`)
  }
  return data.message || data
}

function extractFrappeError(data) {
  if (!data) return ''
  if (data.message && typeof data.message === 'string') return data.message
  if (data._server_messages) {
    try {
      const messages = JSON.parse(data._server_messages)
      const first = messages[0] ? JSON.parse(messages[0]) : null
      if (first?.message) return first.message.replace(/<[^>]*>/g, '')
    } catch {
      // Fall through to lower fidelity error fields.
    }
  }
  return data.exception || data.exc || data.exc_type || ''
}

// If running inside Sena iframe, use the bridge API proxy
function callBridgeAPI(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = `api_${Date.now()}_${Math.random().toString(36).slice(2)}`
    function handler(event) {
      if (event.data?.type === 'sena:api-response' && event.data.id === id) {
        window.removeEventListener('message', handler)
        if (event.data.error) reject(new Error(event.data.error))
        else resolve(event.data.data)
      }
    }
    window.addEventListener('message', handler)
    window.parent.postMessage({
      type: 'sena:api-call',
      id,
      method: `agentapp_migration.agentapp_migration.api.${method}`,
      params,
    }, '*')
    // Timeout after 30s
    setTimeout(() => {
      window.removeEventListener('message', handler)
      reject(new Error('API call timed out'))
    }, 30000)
  })
}

const isIframe = window.self !== window.top

export async function getTargetCompanies() {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('get_target_companies')
}

export async function getIntegrationSourceTypes() {
  if (isIframe) return callBridgeAPI('get_integration_source_types')
  return callAPI('get_integration_source_types', {}, { httpMethod: 'GET' })
}

export async function getMigrationTargetTypes() {
  if (isIframe) return callBridgeAPI('get_migration_target_types')
  return callAPI('get_migration_target_types', {}, { httpMethod: 'GET' })
}

export async function getMigrationTargetSchema(targetKey) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('get_migration_target_schema', { target_key: targetKey })
}

export async function testTallyConnection(host, port) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('test_tally_connection', { host, port })
}

export async function fetchTallyData(host, port) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('fetch_tally_data', { host, port })
}

export async function executeMigration(host, port, companyName, companyAbbr, dryRun = false) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('execute_migration', {
    host, port, company_name: companyName, company_abbr: companyAbbr, dry_run: dryRun,
  })
}

export async function getMigrationStatus(jobId) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('get_migration_status', { job_id: jobId })
}

export async function getMigrationHistory() {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('get_migration_history')
}

export async function createIntegrationTallyPairing(connectionLabel = 'Tally connection') {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('create_integration_tally_pairing', { connection_label: connectionLabel })
}

export async function createSapHanaDiscoverySnapshot(connectionLabel = 'SAP HANA source') {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('create_sap_hana_discovery_snapshot', { connection_label: connectionLabel })
}

export async function extractSapHanaMasterData(connectionId) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('extract_sap_hana_master_data', { connection_id: connectionId })
}

export async function planSapHanaMasterMigration({ connectionId, company, selectedRecordTypes = [] } = {}) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('plan_sap_hana_master_migration', {
    connection_id: connectionId,
    company,
    selected_record_types_json: JSON.stringify(selectedRecordTypes),
  })
}

export async function listIntegrationConnections() {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('list_integration_connections')
}

export async function deleteIntegrationConnection(connectionId) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('delete_integration_connection', { connection_id: connectionId })
}

export async function getIntegrationConnectionStatus(connectionId) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('get_integration_connection_status', { connection_id: connectionId })
}

export async function getIntegrationSourceSchema({ connectionId = '', sourceKey = '', sampleLimit = 5 } = {}) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('get_integration_source_schema', {
    connection_id: connectionId,
    source_key: sourceKey,
    sample_limit: sampleLimit,
  })
}

export async function queryIntegrationRecords({
  connectionId,
  recordType = '',
  filters = [],
  fields = [],
  limit = 50,
} = {}) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('query_integration_records', {
    connection_id: connectionId,
    record_type: recordType,
    filters_json: JSON.stringify(filters),
    fields_json: JSON.stringify(fields),
    limit,
  })
}
