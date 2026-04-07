const API_BASE = '/api/method/custom_app_migration.custom_app_migration.api'

function getCSRF() {
  // Try reading from cookie
  const match = document.cookie.match(/csrf_token=([^;]+)/)
  if (match) return match[1]
  // Try reading from Frappe global (when loaded inside Frappe desk)
  if (window.frappe?.csrf_token) return window.frappe.csrf_token
  return ''
}

async function callAPI(method, params = {}) {
  const res = await fetch(`${API_BASE}.${method}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Frappe-CSRF-Token': getCSRF(),
    },
    body: JSON.stringify(params),
  })
  const data = await res.json()
  if (data.exc) throw new Error(data.exc)
  return data.message || data
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
      method: `custom_app_migration.custom_app_migration.api.${method}`,
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

export async function getMigrationStatus(migrationId) {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('get_migration_status', { migration_id: migrationId })
}

export async function getMigrationHistory() {
  const fn = isIframe ? callBridgeAPI : callAPI
  return fn('get_migration_history')
}
