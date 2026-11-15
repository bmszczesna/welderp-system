window.MaterialPlan = (() => {
    let ready;
    let catalog = [];
    let manager = false;
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));

    function initialize() {
        ready ||= WeldERP.getUser().then(async user => {
            manager = user.role === 'manager';
            if (manager) catalog = await WeldERP.request('/metadata/material-catalog');
        }).catch(error => { ready = null; throw error; });
        return ready;
    }

    function create(part = {}) {
        const fieldset = document.createElement('fieldset');
        fieldset.className = 'material-plan';
        if (!manager) { fieldset.hidden = true; return fieldset; }
        fieldset.innerHTML = `<legend>Plan materiałowy kierownika</legend>
            <p>Te ilości zasilają prognozę magazynu. Wydanie na część pomniejsza jej pozostałe potrzeby.</p>
            <div class="material-plan-grid">
                <label>Materiał magazynowy<select class="plan-material"><option value="">Bez planu materiałowego</option>
                    ${catalog.map(item => `<option value="${item.id}">${escape(item.name)} (${escape(item.unit)})</option>`).join('')}
                </select></label>
                <label>Planowana ilość <span class="plan-unit"></span><input class="plan-quantity" inputmode="decimal" placeholder="np. 12,5" value="${escape(part.planned_quantity ?? '')}"></label>
                <label>Planowana data zużycia<input class="plan-date" type="date" value="${escape(part.planned_for || '')}"></label>
            </div><p>Pusta data oznacza termin realizacji zlecenia. <a href="/frontend/warehouse.html" target="_blank">Otwórz magazyn</a></p>`;
        const select = fieldset.querySelector('.plan-material');
        select.value = part.warehouse_material_id || '';
        const updateUnit = () => {
            const item = catalog.find(item => item.id === Number(select.value));
            fieldset.querySelector('.plan-unit').textContent = item ? `(${item.unit})` : '';
        };
        select.addEventListener('change', updateUnit);
        updateUnit();
        return fieldset;
    }

    function payload(block) {
        if (!manager) return {};
        const fieldset = block.querySelector('.material-plan');
        const id = fieldset.querySelector('.plan-material').value;
        const quantity = fieldset.querySelector('.plan-quantity').value.trim();
        const date = fieldset.querySelector('.plan-date').value;
        if ((!id && (quantity || date)) || (id && !quantity)) throw new Error('Wybierz materiał magazynowy i podaj planowaną ilość');
        return {warehouse_material_id: id ? Number(id) : null, planned_quantity: quantity || null, planned_for: date || null};
    }

    function describe(part, deadline) {
        if (!part.warehouse_material_id) return '';
        return `<p class="material-plan-summary"><strong>Plan magazynu:</strong> ${escape(part.planned_material_name)} ·
            ${escape(Number(part.planned_quantity))} ${escape(part.planned_unit)} · zużycie ${escape(part.planned_for || (deadline || '').slice(0,10))}</p>`;
    }
    return {initialize, create, payload, describe};
})();
