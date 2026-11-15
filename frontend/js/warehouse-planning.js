window.WarehousePlanning = (() => {
    const byId = id => document.getElementById(id);
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
    const amount = (value, unit = '') => `${new Intl.NumberFormat('pl-PL', {maximumFractionDigits: 6}).format(Number(value))} ${unit}`.trim();
    const date = value => new Date(value + 'T12:00:00').toLocaleDateString('pl-PL');
    let materials = [];
    let projection;
    let demands = [];
    let pending = Promise.resolve(false);
    let revision = 0;

    async function refresh(items) {
        materials = items;
        const current = ++revision;
        pending = (async () => {
            try {
                const [forecast, deliveries, needs] = await Promise.all([
                    WeldERP.request(`/api/warehouse/forecast?as_of=${WeldERP.localDate()}&days=${byId('forecastHorizon').value}`),
                    WeldERP.request('/api/warehouse/deliveries'), WeldERP.request('/api/warehouse/demands')
                ]);
                if (current !== revision) return false;
                projection = forecast;
                demands = needs.items;
                const warnings = [];
                if (forecast.unplanned_parts) warnings.push(`${forecast.unplanned_parts} części aktywnych zleceń nie ma kompletnego planu materiałowego. Prognoza nie obejmuje ich potrzeb.`);
                if (forecast.overdue_deliveries) warnings.push(`${forecast.overdue_deliveries} dostaw jest po terminie. Nie uwzględniono ich jako dostępnego materiału.`);
                byId('forecastWarning').textContent = warnings.join(' ');
                byId('forecastWarning').hidden = !warnings.length;
                byId('forecastRows').innerHTML = forecast.items.length ? forecast.items.map(item => `<tr>
                    <td class="warehouse-name">${escape(item.name)} (${escape(item.unit)})</td>
                    <td>${escape(amount(item.current_quantity, item.unit))}</td>
                    <td class="${Number(item.tomorrow_quantity) < 0 ? 'warehouse-shortage' : ''}">${escape(amount(item.tomorrow_quantity, item.unit))}</td>
                    <td>${item.depletion_on ? escape(date(item.depletion_on)) : 'Poza okresem prognozy'}</td>
                    <td class="${Number(item.suggested_order_quantity) > 0 ? 'warehouse-recommendation' : ''}">${escape(amount(item.suggested_order_quantity, item.unit))}</td>
                    <td><div class="warehouse-row-actions"><button class="btn" data-forecast="${item.material_id}" type="button">Szczegóły</button>
                    <button class="btn warehouse-receipt" data-plan="${item.material_id}" type="button">Zaplanuj dostawę</button></div></td></tr>`).join('') : '<tr><td colspan="6" class="warehouse-empty">Dodaj materiały, aby przygotować prognozę.</td></tr>';
                const labels = {ordered: 'Zaplanowana', received: 'Przyjęta', cancelled: 'Anulowana'};
                byId('deliveryRows').innerHTML = deliveries.length ? deliveries.map(delivery => {
                    const material = materials.find(item => item.id === delivery.material_id);
                    const overdue = delivery.status === 'ordered' && delivery.expected_on < WeldERP.localDate();
                    return `<tr><td class="warehouse-name">${escape(material?.name || 'Materiał')}</td><td>${escape(amount(delivery.quantity, material?.unit))}</td>
                        <td>${escape(date(delivery.expected_on))}</td><td class="${overdue ? 'warehouse-shortage' : ''}">${overdue ? 'Po terminie' : labels[delivery.status]}</td>
                        <td>${escape(delivery.note || '—')}</td><td>${delivery.status === 'ordered' ? `<div class="warehouse-row-actions">
                        <button class="btn warehouse-receipt" data-receive="${delivery.id}" type="button">Przyjmij dostawę</button>
                        <button class="btn" data-cancel="${delivery.id}" type="button">Anuluj plan</button></div>` : '—'}</td></tr>`;
                }).join('') : '<tr><td colspan="6" class="warehouse-empty">Brak zaplanowanych dostaw.</td></tr>';
                return true;
            } catch (error) {
                byId('forecastWarning').textContent = `Nie udało się odświeżyć prognozy: ${error.message}`;
                byId('forecastWarning').hidden = false;
                return false;
            }
        })();
        return pending;
    }

    function openDelivery(materialId) {
        const form = byId('deliveryForm');
        form.reset();
        const error = form.querySelector('.warehouse-form-error');
        error.hidden = true;
        byId('deliveryMaterial').innerHTML = '<option value="">Wybierz materiał</option>' + materials.map(item =>
            `<option value="${item.id}">${escape(item.name)} (${escape(item.unit)})</option>`).join('');
        const item = projection?.items.find(item => item.material_id === Number(materialId));
        if (item) {
            byId('deliveryMaterial').value = item.material_id;
            form.elements.quantity.value = Number(item.suggested_order_quantity) > 0 ? item.suggested_order_quantity : '';
        }
        form.elements.expected_on.value = item?.shortage_on && item.shortage_on >= WeldERP.localDate() ? item.shortage_on : WeldERP.localDate();
        byId('deliveryDialog').showModal();
    }

    byId('forecastHorizon').addEventListener('change', () => refresh(materials));
    byId('planDelivery').addEventListener('click', () => openDelivery());
    byId('forecastRows').addEventListener('click', event => {
        const plan = event.target.closest('[data-plan]');
        if (plan) { openDelivery(plan.dataset.plan); return; }
        const button = event.target.closest('[data-forecast]');
        if (!button) return;
        const item = projection.items.find(item => item.material_id === Number(button.dataset.forecast));
        byId('forecastMaterial').textContent = `${item.name} · ${item.unit}`;
        byId('forecastDetails').innerHTML = `<table class="warehouse-table"><thead><tr><th>Dzień</th><th>Pozostałe potrzeby zleceń</th><th>Planowane dostawy</th><th>Przewidywany stan</th></tr></thead>
            <tbody>${item.days.map(day => `<tr><td>${escape(date(day.day))}</td><td>${escape(amount(day.demand_quantity, item.unit))}</td>
            <td>${escape(amount(day.receipt_quantity, item.unit))}</td><td class="${Number(day.projected_quantity) < 0 ? 'warehouse-shortage' : ''}">${escape(amount(day.projected_quantity, item.unit))}</td></tr>`).join('')}</tbody></table>`;
        byId('forecastDialog').showModal();
    });

    byId('deliveryForm').addEventListener('submit', async event => {
        event.preventDefault();
        const form = event.currentTarget;
        if (form.dataset.busy) return;
        form.dataset.busy = 'true';
        form.querySelectorAll('button').forEach(button => { button.disabled = true; });
        const error = form.querySelector('.warehouse-form-error');
        error.hidden = true;
        try {
            const fields = Object.fromEntries(new FormData(form));
            fields.material_id = Number(fields.material_id);
            await WeldERP.request('/api/warehouse/deliveries', WeldERP.json('POST', fields));
            byId('deliveryDialog').close();
            window.dispatchEvent(new Event('warehouse:changed'));
        } catch (problem) { error.textContent = problem.message; error.hidden = false; }
        finally { form.dataset.busy = ''; form.querySelectorAll('button').forEach(button => { button.disabled = false; }); }
    });

    byId('deliveryRows').addEventListener('click', async event => {
        const button = event.target.closest('[data-receive], [data-cancel]');
        if (!button) return;
        button.disabled = true;
        const id = button.dataset.receive || button.dataset.cancel;
        try {
            await WeldERP.request(`/api/warehouse/deliveries/${id}/${button.dataset.receive ? 'receive' : 'cancel'}`, {method: 'POST'});
            window.dispatchEvent(new Event('warehouse:changed'));
        } catch (error) { byId('forecastWarning').textContent = error.message; byId('forecastWarning').hidden = false; }
        finally { if (button.isConnected) button.disabled = false; }
    });

    async function prepareIssue(materialId, kind) {
        if (kind === 'issue' && !await pending) throw new Error('Odśwież prognozę przed przypisaniem rozchodu do zlecenia');
        byId('movementPartLabel').hidden = kind !== 'issue';
        byId('movementPlanHint').hidden = kind !== 'issue';
        byId('movementPart').innerHTML = '<option value="">Rozchód ogólny (bez zlecenia)</option>' +
            demands.filter(item => item.material_id === materialId).map(item => `<option value="${item.part_id}">
            ${escape(item.order_number)} / ${escape(item.part_name)} · pozostało ${escape(amount(item.remaining_quantity))}</option>`).join('');
    }
    return {refresh, prepareIssue};
})();
