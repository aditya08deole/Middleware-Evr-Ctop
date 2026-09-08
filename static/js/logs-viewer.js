/**
 * Logs Viewer Engine (v1.0)
 * Handles surgical updates for the dedicated logs page.
 */

class LogsViewer {
    constructor(pollingInterval = 2000) {
        this.pollingInterval = pollingInterval;
        this.timer = null;
        this.filter = 'all';
        this.deviceId = new URLSearchParams(window.location.search).get('device_id');
        this.knownLogIds = new Set();
        this.abortController = null;
        this.visibilityHandler = null;
    }

    start() {
        this.fetchLogs();
        this.timer = setInterval(() => this.fetchLogs(), this.pollingInterval);

        // Pause polling while the tab is backgrounded; catch up immediately
        // once it's visible again (see realtime-dashboard.js for the same pattern).
        this.visibilityHandler = () => {
            if (document.visibilityState === 'hidden') {
                if (this.timer) {
                    clearInterval(this.timer);
                    this.timer = null;
                }
            } else if (!this.timer) {
                this.fetchLogs();
                this.timer = setInterval(() => this.fetchLogs(), this.pollingInterval);
            }
        };
        document.addEventListener('visibilitychange', this.visibilityHandler);
    }

    stop() {
        if (this.timer) clearInterval(this.timer);
        if (this.visibilityHandler) document.removeEventListener('visibilitychange', this.visibilityHandler);
        if (this.abortController) this.abortController.abort();
    }

    setFilter(status) {
        this.filter = status;
        this.clearContainer();
        this.knownLogIds.clear();
        this.fetchLogs();
    }

    /**
     * HTML-escape a value before interpolating it into an innerHTML template.
     * Device name/type and log messages ultimately originate from
     * user-configured device data — escape them before they land in the DOM.
     */
    escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }

    /**
     * Stable FNV-1a hash of a log's own content, used as a dedupe key when
     * the backend doesn't stamp an `id` on the entry. Math.random() here
     * previously produced a *different* key for the same content-less log on
     * every poll, so it was never recognized as "already shown" and kept
     * getting re-inserted as a duplicate row.
     */
    stableLogKey(log) {
        const basis = `${log.device_id || ''}|${log.created_at || ''}|${log.message || log.error_message || ''}`;
        let hash = 0x811c9dc5;
        for (let i = 0; i < basis.length; i++) {
            hash ^= basis.charCodeAt(i);
            hash = Math.imul(hash, 0x01000193);
        }
        return (hash >>> 0).toString(36);
    }

    clearContainer() {
        const container = document.getElementById('logs-container');
        if (container) container.innerHTML = '';
    }

    async fetchLogs() {
        // Cancel any still-in-flight request before starting a new one, so
        // an older, slower response can't land after a newer one and
        // overwrite the freshly-rendered log list.
        if (this.abortController) this.abortController.abort();
        this.abortController = new AbortController();

        try {
            let url = `/api/local-logs?limit=100`;
            if (this.deviceId) url += `&device_id=${this.deviceId}`;

            const response = await fetch(url, { signal: this.abortController.signal });
            const result = await response.json();

            if (result.success) {
                this.renderLogs(result.data);
                this.updateUI(result.stats);
            }
        } catch (error) {
            if (error.name !== 'AbortError') {
                console.error('[LogsViewer] Fetch error:', error);
            }
        }
    }

    updateUI(stats) {
        const badge = document.getElementById('log-count-badge');
        if (badge) {
            const total = stats.total_logs;
            const displayed = this.knownLogIds.size;
            badge.textContent = total >= 100 ? '99+ Logs Displayed' : `${displayed} Logs Displayed`;
        }
        
        const indicator = document.getElementById('live-indicator');
        if (indicator) {
            indicator.style.opacity = '1';
            setTimeout(() => indicator.style.opacity = '0.5', 500);
        }
    }

    renderLogs(logs) {
        const container = document.getElementById('logs-container');
        if (!container) return;

        if (logs.length === 0) {
            if (container.children.length === 0) {
                container.innerHTML = '<div class="text-center py-5 text-muted"><i class="bi bi-inbox fs-1"></i><p>No logs found.</p></div>';
            }
            return;
        }

        // Filter logs
        const filteredLogs = logs.filter(log => {
            if (this.filter === 'all') return true;
            return log.status === this.filter;
        });

        // Prepend new logs
        filteredLogs.slice().reverse().forEach(log => {
            const logId = `log-${log.id || this.stableLogKey(log)}`;
            if (!this.knownLogIds.has(logId)) {
                const logDiv = document.createElement('div');
                logDiv.id = logId;
                logDiv.className = 'log-entry bg-white p-3 mb-3 shadow-sm rounded border-start border-4 new';
                
                if (log.status === 'error') logDiv.classList.add('border-danger');
                else if (log.status === 'success') logDiv.classList.add('border-success');
                else logDiv.classList.add('border-primary');
                
                logDiv.innerHTML = this.getLogTemplate(log);
                
                // Clear empty state if it exists
                if (container.querySelector('.text-center.py-5.text-muted')) {
                    container.innerHTML = '';
                }
                
                container.prepend(logDiv);
                this.knownLogIds.add(logId);
                
                setTimeout(() => logDiv.classList.remove('new'), 600);
            }
        });

        // Limit display to exactly 99
        while (container.children.length > 99) {
            const lastId = container.lastChild.id;
            this.knownLogIds.delete(lastId);
            container.removeChild(container.lastChild);
        }
    }

    getLogTemplate(log) {
        const timestamp = log.created_at ? new Date(log.created_at).toLocaleString() : 'N/A';
        const deviceName = this.escapeHtml(log.device_name || log.device_id || 'Unknown Device');

        let payloadHtml = '';
        if (log.request_payload) {
            try {
                const parsed = JSON.parse(log.request_payload);
                payloadHtml = `
                    <div class="mt-3">
                        <button class="btn btn-sm btn-link p-0 text-decoration-none" onclick="this.nextElementSibling.classList.toggle('d-none')">
                            <i class="bi bi-code-slash"></i> View Payload
                        </button>
                        <div class="d-none mt-2 bg-dark text-light p-3 rounded" style="font-family: 'Consolas', monospace; font-size: 11px;">
                            <pre class="m-0 text-success"><code>${this.escapeHtml(JSON.stringify(parsed, null, 2))}</code></pre>
                        </div>
                    </div>`;
            } catch(e) {
                payloadHtml = `<div class="mt-2 small text-muted">Payload: ${this.escapeHtml(log.request_payload.substring(0, 50))}...</div>`;
            }
        }

        return `
            <div class="d-flex justify-content-between align-items-center mb-2">
                <div class="d-flex align-items-center gap-2">
                    <div class="bg-light p-2 rounded">
                        <i class="bi bi-cpu text-primary"></i>
                    </div>
                    <div>
                        <h6 class="mb-0 fw-bold">${deviceName}</h6>
                        <small class="text-muted">${this.escapeHtml(log.device_type || 'IoT Node')}</small>
                    </div>
                </div>
                <div class="text-end">
                    <span class="badge ${log.status === 'success' ? 'bg-success' : 'bg-danger'} px-3">
                        ${this.escapeHtml((log.status || '').toUpperCase())}
                    </span>
                    <div class="text-muted" style="font-size: 10px; margin-top: 4px;">
                        <i class="bi bi-clock"></i> ${timestamp}
                    </div>
                </div>
            </div>
            <div class="mt-2 ps-1 border-top pt-2">
                <span class="badge bg-light text-dark border mb-2" style="font-size: 9px;">${this.escapeHtml((log.log_type || '').toUpperCase())}</span>
                <p class="mb-0 text-dark" style="font-size: 14px;">${this.escapeHtml(log.message || log.error_message)}</p>
                ${payloadHtml}
            </div>
        `;
    }
}

// Global functions for the UI
const viewer = new LogsViewer();

function setFilter(status) {
    viewer.setFilter(status);
}

function clearLocalLogs() {
    viewer.clearContainer();
    viewer.knownLogIds.clear();
}

document.addEventListener('DOMContentLoaded', () => {
    viewer.start();
});
