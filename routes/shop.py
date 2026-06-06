import os
import re

from flask import (Blueprint, jsonify, request, session as flask_session, abort)

from db import db_conn
from extensions import csrf

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
            result.append({
                'id':          item['id'],
                'name':        item['name'],
                'description': item['description'],
                'price':       item['price'],
                'image_path':  item['image_path'],
                'preorder':    item['preorder'],
                'variants':    [{'id': v['id'], 'size_label': v['size_label'], 'stock': v['stock']}
                                for v in variants],
            })
    return jsonify(result)


@shop_bp.route('/api/shop/order', methods=['POST'])
@csrf.exempt
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
        result = []
        for item in rows:
            variants = conn.execute(
                "SELECT id, size_label, stock FROM shop_variants WHERE item_id=? ORDER BY id",
                (item['id'],)
            ).fetchall()
            has_orders = conn.execute(
                """SELECT COUNT(*) FROM shop_order_lines sol
                   JOIN shop_variants sv ON sv.id = sol.variant_id
                   JOIN shop_orders so ON so.id = sol.order_id
                   WHERE sv.item_id = ? AND so.status != 'cancelled'""",
                (item['id'],)
            ).fetchone()[0] > 0
            variant_list = []
            for v in variants:
                v_has_orders = conn.execute(
                    """SELECT COUNT(*) FROM shop_order_lines sol
                       JOIN shop_orders so ON so.id = sol.order_id
                       WHERE sol.variant_id = ? AND so.status != 'cancelled'""",
                    (v['id'],)
                ).fetchone()[0] > 0
                variant_list.append({
                    'id':         v['id'],
                    'size_label': v['size_label'],
                    'stock':      v['stock'],
                    'has_orders': v_has_orders,
                })
            result.append({
                'id':          item['id'],
                'name':        item['name'],
                'description': item['description'],
                'price':       item['price'],
                'image_path':  item['image_path'],
                'active':      item['active'],
                'preorder':    item['preorder'],
                'has_orders':  has_orders,
                'variants':    variant_list,
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
        image_path = item['image_path']
        # Supprimer les lignes de commandes (toutes annulées — vérifié ci-dessus)
        # avant la suppression de l'article pour éviter la contrainte FK sur variant_id
        conn.execute(
            "DELETE FROM shop_order_lines WHERE variant_id IN "
            "(SELECT id FROM shop_variants WHERE item_id=?)",
            (item_id,)
        )
        conn.execute("DELETE FROM shop_items WHERE id=?", (item_id,))
        conn.execute('COMMIT')

    if image_path:
        full = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            image_path.lstrip('/'))
        try:
            os.remove(full)
        except OSError:
            pass

    return jsonify({'ok': True})


@shop_bp.route('/api/admin/shop/items/<int:item_id>/image', methods=['POST'])
def admin_upload_image(item_id):
    _require_admin()
    if 'image' not in request.files:
        return jsonify({'ok': False, 'error': 'Champ image manquant'}), 400
    f    = request.files['image']
    ext  = (f.filename or '').rsplit('.', 1)[-1].lower()
    if ext not in _ALLOWED_EXT:
        return jsonify({'ok': False, 'error': 'Extension non autorisée (jpg/jpeg/png/webp)'}), 400

    with db_conn() as conn:
        item = conn.execute("SELECT id FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            return jsonify({'ok': False, 'error': 'Article introuvable'}), 404

    os.makedirs(_SHOP_DIR, exist_ok=True)
    filename    = f"{item_id}.{ext}"
    dest        = os.path.join(_SHOP_DIR, filename)
    image_path  = f"/static/shop/{filename}"

    f.save(dest)

    with db_conn() as conn:
        conn.execute("UPDATE shop_items SET image_path=? WHERE id=?", (image_path, item_id))
        conn.commit()

    return jsonify({'ok': True, 'image_path': image_path})


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
        item = conn.execute("SELECT id FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            return jsonify({'ok': False, 'error': 'Article introuvable'}), 404
        cur = conn.execute(
            "INSERT INTO shop_variants(item_id, size_label, stock) VALUES (?,?,?)",
            (item_id, size_label, stock)
        )
        conn.commit()
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
            "SELECT variant_id, quantity FROM shop_order_lines WHERE order_id=?",
            (order_id,)
        ).fetchall()

        if new_status == 'cancelled' and cur_status in ('pending', 'confirmed'):
            # Restituer le stock
            for line in lines:
                conn.execute(
                    "UPDATE shop_variants SET stock = stock + ? WHERE id=?",
                    (line['quantity'], line['variant_id'])
                )

        elif new_status == 'confirmed' and cur_status == 'cancelled':
            # Vérifier le stock disponible avant de décrémenter
            for line in lines:
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
