// Data Visualization Charts using Chart.js

class DashboardCharts {
    constructor() {
        this.charts = {};
    }

    // Initialize device activity chart
    initDeviceActivityChart(canvasId, data) {
        const ctx = document.getElementById(canvasId);
        if (!ctx) return;

        if (this.charts[canvasId]) {
            this.charts[canvasId].destroy();
        }

        this.charts[canvasId] = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.labels || [],
                datasets: [{
                    label: 'Device Activity',
                    data: data.values || [],
                    borderColor: '#2563eb',
                    backgroundColor: 'rgba(37, 99, 235, 0.1)',
                    fill: true,
                    tension: 0.4,
                    pointRadius: 4,
                    pointHoverRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        backgroundColor: 'rgba(0, 0, 0, 0.8)',
                        padding: 12,
                        cornerRadius: 8
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: {
                            color: 'rgba(0, 0, 0, 0.05)'
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        }
                    }
                }
            }
        });
    }

    // Initialize success/failure rate chart
    initSuccessFailureChart(canvasId, data) {
        const ctx = document.getElementById(canvasId);
        if (!ctx) return;

        if (this.charts[canvasId]) {
            this.charts[canvasId].destroy();
        }

        this.charts[canvasId] = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['Success', 'Failure', 'Pending'],
                datasets: [{
                    data: [data.success || 0, data.failure || 0, data.pending || 0],
                    backgroundColor: [
                        '#10b981',
                        '#ef4444',
                        '#f59e0b'
                    ],
                    borderWidth: 0,
                    hoverOffset: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 20,
                            usePointStyle: true
                        }
                    },
                    tooltip: {
                        backgroundColor: 'rgba(0, 0, 0, 0.8)',
                        padding: 12,
                        cornerRadius: 8
                    }
                },
                cutout: '70%'
            }
        });
    }

    // Initialize data volume chart
    initDataVolumeChart(canvasId, data) {
        const ctx = document.getElementById(canvasId);
        if (!ctx) return;

        if (this.charts[canvasId]) {
            this.charts[canvasId].destroy();
        }

        this.charts[canvasId] = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.labels || [],
                datasets: [{
                    label: 'Data Points',
                    data: data.values || [],
                    backgroundColor: 'rgba(37, 99, 235, 0.8)',
                    borderRadius: 8,
                    borderSkipped: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: {
                            color: 'rgba(0, 0, 0, 0.05)'
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        }
                    }
                }
            }
        });
    }

    // Initialize device status chart
    initDeviceStatusChart(canvasId, data) {
        const ctx = document.getElementById(canvasId);
        if (!ctx) return;

        if (this.charts[canvasId]) {
            this.charts[canvasId].destroy();
        }

        this.charts[canvasId] = new Chart(ctx, {
            type: 'pie',
            data: {
                labels: ['Active', 'Inactive'],
                datasets: [{
                    data: [data.active || 0, data.inactive || 0],
                    backgroundColor: [
                        '#10b981',
                        '#64748b'
                    ],
                    borderWidth: 0,
                    hoverOffset: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 20,
                            usePointStyle: true
                        }
                    }
                }
            }
        });
    }

    // Update chart data
    updateChart(canvasId, newData) {
        if (this.charts[canvasId]) {
            this.charts[canvasId].data = newData;
            this.charts[canvasId].update();
        }
    }

    // Destroy all charts
    destroyAll() {
        for (const [key, chart] of Object.entries(this.charts)) {
            chart.destroy();
        }
        this.charts = {};
    }
}

// Initialize charts when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    const charts = new DashboardCharts();
    
    // Load real data from analytics endpoints
    loadChartData(charts);
    
    // Export for use in other files
    window.dashboardCharts = charts;
});

// Load chart data from API
async function loadChartData(charts) {
    try {
        // Load device activity data
        const activityResponse = await fetch('/analytics/device-activity');
        const activityResult = await activityResponse.json();
        
        if (activityResult.success && document.getElementById('activityChart')) {
            charts.initDeviceActivityChart('activityChart', activityResult.data);
        }
        
        // Load success/failure rate
        const successFailureResponse = await fetch('/analytics/success-failure-rate');
        const successFailureResult = await successFailureResponse.json();
        
        if (successFailureResult.success && document.getElementById('successFailureChart')) {
            charts.initSuccessFailureChart('successFailureChart', successFailureResult.data);
        }
        
        // Load data volume
        const volumeResponse = await fetch('/analytics/data-volume');
        const volumeResult = await volumeResponse.json();
        
        if (volumeResult.success && document.getElementById('volumeChart')) {
            charts.initDataVolumeChart('volumeChart', volumeResult.data);
        }
        
        // Load device status
        const statusResponse = await fetch('/analytics/device-status');
        const statusResult = await statusResponse.json();
        
        if (statusResult.success && document.getElementById('statusChart')) {
            charts.initDeviceStatusChart('statusChart', statusResult.data);
        }
        
    } catch (error) {
        console.error('Error loading chart data:', error);
        
        // Fallback to sample data if API fails
        const activityData = {
            labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
            values: [12, 19, 3, 5, 2, 3, 15]
        };
        
        const successFailureData = {
            success: 75,
            failure: 15,
            pending: 10
        };
        
        const volumeData = {
            labels: ['Device 1', 'Device 2', 'Device 3', 'Device 4', 'Device 5'],
            values: [120, 190, 30, 50, 20]
        };
        
        const statusData = {
            active: 8,
            inactive: 2
        };
        
        setTimeout(() => {
            if (document.getElementById('activityChart')) {
                charts.initDeviceActivityChart('activityChart', activityData);
            }
            if (document.getElementById('successFailureChart')) {
                charts.initSuccessFailureChart('successFailureChart', successFailureData);
            }
            if (document.getElementById('volumeChart')) {
                charts.initDataVolumeChart('volumeChart', volumeData);
            }
            if (document.getElementById('statusChart')) {
                charts.initDeviceStatusChart('statusChart', statusData);
            }
        }, 100);
    }
}

// Refresh chart data (call this to update charts)
function refreshCharts() {
    const charts = window.dashboardCharts;
    if (charts) {
        loadChartData(charts);
    }
}
