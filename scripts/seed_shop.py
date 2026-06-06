"""
One-shot script — crée les 9 articles du catalogue boutique.
Idempotent : relançable sans créer de doublons (vérifie par nom).
"""
import sqlite3

DB_PATH = '/root/casino/casino.db'

ARTICLES = [
    # (name, preorder, variants)
    ('Tote bag',                              0, ['Unique']),
    ('Magnet',                                0, ['Unique']),
    ('Porte-clef 1',                          0, ['Unique']),
    ('Porte-clef 2',                          0, ['Unique']),
    ('Porte-clef 3',                          0, ['Unique']),
    ('T-shirt Festival de Caisnes (Précommande)', 1, ['S', 'M', 'L', 'XL', 'XXL']),
    ('T-shirt Festival de KEN (Précommande)', 1, ['S', 'M', 'L', 'XL', 'XXL']),
    ('Affiche Festival de Caisnes (Précommande)', 1, ['Unique']),
    ('Affiche Reine Fatima (Précommande)',     1, ['Unique']),
]

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
conn.execute('PRAGMA foreign_keys=ON')

created  = []
skipped  = []

for name, preorder, sizes in ARTICLES:
    existing = conn.execute(
        'SELECT id FROM shop_items WHERE name=?', (name,)
    ).fetchone()
    if existing:
        skipped.append(name)
        continue

    conn.execute('BEGIN IMMEDIATE')
    cur = conn.execute(
        'INSERT INTO shop_items(name, preorder, active) VALUES (?,?,1)',
        (name, preorder)
    )
    item_id = cur.lastrowid
    for size in sizes:
        conn.execute(
            'INSERT INTO shop_variants(item_id, size_label, stock) VALUES (?,?,0)',
            (item_id, size)
        )
    conn.execute('COMMIT')
    created.append(name)

conn.close()

print(f'\n{"─"*50}')
print(f'  Créés    : {len(created)}')
for n in created:
    print(f'    + {n}')
print(f'  Ignorés  : {len(skipped)}')
for n in skipped:
    print(f'    = {n} (déjà existant)')
print(f'{"─"*50}\n')
