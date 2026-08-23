// Client-side form validation

// Validation functions
function validateEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

function validateUrl(url) {
    try {
        new URL(url);
        return true;
    } catch {
        return false;
    }
}

function validatePassword(password) {
    // At least 6 characters
    if (password.length < 6) {
        return { valid: false, message: 'Password must be at least 6 characters' };
    }
    
    // Check for strength (optional)
    const hasLower = /[a-z]/.test(password);
    const hasUpper = /[A-Z]/.test(password);
    const hasNumber = /\d/.test(password);
    const hasSpecial = /[!@#$%^&*(),.?":{}|<>]/.test(password);
    
    const strength = [hasLower, hasUpper, hasNumber, hasSpecial].filter(Boolean).length;
    
    if (strength < 2) {
        return { valid: true, message: 'Weak password', strength: 'weak' };
    } else if (strength < 3) {
        return { valid: true, message: 'Medium password', strength: 'medium' };
    } else {
        return { valid: true, message: 'Strong password', strength: 'strong' };
    }
}

function validateChannelId(channelId) {
    return /^\d+$/.test(channelId);
}

function validateApiKey(apiKey) {
    return apiKey.length >= 8 && apiKey.length <= 32;
}


function validateLatitude(latitude) {
    const lat = parseFloat(latitude);
    return !isNaN(lat) && lat >= -90 && lat <= 90;
}

function validateLongitude(longitude) {
    const lon = parseFloat(longitude);
    return !isNaN(lon) && lon >= -180 && lon <= 180;
}

// Form validation setup
function setupFormValidation(formId, rules) {
    const form = document.getElementById(formId);
    if (!form) return;
    
    form.addEventListener('submit', (e) => {
        let isValid = true;
        const errors = [];
        
        // Validate each field according to rules
        for (const [fieldName, rule] of Object.entries(rules)) {
            const field = form.querySelector(`[name="${fieldName}"]`);
            if (!field) continue;
            
            const value = field.value.trim();
            const errorElement = form.querySelector(`[data-error="${fieldName}"]`);
            
            // Required check
            if (rule.required && !value) {
                isValid = false;
                errors.push(`${rule.label || fieldName} is required`);
                showError(field, errorElement, `${rule.label || fieldName} is required`);
                continue;
            }
            
            // Skip validation if field is empty and not required
            if (!value && !rule.required) {
                clearError(field, errorElement);
                continue;
            }
            
            // Type-specific validation
            if (rule.type === 'email' && !validateEmail(value)) {
                isValid = false;
                errors.push('Invalid email format');
                showError(field, errorElement, 'Invalid email format');
            } else if (rule.type === 'url' && !validateUrl(value)) {
                isValid = false;
                errors.push('Invalid URL format');
                showError(field, errorElement, 'Invalid URL format');
            } else if (rule.type === 'password') {
                const result = validatePassword(value);
                if (!result.valid) {
                    isValid = false;
                    errors.push(result.message);
                    showError(field, errorElement, result.message);
                } else {
                    showPasswordStrength(field, result.strength);
                }
            } else if (rule.type === 'channel_id' && !validateChannelId(value)) {
                isValid = false;
                errors.push('Channel ID must be numeric');
                showError(field, errorElement, 'Channel ID must be numeric');
            } else if (rule.type === 'api_key' && !validateApiKey(value)) {
                isValid = false;
                errors.push('API key must be 8-32 characters');
                showError(field, errorElement, 'API key must be 8-32 characters');
            } else if (rule.type === 'latitude' && !validateLatitude(value)) {
                isValid = false;
                errors.push('Latitude must be between -90 and 90');
                showError(field, errorElement, 'Latitude must be between -90 and 90');
            } else if (rule.type === 'longitude' && !validateLongitude(value)) {
                isValid = false;
                errors.push('Longitude must be between -180 and 180');
                showError(field, errorElement, 'Longitude must be between -180 and 180');
            } else if (rule.minLength && value.length < rule.minLength) {
                isValid = false;
                errors.push(`${rule.label || fieldName} must be at least ${rule.minLength} characters`);
                showError(field, errorElement, `${rule.label || fieldName} must be at least ${rule.minLength} characters`);
            } else if (rule.maxLength && value.length > rule.maxLength) {
                isValid = false;
                errors.push(`${rule.label || fieldName} must be less than ${rule.maxLength} characters`);
                showError(field, errorElement, `${rule.label || fieldName} must be less than ${rule.maxLength} characters`);
            } else {
                clearError(field, errorElement);
            }
        }
        
        if (!isValid) {
            e.preventDefault();
            showAlert('Please fix the errors before submitting', 'danger');
        }
    });
    
    // Real-time validation on blur
    for (const [fieldName, rule] of Object.entries(rules)) {
        const field = form.querySelector(`[name="${fieldName}"]`);
        if (!field) continue;
        
        field.addEventListener('blur', () => {
            const value = field.value.trim();
            const errorElement = form.querySelector(`[data-error="${fieldName}"]`);
            
            if (!value && !rule.required) {
                clearError(field, errorElement);
                return;
            }
            
            // Simple validation on blur
            if (rule.type === 'email' && value && !validateEmail(value)) {
                showError(field, errorElement, 'Invalid email format');
            } else if (rule.type === 'url' && value && !validateUrl(value)) {
                showError(field, errorElement, 'Invalid URL format');
            } else {
                clearError(field, errorElement);
            }
        });
    }
}

// UI helper functions
function showError(field, errorElement, message) {
    if (field) {
        field.classList.add('is-invalid');
        field.classList.remove('is-valid');
    }
    if (errorElement) {
        errorElement.textContent = message;
        errorElement.style.display = 'block';
    }
}

function clearError(field, errorElement) {
    if (field) {
        field.classList.remove('is-invalid');
        field.classList.add('is-valid');
    }
    if (errorElement) {
        errorElement.textContent = '';
        errorElement.style.display = 'none';
    }
}

function showPasswordStrength(field, strength) {
    const strengthElement = field.parentElement.querySelector('.password-strength');
    if (!strengthElement) return;
    
    strengthElement.className = 'password-strength';
    
    if (strength === 'weak') {
        strengthElement.classList.add('weak');
        strengthElement.textContent = 'Weak';
    } else if (strength === 'medium') {
        strengthElement.classList.add('medium');
        strengthElement.textContent = 'Medium';
    } else {
        strengthElement.classList.add('strong');
        strengthElement.textContent = 'Strong';
    }
}

// Setup validation for common forms
document.addEventListener('DOMContentLoaded', () => {
    // Login form validation
    setupFormValidation('login-form', {
        email: { type: 'email', required: true, label: 'Email' },
        password: { type: 'password', required: true, label: 'Password' }
    });
    
    // Register form validation
    setupFormValidation('register-form', {
        display_name: { required: true, minLength: 2, maxLength: 50, label: 'Display Name' },
        email: { type: 'email', required: true, label: 'Email' },
        password: { type: 'password', required: true, label: 'Password' },
        confirm_password: { type: 'password', required: true, label: 'Confirm Password' }
    });
    
    // Add device form validation
    setupFormValidation('add-device-form', {
        name: { required: true, minLength: 3, maxLength: 100, label: 'Device Name' },
        device_type: { required: true, label: 'Device Type' },
        channel_id: { type: 'channel_id', required: true, label: 'Channel ID' },
        api_key: { type: 'api_key', required: true, label: 'API Key' },
        ctop_url_1: { type: 'url', required: true, label: 'CTOP URL 1' },
        ctop_url_2: { type: 'url', required: false, label: 'CTOP URL 2' },
        auth_token: { required: true, minLength: 8, label: 'Auth Token' },
        latitude: { type: 'latitude', required: false, label: 'Latitude' },
        longitude: { type: 'longitude', required: false, label: 'Longitude' }
    });
    
    // Password confirmation check
    const registerForm = document.getElementById('register-form');
    if (registerForm) {
        registerForm.addEventListener('submit', (e) => {
            const password = registerForm.querySelector('[name="password"]').value;
            const confirmPassword = registerForm.querySelector('[name="confirm_password"]').value;
            
            if (password !== confirmPassword) {
                e.preventDefault();
                showAlert('Passwords do not match', 'danger');
            }
        });
    }
});
