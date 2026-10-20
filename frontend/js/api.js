/* Shared transport and validation messages for the production-data forms. */
window.WeldERP = (() => {
    function errorMessage(detail) {
        if (Array.isArray(detail)) {
            return detail.map(error => {
                const location = (error.loc || []).filter(item => item !== 'body').join(' → ');
                return `${location ? location + ': ' : ''}${error.msg || 'Niepoprawna wartość'}`;
            }).join('\n');
        }
        return typeof detail === 'string' ? detail : 'Nie udało się wykonać operacji';
    }

    async function request(url, options = {}) {
        const response = await fetch(url, options);
        const contentType = response.headers.get('content-type') || '';
        const data = contentType.includes('application/json') ? await response.json() : null;
        if (!response.ok) throw new Error(errorMessage(data?.detail || `Błąd HTTP ${response.status}`));
        return data;
    }

    function json(method, data) {
        return {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)};
    }

    let metadataPromise;
    async function validatePDF(file) {
        if (!file.name.toLowerCase().endsWith('.pdf')) throw new Error('Wybierz plik PDF');
        if (!file.size) throw new Error('Plik PDF jest pusty');
        metadataPromise ||= request('/metadata/material-units').catch(error => { metadataPromise = null; throw error; });
        const metadata = await metadataPromise;
        if (file.size > metadata.max_pdf_bytes) throw new Error(`Plik PDF przekracza limit ${metadata.max_pdf_bytes / (1024 * 1024)} MB`);
        const header = new TextDecoder().decode(await file.slice(0, 5).arrayBuffer());
        if (header !== '%PDF-') throw new Error(`Plik ${file.name} nie ma nagłówka PDF`);
    }

    async function uploadPDF(partId, file) {
        await validatePDF(file);
        const data = new FormData();
        data.append('file', file);
        return request(`/parts/${partId}/upload-pdf`, {method: 'POST', body: data});
    }

    function clientKey() {
        return window.crypto?.randomUUID?.() || `part-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function localDate() {
        const date = new Date();
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    }

    return {request, json, errorMessage, validatePDF, uploadPDF, clientKey, localDate};
})();
