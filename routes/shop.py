import os
import re
import time

from PIL import Image as _PIL_Image
from flask import (Blueprint, jsonify, request, session as flask_session, abort)

from db import db_conn
from extensions import csrf, limiter

shop_bp = Blueprint('shop', __name__)

_ALLOWED_EXT = {'jpg', 'jpeg', 'png', 'webp'}
_PHONE_RE    = re.compile(r'^0[1-9][0-9]{8}$')
_SHOP_DIR    = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'static', 'shop')


def _require_admin():
    if flask_session.get('role') != 'admin':
        from flask import make_response
        abort(make_response(jsonify({'error': 'Accès refusé'}), 403))


# ─── Public routes (/api/shop/*) ─────────────────────────────────────────────

@shop_bp.route('/api/shop/items')
def shop_items():
    with db_conn() as conn:
        items = conn.execute(
            "SELECT id, name, description, price, image_path, preorder FROM shop_items WHERE active=1 ORDER BY id"
        ).fetchall()
        result = []
        for item in items:
            variants = conn.execute(
                "SELECT id, size_label, stock FROM shop_variants WHERE item_id=? ORDER BY id",
                (item['id'],)
            ).fetchall()
            images = conn.execute(
                "SELECT id, image_path, is_primary FROM shop_item_images"
                " WHERE item_id=? ORDER BY display_order ASC",
                (item['id'],)
            ).fetchall()
            result.append({
                'id':          item['id'],
                'name':        item['name'],
                'description': item['description'],
                'price':       item['price'],
                'image_path':  item['image_path'],
                'preorder':    item['preorder'],
                'variants':    [{'id': v['id'], 'size_label': v['size_label'], 'stock': v['stock']}
                                for v in variants],
                'images':      [{'id': img['id'], 'image_path': img['image_path'],
                                 'is_primary': img['is_primary']}
                                for img in images],
            })
    return jsonify(result)


@shop_bp.route('/api/shop/order', methods=['POST'])
@csrf.exempt
@limiter.limit('5 per minute')
def shop_order():
    data       = request.get_json(force=True) or {}
    first_name = (data.get('first_name') or '').strip()
    last_name  = (data.get('last_name')  or '').strip()
    phone      = (data.get('phone')      or '').strip()
    lines      = data.get('lines')

    # Normalisation format international → format local 0XXXXXXXXX
    if phone.startswith('+33'):
        phone = '0' + phone[3:]
    elif phone.startswith('0033'):
        phone = '0' + phone[4:]
    phone = phone.replace(' ', '').replace('-', '').replace('.', '')

    if not first_name:
        return jsonify({'ok': False, 'error': 'Prénom requis'}), 400
    if not last_name:
        return jsonify({'ok': False, 'error': 'Nom requis'}), 400
    if not _PHONE_RE.match(phone):
        return jsonify({'ok': False, 'error': 'Téléphone invalide (format : 06XXXXXXXX)'}), 400
    if not lines:
        return jsonify({'ok': False, 'error': 'Panier vide'}), 400

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cfg_row = conn.execute(
            "SELECT value FROM app_config WHERE key='shop_enabled'"
        ).fetchone()
        if not cfg_row or cfg_row['value'] != '1':
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'La boutique est fermée'}), 400
        preorder_variants = set()
        for line in lines:
            variant_id = line.get('variant_id')
            quantity   = line.get('quantity')
            if not isinstance(variant_id, int) or not isinstance(quantity, int) or quantity < 1:
                conn.execute('ROLLBACK')
                return jsonify({'ok': False, 'error': 'Ligne invalide'}), 400
            row = conn.execute(
                """SELECT sv.stock, sv.size_label, si.name AS item_name, si.preorder AS item_preorder
                   FROM shop_variants sv
                   JOIN shop_items si ON si.id = sv.item_id
                   WHERE sv.id=?""",
                (variant_id,)
            ).fetchone()
            if not row:
                conn.execute('ROLLBACK')
                return jsonify({'ok': False, 'error': f'Variante {variant_id} introuvable'}), 400
            if row['item_preorder']:
                preorder_variants.add(variant_id)
            elif row['stock'] < quantity:
                conn.execute('ROLLBACK')
                return jsonify({
                    'ok':         False,
                    'error':      f'Stock insuffisant pour {row["item_name"]} ({row["size_label"]})',
                    'variant_id': variant_id,
                }), 400

        cur = conn.execute(
            "INSERT INTO shop_orders(first_name, last_name, phone) VALUES (?,?,?)",
            (first_name, last_name, phone)
        )
        order_id = cur.lastrowid

        for line in lines:
            variant_id = line['variant_id']
            quantity   = line['quantity']
            conn.execute(
                "INSERT INTO shop_order_lines(order_id, variant_id, quantity) VALUES (?,?,?)",
                (order_id, variant_id, quantity)
            )
            if variant_id not in preorder_variants:
                conn.execute(
                    "UPDATE shop_variants SET stock = stock - ? WHERE id=?",
                    (quantity, variant_id)
                )

        conn.execute('COMMIT')

    return jsonify({'ok': True, 'order_id': order_id})


# ─── Admin routes (/api/admin/shop/*) ────────────────────────────────────────

@shop_bp.route('/api/admin/shop/items')
def admin_list_items():
    _require_admin()
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, description, price, image_path, active, preorder FROM shop_items ORDER BY id"
        ).fetchall()

        # Batch fetch: 4 global queries instead of O(items × variants)
        all_variants = conn.execute(
            "SELECT id, item_id, size_label, stock FROM shop_variants ORDER BY item_id, id"
        ).fetchall()
        # items with at least one non-cancelled order
        item_orders = {r['item_id'] for r in conn.execute(
            """SELECT DISTINCT sv.item_id FROM shop_order_lines sol
               JOIN shop_variants sv ON sv.id = sol.variant_id
               JOIN shop_orders so ON so.id = sol.order_id
               WHERE so.status != 'cancelled'"""
        ).fetchall()}
        # variants with at least one non-cancelled order
        variant_orders = {r['variant_id'] for r in conn.execute(
            """SELECT DISTINCT sol.variant_id FROM shop_order_lines sol
               JOIN shop_orders so ON so.id = sol.order_id
               WHERE so.status != 'cancelled'"""
        ).fetchall()}
        all_images = conn.execute(
            "SELECT id, item_id, image_path, is_primary, display_order FROM shop_item_images"
            " ORDER BY item_id, display_order ASC"
        ).fetchall()

        # Build lookup structures
        variants_by_item: dict = {}
        for v in all_variants:
            variants_by_item.setdefault(v['item_id'], []).append(v)
        images_by_item: dict = {}
        for img in all_images:
            images_by_item.setdefault(img['item_id'], []).append(img)

        result = []
        for item in rows:
            iid = item['id']
            variant_list = [
                {
                    'id':         v['id'],
                    'size_label': v['size_label'],
                    'stock':      v['stock'],
                    'has_orders': v['id'] in variant_orders,
                }
                for v in variants_by_item.get(iid, [])
            ]
            result.append({
                'id':          iid,
                'name':        item['name'],
                'description': item['description'],
                'price':       item['price'],
                'image_path':  item['image_path'],
                'active':      item['active'],
                'preorder':    item['preorder'],
                'has_orders':  iid in item_orders,
                'variants':    variant_list,
                'images':      [{'id': img['id'], 'image_path': img['image_path'],
                                 'is_primary': img['is_primary'],
                                 'display_order': img['display_order']}
                                for img in images_by_item.get(iid, [])],
            })
    return jsonify(result)

@shop_bp.route('/api/admin/shop/items', methods=['POST'])
def admin_create_item():
    _require_admin()
    data     = request.get_json(force=True) or {}
    name     = (data.get('name') or '').strip()
    desc     = (data.get('description') or '').strip()
    price    = data.get('price')
    preorder = 1 if data.get('preorder') else 0
    variants = data.get('variants') or []

    if not name:
        return jsonify({'ok': False, 'error': 'Nom requis'}), 400

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cur = conn.execute(
            "INSERT INTO shop_items(name, description, price, preorder) VALUES (?,?,?,?)",
            (name, desc or None, price, preorder)
        )
        item_id = cur.lastrowid
        for v in variants:
            size_label = (v.get('size_label') or '').strip()
            stock      = v.get('stock')
            if not size_label or not isinstance(stock, int) or stock < 0:
                conn.execute('ROLLBACK')
                return jsonify({'ok': False, 'error': 'Variante invalide'}), 400
            conn.execute(
                "INSERT INTO shop_variants(item_id, size_label, stock) VALUES (?,?,?)",
                (item_id, size_label, stock)
            )
        conn.execute('COMMIT')

    return jsonify({'ok': True, 'item_id': item_id})


@shop_bp.route('/api/admin/shop/items/<int:item_id>/name', methods=['POST'])
def admin_update_item_name(item_id):
    _require_admin()
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Nom requis'}), 400
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        item = conn.execute("SELECT id FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Article introuvable'}), 404
        conn.execute("UPDATE shop_items SET name=? WHERE id=?", (name, item_id))
        conn.execute('COMMIT')
    return jsonify({'ok': True, 'name': name})


@shop_bp.route('/api/admin/shop/items/<int:item_id>/description', methods=['POST'])
def admin_update_item_description(item_id):
    _require_admin()
    data = request.get_json(force=True) or {}
    desc = (data.get('description') or '').strip()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        item = conn.execute("SELECT id FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Article introuvable'}), 404
        conn.execute("UPDATE shop_items SET description=? WHERE id=?", (desc or None, item_id))
        conn.execute('COMMIT')
    return jsonify({'ok': True, 'description': desc})


@shop_bp.route('/api/admin/shop/items/<int:item_id>/preorder', methods=['POST'])
def admin_update_item_preorder(item_id):
    _require_admin()
    data     = request.get_json(force=True) or {}
    preorder = 1 if data.get('preorder') else 0
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        item = conn.execute("SELECT id FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Article introuvable'}), 404
        conn.execute("UPDATE shop_items SET preorder=? WHERE id=?", (preorder, item_id))
        conn.execute('COMMIT')
    return jsonify({'ok': True, 'preorder': preorder})


@shop_bp.route('/api/admin/shop/items/<int:item_id>/price', methods=['POST'])
def admin_update_item_price(item_id):
    _require_admin()
    data  = request.get_json(force=True) or {}
    price = data.get('price')
    if price is None or not isinstance(price, (int, float)) or float(price) < 0:
        return jsonify({'ok': False, 'error': 'Prix invalide (nombre >= 0)'}), 400
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        item = conn.execute("SELECT id FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Article introuvable'}), 404
        conn.execute("UPDATE shop_items SET price=? WHERE id=?", (float(price), item_id))
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@shop_bp.route('/api/admin/shop/items/<int:item_id>/toggle', methods=['POST'])
def admin_toggle_item(item_id):
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        item = conn.execute("SELECT id, active FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Article introuvable'}), 404
        new_active = 0 if item['active'] else 1
        conn.execute("UPDATE shop_items SET active=? WHERE id=?", (new_active, item_id))
        conn.execute('COMMIT')
    return jsonify({'ok': True, 'active': new_active})


@shop_bp.route('/api/admin/shop/items/<int:item_id>/delete', methods=['POST'])
def admin_delete_item(item_id):
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        item = conn.execute("SELECT id, image_path FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Article introuvable'}), 404
        orders_exist = conn.execute(
            """SELECT sol.id FROM shop_order_lines sol
               JOIN shop_variants sv ON sv.id = sol.variant_id
               JOIN shop_orders so ON so.id = sol.order_id
               WHERE sv.item_id = ? AND so.status != 'cancelled' LIMIT 1""",
            (item_id,)
        ).fetchone()
        if orders_exist:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Des commandes existent pour cet article'}), 400
        # Collect all image paths before cascade-delete removes them from DB
        image_paths = {r['image_path'] for r in conn.execute(
            "SELECT image_path FROM shop_item_images WHERE item_id=?", (item_id,)
        ).fetchall()}
        if item['image_path']:
            image_paths.add(item['image_path'])
        # Supprimer les lignes de commandes (toutes annulées — vérifié ci-dessus)
        # avant la suppression de l'article pour éviter la contrainte FK sur variant_id
        conn.execute(
            "DELETE FROM shop_order_lines WHERE variant_id IN "
            "(SELECT id FROM shop_variants WHERE item_id=?)",
            (item_id,)
        )
        conn.execute("DELETE FROM shop_items WHERE id=?", (item_id,))
        conn.execute('COMMIT')

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in image_paths:
        try:
            os.remove(os.path.join(base, path.lstrip('/')))
        except OSError:
            pass

    return jsonify({'ok': True})


@shop_bp.route('/api/admin/shop/items/<int:item_id>/image', methods=['POST'])
def admin_upload_image(item_id):
    _require_admin()
    if 'image' not in request.files:
        return jsonify({'ok': False, 'error': 'Champ image manquant'}), 400
    f   = request.files['image']
    ext = (f.filename or '').rsplit('.', 1)[-1].lower()
    if ext not in _ALLOWED_EXT:
        return jsonify({'ok': False, 'error': 'Extension non autorisée (jpg/jpeg/png/webp)'}), 400

    # Verify actual image content via magic bytes (catches extension spoofing)
    try:
        _PIL_Image.open(f.stream).verify()
        f.stream.seek(0)
    except Exception:
        return jsonify({'ok': False, 'error': 'Fichier image invalide ou corrompu'}), 400

    # Pre-flight check (non-transactional read)
    with db_conn() as conn:
        item = conn.execute("SELECT id FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            return jsonify({'ok': False, 'error': 'Article introuvable'}), 404
        if conn.execute(
            "SELECT COUNT(*) FROM shop_item_images WHERE item_id=?", (item_id,)
        ).fetchone()[0] >= 5:
            return jsonify({'ok': False, 'error': 'Maximum 5 photos par article'}), 400

    os.makedirs(_SHOP_DIR, exist_ok=True)
    filename   = f"{item_id}_{int(time.time())}.{ext}"
    dest       = os.path.join(_SHOP_DIR, filename)
    image_path = f"/static/shop/{filename}"
    f.save(dest)

    try:
        with db_conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            count = conn.execute(
                "SELECT COUNT(*) FROM shop_item_images WHERE item_id=?", (item_id,)
            ).fetchone()[0]
            if count >= 5:
                conn.execute('ROLLBACK')
                os.remove(dest)
                return jsonify({'ok': False, 'error': 'Maximum 5 photos par article'}), 400
            order = conn.execute(
                "SELECT COALESCE(MAX(display_order), -1) + 1 FROM shop_item_images WHERE item_id=?",
                (item_id,)
            ).fetchone()[0]
            is_primary = 1 if count == 0 else 0
            cur = conn.execute(
                "INSERT INTO shop_item_images(item_id, image_path, is_primary, display_order)"
                " VALUES(?, ?, ?, ?)",
                (item_id, image_path, is_primary, order)
            )
            image_id = cur.lastrowid
            if is_primary:
                conn.execute(
                    "UPDATE shop_items SET image_path=? WHERE id=?", (image_path, item_id)
                )
            conn.execute('COMMIT')
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise

    return jsonify({'ok': True, 'image': {
        'id':         image_id,
        'image_path': image_path,
        'is_primary': is_primary,
    }})


@shop_bp.route('/api/admin/shop/images/<int:image_id>/delete', methods=['POST'])
def admin_delete_image(image_id):
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        img = conn.execute(
            "SELECT id, item_id, image_path, is_primary FROM shop_item_images WHERE id=?",
            (image_id,)
        ).fetchone()
        if not img:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Image introuvable'}), 404

        item_id    = img['item_id']
        image_path = img['image_path']
        was_primary = img['is_primary'] == 1

        conn.execute("DELETE FROM shop_item_images WHERE id=?", (image_id,))

        new_primary_id = None
        if was_primary:
            next_img = conn.execute(
                "SELECT id, image_path FROM shop_item_images"
                " WHERE item_id=? ORDER BY display_order ASC LIMIT 1",
                (item_id,)
            ).fetchone()
            if next_img:
                conn.execute(
                    "UPDATE shop_item_images SET is_primary=1 WHERE id=?", (next_img['id'],)
                )
                conn.execute(
                    "UPDATE shop_items SET image_path=? WHERE id=?",
                    (next_img['image_path'], item_id)
                )
                new_primary_id = next_img['id']
            else:
                conn.execute(
                    "UPDATE shop_items SET image_path=NULL WHERE id=?", (item_id,)
                )
        conn.execute('COMMIT')

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        os.remove(os.path.join(base, image_path.lstrip('/')))
    except OSError:
        pass

    return jsonify({'ok': True, 'new_primary_id': new_primary_id})


@shop_bp.route('/api/admin/shop/images/<int:image_id>/set_primary', methods=['POST'])
def admin_set_primary_image(image_id):
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        img = conn.execute(
            "SELECT id, item_id, image_path FROM shop_item_images WHERE id=?", (image_id,)
        ).fetchone()
        if not img:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Image introuvable'}), 404

        item_id = img['item_id']
        conn.execute(
            "UPDATE shop_item_images SET is_primary=0 WHERE item_id=?", (item_id,)
        )
        conn.execute(
            "UPDATE shop_item_images SET is_primary=1 WHERE id=?", (image_id,)
        )
        conn.execute(
            "UPDATE shop_items SET image_path=? WHERE id=?", (img['image_path'], item_id)
        )
        conn.execute('COMMIT')

    return jsonify({'ok': True})


@shop_bp.route('/api/admin/shop/variants', methods=['POST'])
def admin_create_variant():
    _require_admin()
    data       = request.get_json(force=True) or {}
    item_id    = data.get('item_id')
    size_label = (data.get('size_label') or '').strip()
    stock      = data.get('stock')

    if not isinstance(item_id, int):
        return jsonify({'ok': False, 'error': 'item_id requis'}), 400
    if not size_label:
        return jsonify({'ok': False, 'error': 'size_label requis'}), 400
    if stock is None or not isinstance(stock, int) or stock < 0:
        return jsonify({'ok': False, 'error': 'stock invalide (entier >= 0)'}), 400

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        item = conn.execute("SELECT id FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Article introuvable'}), 404
        cur = conn.execute(
            "INSERT INTO shop_variants(item_id, size_label, stock) VALUES (?,?,?)",
            (item_id, size_label, stock)
        )
        conn.execute('COMMIT')
    return jsonify({'ok': True, 'variant_id': cur.lastrowid})


@shop_bp.route('/api/admin/shop/variants/<int:variant_id>/stock', methods=['POST'])
def admin_update_stock(variant_id):
    _require_admin()
    data  = request.get_json(force=True) or {}
    stock = data.get('stock')
    if stock is None or not isinstance(stock, int) or stock < 0:
        return jsonify({'ok': False, 'error': 'stock invalide (entier >= 0)'}), 400

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT id FROM shop_variants WHERE id=?", (variant_id,)).fetchone()
        if not row:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Variante introuvable'}), 404
        conn.execute("UPDATE shop_variants SET stock=? WHERE id=?", (stock, variant_id))
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@shop_bp.route('/api/admin/shop/variants/<int:variant_id>/delete', methods=['POST'])
def admin_delete_variant(variant_id):
    _require_admin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT id FROM shop_variants WHERE id=?", (variant_id,)).fetchone()
        if not row:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Variante introuvable'}), 404
        in_order = conn.execute(
            """SELECT sol.id FROM shop_order_lines sol
               JOIN shop_orders so ON so.id = sol.order_id
               WHERE sol.variant_id = ? AND so.status != 'cancelled' LIMIT 1""",
            (variant_id,)
        ).fetchone()
        if in_order:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Variante présente dans des commandes'}), 400
        conn.execute("DELETE FROM shop_variants WHERE id=?", (variant_id,))
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@shop_bp.route('/api/admin/shop/orders')
def admin_list_orders():
    _require_admin()
    item_id = request.args.get('item_id', type=int)

    with db_conn() as conn:
        if item_id is not None:
            order_ids = [r['order_id'] for r in conn.execute(
                """SELECT DISTINCT sol.order_id FROM shop_order_lines sol
                   JOIN shop_variants sv ON sv.id = sol.variant_id
                   WHERE sv.item_id = ?""",
                (item_id,)
            ).fetchall()]
            if not order_ids:
                return jsonify([])
            placeholders = ','.join('?' * len(order_ids))
            orders = conn.execute(
                f"SELECT * FROM shop_orders WHERE id IN ({placeholders}) ORDER BY created_at DESC",
                order_ids
            ).fetchall()
        else:
            orders = conn.execute(
                "SELECT * FROM shop_orders ORDER BY created_at DESC"
            ).fetchall()

        result = []
        for o in orders:
            lines = conn.execute(
                """SELECT si.name AS item_name, sv.size_label, sol.quantity
                   FROM shop_order_lines sol
                   JOIN shop_variants sv ON sv.id = sol.variant_id
                   JOIN shop_items    si ON si.id = sv.item_id
                   WHERE sol.order_id = ?""",
                (o['id'],)
            ).fetchall()
            result.append({
                'id':         o['id'],
                'created_at': o['created_at'],
                'status':     o['status'],
                'first_name': o['first_name'],
                'last_name':  o['last_name'],
                'phone':      o['phone'],
                'lines': [
                    {'item_name': l['item_name'], 'size_label': l['size_label'], 'quantity': l['quantity']}
                    for l in lines
                ],
            })

    return jsonify(result)


@shop_bp.route('/api/admin/shop/orders/<int:order_id>/status', methods=['POST'])
def admin_update_order_status(order_id):
    _require_admin()
    data       = request.get_json(force=True) or {}
    new_status = data.get('status')
    if new_status not in ('confirmed', 'cancelled'):
        return jsonify({'ok': False, 'error': 'Statut invalide'}), 400

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = conn.execute(
            "SELECT id, status FROM shop_orders WHERE id=?", (order_id,)
        ).fetchone()
        if not order:
            conn.execute('ROLLBACK')
            return jsonify({'ok': False, 'error': 'Commande introuvable'}), 404

        cur_status = order['status']

        if cur_status == new_status:
            conn.execute('ROLLBACK')
            return jsonify({'ok': True})

        lines = conn.execute(
            """SELECT sol.variant_id, sol.quantity, si.preorder AS item_preorder
               FROM shop_order_lines sol
               JOIN shop_variants sv ON sv.id = sol.variant_id
               JOIN shop_items si ON si.id = sv.item_id
               WHERE sol.order_id=?""",
            (order_id,)
        ).fetchall()

        if new_status == 'cancelled' and cur_status in ('pending', 'confirmed'):
            # Restituer le stock — uniquement pour les articles non-preorder
            for line in lines:
                if not line['item_preorder']:
                    conn.execute(
                        "UPDATE shop_variants SET stock = stock + ? WHERE id=?",
                        (line['quantity'], line['variant_id'])
                    )

        elif new_status == 'confirmed' and cur_status == 'cancelled':
            # Vérifier le stock disponible avant de décrémenter (non-preorder seulement)
            for line in lines:
                if not line['item_preorder']:
                    v = conn.execute(
                        "SELECT stock, size_label FROM shop_variants WHERE id=?",
                        (line['variant_id'],)
                    ).fetchone()
                    if not v or v['stock'] < line['quantity']:
                        conn.execute('ROLLBACK')
                        label = v['size_label'] if v else str(line['variant_id'])
                        return jsonify({
                            'ok':    False,
                            'error': f'Stock insuffisant pour {label}',
                        }), 400
            for line in lines:
                if not line['item_preorder']:
                    conn.execute(
                        "UPDATE shop_variants SET stock = stock - ? WHERE id=?",
                        (line['quantity'], line['variant_id'])
                    )

        conn.execute("UPDATE shop_orders SET status=? WHERE id=?", (new_status, order_id))
        conn.execute('COMMIT')
    return jsonify({'ok': True})


@shop_bp.route('/api/admin/shop/shop_enabled', methods=['POST'])
def admin_set_shop_enabled():
    _require_admin()
    data    = request.get_json(force=True) or {}
    enabled = data.get('enabled')
    if not isinstance(enabled, bool):
        return jsonify({'ok': False, 'error': 'enabled doit être true ou false'}), 400

    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute(
            "INSERT OR REPLACE INTO app_config(key,value) VALUES ('shop_enabled',?)",
            ('1' if enabled else '0',)
        )
        conn.execute('COMMIT')
    return jsonify({'ok': True, 'enabled': enabled})
