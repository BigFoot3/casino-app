'use strict';

const CSRF = document.querySelector('meta[name="csrf-token"]').content;
let shopEnabled = INITIAL_SHOP_ENABLED;
let items = [];

// ── Helpers ──────────────────────────────────────────────────────────────────

async function apiPost(url, body = {}) {
  const r = await fetch(url, {
    method:  'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRFToken': CSRF},
    body:    JSON.stringify(body),
  });
  return [r.status, await r.json()];
}

async function shopAction(btn, fn) {
  btn.disabled     = true;
  btn.style.opacity = '0.5';
  try {
    return await fn();
  } finally {
    btn.disabled     = false;
    btn.style.opacity = '';
  }
}

function showError(elId, msg, autoHide = true) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent    = msg;
  el.style.display  = '';
  if (autoHide) setTimeout(() => { el.style.display = 'none'; }, 5000);
}

// ── Section 1 — Toggle boutique ──────────────────────────────────────────────

function renderShopToggle() {
  const statusText = document.getElementById('shop-status-text');
  const btn        = document.getElementById('btn-shop-toggle');
  if (shopEnabled) {
    statusText.textContent = 'Ouverte';
    statusText.style.color = 'var(--mg-blush)';
    btn.textContent        = 'Fermer la boutique';
  } else {
    statusText.textContent = 'Fermée';
    statusText.style.color = 'var(--mg-rosewood)';
    btn.textContent        = 'Ouvrir la boutique';
  }
}

document.getElementById('btn-shop-toggle').addEventListener('click', async function () {
  const [status, data] = await shopAction(this, () =>
    apiPost('/api/admin/shop/shop_enabled', {enabled: !shopEnabled})
  );
  if (status === 200 && data.ok) {
    shopEnabled = data.enabled;
    renderShopToggle();
  }
});

// ── Section 2+3 — Catalogue + Stock ─────────────────────────────────────────

async function loadItems() {
  const r = await fetch('/api/admin/shop/items');
  if (!r.ok) return;
  items = await r.json();
  renderItems();
  updateOrdersFilter();
}

function renderItems() {
  const container = document.getElementById('items-list');
  container.replaceChildren();

  if (items.length === 0) {
    const p = document.createElement('p');
    p.className    = 'text-center py-3 mb-0';
    p.style.color  = 'var(--mg-rosewood)';
    p.textContent  = 'Aucun article. Créez le premier avec « + Nouvel article ».';
    container.appendChild(p);
    return;
  }

  items.forEach(item => container.appendChild(buildItemBlock(item)));
}

function buildItemBlock(item) {
  const wrap = document.createElement('div');
  wrap.className            = 'mb-3 pb-3';
  wrap.style.borderBottom   = '1px solid var(--mg-border-strong)';
  wrap.dataset.itemId       = item.id;

  // ── Ligne principale ──
  const row = document.createElement('div');
  row.className = 'd-flex align-items-start gap-3 flex-wrap';

  // Thumbnail
  const thumb = document.createElement('div');
  thumb.style.cssText = 'width:48px;height:48px;flex-shrink:0;border-radius:4px;overflow:hidden;' +
                        'background:var(--mg-velvet);display:flex;align-items:center;justify-content:center;';
  if (item.image_path) {
    const img = document.createElement('img');
    img.src             = item.image_path;
    img.alt             = '';
    img.style.cssText   = 'width:100%;height:100%;object-fit:cover;';
    thumb.appendChild(img);
  } else {
    const ph = document.createElement('span');
    ph.style.fontSize = '1.4rem';
    ph.textContent    = '🛍';
    thumb.appendChild(ph);
  }
  row.appendChild(thumb);

  // Infos
  const info = document.createElement('div');
  info.style.flex = '1';

  const nameEl = document.createElement('div');
  nameEl.className   = 'fw-bold';
  nameEl.style.color = 'var(--mg-ivory)';
  nameEl.textContent = item.name;
  info.appendChild(nameEl);

  const priceRow = document.createElement('div');
  priceRow.className = 'd-flex align-items-center gap-2 mt-1';

  const priceDisplay = document.createElement('span');
  priceDisplay.className    = 'small';
  priceDisplay.style.color  = 'var(--mg-rosewood)';
  priceDisplay.style.minWidth = '60px';
  priceDisplay.textContent  = item.price != null ? Number(item.price).toFixed(2) + ' €' : '—';
  priceRow.appendChild(priceDisplay);

  const priceGroup = document.createElement('div');
  priceGroup.className    = 'input-group input-group-sm';
  priceGroup.style.maxWidth = '150px';

  const priceInput       = document.createElement('input');
  priceInput.type        = 'number';
  priceInput.className   = 'form-control';
  priceInput.min         = '0';
  priceInput.step        = '0.01';
  priceInput.value       = item.price != null ? Number(item.price).toFixed(2) : '';
  priceInput.placeholder = '0.00';

  const btnPriceOk       = document.createElement('button');
  btnPriceOk.type        = 'button';
  btnPriceOk.className   = 'btn btn-outline-secondary';
  btnPriceOk.textContent = 'OK';
  btnPriceOk.addEventListener('click', async function () {
    const newPrice = parseFloat(priceInput.value);
    if (isNaN(newPrice) || newPrice < 0) return;
    const [s, d] = await shopAction(this, () =>
      apiPost(`/api/admin/shop/items/${item.id}/price`, {price: newPrice})
    );
    if (s === 200 && d.ok) {
      priceDisplay.textContent = newPrice.toFixed(2) + ' €';
    } else {
      showError('items-error', d && d.error ? d.error : 'Erreur prix');
    }
  });

  priceGroup.appendChild(priceInput);
  priceGroup.appendChild(btnPriceOk);
  priceRow.appendChild(priceGroup);
  info.appendChild(priceRow);

  const metaEl = document.createElement('div');
  metaEl.className   = 'small mt-1';
  metaEl.style.color = 'var(--mg-rosewood)';
  metaEl.textContent = item.variants.length + ' taille(s)';
  info.appendChild(metaEl);

  const badge = document.createElement('span');
  badge.className   = 'badge mt-1 ' + (item.active ? 'bg-success' : 'bg-secondary');
  badge.textContent = item.active ? 'Actif' : 'Inactif';
  info.appendChild(badge);
  row.appendChild(info);

  // Actions
  const actions = document.createElement('div');
  actions.className = 'd-flex gap-1 flex-wrap align-items-center';

  // Upload image (input caché)
  const fileInput    = document.createElement('input');
  fileInput.type     = 'file';
  fileInput.accept   = '.jpg,.jpeg,.png,.webp';
  fileInput.style.display = 'none';

  const btnUpload      = document.createElement('button');
  btnUpload.type       = 'button';
  btnUpload.className  = 'btn btn-sm btn-outline-secondary';
  btnUpload.textContent = '🖼 Image';
  btnUpload.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    if (!fileInput.files[0]) return;
    await shopAction(btnUpload, async () => {
      const fd = new FormData();
      fd.append('image', fileInput.files[0]);
      const r = await fetch(`/api/admin/shop/items/${item.id}/image`, {
        method:  'POST',
        headers: {'X-CSRFToken': CSRF},
        body:    fd,
      });
      const d = await r.json();
      if (r.ok && d.ok) { await loadItems(); }
      else { showError('items-error', d.error || 'Erreur upload'); }
    });
    fileInput.value = '';
  });
  actions.appendChild(btnUpload);
  actions.appendChild(fileInput);

  // Toggle actif/inactif
  const btnToggle      = document.createElement('button');
  btnToggle.type       = 'button';
  btnToggle.className  = 'btn btn-sm btn-outline-secondary';
  btnToggle.textContent = item.active ? 'Désactiver' : 'Activer';
  btnToggle.addEventListener('click', async function () {
    const [s, d] = await shopAction(this, () =>
      apiPost(`/api/admin/shop/items/${item.id}/toggle`)
    );
    if (s === 200 && d.ok) await loadItems();
    else showError('items-error', d && d.error ? d.error : 'Erreur toggle');
  });
  actions.appendChild(btnToggle);

  // Supprimer article
  const btnDel      = document.createElement('button');
  btnDel.type       = 'button';
  btnDel.className  = 'btn btn-sm btn-outline-danger';
  btnDel.textContent = '🗑';
  btnDel.disabled   = item.has_orders;
  btnDel.title      = item.has_orders ? 'Des commandes existent pour cet article' : 'Supprimer';
  btnDel.addEventListener('click', async function () {
    const [s, d] = await shopAction(this, () =>
      apiPost(`/api/admin/shop/items/${item.id}/delete`)
    );
    if (s === 200 && d.ok) { await loadItems(); await loadOrders(); }
    else showError('items-error', d && d.error ? d.error : 'Erreur suppression');
  });
  actions.appendChild(btnDel);

  row.appendChild(actions);
  wrap.appendChild(row);

  // ── Variantes ──
  wrap.appendChild(buildVariantsSection(item));

  return wrap;
}

function buildVariantsSection(item) {
  const section = document.createElement('div');
  section.className = 'mt-2';

  if (item.variants.length === 0) {
    const noV = document.createElement('p');
    noV.className   = 'small mb-1';
    noV.style.color = 'var(--mg-rosewood)';
    noV.textContent = 'Aucune taille configurée.';
    section.appendChild(noV);
  } else {
    const tableWrap = document.createElement('div');
    tableWrap.className = 'table-responsive mb-1';
    const table = document.createElement('table');
    table.className = 'table table-sm align-middle mb-0';
    const tbody = document.createElement('tbody');

    for (const v of item.variants) {
      const tr = document.createElement('tr');

      // Taille label
      const tdSize      = document.createElement('td');
      tdSize.style.width = '35%';
      tdSize.textContent = v.size_label;
      tr.appendChild(tdSize);

      // Stock inline
      const tdStock     = document.createElement('td');
      const stockGroup  = document.createElement('div');
      stockGroup.className    = 'input-group input-group-sm';
      stockGroup.style.maxWidth = '130px';

      const stockInput  = document.createElement('input');
      stockInput.type   = 'number';
      stockInput.className = 'form-control';
      stockInput.min    = '0';
      stockInput.value  = v.stock;

      const btnOk       = document.createElement('button');
      btnOk.type        = 'button';
      btnOk.className   = 'btn btn-outline-secondary';
      btnOk.textContent = 'OK';
      btnOk.addEventListener('click', async function () {
        const newStock = parseInt(stockInput.value, 10);
        if (isNaN(newStock) || newStock < 0) return;
        const [s, d] = await shopAction(this, () =>
          apiPost(`/api/admin/shop/variants/${v.id}/stock`, {stock: newStock})
        );
        if (s !== 200 || !d.ok) showError('items-error', d && d.error ? d.error : 'Erreur stock');
      });

      stockGroup.appendChild(stockInput);
      stockGroup.appendChild(btnOk);
      tdStock.appendChild(stockGroup);
      tr.appendChild(tdStock);

      // Supprimer variante
      const tdDel       = document.createElement('td');
      const btnDelV     = document.createElement('button');
      btnDelV.type      = 'button';
      btnDelV.className = 'btn btn-sm btn-outline-danger';
      btnDelV.textContent = '×';
      btnDelV.disabled  = v.has_orders;
      btnDelV.title     = v.has_orders ? 'Présente dans des commandes' : 'Supprimer';
      btnDelV.addEventListener('click', async function () {
        const [s, d] = await shopAction(this, () =>
          apiPost(`/api/admin/shop/variants/${v.id}/delete`)
        );
        if (s === 200 && d.ok) await loadItems();
        else showError('items-error', d && d.error ? d.error : 'Erreur suppression');
      });
      tdDel.appendChild(btnDelV);
      tr.appendChild(tdDel);

      tbody.appendChild(tr);
    }

    table.appendChild(tbody);
    tableWrap.appendChild(table);
    section.appendChild(tableWrap);
  }

  // Bouton ajouter taille
  const btnAddV      = document.createElement('button');
  btnAddV.type       = 'button';
  btnAddV.className  = 'btn btn-sm btn-outline-light';
  btnAddV.textContent = '+ Taille';
  btnAddV.addEventListener('click', () => openNewVariantModal(item.id));
  section.appendChild(btnAddV);

  return section;
}

// ── Modal nouvel article ─────────────────────────────────────────────────────

const modalNewItemEl = document.getElementById('modal-new-item');
const modalNewItem   = new bootstrap.Modal(modalNewItemEl);

document.getElementById('btn-add-variant-row').addEventListener('click', addVariantRow);

function addVariantRow() {
  const container = document.getElementById('new-item-variants');
  const row = document.createElement('div');
  row.className = 'd-flex gap-2 mb-1 align-items-center variant-row';

  const sizeInput      = document.createElement('input');
  sizeInput.type       = 'text';
  sizeInput.className  = 'form-control form-control-sm variant-size';
  sizeInput.placeholder = 'Taille (ex : M)';

  const stockInput       = document.createElement('input');
  stockInput.type        = 'number';
  stockInput.className   = 'form-control form-control-sm variant-stock';
  stockInput.style.maxWidth = '80px';
  stockInput.placeholder = 'Stock';
  stockInput.min         = '0';
  stockInput.value       = '0';

  const btnRemove      = document.createElement('button');
  btnRemove.type       = 'button';
  btnRemove.className  = 'btn btn-sm btn-outline-danger';
  btnRemove.textContent = '×';
  btnRemove.addEventListener('click', () => row.remove());

  row.appendChild(sizeInput);
  row.appendChild(stockInput);
  row.appendChild(btnRemove);
  container.appendChild(row);
}

modalNewItemEl.addEventListener('hidden.bs.modal', () => {
  document.getElementById('new-item-name').value  = '';
  document.getElementById('new-item-desc').value  = '';
  document.getElementById('new-item-price').value = '';
  document.getElementById('new-item-variants').replaceChildren();
  document.getElementById('new-item-error').style.display = 'none';
});

document.getElementById('btn-submit-new-item').addEventListener('click', async function () {
  const name  = document.getElementById('new-item-name').value.trim();
  const desc  = document.getElementById('new-item-desc').value.trim();
  const price = document.getElementById('new-item-price').value;
  const errEl = document.getElementById('new-item-error');
  errEl.style.display = 'none';

  if (!name) { errEl.textContent = 'Nom requis.'; errEl.style.display = ''; return; }

  const variants = [];
  for (const row of document.querySelectorAll('#new-item-variants .variant-row')) {
    const sizeLabel = row.querySelector('.variant-size').value.trim();
    const stock     = parseInt(row.querySelector('.variant-stock').value, 10);
    if (!sizeLabel) continue;
    if (isNaN(stock) || stock < 0) {
      errEl.textContent = 'Stock invalide pour une taille.';
      errEl.style.display = '';
      return;
    }
    variants.push({size_label: sizeLabel, stock});
  }

  const [s, d] = await shopAction(this, () =>
    apiPost('/api/admin/shop/items', {
      name,
      description: desc || null,
      price:       price !== '' ? parseFloat(price) : null,
      variants,
    })
  );

  if (s === 200 && d.ok) { modalNewItem.hide(); await loadItems(); }
  else { errEl.textContent = d && d.error ? d.error : 'Erreur création.'; errEl.style.display = ''; }
});

// ── Modal ajout taille (article existant) ────────────────────────────────────

const modalNewVariantEl = document.getElementById('modal-new-variant');
const modalNewVariant   = new bootstrap.Modal(modalNewVariantEl);
let   newVariantItemId  = null;

function openNewVariantModal(itemId) {
  newVariantItemId = itemId;
  document.getElementById('new-variant-size').value  = '';
  document.getElementById('new-variant-stock').value = '0';
  document.getElementById('new-variant-error').style.display = 'none';
  modalNewVariant.show();
}

document.getElementById('btn-submit-new-variant').addEventListener('click', async function () {
  const sizeLabel = document.getElementById('new-variant-size').value.trim();
  const stock     = parseInt(document.getElementById('new-variant-stock').value, 10);
  const errEl     = document.getElementById('new-variant-error');
  errEl.style.display = 'none';

  if (!sizeLabel) { errEl.textContent = 'Taille requise.'; errEl.style.display = ''; return; }
  if (isNaN(stock) || stock < 0) { errEl.textContent = 'Stock invalide.'; errEl.style.display = ''; return; }

  const [s, d] = await shopAction(this, () =>
    apiPost('/api/admin/shop/variants', {item_id: newVariantItemId, size_label: sizeLabel, stock})
  );

  if (s === 200 && d.ok) { modalNewVariant.hide(); await loadItems(); }
  else { errEl.textContent = d && d.error ? d.error : 'Erreur création.'; errEl.style.display = ''; }
});

// ── Section 4 — Commandes ────────────────────────────────────────────────────

async function loadOrders() {
  const itemId = document.getElementById('orders-filter').value;
  const url    = itemId ? `/api/admin/shop/orders?item_id=${itemId}` : '/api/admin/shop/orders';
  const r      = await fetch(url);
  if (!r.ok) return;
  renderOrders(await r.json());
}

function updateOrdersFilter() {
  const sel     = document.getElementById('orders-filter');
  const current = sel.value;
  while (sel.options.length > 1) sel.remove(1);
  for (const item of items) {
    const opt      = document.createElement('option');
    opt.value      = item.id;
    opt.textContent = item.name;
    sel.appendChild(opt);
  }
  sel.value = current;
}

document.getElementById('orders-filter').addEventListener('change', loadOrders);

function renderOrders(orders) {
  const container = document.getElementById('orders-list');
  container.replaceChildren();

  if (orders.length === 0) {
    const p = document.createElement('p');
    p.className    = 'text-center py-3 mb-0';
    p.style.color  = 'var(--mg-rosewood)';
    p.textContent  = 'Aucune commande.';
    container.appendChild(p);
    return;
  }

  const wrap  = document.createElement('div');
  wrap.className = 'table-responsive';
  const table = document.createElement('table');
  table.className = 'table table-sm align-middle';

  const thead   = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['Date', 'Prénom', 'Nom', 'Téléphone', 'Articles', 'Statut', '']) {
    const th = document.createElement('th');
    th.textContent = label;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const order of orders) tbody.appendChild(buildOrderRow(order));
  table.appendChild(tbody);
  wrap.appendChild(table);
  container.appendChild(wrap);
}

function buildOrderRow(order) {
  const tr = document.createElement('tr');

  const tdDate = document.createElement('td');
  tdDate.className   = 'text-nowrap';
  tdDate.style.fontFamily = 'var(--mg-font-mono)';
  tdDate.style.fontSize   = '0.78rem';
  tdDate.textContent = (order.created_at || '').slice(0, 16).replace('T', ' ');
  tr.appendChild(tdDate);

  const tdFirst = document.createElement('td');
  tdFirst.textContent = order.first_name;
  tr.appendChild(tdFirst);

  const tdLast = document.createElement('td');
  tdLast.textContent = order.last_name;
  tr.appendChild(tdLast);

  const tdPhone = document.createElement('td');
  tdPhone.style.fontFamily = 'var(--mg-font-mono)';
  tdPhone.style.fontSize   = '0.78rem';
  tdPhone.textContent      = order.phone;
  tr.appendChild(tdPhone);

  const tdLines = document.createElement('td');
  tdLines.style.fontSize = '0.82rem';
  tdLines.textContent    = order.lines
    .map(l => l.item_name + ' ' + l.size_label + ' ×' + l.quantity)
    .join(', ');
  tr.appendChild(tdLines);

  const tdStatus = document.createElement('td');
  const statusBadge = document.createElement('span');
  statusBadge.className   = 'badge ' + statusBadgeClass(order.status);
  statusBadge.textContent = order.status;
  tdStatus.appendChild(statusBadge);
  tr.appendChild(tdStatus);

  const tdAct = document.createElement('td');
  tdAct.className = 'd-flex gap-1 flex-wrap';

  if (order.status !== 'confirmed') {
    const btnOk      = document.createElement('button');
    btnOk.type       = 'button';
    btnOk.className  = 'btn btn-sm btn-success text-nowrap';
    btnOk.textContent = '✓ Confirmer';
    btnOk.addEventListener('click', async function () {
      const [s, d] = await shopAction(this, () =>
        apiPost(`/api/admin/shop/orders/${order.id}/status`, {status: 'confirmed'})
      );
      if (s === 200 && d.ok) await loadOrders();
      else showError('orders-error', d && d.error ? d.error : 'Erreur');
    });
    tdAct.appendChild(btnOk);
  }

  if (order.status !== 'cancelled') {
    const btnKo      = document.createElement('button');
    btnKo.type       = 'button';
    btnKo.className  = 'btn btn-sm btn-danger text-nowrap';
    btnKo.textContent = '✕ Annuler';
    btnKo.addEventListener('click', async function () {
      const [s, d] = await shopAction(this, () =>
        apiPost(`/api/admin/shop/orders/${order.id}/status`, {status: 'cancelled'})
      );
      if (s === 200 && d.ok) await loadOrders();
      else showError('orders-error', d && d.error ? d.error : 'Erreur');
    });
    tdAct.appendChild(btnKo);
  }

  tr.appendChild(tdAct);
  return tr;
}

function statusBadgeClass(status) {
  if (status === 'confirmed') return 'bg-success';
  if (status === 'cancelled') return 'bg-danger';
  return 'bg-secondary';
}

// ── Init ─────────────────────────────────────────────────────────────────────

renderShopToggle();
loadItems().catch(() => {});
loadOrders().catch(() => {});
