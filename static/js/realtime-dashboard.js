/**
 * Realtime Dashboard Engine (v2.2 - Clean Row Removal & Compact Time Pills)
 * Handles surgical updates for devices, statistics, and device status filtering.
 */

class RealtimeDashboard {
    constructor(pollingInterval = 3000) {
        this.pollingInterval = pollingInterval;
        this.timer = null;
        this.knownDeviceIds = new Set();
        this.currentFilter = 'all';
    }

    start() {
        this.fetchData();
        this.timer = setInterval(() => this.fetchData(), this.pollingInterval);
    }

    stop() {
        if (this.timer) clearInterval(this.timer);
    }

    async fetchData() {
        try {
            const response = await fetch('/api/local-logs');
            const result = await response.json();
            
            if (result.success) {
                this.updateStats(result.stats);
                this.updateDevices(result.stats.active_devices_list || []);
            }
        } catch (error) {
            console.error('[Dashboard] Fetch error:', error);
        }
    }

    setFilter(filterType) {
        this.currentFilter = filterType;
        
        // Highlight active tab button
        document.querySelectorAll('.device-filter-btn').forEach(btn => {
            if (btn.getAttribute('data-filter') === filterType) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });

        this.applyFilter();
    }

    applyFilter() {
        const tbody = document.getElementById('devices-body');
        if (!tbody) return;
        
        const rows = tbody.querySelectorAll('tr[data-device-id]');
        let visibleCount = 0;

        rows.forEach(row => {
            const isActive = row.getAttribute('data-active') === 'true';
            const hasError = row.getAttribute('data-error') === 'true';
            
            let show = false;
            if (this.currentFilter === 'all') {
                show = true;
            } else if (this.currentFilter === 'active') {
                show = isActive;
            } else if (this.currentFilter === 'error') {
                show = hasError;
            } else if (this.currentFilter === 'inactive') {
                show = !isActive;
            }
            
            row.style.display = show ? '' : 'none';
            if (show) visibleCount++;
        });

        // Handle empty filter state message
        let noResultRow = tbody.querySelector('.no-filter-results-row');
        if (visibleCount === 0 && rows.length > 0) {
            if (!noResultRow) {
                noResultRow = document.createElement('tr');
                noResultRow.className = 'no-filter-results-row';
                noResultRow.innerHTML = `<td colspan="6" class="text-center py-4 text-muted"><i class="bi bi-funnel me-1"></i> No devices match the <strong>${this.currentFilter}</strong> filter.</td>`;
                tbody.appendChild(noResultRow);
            }
            noResultRow.style.display = '';
        } else if (noResultRow) {
            noResultRow.style.display = 'none';
        }
    }

    updateStats(stats) {
        const elements = {
            'total-devices': stats.total_devices,
            'active-devices': stats.active_devices,
            'total-logs': stats.total_logs,
            'error-logs': stats.error_logs
        };
        
        for (let [id, value] of Object.entries(elements)) {
            if (id === 'total-logs' && value >= 100) {
                value = '99+';
            }
            
            const el = document.getElementById(id);
            if (el && el.textContent !== String(value)) {
                el.textContent = value;
                const card = el.closest('.stats-card');
                if (card) {
                    card.classList.add('stat-glow');
                    setTimeout(() => card.classList.remove('stat-glow'), 1000);
                }
            }
        }
    }

    updateDevices(devices) {
        const tbody = document.getElementById('devices-body');
        if (!tbody) return;

        // Helpers for robust property checking
        const isDevActive = (d) => d.is_active !== false && d.is_active !== 'false' && d.is_active !== 0;
        const hasDevError = (d) => d.last_status && (String(d.last_status).toLowerCase() === 'error' || String(d.last_status).toLowerCase() === 'failed');

        const formatSyncTime = (isoString) => {
            if (!isoString) return '<span class="text-muted small">Never</span>';
            const d = new Date(isoString);
            if (isNaN(d.getTime())) return '<span class="text-muted small">Never</span>';
            const dateStr = d.toLocaleDateString(undefined, { month: 'numeric', day: 'numeric', year: '2-digit' });
            const timeStr = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
            return `<div class="sync-time-pill"><span class="sync-date">${dateStr}</span><span class="sync-time">${timeStr}</span></div>`;
        };

        // Calculate filter tab counter badges accurately
        const countAll = devices.length;
        const countActive = devices.filter(d => isDevActive(d)).length;
        const countError = devices.filter(d => hasDevError(d)).length;
        const countInactive = devices.filter(d => !isDevActive(d)).length;

        // Update UI pill counts
        const elAll = document.getElementById('filter-count-all');
        if (elAll) elAll.textContent = countAll;
        const elActive = document.getElementById('filter-count-active');
        if (elActive) elActive.textContent = countActive;
        const elError = document.getElementById('filter-count-error');
        if (elError) elError.textContent = countError;
        const elInactive = document.getElementById('filter-count-inactive');
        if (elInactive) elInactive.textContent = countInactive;
        
        if (devices.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-muted">No devices registered. <a href="/add-device" class="fw-semibold">Add your first device</a></td></tr>';
            this.knownDeviceIds.clear();
            return;
        }

        // Clean up initial static/loading placeholder rows
        tbody.querySelectorAll('tr:not([data-device-id])').forEach(r => {
            if (!r.classList.contains('no-filter-results-row')) {
                r.remove();
            }
        });

        devices.forEach(device => {
            let row = tbody.querySelector(`tr[data-device-id="${device.id}"]`);
            const isActive = isDevActive(device);
            const hasError = hasDevError(device);
            const statusClass = device.last_status === 'success' ? 'status-success' : 
                               hasError ? 'status-error' : 'status-pending';
            const lastSyncHtml = formatSyncTime(device.last_sync_time);
            const statusText = (device.last_status || 'pending').toUpperCase();
            
            const isEmqx = device.data_source === 'emqx';
            const channelHtml = isEmqx ? 
                `<span class="badge bg-success me-1">EMQX</span><code class="small text-truncate d-inline-block" style="max-width: 160px;" title="${device.emqx_topic || ''}">${device.emqx_topic || 'MQTT'}</code>` :
                `<span class="badge bg-primary me-1">ThingSpeak</span><code>${device.channel_id}</code>`;
            
            if (!row) {
                // New device row
                row = document.createElement('tr');
                row.setAttribute('data-device-id', device.id);
                row.setAttribute('data-active', isActive ? 'true' : 'false');
                row.setAttribute('data-error', hasError ? 'true' : 'false');
                
                row.innerHTML = `
                    <td class="col-name">
                        <strong class="device-name">${device.name}</strong>
                    </td>
                    <td class="col-type"><span class="badge bg-secondary">${device.device_type || 'Unknown'}</span></td>
                    <td class="col-channel">${channelHtml}</td>
                    <td class="col-status"><span class="status-badge ${statusClass}">${statusText}</span></td>
                    <td class="col-sync">${lastSyncHtml}</td>
                    <td class="col-actions">
                        <div class="btn-group">
                            <button class="btn btn-sm btn-outline-primary" onclick="fetchDevice('${device.id}')" title="Sync">
                                <i class="bi bi-arrow-repeat"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-info" onclick="location.href='/logs?device_id=${device.id}'" title="Logs">
                                <i class="bi bi-journal"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-warning" onclick="toggleDevice('${device.id}')" title="Toggle">
                                <i class="bi bi-power"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-danger" onclick="deleteDevice('${device.id}')" title="Delete">
                                <i class="bi bi-trash"></i>
                            </button>
                        </div>
                    </td>
                `;
                
                if (this.knownDeviceIds.size > 0) {
                    row.className = 'new-device-pulse';
                    tbody.prepend(row);
                    setTimeout(() => row.classList.remove('new-device-pulse'), 2000);
                } else {
                    row.className = 'fade-in';
                    tbody.appendChild(row);
                }
                this.knownDeviceIds.add(device.id);
            } else {
                // Atomic data updates
                row.setAttribute('data-active', isActive ? 'true' : 'false');
                row.setAttribute('data-error', hasError ? 'true' : 'false');
                
                // 1. Update Name
                const nameEl = row.querySelector('.device-name');
                if (nameEl && nameEl.textContent !== device.name) {
                    nameEl.textContent = device.name;
                    this.triggerUpdate(nameEl);
                }

                // 2. Update Status Badge
                const statusBadge = row.querySelector('.status-badge');
                if (statusBadge && (statusBadge.textContent !== statusText || !statusBadge.classList.contains(statusClass))) {
                    statusBadge.textContent = statusText;
                    statusBadge.className = `status-badge ${statusClass}`;
                    this.triggerUpdate(statusBadge);
                }

                // 3. Update Sync Time
                const syncCell = row.querySelector('.col-sync');
                if (syncCell && syncCell.innerHTML !== lastSyncHtml) {
                    syncCell.innerHTML = lastSyncHtml;
                    this.triggerUpdate(syncCell);
                }
                
                // 4. Update Type Badge
                const typeBadge = row.querySelector('.col-type .badge');
                const typeText = device.device_type || 'Unknown';
                if (typeBadge && typeBadge.textContent !== typeText) {
                    typeBadge.textContent = typeText;
                    this.triggerUpdate(typeBadge);
                }

                // 5. Update Channel/Source
                const channelCell = row.querySelector('.col-channel');
                if (channelCell && channelCell.innerHTML !== channelHtml) {
                    channelCell.innerHTML = channelHtml;
                    this.triggerUpdate(channelCell);
                }
            }
        });

        // Cleanup removed devices
        const incomingIds = new Set(devices.map(d => d.id));
        this.knownDeviceIds.forEach(id => {
            if (!incomingIds.has(id)) {
                const row = tbody.querySelector(`tr[data-device-id="${id}"]`);
                if (row) row.remove();
                this.knownDeviceIds.delete(id);
            }
        });

        // Apply current filter state across table rows
        this.applyFilter();
    }

    triggerUpdate(element) {
        element.classList.add('cell-update');
        setTimeout(() => element.classList.remove('cell-update'), 800);
    }
}

// Global window instance for explicit page access
window.dashboard = new RealtimeDashboard();
const dashboard = window.dashboard;

document.addEventListener('DOMContentLoaded', () => {
    window.dashboard.start();
});
