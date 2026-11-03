(() => {
    const list = document.getElementById('alertsList');
    const message = document.getElementById('alertsMessage');
    const filter = document.getElementById('alertFilter');
    const refreshButton = document.getElementById('refreshAlerts');
    let alerts = [];

    const statusLabels = {
        IN_QUEUE: 'Oczekujące',
        IN_PROGRESS: 'W realizacji',
        QUALITY_CHECK: 'Kontrola jakości',
    };

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;',
        })[character]);
    }

    function showMessage(text, kind = '') {
        message.textContent = text;
        message.className = `message alerts-message${kind ? ` ${kind}` : ''}`;
        message.hidden = !text;
    }

    function updateSummary(items) {
        document.getElementById('totalAlerts').textContent = items.length;
        document.getElementById('queueAlerts').textContent =
            items.filter(item => item.alert_type === 'queue_delay').length;
        document.getElementById('deadlineAlerts').textContent =
            items.filter(item => item.alert_type === 'deadline_overdue').length;
        document.getElementById('alertsUpdated').textContent =
            `Zaktualizowano: ${new Date().toLocaleTimeString('pl-PL', {hour: '2-digit', minute: '2-digit'})}`;
    }

    function renderAlerts() {
        const selectedType = filter.value;
        const visibleAlerts = selectedType === 'all'
            ? alerts
            : alerts.filter(item => item.alert_type === selectedType);

        if (!visibleAlerts.length) {
            const text = alerts.length
                ? 'Brak alertów dla wybranego filtra.'
                : 'Brak aktywnych alertów. Wszystkie zlecenia są na bieżąco.';
            list.innerHTML = `<p class="empty-state alerts-empty">${text}</p>`;
            return;
        }

        list.innerHTML = visibleAlerts.map(item => {
            const isQueue = item.alert_type === 'queue_delay';
            const typeLabel = isQueue ? 'Opóźnienie w kolejce' : 'Przekroczony termin';
            const typeClass = isQueue ? 'queue' : 'deadline';
            const daysLabel = item.days_count === 1 ? 'dzień' : 'dni';

            return `
                <article class="order-card alert-card ${typeClass}">
                    <div class="alert-card-heading">
                        <div>
                            <p class="order-number">${escapeHtml(item.order_number)}</p>
                            <p class="client">${escapeHtml(item.client_name || 'Brak nazwy klienta')}</p>
                        </div>
                        <span class="alert-type-badge ${typeClass}">${typeLabel}</span>
                    </div>
                    <p class="alert-card-message">${escapeHtml(item.message)}</p>
                    <div class="alert-card-footer">
                        <span class="alert-status">${escapeHtml(statusLabels[item.status] || item.status)}</span>
                        <span class="alert-days">${escapeHtml(item.days_count)} ${daysLabel}</span>
                    </div>
                </article>
            `;
        }).join('');
    }

    async function loadAlerts() {
        refreshButton.disabled = true;
        refreshButton.textContent = 'Odświeżanie…';
        showMessage('');

        try {
            const response = await WeldERP.request('/api/alerts');
            alerts = response.items;
            updateSummary(alerts);
            renderAlerts();
        } catch (error) {
            showMessage(`Nie udało się pobrać alertów: ${error.message}`, 'error');
            list.innerHTML = '';
        } finally {
            refreshButton.disabled = false;
            refreshButton.textContent = 'Odśwież alerty';
        }
    }

    filter.addEventListener('change', renderAlerts);
    refreshButton.addEventListener('click', loadAlerts);
    loadAlerts();
})();
