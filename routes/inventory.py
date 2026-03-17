"""
AMILCAR — Inventory & Stock Management
Blueprint: inventory_bp
Routes: 19
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, jsonify, session, send_file
from helpers import login_required, admin_required, client_required, get_db, get_services, get_setting, get_all_settings
from helpers import allowed_file, safe_page, log_activity, build_wa_url, STATUS_MESSAGES, UPLOAD_FOLDER, MAX_FILE_SIZE, MAX_FILES, PER_PAGE
from database.db import get_db
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import os, re, uuid, io
import time as time_module
import sqlite3

inventory_bp = Blueprint("inventory_bp", __name__)

INVENTORY_CATEGORIES = [
    'Produits de lavage',
    'Polisseuses & Outils',
    'Céramique & Protection',
    'Chiffons & Éponges',
    'Consommables',
    'Autre',
]


# ─── Service-Inventory Linking ───
@inventory_bp.route("/service_inventory")
@admin_required
def service_inventory():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS service_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT, service_name TEXT NOT NULL,
            inventory_id INTEGER NOT NULL, quantity_used REAL DEFAULT 1,
            FOREIGN KEY (inventory_id) REFERENCES inventory (id))""")
        links = conn.execute(
            "SELECT si.id, si.service_name, i.name, si.quantity_used "
            "FROM service_inventory si JOIN inventory i ON si.inventory_id = i.id "
            "ORDER BY si.service_name").fetchall()
        items = conn.execute("SELECT id, name FROM inventory ORDER BY name").fetchall()
    return render_template("service_inventory.html", links=links, services=get_services(), items=items)



@inventory_bp.route("/add_service_inventory", methods=["POST"])
@admin_required
def add_service_inventory():
    service_name = request.form.get("service_name", "").strip()
    inventory_id = request.form.get("inventory_id", "")
    quantity_used = request.form.get("quantity_used", "1")
    if not service_name or not inventory_id:
        flash("Service et produit requis", "error")
        return redirect("/service_inventory")
    try:
        qty = float(quantity_used)
        if qty <= 0: qty = 1
    except ValueError:
        qty = 1
    with get_db() as conn:
        conn.execute("INSERT INTO service_inventory (service_name, inventory_id, quantity_used) VALUES (?,?,?)",
            (service_name, int(inventory_id), qty))
        conn.commit()
    flash("Liaison ajoutée", "success")
    return redirect("/service_inventory")



@inventory_bp.route("/delete_service_inventory/<int:link_id>", methods=["POST"])
@admin_required
def delete_service_inventory(link_id):
    with get_db() as conn:
        conn.execute("DELETE FROM service_inventory WHERE id = ?", (link_id,))
        conn.commit()
    flash("Liaison supprimée", "success")
    return redirect("/service_inventory")



@inventory_bp.route("/inventory")
@login_required
def inventory_list():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, category TEXT DEFAULT '',
            quantity INTEGER DEFAULT 0, min_quantity INTEGER DEFAULT 5,
            unit_price REAL DEFAULT 0, supplier TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        items = conn.execute("SELECT * FROM inventory ORDER BY category, name").fetchall()
    low_stock = [i for i in items if i[3] <= i[4]]
    return render_template("inventory.html", items=items, low_stock=low_stock,
                           categories=INVENTORY_CATEGORIES)



@inventory_bp.route("/add_inventory", methods=["POST"])
@login_required
def add_inventory():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    quantity = request.form.get("quantity", "0")
    min_quantity = request.form.get("min_quantity", "5")
    unit_price = request.form.get("unit_price", "0")
    supplier = request.form.get("supplier", "").strip()
    if not name:
        flash("Le nom du produit est requis", "error")
        return redirect("/inventory")
    try:
        qty = int(quantity)
        min_qty = int(min_quantity)
        price = float(unit_price)
    except ValueError:
        qty, min_qty, price = 0, 5, 0
    with get_db() as conn:
        conn.execute("INSERT INTO inventory (name, category, quantity, min_quantity, unit_price, supplier) VALUES (?,?,?,?,?,?)",
            (name, category, qty, min_qty, price, supplier))
        conn.commit()
    log_activity('Add Inventory', f'{name} (x{qty})')
    flash(f"Produit '{name}' ajouté au stock", "success")
    return redirect("/inventory")



@inventory_bp.route("/update_inventory/<int:item_id>", methods=["POST"])
@login_required
def update_inventory(item_id):
    quantity = request.form.get("quantity", "0")
    min_quantity = request.form.get("min_quantity", "5")
    unit_price = request.form.get("unit_price", "0")
    supplier = request.form.get("supplier", "").strip()
    try:
        qty = int(quantity)
        min_qty = int(min_quantity)
        price = float(unit_price)
    except ValueError:
        flash("Valeurs invalides", "error")
        return redirect("/inventory")
    with get_db() as conn:
        conn.execute("UPDATE inventory SET quantity = ?, min_quantity = ?, unit_price = ?, supplier = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (qty, min_qty, price, supplier, item_id))
        conn.commit()
    log_activity('Update Inventory', f'Item #{item_id} → qty={qty}')
    flash("Stock mis à jour", "success")
    return redirect("/inventory")



@inventory_bp.route("/delete_inventory/<int:item_id>", methods=["POST"])
@login_required
def delete_inventory(item_id):
    with get_db() as conn:
        conn.execute("DELETE FROM inventory WHERE id = ?", (item_id,))
        conn.commit()
    log_activity('Delete Inventory', f'Item #{item_id}')
    flash("Produit supprimé", "success")
    return redirect("/inventory")



@inventory_bp.route("/inventory_dashboard")
@login_required
def inventory_dashboard():
    return render_template("inventory_dashboard.html")



# ─── Phase 6 Feature 6: Supplier Management ───
@inventory_bp.route("/suppliers")
@login_required
def suppliers_page():
    with get_db() as conn:
        suppliers = conn.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
    return render_template("suppliers.html", suppliers=suppliers)



@inventory_bp.route("/suppliers/add", methods=["POST"])
@login_required
def add_supplier():
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    address = request.form.get("address", "").strip()
    notes = request.form.get("notes", "").strip()
    if not name:
        flash("Nom requis", "error")
        return redirect("/suppliers")
    with get_db() as conn:
        conn.execute("INSERT INTO suppliers (name, phone, email, address, notes) VALUES (?,?,?,?,?)",
            (name, phone, email, address, notes))
        conn.commit()
    log_activity('Supplier Added', name)
    flash(f"Fournisseur {name} ajouté", "success")
    return redirect("/suppliers")



@inventory_bp.route("/suppliers/delete/<int:sid>", methods=["POST"])
@login_required
def delete_supplier(sid):
    with get_db() as conn:
        conn.execute("DELETE FROM suppliers WHERE id = ?", (sid,))
        conn.commit()
    flash("Fournisseur supprimé", "success")
    return redirect("/suppliers")



@inventory_bp.route("/purchase_orders")
@login_required
def purchase_orders():
    with get_db() as conn:
        orders = conn.execute(
            "SELECT po.id, s.name, po.order_date, po.status, po.total_amount, po.notes "
            "FROM purchase_orders po JOIN suppliers s ON po.supplier_id = s.id "
            "ORDER BY po.order_date DESC").fetchall()
        suppliers = conn.execute("SELECT id, name FROM suppliers ORDER BY name").fetchall()
        inventory = conn.execute("SELECT id, name, unit_price FROM inventory ORDER BY name").fetchall()
    return render_template("purchase_orders.html", orders=orders, suppliers=suppliers, inventory=inventory)



@inventory_bp.route("/purchase_orders/add", methods=["POST"])
@login_required
def add_purchase_order():
    supplier_id = request.form.get("supplier_id")
    order_date = request.form.get("order_date", "")
    notes = request.form.get("notes", "").strip()
    items_json = request.form.get("items", "[]")
    import json
    try:
        items = json.loads(items_json)
    except (json.JSONDecodeError, TypeError):
        items = []
    if not supplier_id or not items:
        flash("Fournisseur et articles requis", "error")
        return redirect("/purchase_orders")
    total = sum(float(i.get('quantity', 0)) * float(i.get('unit_price', 0)) for i in items)
    with get_db() as conn:
        cursor = conn.execute("INSERT INTO purchase_orders (supplier_id, order_date, total_amount, notes) VALUES (?,?,?,?)",
            (supplier_id, order_date, total, notes))
        order_id = cursor.lastrowid
        for item in items:
            inv_id = item.get('inventory_id') or None
            conn.execute("INSERT INTO purchase_items (order_id, inventory_id, item_name, quantity, unit_price) VALUES (?,?,?,?,?)",
                (order_id, inv_id, item.get('name', ''), float(item.get('quantity', 0)), float(item.get('unit_price', 0))))
        conn.commit()
    log_activity('Purchase Order', f'Order #{order_id} total: {total} DT')
    flash(f"Commande #{order_id} créée ({total} DT)", "success")
    return redirect("/purchase_orders")



@inventory_bp.route("/purchase_orders/receive/<int:order_id>", methods=["POST"])
@login_required
def receive_purchase_order(order_id):
    with get_db() as conn:
        items = conn.execute("SELECT inventory_id, quantity FROM purchase_items WHERE order_id = ? AND inventory_id IS NOT NULL", (order_id,)).fetchall()
        for item in items:
            conn.execute("UPDATE inventory SET quantity = quantity + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (item[1], item[0]))
        conn.execute("UPDATE purchase_orders SET status = 'received' WHERE id = ?", (order_id,))
        conn.commit()
    log_activity('Order Received', f'Order #{order_id} stock updated')
    flash(f"Commande #{order_id} reçue — stock mis à jour", "success")
    return redirect("/purchase_orders")



# ─── 7. Auto Purchase Orders ───
@inventory_bp.route("/auto_purchase_orders")
@login_required
@admin_required
def auto_purchase_orders():
    with get_db() as conn:
        # Find low stock items
        low_stock = conn.execute("""SELECT id, name, quantity, min_quantity, supplier
            FROM inventory WHERE quantity <= min_quantity""").fetchall()
        # Generate suggestions
        for item in low_stock:
            existing = conn.execute("SELECT id FROM auto_purchase_orders WHERE inventory_id=? AND status='suggested'",
                                   (item[0],)).fetchone()
            if not existing:
                order_qty = max(item[3] * 2 - item[2], item[3])
                supplier_id = None
                if item[4]:
                    sup = conn.execute("SELECT id FROM suppliers WHERE name=?", (item[4],)).fetchone()
                    supplier_id = sup[0] if sup else None
                conn.execute("""INSERT INTO auto_purchase_orders (inventory_id, supplier_id, item_name, current_qty, min_qty, order_qty)
                    VALUES (?,?,?,?,?,?)""", (item[0], supplier_id, item[1], item[2], item[3], order_qty))
        conn.commit()
        orders = conn.execute("""SELECT apo.*, s.name as supplier_name FROM auto_purchase_orders apo
            LEFT JOIN suppliers s ON apo.supplier_id=s.id ORDER BY apo.status, apo.created_at DESC""").fetchall()
    return render_template("auto_purchase_orders.html", orders=orders)



@inventory_bp.route("/auto_purchase_order/approve/<int:oid>", methods=["POST"])
@login_required
@admin_required
def auto_po_approve(oid):
    with get_db() as conn:
        order = conn.execute("SELECT * FROM auto_purchase_orders WHERE id=?", (oid,)).fetchone()
        if order and order[7] == 'suggested':
            # Create actual purchase order
            if order[2]:
                conn.execute("""INSERT INTO purchase_orders (supplier_id, order_date, status, total_amount)
                    VALUES (?, date('now'), 'pending', 0)""", (order[2],))
                po_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute("""INSERT INTO purchase_items (order_id, inventory_id, item_name, quantity, unit_price)
                    VALUES (?,?,?,?,0)""", (po_id, order[1], order[3], order[6]))
            conn.execute("UPDATE auto_purchase_orders SET status='approved' WHERE id=?", (oid,))
            conn.commit()
            flash("Commande approuvée et créée !", "success")
    return redirect("/auto_purchase_orders")



@inventory_bp.route("/auto_purchase_order/dismiss/<int:oid>", methods=["POST"])
@login_required
@admin_required
def auto_po_dismiss(oid):
    with get_db() as conn:
        conn.execute("UPDATE auto_purchase_orders SET status='dismissed' WHERE id=?", (oid,))
        conn.commit()
    return redirect("/auto_purchase_orders")



# ─── 7. Prévision Stock ───

@inventory_bp.route("/stock_forecast")
@login_required
def stock_forecast():
    from datetime import date, timedelta
    with get_db() as conn:
        products = conn.execute("SELECT * FROM inventory ORDER BY name").fetchall()
        forecasts = []
        for p in products:
            usage_30d = conn.execute("""SELECT COALESCE(SUM(quantity_used), 0) FROM product_usage
                WHERE product_id=? AND DATE(created_at) >= DATE('now', '-30 days')""", (p['id'],)).fetchone()[0]
            daily_avg = usage_30d / 30 if usage_30d else 0
            stock = p['quantity'] or 0
            days_left = int(stock / daily_avg) if daily_avg > 0 else 999
            scheduled = conn.execute("""SELECT COUNT(*) FROM appointments
                WHERE date >= DATE('now') AND date <= DATE('now', '+7 days') AND status='pending'""").fetchone()[0]
            recommended = max(0, (daily_avg * 30) - stock)
            status = 'critical' if days_left <= 7 else 'warning' if days_left <= 14 else 'ok'
            forecasts.append({
                'id': p['id'], 'name': p['name'], 'stock': stock,
                'daily_avg': round(daily_avg, 2), 'days_left': days_left,
                'recommended': round(recommended, 1), 'status': status,
                'usage_30d': usage_30d, 'scheduled_appt': scheduled,
                'unit': p['name'],  # inventory has no unit column
            })
        forecasts.sort(key=lambda x: x['days_left'])
    return render_template("stock_forecast.html", forecasts=forecasts)



@inventory_bp.route('/supplier_review/add', methods=['POST'])
@login_required
def supplier_review_add():
    delivery = int(request.form.get('delivery_rating', 5))
    quality = int(request.form.get('quality_rating', 5))
    price = int(request.form.get('price_rating', 5))
    overall = (delivery + quality + price) / 3
    with get_db() as conn:
        conn.execute("""INSERT INTO supplier_reviews
            (supplier_id, purchase_order_id, delivery_rating, quality_rating,
             price_rating, overall_rating, comment)
            VALUES (?,?,?,?,?,?,?)""",
            (int(request.form['supplier_id']),
             int(request.form.get('purchase_order_id', 0)),
             delivery, quality, price, overall,
             request.form.get('comment', '')))
        # Update supplier average
        sid = int(request.form['supplier_id'])
        avg = conn.execute("SELECT AVG(overall_rating) FROM supplier_reviews WHERE supplier_id=?",
                          (sid,)).fetchone()[0]
        conn.execute("UPDATE suppliers SET rating=? WHERE id=?", (avg or 0, sid))
        conn.commit()
    flash("Évaluation ajoutée", "success")
    return redirect("/supplier_performance")

# ── 9. Multi-Currency ──
@inventory_bp.route('/multi_currency')
@login_required
def multi_currency():
    with get_db() as conn:
        rates = conn.execute("SELECT * FROM currency_rates ORDER BY currency_code").fetchall()
        if not rates:
            defaults = [
                ('EUR', 'Euro', 3.35), ('USD', 'Dollar US', 3.10),
                ('GBP', 'Livre Sterling', 3.95), ('SAR', 'Riyal Saoudien', 0.83),
                ('AED', 'Dirham EAU', 0.84), ('LYD', 'Dinar Libyen', 0.64),
                ('DZD', 'Dinar Algérien', 0.023), ('MAD', 'Dirham Marocain', 0.31)
            ]
            for code, name, rate in defaults:
                conn.execute("INSERT INTO currency_rates (currency_code, currency_name, rate_to_tnd) VALUES (?,?,?)",
                            (code, name, rate))
            conn.commit()
            rates = conn.execute("SELECT * FROM currency_rates ORDER BY currency_code").fetchall()
    return render_template('multi_currency.html', rates=rates)


