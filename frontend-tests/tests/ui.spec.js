const path = require('node:path');
const {test, expect} = require('@playwright/test');

const manager = {
    id: 1,
    username: 'test-manager',
    full_name: 'Test Kierownik',
    role: 'manager',
};

const operator = {
    id: 2,
    username: 'test-operator',
    full_name: 'Test Operator',
    role: 'operator',
};

async function respondJson(route, body, status = 200) {
    await route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
    });
}

async function mockLogin(page, user, status = 200) {
    await page.route('**/login/', route => respondJson(
        route,
        status === 200 ? {user} : {detail: 'Nieprawidłowy login lub hasło.'},
        status,
    ));
    await page.route('**/auth/me', route => respondJson(route, user));
    await page.route('**/orders/', route => respondJson(route, []));
    await page.route('**/operators/', route => respondJson(route, []));
    await page.route('**/metadata/material-units', route => respondJson(
        route, {units: ['kg', 'szt.'], max_pdf_bytes: 10 * 1024 * 1024},
    ));
}

async function serveKanbanMarkup(page) {
    await page.route('**/kanban.html', route => route.fulfill({
        path: path.resolve(__dirname, '../../frontend/kanban.html'),
    }));
}

async function serveArchiveMarkup(page) {
    await page.route('**/archive.html', route => route.fulfill({
        path: path.resolve(__dirname, '../../frontend/archive.html'),
    }));
}

function kanbanOrder(id, status, overrides = {}) {
    return {
        id,
        order_number: `UI/${id}`,
        client_name: `Klient ${id}`,
        status,
        date_received: '2026-11-01T00:00:00',
        date_deadline: '2026-12-20T00:00:00',
        materials_ready: true,
        is_approved: false,
        needs_correction: false,
        parts: [],
        ...overrides,
    };
}

async function mockKanban(page, orders) {
    await page.route('**/auth/me', route => respondJson(route, manager));
    await page.route('**/metadata/material-units', route => respondJson(
        route, {units: ['kg'], max_pdf_bytes: 10 * 1024 * 1024},
    ));
    await page.route('**/metadata/material-catalog', route => respondJson(route, [
        {id: 5, name: 'Stal S235', unit: 'kg', available_quantity: 80},
    ]));
    await page.route('**/operators/', route => respondJson(route, [{id: 1, name: 'Ada'}]));
    await page.route('**/orders/', route => respondJson(route, orders));
    await serveKanbanMarkup(page);
}

function material(id, name, quantity, reserved, available, minimum, isLowStock = false) {
    return {
        id,
        name,
        unit: 'kg',
        quantity: String(quantity),
        reserved_quantity: String(reserved),
        available_quantity: String(available),
        minimum_quantity: String(minimum),
        is_low_stock: isLowStock,
    };
}

async function mockWarehouse(page, user, materials, options = {}) {
    const deliveries = options.deliveries || [];
    await page.route('**/auth/me', route => respondJson(route, user));
    await page.route('**/metadata/material-units', route => respondJson(
        route, {units: ['kg', 'szt.'], max_pdf_bytes: 10 * 1024 * 1024},
    ));
    await page.route('**/api/warehouse/materials', async route => {
        if (route.request().method() === 'GET') return respondJson(route, materials);
        if (options.createError) {
            return respondJson(route, {detail: options.createError}, options.createStatus || 409);
        }

        const values = route.request().postDataJSON();
        const item = options.onCreate
            ? options.onCreate(values, materials.length + 1)
            : material(materials.length + 1, values.name, values.opening_quantity, 0,
                values.opening_quantity, values.minimum_quantity);
        materials.push(item);
        return respondJson(route, item, 201);
    });
    await page.route('**/api/warehouse/materials/*/minimum', async route => {
        const values = route.request().postDataJSON();
        const id = Number(new URL(route.request().url()).pathname.split('/').slice(-2, -1)[0]);
        const item = materials.find(row => row.id === id);
        item.minimum_quantity = values.minimum_quantity;
        item.is_low_stock = Number(item.available_quantity) <= Number(item.minimum_quantity);
        if (options.onMinimum) options.onMinimum(values);
        return respondJson(route, item);
    });
    await page.route('**/api/warehouse/materials/*/movements', async route => {
        if (route.request().method() === 'GET') {
            return respondJson(route, options.history || []);
        }
        const values = route.request().postDataJSON();
        if (options.onMovement) options.onMovement(values);
        return respondJson(route, {
            id: 1,
            material_id: Number(new URL(route.request().url()).pathname.split('/').slice(-2, -1)[0]),
            kind: values.kind,
            quantity: values.quantity,
            balance_after: '8.5',
        }, 201);
    });
    await page.route('**/api/warehouse/forecast**', route => {
        const url = new URL(route.request().url());
        const targetDate = url.searchParams.get('target_date');
        if (options.forecastDates) options.forecastDates.push(targetDate);
        return respondJson(route, {
            as_of: url.searchParams.get('as_of'),
            target_date: targetDate,
            items: options.forecastItems || [],
            unplanned_parts: 0,
            overdue_deliveries: 0,
        });
    });
    await page.route('**/api/warehouse/deliveries', async route => {
        if (route.request().method() === 'GET') return respondJson(route, deliveries);
        const values = route.request().postDataJSON();
        const delivery = options.onDeliveryCreate
            ? options.onDeliveryCreate(values, deliveries.length + 1)
            : {id: deliveries.length + 1, ...values, status: 'ordered'};
        deliveries.push(delivery);
        return respondJson(route, delivery, 201);
    });
    await page.route('**/api/warehouse/deliveries/*/*', async route => {
        const segments = new URL(route.request().url()).pathname.split('/');
        const id = Number(segments[segments.length - 2]);
        const action = segments[segments.length - 1];
        const delivery = deliveries.find(row => row.id === id);
        if (options.onDeliveryAction) options.onDeliveryAction(action, delivery);
        else delivery.status = action === 'receive' ? 'received' : 'cancelled';
        return respondJson(route, delivery);
    });
    await page.route('**/api/warehouse/demands', route => respondJson(route, {items: []}));
}

async function openWarehouse(page) {
    await page.goto('/frontend/warehouse.html');
    await expect(page.locator('#warehousePage')).toBeVisible();
}

async function fillMaterialForm(dialog, name = 'Stal S235JR') {
    await dialog.getByLabel('Nazwa materiału').fill(name);
    await dialog.getByLabel('Jednostka').selectOption('kg');
    await dialog.locator('[name="opening_quantity"]').fill('100');
    await dialog.locator('[name="minimum_quantity"]').fill('15');
}

test('manager can log in, open warehouse, and add a material', async ({page}) => {
    const materials = [];
    let submittedMaterial;
    await mockLogin(page, manager);
    await serveKanbanMarkup(page);
    await mockWarehouse(page, manager, materials, {
        onCreate: (values, id) => {
            submittedMaterial = values;
            return material(id, values.name, values.opening_quantity, 0,
                values.opening_quantity, values.minimum_quantity);
        },
    });

    await page.goto('/frontend/login.html');
    await page.locator('#username').fill(manager.username);
    await page.locator('#password').fill('test-password');
    await page.getByRole('button', {name: 'Zaloguj się'}).click();

    await expect(page).toHaveURL(/\/kanban\.html$/);
    const warehouseButton = page.getByRole('button', {name: 'Magazyn'});
    await expect(warehouseButton).toBeVisible();
    await warehouseButton.click();
    await expect(page).toHaveURL(/\/frontend\/warehouse\.html$/);
    await expect(page.locator('#warehouseUser')).toHaveText('Test Kierownik · kierownik');

    await page.locator('#addMaterial').click();
    const dialog = page.locator('#createMaterialDialog');
    await fillMaterialForm(dialog);
    await dialog.getByRole('button', {name: 'Dodaj materiał'}).click();

    await expect(dialog).not.toBeVisible();
    await expect(page.locator('#warehouseRows')).toContainText('Stal S235JR');
    await expect(page.locator('#warehouseMessage')).toContainText('Materiał dodany');
    expect(submittedMaterial).toEqual({
        name: 'Stal S235JR',
        unit: 'kg',
        opening_quantity: '100',
        minimum_quantity: '15',
    });
});

test('operator cannot see the manager warehouse link or open the warehouse page', async ({page}) => {
    await mockLogin(page, operator);
    await serveKanbanMarkup(page);
    await mockWarehouse(page, operator, []);

    await page.goto('/frontend/login.html');
    await page.locator('#username').fill(operator.username);
    await page.locator('#password').fill('test-password');
    await page.getByRole('button', {name: 'Zaloguj się'}).click();
    await expect(page).toHaveURL(/\/kanban\.html$/);
    await expect(page.getByRole('button', {name: 'Magazyn'})).toBeHidden();

    await page.goto('/frontend/warehouse.html');
    await expect(page.getByText(
        'Twoje konto operatora nie ma dostępu do stanów i prognoz magazynu.',
    )).toBeVisible();
});

test('login displays an API error without leaving the login page', async ({page}) => {
    await mockLogin(page, operator, 401);
    let alertMessage;
    page.on('dialog', async dialog => {
        alertMessage = dialog.message();
        await dialog.dismiss();
    });

    await page.goto('/frontend/login.html');
    await page.locator('#username').fill(operator.username);
    await page.locator('#password').fill('wrong-password');
    await page.getByRole('button', {name: 'Zaloguj się'}).click();

    await expect.poll(() => alertMessage).toBe('Nieprawidłowy login lub hasło.');
    await expect(page).toHaveURL(/\/frontend\/login\.html$/);
});

test('unauthenticated users are redirected from the Kanban board to login', async ({page}) => {
    await page.goto('/kanban.html');
    await expect(page).toHaveURL(/\/frontend\/login\.html$/);
});

test('warehouse form shows an API validation error and keeps the dialog open', async ({page}) => {
    await mockWarehouse(page, manager, [], {createError: 'Materiał o tej nazwie już istnieje'});
    await openWarehouse(page);
    await page.locator('#addMaterial').click();
    const dialog = page.locator('#createMaterialDialog');
    await fillMaterialForm(dialog);
    await dialog.getByRole('button', {name: 'Dodaj materiał'}).click();

    await expect(dialog).toBeVisible();
    await expect(dialog.locator('.warehouse-form-error')).toHaveText(
        'Materiał o tej nazwie już istnieje',
    );
    await expect(page.locator('#warehouseRows')).toContainText('Magazyn jest pusty');
});

test('warehouse search filters materials and shows empty search results', async ({page}) => {
    const materials = [
        material(1, 'Stal S235', 100, 25, 75, 20),
        material(2, 'Aluminium', 0, 0, 0, 5, true),
    ];
    await mockWarehouse(page, manager, materials);
    await openWarehouse(page);

    await expect(page.locator('#warehouseRows tr')).toHaveCount(2);
    await page.locator('#materialSearch').fill('s235');
    await expect(page.locator('#warehouseRows tr')).toHaveCount(1);
    await expect(page.locator('#warehouseRows')).toContainText('Stal S235');
    await expect(page.locator('#warehouseRows')).not.toContainText('Aluminium');

    await page.locator('#materialSearch').fill('brak-takiego-materiału');
    await expect(page.locator('#warehouseRows')).toContainText('Brak materiałów pasujących do wyszukiwania.');
});

test('manager can change a material minimum from its row', async ({page}) => {
    const materials = [material(7, 'Stal S235', 100, 0, 100, 10)];
    let submittedMinimum;
    await mockWarehouse(page, manager, materials, {
        onMinimum: values => { submittedMinimum = values; },
    });
    await openWarehouse(page);

    const row = page.locator('#warehouseRows tr').filter({hasText: 'Stal S235'});
    await row.getByRole('button', {name: 'Minimum'}).click();
    const dialog = page.locator('#minimumDialog');
    await dialog.locator('[name="minimum_quantity"]').fill('120');
    await dialog.getByRole('button', {name: 'Zapisz minimum'}).click();

    await expect(dialog).not.toBeVisible();
    await expect(page.locator('#warehouseRows')).toContainText('Niski wolny stan');
    await expect(page.locator('#warehouseMessage')).toContainText('Stan minimalny zapisany');
    expect(submittedMinimum).toEqual({minimum_quantity: '120'});
});

test('manager can receive a stock movement from the material row', async ({page}) => {
    const item = material(4, 'Stal S235', 5, 0, 5, 1);
    let submittedMovement;
    await mockWarehouse(page, manager, [item], {
        onMovement: values => {
            submittedMovement = values;
            item.quantity = '8.5';
            item.available_quantity = '8.5';
        },
    });
    await openWarehouse(page);

    const row = page.locator('#warehouseRows tr').filter({hasText: 'Stal S235'});
    await row.getByRole('button', {name: 'Przyjmij'}).click();
    const dialog = page.locator('#movementDialog');
    await dialog.locator('[name="quantity"]').fill('3,5');
    await dialog.locator('[name="note"]').fill('Dostawa UI');
    await dialog.getByRole('button', {name: 'Zapisz przyjęcie'}).click();

    await expect(dialog).not.toBeVisible();
    await expect(page.locator('#warehouseRows')).toContainText('8,5 kg');
    await expect(page.locator('#warehouseMessage')).toContainText('Przyjęcie zapisane');
    expect(submittedMovement).toMatchObject({
        kind: 'receipt',
        quantity: '3,5',
        note: 'Dostawa UI',
        order_part_id: null,
    });
    expect(submittedMovement.request_id).toBeTruthy();
});

test('warehouse history dialog renders returned movement entries', async ({page}) => {
    const materials = [material(3, 'Stal S235', 100, 0, 100, 10)];
    const history = [{
        created_at: '2026-11-01T10:00:00',
        kind: 'receipt',
        quantity: 100,
        balance_after: 100,
        note: 'Dostawa testowa',
    }];
    await mockWarehouse(page, manager, materials, {history});
    await openWarehouse(page);

    const row = page.locator('#warehouseRows tr').filter({hasText: 'Stal S235'});
    await row.getByRole('button', {name: 'Historia'}).click();

    const dialog = page.locator('#historyDialog');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('Przyjęcie');
    await expect(dialog).toContainText('Dostawa testowa');
});

test('forecast shortcut updates the selected date and refreshes the API query', async ({page}) => {
    const forecastDates = [];
    await mockWarehouse(page, manager, [], {forecastDates});
    await openWarehouse(page);
    const expectedDate = await page.evaluate(() => {
        const value = new Date();
        value.setDate(value.getDate() + 14);
        return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
    });

    await page.locator('[data-forecast-offset="14"]').click();

    await expect(page.locator('#forecastDate')).toHaveValue(expectedDate);
    await expect(page.locator('[data-forecast-offset="14"]')).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(() => forecastDates.includes(expectedDate)).toBe(true);
});

test('alerts can be filtered by category and update summary counts', async ({page}) => {
    const alerts = [
        {order_number: 'ZL/QUEUE', client_name: 'Klient A', alert_type: 'queue_delay',
            message: 'Oczekuje na realizację', status: 'IN_QUEUE', days_count: 2},
        {order_number: 'ZL/DEADLINE', client_name: 'Klient B', alert_type: 'deadline_overdue',
            message: 'Termin został przekroczony', status: 'IN_PROGRESS', days_count: 1},
    ];
    await page.route('**/auth/me', route => respondJson(route, manager));
    await page.route('**/api/alerts', route => respondJson(route, {items: alerts}));
    await page.goto('/frontend/alerts.html');

    await expect(page.locator('#totalAlerts')).toHaveText('2');
    await expect(page.locator('#queueAlerts')).toHaveText('1');
    await expect(page.locator('#deadlineAlerts')).toHaveText('1');
    await expect(page.locator('#alertsList article')).toHaveCount(2);
    await page.locator('#alertFilter').selectOption('queue_delay');
    await expect(page.locator('#alertsList article')).toHaveCount(1);
    await expect(page.locator('#alertsList')).toContainText('ZL/QUEUE');
    await expect(page.locator('#alertsList')).not.toContainText('ZL/DEADLINE');
    await page.locator('#alertFilter').selectOption('deadline_overdue');
    await expect(page.locator('#alertsList')).toContainText('ZL/DEADLINE');
});

test('alerts display an API failure and restore the refresh button', async ({page}) => {
    await page.route('**/api/alerts', route =>
        respondJson(route, {detail: 'Usługa alertów niedostępna'}, 503));
    await page.goto('/frontend/alerts.html');

    await expect(page.locator('#alertsMessage')).toContainText('Usługa alertów niedostępna');
    await expect(page.locator('#refreshAlerts')).toBeEnabled();
    await expect(page.locator('#alertsList article')).toHaveCount(0);
});

test('order form submits the selected material, part, and operation to the API', async ({page}) => {
    let submittedOrder;
    let successAlert;
    const uploadedPdfs = [];
    const catalog = [{id: 5, name: 'Stal S235', unit: 'kg', available_quantity: 80}];
    await page.route('**/auth/me', route => respondJson(route, manager));
    await page.route('**/metadata/material-catalog', route => respondJson(route, catalog));
    await page.route('**/operators/', route => respondJson(route, [{id: 1, name: 'Ada'}]));
    await page.route('**/metadata/material-units', route => respondJson(
        route, {units: ['kg'], max_pdf_bytes: 10 * 1024 * 1024},
    ));
    await page.route('**/orders/', async route => {
        if (route.request().method() === 'GET') return respondJson(route, []);
        submittedOrder = route.request().postDataJSON();
        return respondJson(route, {
            order_id: 50,
            order_number: submittedOrder.order_number,
            parts: submittedOrder.parts.map((part, index) => ({
                id: index + 10,
                client_key: part.client_key,
                pdfs: [],
            })),
            material_warnings: [],
        }, 201);
    });
    await page.route('**/parts/*/upload-pdf', async route => {
        uploadedPdfs.push({
            url: new URL(route.request().url()).pathname,
            contentType: route.request().headers()['content-type'],
        });
        return respondJson(route, {id: 90, file_name: 'rysunek.pdf'}, 201);
    });
    await serveKanbanMarkup(page);
    page.on('dialog', async dialog => {
        successAlert = dialog.message();
        await dialog.dismiss();
    });

    await page.goto('/index.html');
    const part = page.locator('.part-block').first();
    await expect(part).toBeVisible();
    await page.locator('#order_number').fill('UI/CREATE/1');
    await page.locator('#client_name').fill('Klient testowy');
    const deadline = await page.evaluate(() => {
        const value = new Date();
        value.setDate(value.getDate() + 30);
        return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
    });
    await page.locator('#deadline').fill(deadline);
    await page.locator('#operator').selectOption({label: 'Ada'});
    await part.locator('.part-name').fill('Rama');
    await part.locator('.plan-material').selectOption('5');
    await part.locator('.plan-quantity').fill('12,5');
    await part.locator('.step-name').fill('Spawanie');
    await part.locator('.part-pdf-input').setInputFiles({
        name: 'rysunek.pdf',
        mimeType: 'application/pdf',
        buffer: Buffer.from('%PDF-1.4\nUI test document'),
    });
    await page.getByRole('button', {name: 'Utwórz Zlecenie'}).click();

    await expect.poll(() => submittedOrder).not.toBeNull();
    await expect.poll(() => successAlert).toContain('Zlecenie i załączniki zapisane.');
    expect(submittedOrder.order_number).toBe('UI/CREATE/1');
    expect(submittedOrder.operator).toBe('Ada');
    expect(submittedOrder.parts).toHaveLength(1);
    expect(submittedOrder.parts[0]).toMatchObject({
        part_name: 'Rama',
        material: 'Stal S235',
        warehouse_material_id: 5,
        planned_quantity: '12,5',
        steps: [{step_name: 'Spawanie', operator: 'Ada'}],
    });
    expect(uploadedPdfs).toHaveLength(1);
    expect(uploadedPdfs[0].url).toBe('/parts/10/upload-pdf');
    expect(uploadedPdfs[0].contentType).toContain('multipart/form-data');
    await expect(page).toHaveURL(/\/kanban\.html$/);
});

test('order form rejects a file without a PDF header before creating the order', async ({page}) => {
    let createRequests = 0;
    let alertMessage;
    await page.route('**/auth/me', route => respondJson(route, manager));
    await page.route('**/metadata/material-catalog', route => respondJson(route, [
        {id: 5, name: 'Stal S235', unit: 'kg', available_quantity: 80},
    ]));
    await page.route('**/metadata/material-units', route => respondJson(
        route, {units: ['kg'], max_pdf_bytes: 10 * 1024 * 1024},
    ));
    await page.route('**/operators/', route => respondJson(route, []));
    await page.route('**/orders/', async route => {
        if (route.request().method() === 'POST') createRequests += 1;
        return respondJson(route, []);
    });
    page.on('dialog', async dialog => {
        alertMessage = dialog.message();
        await dialog.dismiss();
    });

    await page.goto('/index.html');
    const part = page.locator('.part-block').first();
    await page.locator('#order_number').fill('UI/INVALID-PDF');
    await page.locator('#client_name').fill('Klient testowy');
    await page.locator('#deadline').fill(await page.evaluate(() => {
        const value = new Date();
        value.setDate(value.getDate() + 30);
        return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
    }));
    await part.locator('.part-name').fill('Rama');
    await part.locator('.plan-material').selectOption('5');
    await part.locator('.plan-quantity').fill('1');
    await part.locator('.step-name').fill('Spawanie');
    await part.locator('.part-pdf-input').setInputFiles({
        name: 'rysunek.pdf',
        mimeType: 'application/pdf',
        buffer: Buffer.from('not a PDF'),
    });
    await page.getByRole('button', {name: 'Utwórz Zlecenie'}).click();

    await expect.poll(() => alertMessage).toContain('nie ma nagłówka PDF');
    expect(createRequests).toBe(0);
});

test('order form reports missing required data without sending an API request', async ({page}) => {
    let createRequests = 0;
    let alertMessage;
    await page.route('**/auth/me', route => respondJson(route, manager));
    await page.route('**/metadata/material-catalog', route => respondJson(route, []));
    await page.route('**/operators/', route => respondJson(route, []));
    await page.route('**/orders/', async route => {
        if (route.request().method() === 'POST') createRequests += 1;
        return respondJson(route, []);
    });
    page.on('dialog', async dialog => {
        alertMessage = dialog.message();
        await dialog.dismiss();
    });

    await page.goto('/index.html');
    await expect(page.locator('.part-block')).toBeVisible();
    await page.getByRole('button', {name: 'Utwórz Zlecenie'}).click();

    await expect.poll(() => alertMessage).toContain('Uzupełnij numer zlecenia, klienta i termin');
    expect(createRequests).toBe(0);
});

test('Kanban search filters active orders by number or client', async ({page}) => {
    const orders = [
        {id: 1, order_number: 'ZL/100', client_name: 'MetalForma', status: 'IN_QUEUE',
            date_deadline: '2026-12-20T00:00:00', materials_ready: true, is_approved: false,
            needs_correction: false, parts: []},
        {id: 2, order_number: 'ZL/200', client_name: 'Inny Klient', status: 'IN_QUEUE',
            date_deadline: '2026-12-20T00:00:00', materials_ready: true, is_approved: false,
            needs_correction: false, parts: []},
        {id: 3, order_number: 'ZL/ARCH', client_name: 'MetalForma', status: 'ARCHIVED',
            date_deadline: '2026-12-20T00:00:00', materials_ready: true, is_approved: false,
            needs_correction: false, parts: []},
    ];
    await page.route('**/auth/me', route => respondJson(route, manager));
    await page.route('**/metadata/material-units', route => respondJson(
        route, {units: ['kg'], max_pdf_bytes: 10 * 1024 * 1024},
    ));
    await page.route('**/orders/', route => respondJson(route, orders));
    await serveKanbanMarkup(page);
    await page.goto('/kanban.html');

    await expect(page.locator('#col-in-queue .order-card')).toHaveCount(2);
    await expect(page.locator('#col-completed .order-card')).toHaveCount(0);
    await page.getByPlaceholder('Szukaj zlecenia...').fill('METALFORMA');
    await expect(page.locator('#col-in-queue .order-card')).toHaveCount(1);
    await expect(page.locator('#col-in-queue')).toContainText('ZL/100');
    await page.getByPlaceholder('Szukaj zlecenia...').fill('ZL/200');
    await expect(page.locator('#col-in-queue .order-card')).toHaveCount(1);
    await expect(page.locator('#col-in-queue')).toContainText('ZL/200');
});

test('quality approval updates the order and removes quality actions', async ({page}) => {
    const order = kanbanOrder(61, 'QUALITY_CHECK');
    await mockKanban(page, [order]);
    await page.route(`**/orders/${order.id}/approve`, async route => {
        order.is_approved = true;
        return respondJson(route, {message: 'Zlecenie zatwierdzone'});
    });
    await page.goto('/kanban.html');

    const card = page.locator('#col-quality-check .order-card');
    await card.getByRole('button', {name: 'Zatwierdź jakość'}).click();

    await expect.poll(() => order.is_approved).toBe(true);
    await expect(card.getByRole('button', {name: 'Zatwierdź jakość'})).toHaveCount(0);
    await expect(card.getByRole('button', {name: 'Do poprawy'})).toHaveCount(0);
    await expect(page.locator('#message')).toContainText('Zlecenie zatwierdzone!');
});

test('quality rejection returns the order to the queue with a correction marker', async ({page}) => {
    const order = kanbanOrder(62, 'QUALITY_CHECK');
    await mockKanban(page, [order]);
    await page.route(`**/orders/${order.id}/needs-correction`, async route => {
        order.status = 'IN_QUEUE';
        order.needs_correction = true;
        return respondJson(route, {message: 'Zlecenie wróciło do oczekujących'});
    });
    await page.goto('/kanban.html');

    await page.locator('#col-quality-check .order-card')
        .getByRole('button', {name: 'Do poprawy'}).click();

    const correctedCard = page.locator('#col-in-queue .order-card');
    await expect(correctedCard).toContainText('Do poprawy - priorytet');
    await expect(correctedCard).toHaveClass(/needs-correction-card/);
    await expect(page.locator('#col-quality-check .order-card')).toHaveCount(0);
    expect(order.status).toBe('IN_QUEUE');
});

test('completed orders can be archived and found through archive filters', async ({page}) => {
    const completedOrder = kanbanOrder(63, 'COMPLETED', {
        order_number: 'UI/ARCHIVE/63',
        client_name: 'Klient Archiwalny A',
        date_received: '2026-11-05T00:00:00',
    });
    const existingArchivedOrder = kanbanOrder(64, 'ARCHIVED', {
        order_number: 'UI/ARCHIVE/64',
        client_name: 'Klient Archiwalny B',
        date_received: '2026-10-11T00:00:00',
    });
    const orders = [completedOrder, existingArchivedOrder];
    await mockKanban(page, orders);
    await page.route(`**/orders/${completedOrder.id}/status*`, async route => {
        completedOrder.status = new URL(route.request().url()).searchParams.get('status');
        return respondJson(route, completedOrder);
    });
    await serveArchiveMarkup(page);
    await page.goto('/kanban.html');

    await page.locator('#col-completed .order-card')
        .getByRole('button', {name: 'Archiwizuj'}).click();
    await expect(page.locator('#archiveConfirmModal')).toBeVisible();
    await page.locator('#confirmArchiveBtn').click();
    await expect(page.locator('#col-completed .order-card')).toHaveCount(0);

    await page.goto('/archive.html');
    await expect(page.locator('#archiveContainer .order-card')).toHaveCount(2);
    await page.locator('#archiveClient').fill('Klient Archiwalny A');
    await page.locator('#archiveDateFrom').fill('2026-11-01');
    await expect(page.locator('#archiveContainer .order-card')).toHaveCount(1);
    await expect(page.locator('#archiveContainer')).toContainText('UI/ARCHIVE/63');
    await expect(page.locator('#archiveCounter')).toHaveText('Wyświetlono 1 z 2 zleceń');
});

test('editing an order deletes a selected PDF only after saving changes', async ({page}) => {
    const part = {
        id: 91,
        part_name: 'Rama',
        material: 'Stal S235',
        operator: 'Ada',
        warehouse_material_id: 5,
        planned_material_name: 'Stal S235',
        planned_quantity: '4',
        planned_unit: 'kg',
        reserved_quantity: '4',
        shortage_quantity: '0',
        issued_quantity: '0',
        material_usage_version: 0,
        pdfs: [{id: 77, file_name: 'rysunek.pdf', url: '/uploads/rysunek.pdf'}],
        steps: [{id: 92, step_name: 'Spawanie', operator: 'Ada', is_completed: false}],
    };
    const order = kanbanOrder(65, 'IN_QUEUE', {
        order_number: 'UI/PDF/65',
        parts: [part],
    });
    let updatedPayload;
    const deletedPdfRequests = [];
    await mockKanban(page, [order]);
    await page.route(`**/orders/${order.id}`, async route => {
        if (route.request().method() === 'PUT') {
            updatedPayload = route.request().postDataJSON();
            part.pdfs = [];
            return respondJson(route, {
                parts: [{
                    id: part.id,
                    client_key: updatedPayload.parts[0].client_key,
                    steps: [{id: 92}],
                }],
                material_warnings: [],
            });
        }
        return respondJson(route, order);
    });
    await page.route('**/parts/pdf/77', async route => {
        deletedPdfRequests.push(route.request().url());
        return respondJson(route, {message: 'Załącznik usunięty'});
    });
    await page.goto('/kanban.html');

    await page.locator('#col-in-queue .order-card .order-number').click();
    await page.getByRole('button', {name: 'Edytuj dane zlecenia'}).click();
    const editPart = page.locator('.edit-part-item');
    await expect(editPart.locator('.edit-pdfs-list')).toContainText('rysunek.pdf');
    await editPart.locator('.edit-pdfs-list button').click();
    await expect(editPart.locator('.edit-pdfs-list')).not.toContainText('rysunek.pdf');
    await page.locator('#saveOrderButton').click();

    await expect.poll(() => deletedPdfRequests.length).toBe(1);
    expect(deletedPdfRequests[0]).toContain('/parts/pdf/77');
    expect(updatedPayload.parts[0]).toMatchObject({
        id: 91,
        part_name: 'Rama',
        warehouse_material_id: 5,
    });
    await expect(page.locator('#message')).toContainText('Zlecenie zapisane.');
});

test('manager can schedule a delivery for a warehouse material', async ({page}) => {
    const materials = [material(7, 'Stal S235', 20, 0, 20, 5)];
    let submittedDelivery;
    await mockWarehouse(page, manager, materials, {
        onDeliveryCreate: (values, id) => {
            submittedDelivery = values;
            return {
                id,
                ...values,
                quantity: values.quantity.replace(',', '.'),
                status: 'ordered',
            };
        },
    });
    await openWarehouse(page);

    await page.locator('#planDelivery').click();
    const dialog = page.locator('#deliveryDialog');
    await dialog.locator('#deliveryMaterial').selectOption('7');
    await dialog.locator('[name="quantity"]').fill('12,5');
    await dialog.locator('[name="expected_on"]').fill(await page.evaluate(() => {
        const value = new Date();
        value.setDate(value.getDate() + 10);
        return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
    }));
    await dialog.locator('[name="note"]').fill('Dostawa UI');
    await dialog.getByRole('button', {name: 'Zapisz plan dostawy'}).click();

    await expect(dialog).not.toBeVisible();
    const row = page.locator('#deliveryRows tr').filter({hasText: 'Stal S235'});
    await expect(row).toContainText('12,5 kg');
    await expect(row).toContainText('Zaplanowana');
    await expect(row).toContainText('Dostawa UI');
    expect(submittedDelivery).toMatchObject({
        material_id: 7,
        quantity: '12,5',
        note: 'Dostawa UI',
    });
    expect(submittedDelivery.expected_on).toBeTruthy();
});

test('manager can confirm a planned delivery and refresh warehouse stock', async ({page}) => {
    const item = material(7, 'Stal S235', 20, 0, 20, 5);
    const deliveries = [{
        id: 71,
        material_id: 7,
        quantity: '12',
        expected_on: '2026-12-15',
        status: 'ordered',
        note: 'Dostawa testowa',
    }];
    const actions = [];
    await mockWarehouse(page, manager, [item], {
        deliveries,
        onDeliveryAction: (action, delivery) => {
            actions.push(action);
            delivery.status = 'received';
            item.quantity = '32';
            item.available_quantity = '32';
        },
    });
    await openWarehouse(page);

    const row = page.locator('#deliveryRows tr').filter({hasText: 'Dostawa testowa'});
    await row.getByRole('button', {name: 'Przyjmij dostawę'}).click();

    await expect(page.locator('#deliveryRows tr').filter({hasText: 'Dostawa testowa'}))
        .toContainText('Przyjęta');
    await expect(page.locator('#warehouseRows')).toContainText('32 kg');
    expect(actions).toEqual(['receive']);
});

test('manager can cancel a planned delivery without changing warehouse stock', async ({page}) => {
    const item = material(7, 'Stal S235', 20, 0, 20, 5);
    const deliveries = [{
        id: 72,
        material_id: 7,
        quantity: '12',
        expected_on: '2026-12-15',
        status: 'ordered',
        note: 'Anulowana dostawa',
    }];
    const actions = [];
    await mockWarehouse(page, manager, [item], {
        deliveries,
        onDeliveryAction: (action, delivery) => {
            actions.push(action);
            delivery.status = 'cancelled';
        },
    });
    await openWarehouse(page);

    const row = page.locator('#deliveryRows tr').filter({hasText: 'Anulowana dostawa'});
    await row.getByRole('button', {name: 'Anuluj plan'}).click();

    const cancelledRow = page.locator('#deliveryRows tr').filter({hasText: 'Anulowana dostawa'});
    await expect(cancelledRow).toContainText('Anulowana');
    await expect(cancelledRow.getByRole('button', {name: 'Anuluj plan'})).toHaveCount(0);
    await expect(page.locator('#warehouseRows')).toContainText('20 kg');
    expect(actions).toEqual(['cancel']);
});
