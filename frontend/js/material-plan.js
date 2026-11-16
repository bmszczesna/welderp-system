window.MaterialPlan = (() => {
    let ready;
    let catalog = [];
    let manager = false;
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));

    function initialize() {
        ready ||= WeldERP.getUser().then(async user => {
            manager = user.role === 'manager';
            catalog = await WeldERP.request('/metadata/material-catalog');
        }).catch(error => { ready = null; throw error; });
        return ready;
    }

    function create(part = {}) {
        const fieldset = document.createElement('fieldset');
        fieldset.className = 'material-plan';
        fieldset.dataset.initialMaterial = part.warehouse_material_id || '';
        fieldset.dataset.reserved = part.reserved_quantity || '0';
        fieldset.innerHTML = `<legend>Materiał potrzebny do zlecenia</legend>
            <p>Po zapisaniu zlecenia materiał zostanie od razu zarezerwowany. Przy braku zapasu zlecenie poczeka na uzupełnienie magazynu.</p>
            <div class="material-plan-grid">
                <label>Materiał<select class="plan-material" required><option value="">Wybierz materiał z magazynu</option>
                    ${catalog.map(item => `<option value="${item.id}">${escape(item.name)} (${escape(item.unit)}) · wolne ${escape(Number(item.available_quantity))}</option>`).join('')}
                </select></label>
                <label>Potrzebna ilość <span class="plan-unit"></span><input class="plan-quantity" inputmode="decimal" required placeholder="np. 12,5" value="${escape(part.planned_quantity ?? '')}"></label>
            </div><p class="material-availability" role="status"></p><p>${manager ? '<a href="/frontend/warehouse.html" target="_blank">Otwórz magazyn</a>. Materiał spoza listy dodaj w magazynie, nawet ze stanem 0.' : 'Materiału nie ma na liście? Poproś kierownika o dodanie go do magazynu.'}</p>`;
        const select = fieldset.querySelector('.plan-material');
        select.value = part.warehouse_material_id || '';
        const updateUnit = () => {
            const item = catalog.find(item => item.id === Number(select.value));
            fieldset.querySelector('.plan-unit').textContent = item ? `(${item.unit})` : '';
        };
        const update = () => { updateUnit(); updateAvailability(fieldset); };
        select.addEventListener('change', update);
        fieldset.querySelector('.plan-quantity').addEventListener('input', update);
        updateUnit();
        updateAvailability(fieldset);
        return fieldset;
    }

    function payload(block) {
        const fieldset = block.querySelector('.material-plan');
        const id = fieldset.querySelector('.plan-material').value;
        const quantity = fieldset.querySelector('.plan-quantity').value.trim();
        if (!id || !quantity) throw new Error('Wybierz materiał i podaj potrzebną ilość przy każdej części');
        const item = catalog.find(item => item.id === Number(id));
        return {material: item.name, warehouse_material_id: Number(id), planned_quantity: quantity, planned_for: null};
    }

    function describe(part, deadline) {
        if (!part.warehouse_material_id) return '<p class="material-plan-summary material-shortage">Wybierz materiał z magazynu i podaj potrzebną ilość w edycji zlecenia.</p>';
        return `<p class="material-plan-summary ${Number(part.shortage_quantity) > 0 ? 'material-shortage' : ''}"><strong>Materiał:</strong> ${escape(part.planned_material_name)} ·
            potrzeba ${escape(Number(part.planned_quantity))} ${escape(part.planned_unit)} · zarezerwowano ${escape(Number(part.reserved_quantity))} · wydano ${escape(Number(part.issued_quantity))}
            ${Number(part.shortage_quantity) > 0 ? `· <strong>brakuje ${escape(Number(part.shortage_quantity))} ${escape(part.planned_unit)}. Uzupełnij magazyn.</strong>` : ''}</p>`;
    }
    function updateAvailability(current) {
        const fields = [...document.querySelectorAll('.material-plan')].filter(item => item.getClientRects().length);
        if (!fields.includes(current)) fields.push(current);
        fields.forEach(field => {
            const id = Number(field.querySelector('.plan-material').value);
            const item = catalog.find(item => item.id === id);
            const hint = field.querySelector('.material-availability');
            if (!item) { hint.textContent = 'Wybierz materiał, aby sprawdzić wolny zapas.'; return; }
            const own = fields.reduce((sum, f) => sum + (Number(f.dataset.initialMaterial) === id ? Number(f.dataset.reserved) : 0), 0);
            const needed = fields.reduce((sum, f) => sum + (Number(f.querySelector('.plan-material').value) === id ?
                Number(f.querySelector('.plan-quantity').value.replace(',', '.')) || 0 : 0), 0);
            const available = Number(item.available_quantity) + own;
            const missing = Math.max(0, needed - available);
            hint.classList.toggle('material-shortage', missing > 0);
            hint.textContent = missing > 0 ? `Brakuje ${Number(missing.toFixed(6))} ${item.unit}. Zlecenie zapisze się w oczekujących i będzie zablokowane do uzupełnienia magazynu.` :
                `Dostępne do rezerwacji: ${Number(available.toFixed(6))} ${item.unit}. Rezerwacja powstanie po zapisaniu zlecenia.`;
        });
    }
    return {initialize, create, payload, describe};
})();
