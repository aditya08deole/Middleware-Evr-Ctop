// Multi-step form functionality for device creation

let currentStep = 1;
const totalSteps = 4;
const formData = {};

// Initialize form
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('multi-step-form');
    if (form) {
        form.addEventListener('submit', handleSubmit);
    }
    // Initialize platform toggle state
    togglePlatformFields();
});

// Toggle between ThingSpeak and EMQX field groups
function togglePlatformFields() {
    const isEmqx = document.getElementById('platform-emqx').checked;
    const tsFields = document.getElementById('thingspeak-fields');
    const emqxFields = document.getElementById('emqx-fields');
    
    if (isEmqx) {
        tsFields.style.display = 'none';
        emqxFields.style.display = 'block';
        
        // Remove required from ThingSpeak fields
        document.getElementById('channel-id').removeAttribute('required');
        document.getElementById('api-key').removeAttribute('required');
        
        // Add required to EMQX fields
        document.getElementById('emqx-broker-url').setAttribute('required', 'required');
        document.getElementById('emqx-topic').setAttribute('required', 'required');
    } else {
        tsFields.style.display = 'block';
        emqxFields.style.display = 'none';
        
        // Add required to ThingSpeak fields
        document.getElementById('channel-id').setAttribute('required', 'required');
        document.getElementById('api-key').setAttribute('required', 'required');
        
        // Remove required from EMQX fields
        document.getElementById('emqx-broker-url').removeAttribute('required');
        document.getElementById('emqx-topic').removeAttribute('required');
    }
}

function nextStep() {
    if (!validateStep(currentStep)) {
        return;
    }
    
    saveStepData(currentStep);
    
    // Hide current step
    document.getElementById(`step-${currentStep}`).style.display = 'none';
    document.querySelector(`.step-indicator[data-step="${currentStep}"]`).classList.remove('active');
    document.querySelector(`.step-indicator[data-step="${currentStep}"]`).classList.add('completed');
    
    // Show next step
    currentStep++;
    document.getElementById(`step-${currentStep}`).style.display = 'block';
    document.querySelector(`.step-indicator[data-step="${currentStep}"]`).classList.add('active');
    
    // Update progress bar
    updateProgress();
    
    // If on review step, populate review content
    if (currentStep === 4) {
        populateReview();
    }
}

function previousStep() {
    // Hide current step
    document.getElementById(`step-${currentStep}`).style.display = 'none';
    document.querySelector(`.step-indicator[data-step="${currentStep}"]`).classList.remove('active');
    
    // Show previous step
    currentStep--;
    document.getElementById(`step-${currentStep}`).style.display = 'block';
    document.querySelector(`.step-indicator[data-step="${currentStep}"]`).classList.add('active');
    document.querySelector(`.step-indicator[data-step="${currentStep}"]`).classList.remove('completed');
    
    // Update progress bar
    updateProgress();
}

function validateStep(step) {
    const stepElement = document.getElementById(`step-${step}`);
    const inputs = stepElement.querySelectorAll('input[required]');
    let isValid = true;
    
    inputs.forEach(input => {
        if (!input.value.trim()) {
            isValid = false;
            input.classList.add('is-invalid');
            
            // Show error message
            const errorElement = stepElement.querySelector(`[data-error="${input.name}"]`);
            if (errorElement) {
                errorElement.textContent = `${input.previousElementSibling.textContent.replace(' *', '')} is required`;
                errorElement.style.display = 'block';
            }
        } else {
            input.classList.remove('is-invalid');
            input.classList.add('is-valid');
            
            // Clear error message
            const errorElement = stepElement.querySelector(`[data-error="${input.name}"]`);
            if (errorElement) {
                errorElement.textContent = '';
                errorElement.style.display = 'none';
            }
        }
    });
    
    // Additional validation for specific fields
    if (step === 1) {
        const isEmqx = document.getElementById('platform-emqx').checked;
        
        if (!isEmqx) {
            // ThingSpeak validation
            const channelId = document.getElementById('channel-id');
            if (channelId.value && !/^\d+$/.test(channelId.value)) {
                isValid = false;
                channelId.classList.add('is-invalid');
                const errorElement = stepElement.querySelector('[data-error="channel_id"]');
                if (errorElement) {
                    errorElement.textContent = 'Channel ID must be numeric';
                    errorElement.style.display = 'block';
                }
            }
        } else {
            // EMQX validation
            const brokerUrl = document.getElementById('emqx-broker-url');
            if (!brokerUrl.value.trim()) {
                isValid = false;
                brokerUrl.classList.add('is-invalid');
                const errorElement = stepElement.querySelector('[data-error="emqx_broker_url"]');
                if (errorElement) {
                    errorElement.textContent = 'Broker URL is required';
                    errorElement.style.display = 'block';
                }
            }
            
            const topic = document.getElementById('emqx-topic');
            if (!topic.value.trim()) {
                isValid = false;
                topic.classList.add('is-invalid');
                const errorElement = stepElement.querySelector('[data-error="emqx_topic"]');
                if (errorElement) {
                    errorElement.textContent = 'MQTT topic is required';
                    errorElement.style.display = 'block';
                }
            }
            
            const port = document.getElementById('emqx-port');
            if (port.value && (parseInt(port.value) < 1 || parseInt(port.value) > 65535)) {
                isValid = false;
                port.classList.add('is-invalid');
            }
        }
    }
    
    if (step === 2) {
        const ctopUrl1 = document.getElementById('ctop-url-1');
        if (ctopUrl1.value && !isValidUrl(ctopUrl1.value)) {
            isValid = false;
            ctopUrl1.classList.add('is-invalid');
            const errorElement = stepElement.querySelector('[data-error="ctop_url_1"]');
            if (errorElement) {
                errorElement.textContent = 'Invalid URL format';
                errorElement.style.display = 'block';
            }
        }
        
        const ctopUrl2 = document.getElementById('ctop-url-2');
        if (ctopUrl2.value && !isValidUrl(ctopUrl2.value)) {
            isValid = false;
            ctopUrl2.classList.add('is-invalid');
            const errorElement = stepElement.querySelector('[data-error="ctop_url_2"]');
            if (errorElement) {
                errorElement.textContent = 'Invalid URL format';
                errorElement.style.display = 'block';
            }
        }
    }
    
    if (step === 3) {
        const latitude = document.getElementById('latitude');
        if (latitude.value && (parseFloat(latitude.value) < -90 || parseFloat(latitude.value) > 90)) {
            isValid = false;
            latitude.classList.add('is-invalid');
            const errorElement = stepElement.querySelector('[data-error="latitude"]');
            if (errorElement) {
                errorElement.textContent = 'Latitude must be between -90 and 90';
                errorElement.style.display = 'block';
            }
        }
        
        const longitude = document.getElementById('longitude');
        if (longitude.value && (parseFloat(longitude.value) < -180 || parseFloat(longitude.value) > 180)) {
            isValid = false;
            longitude.classList.add('is-invalid');
            const errorElement = stepElement.querySelector('[data-error="longitude"]');
            if (errorElement) {
                errorElement.textContent = 'Longitude must be between -180 and 180';
                errorElement.style.display = 'block';
            }
        }
    }
    
    if (!isValid) {
        showAlert('Please fix the errors before proceeding', 'danger');
    }
    
    return isValid;
}

function saveStepData(step) {
    const stepElement = document.getElementById(`step-${step}`);
    const inputs = stepElement.querySelectorAll('input');
    
    inputs.forEach(input => {
        if (input.name) {
            if (input.type === 'radio') {
                // Only save the checked radio
                if (input.checked) {
                    formData[input.name] = input.value;
                }
            } else if (input.type === 'checkbox') {
                formData[input.name] = input.checked;
            } else {
                formData[input.name] = input.value;
            }
        }
    });
    
    // Ensure data_source is always captured from Step 1
    if (step === 1) {
        const isEmqx = document.getElementById('platform-emqx').checked;
        formData.data_source = isEmqx ? 'emqx' : 'thingspeak';
        
        if (isEmqx) {
            // Set placeholder values for ThingSpeak fields (backend expects them)
            formData.channel_id = formData.channel_id || 'emqx';
            formData.api_key = formData.api_key || 'not_used_emqx';
            // Convert port to integer
            if (formData.emqx_port) {
                formData.emqx_port = parseInt(formData.emqx_port);
            }
        }
    }
}

function updateProgress() {
    const progressBar = document.getElementById('form-progress');
    const percentage = (currentStep / totalSteps) * 100;
    progressBar.style.width = `${percentage}%`;
    progressBar.setAttribute('aria-valuenow', currentStep);
}

function populateReview() {
    const reviewContent = document.getElementById('review-content');
    const isEmqx = formData.data_source === 'emqx';
    
    let reviewItems = [
        { label: 'Device ID', value: formData.name || 'Not provided' },
        { label: 'Platform', value: isEmqx ? '<span class="badge bg-success">EMQX (MQTT)</span>' : '<span class="badge bg-primary">ThingSpeak</span>' },
    ];
    
    if (isEmqx) {
        reviewItems.push(
            { label: 'Broker URL', value: formData.emqx_broker_url || 'Not provided' },
            { label: 'Port', value: formData.emqx_port || '1883' },
            { label: 'Username', value: formData.emqx_username || 'None' },
            { label: 'Password', value: formData.emqx_password ? '••••••••••••' : 'None' },
            { label: 'MQTT Topic', value: formData.emqx_topic || 'Not provided' },
            { label: 'TLS/SSL', value: formData.emqx_use_tls ? 'Enabled' : 'Disabled' },
        );
    } else {
        reviewItems.push(
            { label: 'Channel ID', value: formData.channel_id || 'Not provided' },
            { label: 'API Key', value: formData.api_key ? '••••••••••••' : 'Not provided' },
        );
    }
    
    reviewItems.push(
        { label: 'CTOP URL', value: formData.ctop_url_1 || 'Not provided' },
        { label: 'Authorization', value: formData.auth_token ? (formData.auth_token.substring(0, 20) + '...') : 'Not provided' },
        { label: 'Latitude', value: formData.latitude || 'Not provided' },
        { label: 'Longitude', value: formData.longitude || 'Not provided' }
    );
    
    reviewContent.innerHTML = reviewItems.map(item => `
        <div class="review-item">
            <span class="review-label">${item.label}</span>
            <span class="review-value">${item.value}</span>
        </div>
    `).join('');
}

async function testConnection() {
    const ctopUrl1 = document.getElementById('ctop-url-1').value;
    const authToken = document.getElementById('auth-token').value;
    const statusElement = document.getElementById('connection-status');
    
    if (!ctopUrl1 || !authToken) {
        showAlert('Please enter CTOP URL and Auth Token first', 'warning');
        return;
    }
    
    statusElement.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Testing...';
    
    try {
        const response = await fetch('/api/test-connection', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                url: ctopUrl1,
                auth_token: authToken
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            statusElement.innerHTML = '<span class="text-success"><i class="bi bi-check-circle"></i> Connection successful</span>';
            showAlert('Connection test successful!', 'success');
        } else {
            statusElement.innerHTML = '<span class="text-danger"><i class="bi bi-x-circle"></i> Connection failed</span>';
            showAlert(`Connection failed: ${result.error}`, 'danger');
        }
    } catch (error) {
        statusElement.innerHTML = '<span class="text-danger"><i class="bi bi-x-circle"></i> Connection failed</span>';
        showAlert(`Connection test error: ${error.message}`, 'danger');
    }
}

async function handleSubmit(e) {
    e.preventDefault();
    
    const confirmCheckbox = document.getElementById('confirm-create');
    if (!confirmCheckbox.checked) {
        showAlert('Please confirm the device configuration', 'warning');
        return;
    }
    
    const submitBtn = document.getElementById('submit-btn');
    window.loading.showButtonSpinner(submitBtn, 'Creating Device...');
    
    try {
        const response = await fetch('/devices/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(formData)
        });
        
        const result = await response.json();
        
        if (result.success) {
            showAlert('Device created successfully!', 'success');
            setTimeout(() => {
                window.location.href = '/';
            }, 1500);
        } else {
            showAlert(`Failed to create device: ${result.error}`, 'danger');
            window.loading.hideButtonSpinner(submitBtn);
        }
    } catch (error) {
        showAlert(`Error creating device: ${error.message}`, 'danger');
        window.loading.hideButtonSpinner(submitBtn);
    }
}

// Helper function
function isValidUrl(string) {
    try {
        new URL(string);
        return true;
    } catch (_) {
        return false;
    }
}

// Alert function (assuming it exists globally)
function showAlert(message, type = 'info') {
    // Check if Bootstrap alert exists
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.style.position = 'fixed';
    alertDiv.style.top = '20px';
    alertDiv.style.right = '20px';
    alertDiv.style.zIndex = '9999';
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(alertDiv);
    
    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        alertDiv.remove();
    }, 5000);
}
