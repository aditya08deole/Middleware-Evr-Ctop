// Error Boundary for catching and handling JavaScript errors

class ErrorBoundary {
    constructor() {
        this.hasError = false;
        this.setupErrorHandlers();
    }
    
    setupErrorHandlers() {
        // Catch global errors
        window.addEventListener('error', (event) => {
            this.handleError(event.error, event.message, event.filename, event.lineno);
        });
        
        // Catch unhandled promise rejections
        window.addEventListener('unhandledrejection', (event) => {
            this.handleError(event.reason, 'Unhandled Promise Rejection');
        });
        
        // Catch Vue/React errors if frameworks are used
        if (typeof Vue !== 'undefined') {
            Vue.config.errorHandler = (err, vm, info) => {
                this.handleError(err, `Vue Error: ${info}`);
            };
        }
    }
    
    handleError(error, message, filename = '', lineno = 0) {
        console.error('Error caught by boundary:', error);
        
        this.hasError = true;
        
        // Log error to server
        this.logError(error, message, filename, lineno);
        
        // Show user-friendly error UI
        this.showErrorUI(error, message);
    }
    
    logError(error, message, filename, lineno) {
        const errorData = {
            message: message || error.message,
            stack: error.stack,
            filename: filename,
            lineno: lineno,
            url: window.location.href,
            userAgent: navigator.userAgent,
            timestamp: new Date().toISOString()
        };
        
        // Send to server for logging
        fetch('/api/log-error', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(errorData)
        }).catch(err => {
            console.error('Failed to log error to server:', err);
        });
    }
    
    showErrorUI(error, message) {
        // Remove existing error boundary if present
        const existing = document.querySelector('.error-boundary');
        if (existing) {
            existing.remove();
        }
        
        // Create error UI
        const errorDiv = document.createElement('div');
        errorDiv.className = 'error-boundary';
        errorDiv.innerHTML = `
            <div class="error-content">
                <i class="bi bi-exclamation-triangle" style="font-size: 4rem; color: var(--error-color); margin-bottom: 1rem;"></i>
                <h3>Something went wrong</h3>
                <p>${message || 'An unexpected error occurred. Please try again.'}</p>
                ${error && error.stack ? `
                    <details style="margin: 1rem 0; text-align: left; max-width: 600px;">
                        <summary style="cursor: pointer; color: var(--text-secondary);">Technical details</summary>
                        <pre style="background: var(--surface-hover); padding: 1rem; border-radius: var(--radius-md); overflow: auto; font-size: 0.75rem; margin-top: 0.5rem;">${error.stack}</pre>
                    </details>
                ` : ''}
                <div class="error-actions">
                    <button onclick="location.reload()">
                        <i class="bi bi-arrow-clockwise"></i> Reload Page
                    </button>
                    <button onclick="window.history.back()">
                        <i class="bi bi-arrow-left"></i> Go Back
                    </button>
                </div>
            </div>
        `;
        
        document.body.appendChild(errorDiv);
    }
    
    reset() {
        this.hasError = false;
        const errorDiv = document.querySelector('.error-boundary');
        if (errorDiv) {
            errorDiv.remove();
        }
    }
}

// Initialize error boundary
const errorBoundary = new ErrorBoundary();

// Export for use in other files
window.errorBoundary = errorBoundary;

// Helper function for async error handling
async function withErrorHandling(asyncFn, fallback = null) {
    try {
        return await asyncFn();
    } catch (error) {
        console.error('Async error:', error);
        errorBoundary.handleError(error);
        return fallback;
    }
}

// Wrap async functions with error handling
function safeAsync(asyncFn) {
    return async function(...args) {
        try {
            return await asyncFn.apply(this, args);
        } catch (error) {
            console.error('Async function error:', error);
            errorBoundary.handleError(error);
            throw error;
        }
    };
}

// Add error handling to fetch calls
function safeFetch(url, options = {}) {
    return fetch(url, options)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response;
        })
        .catch(error => {
            console.error('Fetch error:', error);
            errorBoundary.handleError(error);
            throw error;
        });
}

// Export helper functions
window.withErrorHandling = withErrorHandling;
window.safeAsync = safeAsync;
window.safeFetch = safeFetch;
