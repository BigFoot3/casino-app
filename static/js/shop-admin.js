'use strict';

const CSRF = document.querySelector('meta[name="csrf-token"]').content;
let shopEnabled      = INITIAL_SHOP_ENABLED;
let items            = [];
let currentDrawerItem = null;
let activeFilterType  = 'all';

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
  btn.disabled      = true;
  btn.style.opacity = '0.5';
  try {
    return await fn();
  } finally {
    btn.disabled      = false;
    btn.style.opacity = '';
  }
}

function showError(elId, msg, autoHide = true) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent   = msg;
  el.style.display = '';
  if (autoHide) setTimeout(() => { el.style.display = 'none'; }, 5000);
}

// ── Toggle boutique ──────────────────────────────────────────────────────────

function renderShopToggle() {
  const statusText = document.getElementById('shop-status-text');
  const btn        = document.getElementById('btn-shop-toggle');
  if (shopEnabled) {
    statusText.textContent = '● Boutique ouverte';
    statusText.style.color = 'var(--mg-blush)';
    btn.textContent        = 'Fermer la boutique';
  } else {
    statusText.textContent = 'Boutique fermée';
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

// ── Catalogue (tableau) ──────────────────────────────────────────────────────

function updateShopCounter() {
  const el = document.getElementById('shop-counter');
  if (!el) return;
  const active = items.filter(i => i.active).length;
  el.textContent = items.length + ' article' + (items.length !== 1 ? 's' : '') +
                   ' · ' + active + ' actif' + (active !== 1 ? 's' : '');
}

async function loadItems() {
  const r = await fetch('/api/admin/shop/items');
  if (!r.ok) return;
  items = await r.json();
  renderItems();
  updateOrdersFilter();
}

function renderItems() {
  updateShopCounter();
  applyFilters();
}

function applyFilters() {
  const search   = (document.getElementById('filter-search').value || '').toLowerCase();
  const stateVal = document.getElementById('filter-state').value;

  const filtered = items.filter(item => {
    if (activeFilterType === 'standard' &&  item.preorder)  return false;
    if (activeFilterType === 'preorder' && !item.preorder)  return false;
    if (stateVal === 'active'   && !item.active) return false;
    if (stateVal === 'inactive' &&  item.active) return false;
    if (search) {
      const hay = (item.name + ' ' + (item.description || '')).toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });

  renderRows(filtered);
}

function renderRows(list) {
  const tbody = document.getElementById('items-tbody');
  tbody.replaceChildren();

  if (list.length === 0) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan        = 7;
    td.className      = 'text-center py-3';
    td.style.color    = 'var(--mg-rosewood)';
    td.textContent    = 'Aucun article correspondant.';
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  list.forEach(item => tbody.appendChild(buildTableRow(item)));
}

function buildTableRow(item) {
  const tr = document.createElement('tr');
  tr.dataset.itemId = item.id;
  if (!item.active) tr.classList.add('shop-row--inactive');

  // Thumbnail
  const tdThumb = document.createElement('td');
  const thumb   = document.createElement('div');
  thumb.className = 'shop-table-thumb' + (item.image_path ? '' : ' shop-table-thumb--placeholder');
  if (item.image_path) {
    const img         = document.createElement('img');
    img.src           = item.image_path;
    img.alt           = '';
    img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;';
    thumb.appendChild(img);
  } else {
    const ph       = document.createElement('span');
    ph.textContent = 'IMG';
    thumb.appendChild(ph);
  }
  tdThumb.appendChild(thumb);
  tr.appendChild(tdThumb);

  // Article : nom + description
  const tdName  = document.createElement('td');
  const nameEl  = document.createElement('div');
  nameEl.className   = 'fw-bold';
  nameEl.style.color = 'var(--mg-ivory)';
  nameEl.textContent = item.name;
  tdName.appendChild(nameEl);
  if (item.description) {
    const descEl           = document.createElement('div');
    descEl.className       = 'small';
    descEl.style.color     = 'var(--mg-rosewood)';
    descEl.style.marginTop = '2px';
    descEl.textContent     = item.description;
    tdName.appendChild(descEl);
  }
  tr.appendChild(tdName);

  // Type
  const tdType  = document.createElement('td');
  const typeBadge = document.createElement('span');
  typeBadge.className   = 'badge ' + (item.preorder ? 'shop-badge--preorder' : 'shop-badge--standard');
  typeBadge.textContent = item.preorder ? 'Précommande' : 'Standard';
  tdType.appendChild(typeBadge);
  tr.appendChild(tdType);

  // Prix
  const tdPrice = document.createElement('td');
  tdPrice.style.fontFamily = 'var(--mg-font-mono)';
  tdPrice.style.whiteSpace = 'nowrap';
  tdPrice.textContent = item.price != null
    ? Number(item.price).toFixed(2).replace('.', ',') + ' €'
    : '—';
  tr.appendChild(tdPrice);

  // Stock
  const tdStock    = document.createElement('td');
  const totalStock = item.variants.reduce((s, v) => s + v.stock, 0);
  if (!item.preorder && totalStock === 0 && item.variants.length > 0) {
    const b       = document.createElement('span');
    b.className   = 'badge shop-badge--sold-out';
    b.textContent = 'Épuisé';
    tdStock.appendChild(b);
  } else {
    const num            = document.createElement('div');
    num.style.fontFamily = 'var(--mg-font-mono)';
    num.style.fontWeight = '700';
    num.textContent      = item.preorder ? '∞' : String(totalStock);
    tdStock.appendChild(num);
  }
  if (item.variants.length > 0 && !item.preorder) {
    const varEl       = document.createElement('div');
    varEl.className   = 'shop-stock-variants';
    varEl.textContent = item.variants.map(v => v.size_label + ' ' + v.stock).join(' · ');
    tdStock.appendChild(varEl);
  }
  tr.appendChild(tdStock);

  // État — toggle inline, mise à jour locale (pas de re-fetch)
  const tdState   = document.createElement('td');
  const btnToggle = document.createElement('button');
  btnToggle.type       = 'button';
  btnToggle.className  = 'btn btn-sm ' + (item.active ? 'btn-success' : 'btn-outline-secondary');
  btnToggle.textContent = item.active ? 'Actif' : 'Désactivé';
  btnToggle.addEventListener('click', async function (e) {
    e.stopPropagation();
    const [s, d] = await shopAction(this, () =>
      apiPost('/api/admin/shop/items/' + item.id + '/toggle')
    );
    if (s === 200 && d.ok) {
      item.active = !!d.active;
      tr.classList.toggle('shop-row--inactive', !item.active);
      this.textContent = item.active ? 'Actif' : 'Désactivé';
      this.className   = 'btn btn-sm ' + (item.active ? 'btn-success' : 'btn-outline-secondary');
      updateShopCounter();
    } else {
      showError('items-error', d && d.error ? d.error : 'Erreur toggle');
    }
  });
  tdState.appendChild(btnToggle);
  tr.appendChild(tdState);

  // Actions
  const tdAct    = document.createElement('td');
  tdAct.style.whiteSpace = 'nowrap';

  const btnEdit       = document.createElement('button');
  btnEdit.type        = 'button';
  btnEdit.className   = 'btn btn-sm btn-outline-light';
  btnEdit.textContent = 'Éditer';
  btnEdit.addEventListener('click', (e) => { e.stopPropagation(); openDrawer(item); });

  const btnDel    = document.createElement('button');
  btnDel.type     = 'button';
  btnDel.className = 'btn btn-sm btn-outline-danger';
  btnDel.textContent = '🗑';
  btnDel.disabled = item.has_orders;
  btnDel.title    = item.has_orders ? 'Des commandes existent pour cet article' : 'Supprimer';
  btnDel.addEventListener('click', function (e) {
    e.stopPropagation();
    document.getElementById('modal-confirm-delete-name').textContent = item.name;
    pendingDeleteFn = async () => {
      const [s, d] = await shopAction(
        document.getElementById('btn-confirm-delete'),
        () => apiPost('/api/admin/shop/items/' + item.id + '/delete')
      );
      if (s === 200 && d.ok) {
        modalConfirmDelete.hide();
        const idx = items.findIndex(i => i.id === item.id);
        if (idx !== -1) items.splice(idx, 1);
        updateShopCounter();
        applyFilters();
      } else {
        showError('items-error', d && d.error ? d.error : 'Erreur suppression');
      }
    };
    modalConfirmDelete.show();
  });

  const actWrap = document.createElement('div');
  actWrap.className = 'd-flex gap-1 align-items-center';
  actWrap.appendChild(btnEdit);
  actWrap.appendChild(btnDel);
  tdAct.appendChild(actWrap);

  tr.appendChild(tdAct);
  return tr;
}

// ── Drawer ───────────────────────────────────────────────────────────────────

const drawerEl          = document.getElementById('shop-drawer');
const drawerOverlay     = document.getElementById('shop-drawer-overlay');
const drawerTitleEl     = document.getElementById('drawer-title');
const drawerNameEl      = document.getElementById('drawer-name');
const drawerDescEl      = document.getElementById('drawer-desc');
const drawerPriceEl     = document.getElementById('drawer-price');
const drawerTypeEl      = document.getElementById('drawer-type');
const drawerActiveEl    = document.getElementById('drawer-active');
const drawerPhotosEl    = document.getElementById('drawer-photos');
const drawerVariantsEl  = document.getElementById('drawer-variants');
const drawerErrorEl     = document.getElementById('drawer-error');
const drawerBtnAddPhoto = document.getElementById('drawer-btn-add-photo');
const drawerFileInput   = document.getElementById('drawer-file-input');

function openDrawer(item) {
  currentDrawerItem = item;

  drawerTitleEl.textContent   = item.name;
  drawerNameEl.value          = item.name;
  drawerDescEl.value          = item.description || '';
  drawerPriceEl.value         = item.price != null ? Number(item.price).toFixed(2) : '';
  drawerTypeEl.value          = item.preorder ? 'preorder' : 'standard';
  drawerActiveEl.checked      = !!item.active;
  drawerErrorEl.style.display = 'none';

  buildDrawerPhotos(item);
  buildDrawerVariants(item);

  drawerEl.classList.add('is-open');
  drawerOverlay.classList.add('is-open');
}

function closeDrawer() {
  drawerEl.classList.remove('is-open');
  drawerOverlay.classList.remove('is-open');
  drawerErrorEl.style.display = 'none';
  currentDrawerItem = null;
}

async function submitDrawer() {
  if (!currentDrawerItem) return;
  const btn = document.getElementById('shop-drawer-submit');

  drawerErrorEl.style.display = 'none';

  const newName   = drawerNameEl.value.trim();
  const newDesc   = drawerDescEl.value.trim();
  const rawPrice  = drawerPriceEl.value;
  const newType   = drawerTypeEl.value;
  const newActive = drawerActiveEl.checked;

  if (!newName) {
    drawerErrorEl.textContent   = 'Le nom est requis.';
    drawerErrorEl.style.display = '';
    return;
  }

  const nameChanged   = newName  !== currentDrawerItem.name;
  const descChanged   = newDesc  !== (currentDrawerItem.description || '');
  const parsedPrice   = rawPrice !== '' ? parseFloat(rawPrice) : null;
  const origPrice     = currentDrawerItem.price != null ? Number(currentDrawerItem.price) : null;
  const priceChanged  = parsedPrice !== origPrice;
  const typeChanged   = newType   !== (currentDrawerItem.preorder ? 'preorder' : 'standard');
  const activeChanged = newActive !== !!currentDrawerItem.active;

  if (!nameChanged && !descChanged && !priceChanged && !typeChanged && !activeChanged) {
    closeDrawer();
    return;
  }

  await shopAction(btn, async () => {
    if (nameChanged) {
      const [s, d] = await apiPost(
        '/api/admin/shop/items/' + currentDrawerItem.id + '/name', {name: newName}
      );
      if (s !== 200 || !d.ok) {
        drawerErrorEl.textContent   = d && d.error ? d.error : 'Erreur nom';
        drawerErrorEl.style.display = '';
        return;
      }
      currentDrawerItem.name = d.name;
    }

    if (descChanged) {
      const [s, d] = await apiPost(
        '/api/admin/shop/items/' + currentDrawerItem.id + '/description', {description: newDesc}
      );
      if (s !== 200 || !d.ok) {
        drawerErrorEl.textContent   = d && d.error ? d.error : 'Erreur description';
        drawerErrorEl.style.display = '';
        return;
      }
      currentDrawerItem.description = newDesc || null;
    }

    if (priceChanged) {
      if (parsedPrice !== null && (isNaN(parsedPrice) || parsedPrice < 0)) {
        drawerErrorEl.textContent   = 'Prix invalide.';
        drawerErrorEl.style.display = '';
        return;
      }
      const [s, d] = await apiPost(
        '/api/admin/shop/items/' + currentDrawerItem.id + '/price',
        {price: parsedPrice !== null ? parsedPrice : 0}
      );
      if (s !== 200 || !d.ok) {
        drawerErrorEl.textContent   = d && d.error ? d.error : 'Erreur prix';
        drawerErrorEl.style.display = '';
        return;
      }
      currentDrawerItem.price = parsedPrice;
    }

    if (typeChanged) {
      const newPreorder = newType === 'preorder';
      const [s, d] = await apiPost(
        '/api/admin/shop/items/' + currentDrawerItem.id + '/preorder', {preorder: newPreorder}
      );
      if (s !== 200 || !d.ok) {
        drawerErrorEl.textContent   = d && d.error ? d.error : 'Erreur type';
        drawerErrorEl.style.display = '';
        return;
      }
      currentDrawerItem.preorder = d.preorder;
    }

    if (activeChanged) {
      const [s, d] = await apiPost('/api/admin/shop/items/' + currentDrawerItem.id + '/toggle');
      if (s !== 200 || !d.ok) {
        drawerErrorEl.textContent   = d && d.error ? d.error : 'Erreur visibilité';
        drawerErrorEl.style.display = '';
        return;
      }
      currentDrawerItem.active = !!d.active;
    }

    updateShopCounter();
    applyFilters();
    closeDrawer();
  });
}

// ── Drawer — Photos ──────────────────────────────────────────────────────────

function buildDrawerPhotos(item) {
  drawerPhotosEl.replaceChildren();
  drawerBtnAddPhoto.style.display = item.images.length >= 5 ? 'none' : '';

  function updateBorders() {
    for (const img of item.images) {
      const el = drawerPhotosEl.querySelector('[data-image-id="' + img.id + '"]');
      if (!el) continue;
      el.style.borderColor = img.is_primary === 1 ? 'var(--mg-flame)' : 'var(--mg-velvet)';
      const btnStar = el.querySelector('.img-btn-star');
      if (btnStar) btnStar.disabled = img.is_primary === 1;
    }
  }

  function buildThumb(img) {
    const wrap = document.createElement('div');
    wrap.dataset.imageId = img.id;
    wrap.style.cssText = 'position:relative;width:72px;height:72px;border-radius:4px;' +
      'overflow:hidden;flex-shrink:0;border:2px solid ' +
      (img.is_primary === 1 ? 'var(--mg-flame)' : 'var(--mg-velvet)') + ';';

    const imgEl         = document.createElement('img');
    imgEl.src           = img.image_path;
    imgEl.alt           = '';
    imgEl.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;';
    wrap.appendChild(imgEl);

    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:absolute;inset:0;display:flex;flex-direction:column;' +
      'justify-content:space-between;padding:2px;pointer-events:none;';

    const btnStar = document.createElement('button');
    btnStar.type      = 'button';
    btnStar.className = 'img-btn-star';
    btnStar.style.cssText = 'pointer-events:auto;background:rgba(14,4,5,0.72);' +
      'color:var(--mg-ivory);border:none;border-radius:2px;font-size:0.6rem;' +
      'line-height:1;padding:2px 4px;cursor:pointer;align-self:flex-start;';
    btnStar.textContent = '★';
    btnStar.disabled    = img.is_primary === 1;
    btnStar.title       = 'Définir comme principale';
    btnStar.addEventListener('click', async function () {
      const [s, d] = await shopAction(this, () =>
        apiPost('/api/admin/shop/images/' + img.id + '/set_primary')
      );
      if (s === 200 && d.ok) {
        for (const i of item.images) i.is_primary = 0;
        img.is_primary  = 1;
        item.image_path = img.image_path;
        updateBorders();
        const row = document.querySelector('tr[data-item-id="' + item.id + '"]');
        if (row) {
          const ti = row.querySelector('.shop-table-thumb img');
          if (ti) ti.src = img.image_path;
        }
      } else {
        drawerErrorEl.textContent   = d && d.error ? d.error : 'Erreur';
        drawerErrorEl.style.display = '';
      }
    });

    const btnDel = document.createElement('button');
    btnDel.type  = 'button';
    btnDel.style.cssText = 'pointer-events:auto;background:rgba(14,4,5,0.72);' +
      'color:var(--mg-ember);border:none;border-radius:2px;font-size:0.75rem;' +
      'line-height:1;padding:2px 4px;cursor:pointer;align-self:flex-end;';
    btnDel.textContent = '×';
    btnDel.title       = 'Supprimer';
    btnDel.addEventListener('click', async function () {
      const [s, d] = await shopAction(this, () =>
        apiPost('/api/admin/shop/images/' + img.id + '/delete')
      );
      if (s === 200 && d.ok) {
        wrap.remove();
        const idx = item.images.findIndex(i => i.id === img.id);
        if (idx !== -1) item.images.splice(idx, 1);
        if (d.new_primary_id !== null) {
          for (const i of item.images) i.is_primary = (i.id === d.new_primary_id ? 1 : 0);
          const np = item.images.find(i => i.id === d.new_primary_id);
          if (np) item.image_path = np.image_path;
        } else {
          item.image_path = null;
        }
        updateBorders();
        drawerBtnAddPhoto.style.display = item.images.length >= 5 ? 'none' : '';
        const row = document.querySelector('tr[data-item-id="' + item.id + '"]');
        if (row) refreshThumbCell(row, item);
      } else {
        drawerErrorEl.textContent   = d && d.error ? d.error : 'Erreur suppression image';
        drawerErrorEl.style.display = '';
      }
    });

    overlay.appendChild(btnStar);
    overlay.appendChild(btnDel);
    wrap.appendChild(overlay);
    return wrap;
  }

  for (const img of item.images) drawerPhotosEl.appendChild(buildThumb(img));
}

drawerBtnAddPhoto.addEventListener('click', () => drawerFileInput.click());

drawerFileInput.addEventListener('change', async () => {
  if (!drawerFileInput.files[0] || !currentDrawerItem) return;
  await shopAction(drawerBtnAddPhoto, async () => {
    const fd = new FormData();
    fd.append('image', drawerFileInput.files[0]);
    const r = await fetch('/api/admin/shop/items/' + currentDrawerItem.id + '/image', {
      method:  'POST',
      headers: {'X-CSRFToken': CSRF},
      body:    fd,
    });
    const d = await r.json();
    if (r.ok && d.ok) {
      const newImg = d.image;
      currentDrawerItem.images.push(newImg);
      if (newImg.is_primary === 1) currentDrawerItem.image_path = newImg.image_path;
      buildDrawerPhotos(currentDrawerItem);
      if (newImg.is_primary === 1) {
        const row = document.querySelector('tr[data-item-id="' + currentDrawerItem.id + '"]');
        if (row) refreshThumbCell(row, currentDrawerItem);
      }
    } else {
      drawerErrorEl.textContent   = d.error || 'Erreur upload';
      drawerErrorEl.style.display = '';
    }
  });
  drawerFileInput.value = '';
});

function refreshThumbCell(row, item) {
  const thumbDiv = row.querySelector('.shop-table-thumb');
  if (!thumbDiv) return;
  if (item.image_path) {
    thumbDiv.classList.remove('shop-table-thumb--placeholder');
    let imgEl = thumbDiv.querySelector('img');
    if (!imgEl) {
      imgEl           = document.createElement('img');
      imgEl.alt       = '';
      imgEl.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;';
      thumbDiv.replaceChildren(imgEl);
    }
    imgEl.src = item.image_path;
  } else {
    thumbDiv.classList.add('shop-table-thumb--placeholder');
    const ph       = document.createElement('span');
    ph.textContent = 'IMG';
    thumbDiv.replaceChildren(ph);
  }
}

// ── Drawer — Variants ────────────────────────────────────────────────────────

function buildDrawerVariants(item) {
  drawerVariantsEl.replaceChildren();

  if (item.variants.length === 0) {
    const p       = document.createElement('p');
    p.className   = 'small mb-1';
    p.style.color = 'var(--mg-rosewood)';
    p.textContent = 'Aucune taille configurée.';
    drawerVariantsEl.appendChild(p);
    return;
  }

  const tableWrap     = document.createElement('div');
  tableWrap.className = 'table-responsive mb-1';
  const table         = document.createElement('table');
  table.className     = 'table table-sm align-middle mb-0';
  const tbody         = document.createElement('tbody');

  for (const v of item.variants) {
    const tr = document.createElement('tr');

    const tdSize      = document.createElement('td');
    tdSize.style.width = '35%';
    tdSize.textContent = v.size_label;
    tr.appendChild(tdSize);

    const tdStock    = document.createElement('td');
    const stockGroup = document.createElement('div');
    stockGroup.className      = 'input-group input-group-sm';
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
        apiPost('/api/admin/shop/variants/' + v.id + '/stock', {stock: newStock})
      );
      if (s === 200 && d.ok) {
        v.stock = newStock;
        const row = document.querySelector('tr[data-item-id="' + item.id + '"]');
        if (row) refreshStockCell(row, item);
      } else {
        drawerErrorEl.textContent   = d && d.error ? d.error : 'Erreur stock';
        drawerErrorEl.style.display = '';
      }
    });

    stockGroup.appendChild(stockInput);
    stockGroup.appendChild(btnOk);
    tdStock.appendChild(stockGroup);
    tr.appendChild(tdStock);

    const tdDel   = document.createElement('td');
    const btnDelV = document.createElement('button');
    btnDelV.type        = 'button';
    btnDelV.className   = 'btn btn-sm btn-outline-danger';
    btnDelV.textContent = '×';
    btnDelV.disabled    = v.has_orders;
    btnDelV.title       = v.has_orders ? 'Présente dans des commandes' : 'Supprimer';
    btnDelV.addEventListener('click', async function () {
      const [s, d] = await shopAction(this, () =>
        apiPost('/api/admin/shop/variants/' + v.id + '/delete')
      );
      if (s === 200 && d.ok) {
        const savedId = currentDrawerItem ? currentDrawerItem.id : null;
        await loadItems();
        if (savedId) {
          const refreshed = items.find(i => i.id === savedId);
          if (refreshed) openDrawer(refreshed);
        }
      } else {
        drawerErrorEl.textContent   = d && d.error ? d.error : 'Erreur suppression';
        drawerErrorEl.style.display = '';
      }
    });
    tdDel.appendChild(btnDelV);
    tr.appendChild(tdDel);

    tbody.appendChild(tr);
  }

  table.appendChild(tbody);
  tableWrap.appendChild(table);
  drawerVariantsEl.appendChild(tableWrap);
}

function refreshStockCell(row, item) {
  const tds     = row.querySelectorAll('td');
  const stockTd = tds[4];
  if (!stockTd) return;
  const total = item.variants.reduce((s, v) => s + v.stock, 0);
  stockTd.replaceChildren();
  if (!item.preorder && total === 0 && item.variants.length > 0) {
    const b       = document.createElement('span');
    b.className   = 'badge shop-badge--sold-out';
    b.textContent = 'Épuisé';
    stockTd.appendChild(b);
  } else {
    const num            = document.createElement('div');
    num.style.fontFamily = 'var(--mg-font-mono)';
    num.style.fontWeight = '700';
    num.textContent      = item.preorder ? '∞' : String(total);
    stockTd.appendChild(num);
  }
  if (item.variants.length > 0 && !item.preorder) {
    const varEl       = document.createElement('div');
    varEl.className   = 'shop-stock-variants';
    varEl.textContent = item.variants.map(v => v.size_label + ' ' + v.stock).join(' · ');
    stockTd.appendChild(varEl);
  }
}

// ── Drawer — listeners ───────────────────────────────────────────────────────

document.getElementById('shop-drawer-close').addEventListener('click', closeDrawer);
document.getElementById('shop-drawer-cancel').addEventListener('click', closeDrawer);
drawerOverlay.addEventListener('click', closeDrawer);
document.getElementById('shop-drawer-submit').addEventListener('click', submitDrawer);

document.getElementById('drawer-btn-add-variant').addEventListener('click', () => {
  if (currentDrawerItem) openNewVariantModal(currentDrawerItem.id);
});

// ── Filtres ──────────────────────────────────────────────────────────────────

document.getElementById('filter-search').addEventListener('input', applyFilters);
document.getElementById('filter-state').addEventListener('change', applyFilters);

document.querySelectorAll('.shop-filter-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    activeFilterType = tab.dataset.type;
    document.querySelectorAll('.shop-filter-tab').forEach(t => t.classList.remove('is-active'));
    tab.classList.add('is-active');
    applyFilters();
  });
});

// ── Modal nouvel article ─────────────────────────────────────────────────────

const modalNewItemEl = document.getElementById('modal-new-item');
const modalNewItem   = new bootstrap.Modal(modalNewItemEl);

document.getElementById('btn-add-variant-row').addEventListener('click', addVariantRow);

function addVariantRow() {
  const container = document.getElementById('new-item-variants');
  const row = document.createElement('div');
  row.className = 'd-flex gap-2 mb-1 align-items-center variant-row';

  const sizeInput       = document.createElement('input');
  sizeInput.type        = 'text';
  sizeInput.className   = 'form-control form-control-sm variant-size';
  sizeInput.placeholder = 'Taille (ex : M)';

  const stockInput          = document.createElement('input');
  stockInput.type           = 'number';
  stockInput.className      = 'form-control form-control-sm variant-stock';
  stockInput.style.maxWidth = '80px';
  stockInput.placeholder    = 'Stock';
  stockInput.min            = '0';
  stockInput.value          = '0';

  const btnRemove       = document.createElement('button');
  btnRemove.type        = 'button';
  btnRemove.className   = 'btn btn-sm btn-outline-danger';
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
  document.getElementById('new-item-type').value  = 'standard';
  document.getElementById('new-item-variants').replaceChildren();
  document.getElementById('new-item-error').style.display = 'none';
});

document.getElementById('btn-submit-new-item').addEventListener('click', async function () {
  const name     = document.getElementById('new-item-name').value.trim();
  const desc     = document.getElementById('new-item-desc').value.trim();
  const price    = document.getElementById('new-item-price').value;
  const preorder = document.getElementById('new-item-type').value === 'preorder';
  const errEl    = document.getElementById('new-item-error');
  errEl.style.display = 'none';

  if (!name) { errEl.textContent = 'Nom requis.'; errEl.style.display = ''; return; }

  const variants = [];
  for (const row of document.querySelectorAll('#new-item-variants .variant-row')) {
    const sizeLabel = row.querySelector('.variant-size').value.trim();
    const stock     = parseInt(row.querySelector('.variant-stock').value, 10);
    if (!sizeLabel) continue;
    if (isNaN(stock) || stock < 0) {
      errEl.textContent   = 'Stock invalide pour une taille.';
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
      preorder,
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

  if (s === 200 && d.ok) {
    modalNewVariant.hide();
    const savedId = currentDrawerItem ? currentDrawerItem.id : null;
    await loadItems();
    if (savedId) {
      const refreshed = items.find(i => i.id === savedId);
      if (refreshed) openDrawer(refreshed);
    }
  } else {
    errEl.textContent   = d && d.error ? d.error : 'Erreur création.';
    errEl.style.display = '';
  }
});

// ── Modal confirmation suppression article ───────────────────────────────────

const modalConfirmDeleteEl = document.getElementById('modal-confirm-delete');
const modalConfirmDelete   = new bootstrap.Modal(modalConfirmDeleteEl);
let   pendingDeleteFn      = null;

document.getElementById('btn-confirm-delete').addEventListener('click', async function () {
  if (pendingDeleteFn) await pendingDeleteFn();
});

modalConfirmDeleteEl.addEventListener('hidden.bs.modal', () => {
  pendingDeleteFn = null;
});

// ── Commandes ────────────────────────────────────────────────────────────────

async function loadOrders() {
  const itemId = document.getElementById('orders-filter').value;
  const url    = itemId ? '/api/admin/shop/orders?item_id=' + itemId : '/api/admin/shop/orders';
  const r      = await fetch(url);
  if (!r.ok) return;
  renderOrders(await r.json());
}

function updateOrdersFilter() {
  const sel     = document.getElementById('orders-filter');
  const current = sel.value;
  while (sel.options.length > 1) sel.remove(1);
  for (const item of items) {
    const opt       = document.createElement('option');
    opt.value       = item.id;
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
    const p       = document.createElement('p');
    p.className   = 'text-center py-3 mb-0';
    p.style.color = 'var(--mg-rosewood)';
    p.textContent = 'Aucune commande.';
    container.appendChild(p);
    return;
  }

  const wrap      = document.createElement('div');
  wrap.className  = 'table-responsive';
  const table     = document.createElement('table');
  table.className = 'table table-sm align-middle';

  const thead   = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['Date', 'Prénom', 'Nom', 'Téléphone', 'Articles', 'Montant', 'Statut', '']) {
    const th       = document.createElement('th');
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
  tdDate.className        = 'text-nowrap';
  tdDate.style.fontFamily = 'var(--mg-font-mono)';
  tdDate.style.fontSize   = '0.78rem';
  tdDate.textContent      = (order.created_at || '').slice(0, 16).replace('T', ' ');
  tr.appendChild(tdDate);

  const tdFirst       = document.createElement('td');
  tdFirst.textContent = order.first_name;
  tr.appendChild(tdFirst);

  const tdLast       = document.createElement('td');
  tdLast.textContent = order.last_name;
  tr.appendChild(tdLast);

  const tdPhone            = document.createElement('td');
  tdPhone.style.fontFamily = 'var(--mg-font-mono)';
  tdPhone.style.fontSize   = '0.78rem';
  tdPhone.textContent      = order.phone;
  tr.appendChild(tdPhone);

  const tdLines = document.createElement('td');
  const ul      = document.createElement('ul');
  ul.className  = 'order-lines-list';
  for (const l of order.lines) {
    const li   = document.createElement('li');
    li.className = 'order-lines-list__item';

    const bullet = document.createElement('span');
    bullet.className  = 'order-lines-list__bullet';
    bullet.textContent = '•';

    const nameEl = document.createElement('span');
    nameEl.textContent = l.item_name;

    const meta = document.createElement('span');
    meta.className  = 'order-lines-list__meta';
    meta.textContent = ' — ' + l.size_label + ' ×' + l.quantity;

    li.appendChild(bullet);
    li.appendChild(nameEl);
    li.appendChild(meta);
    ul.appendChild(li);
  }
  tdLines.appendChild(ul);
  tr.appendChild(tdLines);

  const tdTotal = document.createElement('td');
  tdTotal.style.fontFamily = 'var(--mg-font-mono)';
  tdTotal.style.fontSize   = '0.78rem';
  tdTotal.style.textAlign  = 'right';
  tdTotal.textContent = (order.total != null) ? order.total.toFixed(2) + ' €' : '—';
  tr.appendChild(tdTotal);

  const tdStatus    = document.createElement('td');
  const statusBadge = document.createElement('span');
  statusBadge.className   = 'badge ' + statusBadgeClass(order.status);
  statusBadge.textContent = order.status;
  tdStatus.appendChild(statusBadge);
  tr.appendChild(tdStatus);

  const tdAct    = document.createElement('td');
  const actWrap  = document.createElement('div');
  actWrap.className = 'd-flex flex-column gap-1';

  if (order.status !== 'cancelled') {
    const btnEdit       = document.createElement('button');
    btnEdit.type        = 'button';
    btnEdit.className   = 'btn btn-sm btn-outline-light text-nowrap';
    btnEdit.textContent = '✏ Éditer';
    btnEdit.addEventListener('click', () => openEditOrderModal(order));
    actWrap.appendChild(btnEdit);
  }

  if (order.status !== 'confirmed') {
    const btnOk       = document.createElement('button');
    btnOk.type        = 'button';
    btnOk.className   = 'btn btn-sm btn-success text-nowrap';
    btnOk.textContent = '✓ Confirmer';
    btnOk.addEventListener('click', async function () {
      const [s, d] = await shopAction(this, () =>
        apiPost('/api/admin/shop/orders/' + order.id + '/status', {status: 'confirmed'})
      );
      if (s === 200 && d.ok) await loadOrders();
      else showError('orders-error', d && d.error ? d.error : 'Erreur');
    });
    actWrap.appendChild(btnOk);
  }

  if (order.status !== 'cancelled') {
    const btnKo       = document.createElement('button');
    btnKo.type        = 'button';
    btnKo.className   = 'btn btn-sm btn-danger text-nowrap';
    btnKo.textContent = '✕ Annuler';
    btnKo.addEventListener('click', async function () {
      const [s, d] = await shopAction(this, () =>
        apiPost('/api/admin/shop/orders/' + order.id + '/status', {status: 'cancelled'})
      );
      if (s === 200 && d.ok) await loadOrders();
      else showError('orders-error', d && d.error ? d.error : 'Erreur');
    });
    actWrap.appendChild(btnKo);
  }

  tdAct.appendChild(actWrap);
  tr.appendChild(tdAct);
  return tr;
}

function statusBadgeClass(status) {
  if (status === 'confirmed') return 'bg-success';
  if (status === 'cancelled') return 'bg-danger';
  return 'bg-secondary';
}

// ── Modale édition commande ───────────────────────────────────────────────────

const modalEditOrderEl = document.getElementById('modal-edit-order');
const modalEditOrder   = new bootstrap.Modal(modalEditOrderEl);
let   currentEditOrder = null;

function _buildVariantOptions(itemId) {
  const it = items.find(i => i.id === itemId);
  return it ? it.variants : [];
}

function _addEditOrderLine(container, variantId, quantity) {
  const row = document.createElement('div');
  row.className = 'd-flex gap-2 mb-2 align-items-center edit-order-line-row';

  const selItem = document.createElement('select');
  selItem.className = 'form-select form-select-sm';
  for (const it of items) {
    const opt = document.createElement('option');
    opt.value       = it.id;
    opt.textContent = it.name;
    selItem.appendChild(opt);
  }

  const selVariant = document.createElement('select');
  selVariant.className = 'form-select form-select-sm';

  function refreshVariants(selectedVariantId) {
    selVariant.replaceChildren();
    const vs = _buildVariantOptions(parseInt(selItem.value, 10));
    for (const v of vs) {
      const opt = document.createElement('option');
      opt.value       = v.id;
      opt.textContent = v.size_label;
      if (v.id === selectedVariantId) opt.selected = true;
      selVariant.appendChild(opt);
    }
  }

  // Pre-select item from variantId
  if (variantId) {
    for (const it of items) {
      if (it.variants.some(v => v.id === variantId)) {
        selItem.value = it.id;
        break;
      }
    }
  }
  refreshVariants(variantId || null);
  selItem.addEventListener('change', () => refreshVariants(null));

  const qtyInput = document.createElement('input');
  qtyInput.type      = 'number';
  qtyInput.min       = '1';
  qtyInput.value     = quantity || 1;
  qtyInput.className = 'form-control form-control-sm';
  qtyInput.style.width = '70px';

  const btnDel = document.createElement('button');
  btnDel.type        = 'button';
  btnDel.className   = 'btn btn-sm btn-outline-danger';
  btnDel.textContent = '×';
  btnDel.addEventListener('click', () => row.remove());

  row.appendChild(selItem);
  row.appendChild(selVariant);
  row.appendChild(qtyInput);
  row.appendChild(btnDel);
  container.appendChild(row);
}

function openEditOrderModal(order) {
  currentEditOrder = order;
  document.getElementById('edit-order-first-name').value = order.first_name;
  document.getElementById('edit-order-last-name').value  = order.last_name;
  document.getElementById('edit-order-phone').value      = order.phone;
  document.getElementById('edit-order-error').style.display = 'none';

  const container = document.getElementById('edit-order-lines');
  container.replaceChildren();

  // Rebuild lines from order.lines using variant matching
  for (const l of order.lines) {
    // Find variant_id from item + size_label
    let variantId = null;
    for (const it of items) {
      const v = it.variants.find(v => v.size_label === l.size_label);
      if (v && it.name === l.item_name) { variantId = v.id; break; }
    }
    _addEditOrderLine(container, variantId, l.quantity);
  }

  modalEditOrder.show();
}

document.getElementById('btn-edit-order-add-line').addEventListener('click', () => {
  _addEditOrderLine(document.getElementById('edit-order-lines'), null, 1);
});

document.getElementById('btn-submit-edit-order').addEventListener('click', async function () {
  if (!currentEditOrder) return;
  const errEl = document.getElementById('edit-order-error');
  errEl.style.display = 'none';

  const lines = [];
  for (const row of document.querySelectorAll('#edit-order-lines .edit-order-line-row')) {
    const sels = row.querySelectorAll('select');
    const qty  = parseInt(row.querySelector('input[type=number]').value, 10);
    const variantId = parseInt(sels[1].value, 10);
    if (!variantId || !qty || qty < 1) continue;
    lines.push({variant_id: variantId, quantity: qty});
  }

  if (lines.length === 0) {
    errEl.textContent    = 'Au moins un article requis.';
    errEl.style.display  = '';
    return;
  }

  const [s, d] = await shopAction(this, () =>
    apiPost('/api/admin/shop/orders/' + currentEditOrder.id + '/edit', {
      first_name: document.getElementById('edit-order-first-name').value.trim(),
      last_name:  document.getElementById('edit-order-last-name').value.trim(),
      phone:      document.getElementById('edit-order-phone').value.trim(),
      lines,
    })
  );
  if (s === 200 && d.ok) {
    modalEditOrder.hide();
    await loadOrders();
  } else {
    errEl.textContent   = (d && d.error) ? d.error : 'Erreur lors de la sauvegarde.';
    errEl.style.display = '';
  }
});

modalEditOrderEl.addEventListener('hidden.bs.modal', () => {
  currentEditOrder = null;
});

// ── Init ─────────────────────────────────────────────────────────────────────

renderShopToggle();
loadItems().catch(() => {});
loadOrders().catch(() => {});
