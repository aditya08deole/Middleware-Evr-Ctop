/**
 * Shared DOM/string helpers used across pages and scripts.
 *
 * escapeHtml() used to be copy-pasted (byte-identical) into
 * realtime-dashboard.js, logs-viewer.js, multi-step-form.js,
 * settings.html, and device_detail.html (as escapeHtmlAttr) — a future
 * hardening fix (e.g. escaping backticks for template-literal injection)
 * would have had to be applied by hand in all 5 places, and missing one
 * would leave a live injection vector in that specific view. Loaded via
 * base.html before every other script, so it's available everywhere.
 */
function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
}
