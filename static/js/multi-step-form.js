// Multi-step form functionality for device creation

let currentStep = 1;
const totalSteps = 5;
const formData = {};

// field1..field8 options are identical across every sensor-field <select> —
// generate them once instead of hand-duplicating <option> blocks per select.
function populateFieldSelectOptions() {
    document.querySelectorAll('select.field-select').forEach(select => {
        for (let i = 1; i <= 8; i++) {
            const option = document.createElement('option');
            option.value = `field${i}`;
            option.textContent = `Field ${i}`;
            select.appendChild(option);
        }
    });
}

// Show the sensor-field group for the selected device type and mark its
// fields required (mirrors add_device.html's showDeviceFields()).
function showDeviceTypeFields(deviceType) {
    document.querySelectorAll('.device-specific-fields').forEach(el => {
        el.style.display = 'none';
    });
    document.querySelectorAll('#step-2 .field-select').forEach(el => {
        el.removeAttribute('required');
    });
    document.getElementById('tank-height').removeAttribute('required');

    if (deviceType === 'EvaraTank') {
        document.getElementById('evaratank-fields').style.display = 'block';
        document.getElementById('tank-height').setAttribute('required', 'required');
        document.getElementById('distance-field').setAttribute('required', 'required');
        document.getElementById('temperature-field').setAttribute('required', 'required');
    } else if (deviceType === 'EvaraFlow') {
        document.getElementById('evaraflow-fields').style.display = 'block';
        document.getElementById('meter-reading-field').setAttribute('required', 'required');
        document.getElementById('flow-rate-field-ef').setAttribute('required', 'required');
    } else if (deviceType === 'EvaraValve') {
        document.getElementById('evaravalve-fields').style.display = 'block';
        document.getElementById('flow-rate-field-ev').setAttribute('required', 'required');
        document.getElementById('liters-field').setAttribute('required', 'required');
    } else if (deviceType === 'EvaraDeep') {
        document.getElementById('evaradeep-fields').style.display = 'block';
        document.getElementById('distance-field-ed').setAttribute('required', 'required');
    } else if (deviceType === 'EvaraTDS') {
        document.getElementById('evaratds-fields').style.display = 'block';
        document.getElementById('temperature-field-et').setAttribute('required', 'required');
        document.getElementById('tds-field').setAttribute('required', 'required');
    }
}

// Initialize form
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('multi-step-form');
    if (form) {
        form.addEventListener('submit', handleSubmit);
    }
    // Initialize platform toggle state
    togglePlatformFields();
    populateFieldSelectOptions();

    const deviceTypeSelect = document.getElementById('device-type');
    if (deviceTypeSelect) {
        deviceTypeSelect.addEventListener('change', () => showDeviceTypeFields(deviceTypeSelect.value));
    }
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

// Toggle TLS-only EMQX fields (CA cert path, insecure checkbox) and
// auto-switch the port between the plaintext/TLS MQTT defaults — but only
// when the port still holds one of those two known defaults, so a custom
// port the user already typed is never silently overwritten.
function toggleEmqxTlsFieldsMulti() {
    const useTls = document.getElementById('emqx-use-tls').checked;
    document.getElementById('emqx-tls-fields-multi').style.display = useTls ? 'block' : 'none';

    const portInput = document.getElementById('emqx-port');
    const currentPort = portInput.value;
    if (useTls && currentPort === '1883') {
        portInput.value = '8883';
    } else if (!useTls && currentPort === '8883') {
        portInput.value = '1883';
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
    if (currentStep === totalSteps) {
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
    const inputs = stepElement.querySelectorAll('input[required], select[required]');
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
        const deviceType = document.getElementById('device-type').value;
        if (!deviceType) {
            isValid = false;
            showAlert('Please select a device type', 'warning');
        }
        // Field-selects are only marked required once showDeviceTypeFields()
        // runs for the chosen type, so the generic required-input loop above
        // already validates them — nothing further needed here.
    }

    if (step === 3) {
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

    if (step === 4) {
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
    const inputs = stepElement.querySelectorAll('input, select');

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

    // Only keep sensor-config fields relevant to the selected device type —
    // hidden groups for other types share input `name`s (e.g. every group
    // has a `filtering_method` select), so without this the last group in
    // DOM order would silently overwrite the one the user actually filled in.
    if (step === 2) {
        const deviceType = formData.device_type;
        const relevantFieldsByType = {
            EvaraTank: ['tank_height', 'distance_field', 'temperature_field'],
            EvaraFlow: ['meter_reading_field', 'flow_rate_field'],
            EvaraValve: ['flow_rate_field', 'liters_field'],
            EvaraDeep: ['distance_field'],
            EvaraTDS: ['temperature_field', 'tds_field']
        };
        const groupIdByType = {
            EvaraTank: 'evaratank-fields',
            EvaraFlow: 'evaraflow-fields',
            EvaraValve: 'evaravalve-fields',
            EvaraDeep: 'evaradeep-fields',
            EvaraTDS: 'evaratds-fields'
        };
        const activeGroup = document.getElementById(groupIdByType[deviceType]);
        if (activeGroup) {
            // Re-read filtering_method/filter_window/field-selects from the
            // active group specifically, since the generic loop above just
            // took whichever group's inputs happened to appear last in the DOM.
            activeGroup.querySelectorAll('input, select').forEach(el => {
                if (el.name) formData[el.name] = el.value;
            });
        }
        (relevantFieldsByType[deviceType] || []).forEach(name => {
            if (!(name in formData)) formData[name] = null;
        });
    }

    // Ensure data_source is always captured from Step 1
    if (step === 1) {
        const isEmqx = document.getElementById('platform-emqx').checked;
        formData.data_source = isEmqx ? 'emqx' : 'thingspeak';
        
        if (isEmqx) {
            // Set placeholder values for ThingSpeak fields (backend expects them)
            formData.channel_id = formData.channel_id || 'emqx';
            formData.api_key = formData.api_key || 'not_used_emqx';
            // Convert port/QoS to integers
            if (formData.emqx_port) {
                formData.emqx_port = parseInt(formData.emqx_port);
            }
            formData.emqx_qos = parseInt(formData.emqx_qos) || 1;
            formData.emqx_ca_cert_path = formData.emqx_ca_cert_path || null;
            // emqx_tls_insecure only means anything alongside TLS itself
            formData.emqx_tls_insecure = formData.emqx_use_tls ? !!formData.emqx_tls_insecure : false;
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
        { label: 'Device Type', value: formData.device_type || 'Not provided' },
        { label: 'Platform', value: isEmqx ? '<span class="badge bg-success">EMQX (MQTT)</span>' : '<span class="badge bg-primary">ThingSpeak</span>', safe: true },
    ];

    const sensorFieldsByType = {
        EvaraTank: [['Tank Height (cm)', 'tank_height'], ['Distance Field', 'distance_field'], ['Temperature Field', 'temperature_field']],
        EvaraFlow: [['Meter Reading Field', 'meter_reading_field'], ['Flow Rate Field', 'flow_rate_field']],
        EvaraValve: [['Flow Rate Field', 'flow_rate_field'], ['Liters Field', 'liters_field']],
        EvaraDeep: [['Distance Field', 'distance_field']],
        EvaraTDS: [['Temperature Field', 'temperature_field'], ['TDS Field', 'tds_field']]
    };
    (sensorFieldsByType[formData.device_type] || []).forEach(([label, key]) => {
        reviewItems.push({ label, value: formData[key] || 'Not provided' });
    });
    reviewItems.push({ label: 'Filtering Method', value: formData.filtering_method || 'none' });

    if (isEmqx) {
        reviewItems.push(
            { label: 'Broker URL', value: formData.emqx_broker_url || 'Not provided' },
            { label: 'Port', value: formData.emqx_port || '1883' },
            { label: 'Username', value: formData.emqx_username || 'None' },
            { label: 'Password', value: formData.emqx_password ? '••••••••••••' : 'None' },
            { label: 'MQTT Topic', value: formData.emqx_topic || 'Not provided' },
            { label: 'QoS', value: formData.emqx_qos || '1' },
            { label: 'TLS/SSL', value: formData.emqx_use_tls ? (formData.emqx_tls_insecure ? 'Enabled (cert verification skipped)' : 'Enabled') : 'Disabled' },
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
            <span class="review-label">${escapeHtml(item.label)}</span>
            <span class="review-value">${item.safe ? item.value : escapeHtml(item.value)}</span>
        </div>
    `).join('');
}

// device/form values here are whatever the operator just typed in the
// preceding steps — escape before interpolating into innerHTML so a device
// name or MQTT topic containing HTML can't execute in the review panel.
function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
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

// Fields the backend/preprocessing pipeline does real arithmetic on (e.g.
// `tank_height - distance` in preprocess_service.py) — every other captured
// field is fine as a string, but these must be actual numbers or device
// processing throws on the very first reading.
function coerceNumericFields(data) {
    const floatFields = ['tank_height', 'latitude', 'longitude'];
    const intFields = ['filter_window', 'emqx_port', 'emqx_qos'];

    floatFields.forEach(field => {
        if (data[field] !== undefined && data[field] !== null && data[field] !== '') {
            data[field] = parseFloat(data[field]);
        } else if (data[field] === '') {
            data[field] = null;
        }
    });
    intFields.forEach(field => {
        if (data[field] !== undefined && data[field] !== null && data[field] !== '') {
            data[field] = parseInt(data[field], 10);
        }
    });
    return data;
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
            body: JSON.stringify(coerceNumericFields(formData))
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

// showAlert is defined globally in base.html (add_device_multi.html extends
// it) — reused here rather than redefined.
