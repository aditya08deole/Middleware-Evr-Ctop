// Loading states and skeleton screens

// Show loading skeleton for an element
function showLoadingSkeleton(element, type = 'card') {
    if (!element) return;
    
    const skeletons = {
        card: `
            <div class="skeleton-loader">
                <div class="skeleton-header"></div>
                <div class="skeleton-body"></div>
                <div class="skeleton-body"></div>
            </div>
        `,
        list: `
            <div class="skeleton-list">
                <div class="skeleton-item"></div>
                <div class="skeleton-item"></div>
                <div class="skeleton-item"></div>
            </div>
        `,
        table: `
            <div class="skeleton-table">
                <div class="skeleton-row">
                    <div class="skeleton-cell"></div>
                    <div class="skeleton-cell"></div>
                    <div class="skeleton-cell"></div>
                </div>
                <div class="skeleton-row">
                    <div class="skeleton-cell"></div>
                    <div class="skeleton-cell"></div>
                    <div class="skeleton-cell"></div>
                </div>
                <div class="skeleton-row">
                    <div class="skeleton-cell"></div>
                    <div class="skeleton-cell"></div>
                    <div class="skeleton-cell"></div>
                </div>
            </div>
        `,
        text: `
            <div class="skeleton-text">
                <div class="skeleton-line"></div>
                <div class="skeleton-line"></div>
                <div class="skeleton-line"></div>
            </div>
        `
    };
    
    element.innerHTML = skeletons[type] || skeletons.card;
}

// Hide loading skeleton and show content
function hideLoadingSkeleton(element, content) {
    if (!element) return;
    element.innerHTML = content;
}

// Show spinner on button
function showButtonSpinner(button, originalText = 'Loading...') {
    if (!button) return;

    // A button can be armed twice for one submit (e.g. a page-wide generic
    // form listener plus a form's own submit handler both call this) —
    // without this guard, the second call captures the *spinner's own*
    // "Loading..." text as "original", so hideButtonSpinner later restores
    // the wrong label instead of the button's real text.
    if (button.disabled && button.dataset.originalText) {
        return;
    }

    button.disabled = true;
    button.dataset.originalText = button.textContent;
    button.innerHTML = `
        <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
        ${originalText}
    `;
}

// Hide spinner on button
function hideButtonSpinner(button) {
    if (!button) return;

    button.disabled = false;
    button.textContent = button.dataset.originalText || 'Submit';
    delete button.dataset.originalText;
}

// Show page loading overlay
function showPageLoading() {
    let overlay = document.getElementById('page-loading-overlay');
    
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'page-loading-overlay';
        overlay.className = 'page-loading-overlay';
        overlay.innerHTML = `
            <div class="loading-spinner">
                <div class="spinner-border" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="loading-text">Loading...</p>
            </div>
        `;
        document.body.appendChild(overlay);
    }
    
    overlay.style.display = 'flex';
}

// Hide page loading overlay
function hidePageLoading() {
    const overlay = document.getElementById('page-loading-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

// Show inline loading for a container
function showInlineLoading(container, message = 'Loading...') {
    if (!container) return;
    
    container.innerHTML = `
        <div class="inline-loading">
            <div class="spinner-border spinner-border-sm" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <span>${message}</span>
        </div>
    `;
}

// Hide inline loading
function hideInlineLoading(container, content) {
    if (!container) return;
    container.innerHTML = content;
}

// Optimistic UI update helper
function optimisticUpdate(element, newContent, callback) {
    const originalContent = element.innerHTML;
    element.innerHTML = newContent;
    
    // If callback fails, revert
    if (callback) {
        callback().catch(() => {
            element.innerHTML = originalContent;
        });
    }
}

// Progress bar for long-running operations
function showProgressBar(container, steps = 100) {
    if (!container) return;
    
    container.innerHTML = `
        <div class="progress-container">
            <div class="progress">
                <div class="progress-bar" role="progressbar" style="width: 0%" aria-valuenow="0" aria-valuemin="0" aria-valuemax="${steps}"></div>
            </div>
            <div class="progress-text">0%</div>
        </div>
    `;
    
    return {
        update: (current) => {
            const percentage = Math.round((current / steps) * 100);
            const progressBar = container.querySelector('.progress-bar');
            const progressText = container.querySelector('.progress-text');
            
            if (progressBar) {
                progressBar.style.width = `${percentage}%`;
                progressBar.setAttribute('aria-valuenow', current);
            }
            
            if (progressText) {
                progressText.textContent = `${percentage}%`;
            }
        },
        complete: () => {
            const progressBar = container.querySelector('.progress-bar');
            const progressText = container.querySelector('.progress-text');
            
            if (progressBar) {
                progressBar.classList.add('bg-success');
            }
            
            if (progressText) {
                progressText.textContent = 'Complete!';
            }
        }
    };
}

// Initialize loading states for common elements
document.addEventListener('DOMContentLoaded', () => {
    // Add loading states to all forms
    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('submit', (e) => {
            const submitButton = form.querySelector('button[type="submit"]');
            if (submitButton) {
                showButtonSpinner(submitButton, 'Processing...');
            }
        });
    });
    
    // Add loading states to all async buttons
    document.querySelectorAll('[data-loading]').forEach(button => {
        button.addEventListener('click', () => {
            const loadingText = button.dataset.loading || 'Loading...';
            showButtonSpinner(button, loadingText);
        });
    });
});

// Export functions
window.loading = {
    showSkeleton: showLoadingSkeleton,
    hideSkeleton: hideLoadingSkeleton,
    showButtonSpinner: showButtonSpinner,
    hideButtonSpinner: hideButtonSpinner,
    showPage: showPageLoading,
    hidePage: hidePageLoading,
    showInline: showInlineLoading,
    hideInline: hideInlineLoading,
    optimistic: optimisticUpdate,
    progressBar: showProgressBar
};
