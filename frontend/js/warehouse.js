(() => {
    const byId = id => document.getElementById(id);
    const rows = byId('warehouseRows');
    const message = byId('warehouseMessage');
    const refresh = byId('refreshWarehouse');
    const format = new Intl.NumberFormat('pl-PL', {maximumFractionDigits: 6});
    const kinds = {opening: 'Stan początkowy', receipt: 'Przyjęcie', issue: 'Rozchód'};
    let materials = [];
    let materialLoadRevision = 0;
    let movementContext;
    let minimumId;

    function escape(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
        })[char]);
    }

    function quantity(value, unit = '') { return `${format.format(Number(value))} ${unit}`.trim(); }
    function showMessage(text, kind = 'error') {
        message.textContent = text;
        message.className = `message ${kind}`;
        message.hidden = !text;
    }
    function formError(form, text = '') {
        const error = form.querySelector('.warehouse-form-error');
        error.textContent = text;
        error.hidden = !text;
    }
    function busy(form, value) {
        form.dataset.busy = value ? 'true' : '';
        form.querySelectorAll('button').forEach(button => { button.disabled = value; });
    }

    function render() {
        byId('materialCount').textContent = materials.length;
        byId('lowStockCount').textContent = materials.filter(item => item.is_low_stock && Number(item.quantity) > 0).length;
        byId('emptyStockCount').textContent = materials.filter(item => Number(item.quantity) === 0).length;
        const search = byId('materialSearch').value.trim().toLocaleLowerCase('pl-PL');
        const visible = materials.filter(item => item.name.toLocaleLowerCase('pl-PL').includes(search));
        if (!visible.length) {
            rows.innerHTML = `<tr><td colspan="5" class="warehouse-empty">${materials.length ? 'Brak materiałów pasujących do wyszukiwania.' : 'Magazyn jest pusty. Dodaj pierwszy materiał i jego stan początkowy.'}</td></tr>`;
            return;
        }
        rows.innerHTML = visible.map(item => {
            const empty = Number(item.quantity) === 0;
            const state = empty ? 'empty' : item.is_low_stock ? 'low' : 'ok';
            const label = empty ? 'Brak na stanie' : item.is_low_stock ? 'Niski stan' : 'Dostępny';
            return `<tr>
                <td class="warehouse-name">${escape(item.name)}</td>
                <td><strong>${escape(quantity(item.quantity, item.unit))}</strong></td>
                <td>${escape(quantity(item.minimum_quantity, item.unit))}</td>
                <td><span class="warehouse-status ${state}">${label}</span></td>
                <td><div class="warehouse-row-actions">
                    <button type="button" class="btn warehouse-receipt" data-action="receipt" data-id="${item.id}">Przyjmij</button>
                    <button type="button" class="btn warehouse-issue" data-action="issue" data-id="${item.id}" ${empty ? 'disabled' : ''}>Wydaj</button>
                    <button type="button" class="btn" data-action="history" data-id="${item.id}">Historia</button>
                    <button type="button" class="btn" data-action="minimum" data-id="${item.id}">Minimum</button>
                </div></td>
            </tr>`;
        }).join('');
    }

    async function loadMaterials() {
        const revision = ++materialLoadRevision;
        refresh.disabled = true;
        try {
            const loaded = await WeldERP.request('/api/warehouse/materials');
            if (revision !== materialLoadRevision) return false;
            materials = loaded;
            render();
            await WarehousePlanning.refresh(materials);
            return true;
        } catch (error) {
            if (revision !== materialLoadRevision) return false;
            showMessage(`Nie udało się odświeżyć magazynu: ${error.message}`);
            if (!materials.length) rows.innerHTML = '<tr><td colspan="5" class="warehouse-empty">Nie można pobrać stanów. Spróbuj ponownie przyciskiem „Odśwież”.</td></tr>';
            return false;
        } finally { if (revision === materialLoadRevision) refresh.disabled = false; }
    }

    async function submitForm(form, dialog, save, successText) {
        if (form.dataset.busy) return;
        busy(form, true);
        formError(form);
        try {
            await save();
            dialog.close();
            showMessage(successText, 'success');
            await loadMaterials();
        } catch (error) { formError(form, error.message); }
        finally { busy(form, false); }
    }

    document.querySelectorAll('dialog').forEach(dialog => {
        dialog.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
        dialog.addEventListener('cancel', event => {
            if (dialog.querySelector('form[data-busy="true"]')) event.preventDefault();
        });
    });
    byId('materialSearch').addEventListener('input', render);
    refresh.addEventListener('click', () => { showMessage(''); loadMaterials(); });
    byId('addMaterial').addEventListener('click', () => {
        const form = byId('createMaterialForm');
        form.reset();
        byId('materialUnit').value = 'kg';
        formError(form);
        byId('createMaterialDialog').showModal();
    });

    byId('createMaterialForm').addEventListener('submit', event => {
        event.preventDefault();
        const form = event.currentTarget;
        const fields = Object.fromEntries(new FormData(form));
        submitForm(form, byId('createMaterialDialog'), () =>
            WeldERP.request('/api/warehouse/materials', WeldERP.json('POST', fields)), 'Materiał dodany');
    });

    byId('movementForm').addEventListener('submit', event => {
        event.preventDefault();
        const form = event.currentTarget;
        const fields = {...Object.fromEntries(new FormData(form)), kind: movementContext.kind, request_id: movementContext.requestId};
        fields.order_part_id = movementContext.kind === 'issue' && fields.order_part_id ? Number(fields.order_part_id) : null;
        submitForm(form, byId('movementDialog'), () => WeldERP.request(
            `/api/warehouse/materials/${movementContext.id}/movements`, WeldERP.json('POST', fields)),
            movementContext.kind === 'receipt' ? 'Przyjęcie zapisane' : 'Rozchód zapisany');
    });

    byId('minimumForm').addEventListener('submit', event => {
        event.preventDefault();
        const form = event.currentTarget;
        submitForm(form, byId('minimumDialog'), () => WeldERP.request(
            `/api/warehouse/materials/${minimumId}/minimum`, WeldERP.json('PATCH', Object.fromEntries(new FormData(form)))),
            'Stan minimalny zapisany');
    });

    rows.addEventListener('click', async event => {
        const button = event.target.closest('button[data-action]');
        if (!button) return;
        const item = materials.find(material => material.id === Number(button.dataset.id));
        if (!item) return;
        const action = button.dataset.action;
        if (action === 'history') {
            byId('historyMaterial').textContent = `${item.name} · ${item.unit}`;
            byId('movementHistory').textContent = 'Ładowanie historii…';
            byId('historyDialog').showModal();
            try {
                const history = await WeldERP.request(`/api/warehouse/materials/${item.id}/movements`);
                byId('movementHistory').innerHTML = history.length ? `<table class="warehouse-table"><thead><tr>
                    <th>Data</th><th>Rodzaj</th><th>Zmiana</th><th>Stan po ruchu</th><th>Zlecenie / uwagi</th>
                    </tr></thead><tbody>${history.map(entry => `<tr>
                        <td>${escape(new Date(entry.created_at + 'Z').toLocaleString('pl-PL'))}</td>
                        <td>${escape(kinds[entry.kind] || entry.kind)}</td>
                        <td>${Number(entry.quantity) > 0 ? '+' : ''}${escape(quantity(entry.quantity, item.unit))}</td>
                        <td>${escape(quantity(entry.balance_after, item.unit))}</td><td>${escape([entry.order_number, entry.part_name, entry.note].filter(Boolean).join(' · ') || '—')}</td>
                    </tr>`).join('')}</tbody></table>` : '<p class="warehouse-help">Brak ruchów. Przyjmij pierwszą dostawę materiału.</p>';
            } catch (error) { byId('movementHistory').textContent = `Nie udało się pobrać historii: ${error.message}`; }
        } else if (action === 'minimum') {
            const form = byId('minimumForm');
            minimumId = item.id;
            form.elements.minimum_quantity.value = item.minimum_quantity;
            byId('minimumMaterial').textContent = `${item.name} · ${item.unit}`;
            formError(form);
            byId('minimumDialog').showModal();
        } else {
            try { await WarehousePlanning.prepareIssue(item.id, action); }
            catch (error) { showMessage(error.message); return; }
            movementContext = {id: item.id, kind: action, requestId: WeldERP.clientKey()};
            const form = byId('movementForm');
            form.reset();
            formError(form);
            byId('movementHeading').textContent = action === 'receipt' ? 'Przyjęcie materiału' : 'Wydanie materiału';
            byId('saveMovement').textContent = action === 'receipt' ? 'Zapisz przyjęcie' : 'Zapisz rozchód';
            byId('movementMaterial').textContent = `${item.name} · ${item.unit}`;
            byId('movementAvailable').textContent = `Dostępne: ${quantity(item.quantity, item.unit)}`;
            byId('movementDialog').showModal();
        }
    });

    async function initialize() {
        let user;
        try { user = await WeldERP.getUser(); }
        catch { window.location.replace('/'); return; }
        if (user.role !== 'manager') {
            document.body.innerHTML = '<main class="page"><h1>Magazyn kierownika</h1><p>Twoje konto operatora nie ma dostępu do stanów i prognoz magazynu.</p><p><a href="/kanban.html">Wróć do tablicy Kanban</a></p></main>';
            return;
        }
        byId('warehousePage').hidden = false;
        byId('warehouseUser').textContent = `${user.full_name} · kierownik`;
        try {
            const metadata = await WeldERP.request('/metadata/material-units');
            byId('materialUnit').innerHTML = metadata.units.map(unit => `<option value="${escape(unit)}">${escape(unit)}</option>`).join('');
            byId('materialUnit').value = 'kg';
        } catch (error) {
            byId('addMaterial').disabled = true;
            showMessage(`Nie udało się pobrać jednostek: ${error.message}. Odśwież stronę.`);
        }
        await loadMaterials();
    }
    window.addEventListener('warehouse:changed', () => loadMaterials());
    byId('warehouseLogout').addEventListener('click', async () => {
        await WeldERP.request('/logout/', {method: 'POST'});
        window.location.href = '/';
    });
    initialize();
})();
