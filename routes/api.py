"""
AMILCAR — API Endpoints
Blueprint: api_bp
Routes: 28
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, jsonify, session, send_file
from helpers import login_required, admin_required, client_required, get_db, get_services, get_setting, get_all_settings
from helpers import allowed_file, safe_page, log_activity, build_wa_url, STATUS_MESSAGES, UPLOAD_FOLDER, MAX_FILE_SIZE, MAX_FILES, PER_PAGE, csrf, cache
from database.db import get_db
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import os, re, uuid, io
import time as time_module
import sqlite3

api_bp = Blueprint("api_bp", __name__)


def check_api_key():
    key = request.headers.get('X-API-Key', '') or request.args.get('api_key', '')
    if not key:
        return None
    with get_db() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE api_key=? AND active=1", (key,)).fetchone()
        if row:
            conn.execute("UPDATE api_keys SET last_used=datetime('now') WHERE id=?", (row[0],))
            conn.commit()
            return row
    return None


@api_bp.route("/api/docs")
@login_required
def api_docs():
    """API documentation page."""
    endpoints = [
        {"method": "GET", "path": "/api/v1/customers", "desc": "List all customers", "auth": "API Key"},
        {"method": "GET", "path": "/api/v1/appointments", "desc": "List all appointments", "auth": "API Key"},
        {"method": "GET", "path": "/api/v1/invoices", "desc": "List all invoices", "auth": "API Key"},
        {"method": "GET", "path": "/api/v1/stats", "desc": "Dashboard statistics", "auth": "API Key"},
        {"method": "GET", "path": "/api/appointments_calendar", "desc": "Calendar data (JSON)", "auth": "Session"},
        {"method": "GET", "path": "/api/chart_data", "desc": "Chart data for dashboard", "auth": "Session"},
        {"method": "GET", "path": "/api/search?q=term", "desc": "Global search", "auth": "Session"},
        {"method": "POST", "path": "/api/v1/customers", "desc": "Create customer", "auth": "API Key"},
        {"method": "POST", "path": "/api/v1/appointments", "desc": "Create appointment", "auth": "API Key"},
    ]
    return render_template("api_docs.html", endpoints=endpoints)


@api_bp.route("/api/appointments_calendar")
@login_required
def appointments_calendar():
    with get_db() as conn:
        appointments = conn.execute(
            "SELECT a.id, cu.name, ca.brand || ' ' || ca.model, a.date, a.service, a.status, COALESCE(a.time, '') "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id"
        ).fetchall()
    events = []
    colors = {'pending': '#D4AF37', 'completed': '#2d6a4f', 'cancelled': '#555', 'in_progress': '#1B6B93'}
    for a in appointments:
        start = a[3]
        if a[6]:
            start = f"{a[3]}T{a[6]}"
        events.append({
            'id': a[0],
            'title': f"{a[1]} — {a[4]}",
            'start': start,
            'color': colors.get(a[5], '#D4AF37'),
            'extendedProps': {'car': a[2], 'status': a[5]}
        })
    return jsonify(events)



# ─── Calendar Drag-Drop Reschedule ───
@api_bp.route("/api/reschedule_appointment", methods=["POST"])
@login_required
def reschedule_appointment():
    data = request.get_json()
    if not data or 'id' not in data or 'date' not in data:
        return jsonify({'error': 'Données manquantes'}), 400
    appt_id = data['id']
    new_date = data['date']
    new_time = data.get('time', '')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', new_date):
        return jsonify({'error': 'Format de date invalide'}), 400
    # Validate date is real
    from datetime import datetime
    try:
        datetime.strptime(new_date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Date invalide'}), 400
    with get_db() as conn:
        appt = conn.execute("SELECT id, status FROM appointments WHERE id = ?", (appt_id,)).fetchone()
        if not appt:
            return jsonify({'error': 'Rendez-vous introuvable'}), 404
        if appt[1] in ('completed', 'cancelled'):
            return jsonify({'error': 'Impossible de déplacer un rendez-vous terminé ou annulé'}), 400
        if new_time:
            conflict = conn.execute(
                "SELECT id FROM appointments WHERE date = ? AND time = ? AND id != ? AND status != 'cancelled'",
                (new_date, new_time, appt_id)).fetchone()
            if conflict:
                return jsonify({'error': f'Le créneau {new_time} du {new_date} est déjà réservé'}), 409
            conn.execute("UPDATE appointments SET date = ?, time = ? WHERE id = ?", (new_date, new_time, appt_id))
        else:
            conn.execute("UPDATE appointments SET date = ? WHERE id = ?", (new_date, appt_id))
        conn.commit()
    log_activity('Reschedule', f'Appointment #{appt_id} → {new_date} {new_time}')
    return jsonify({'success': True})



# ─── AJAX Search API ───
@api_bp.route("/api/search")
@login_required
def api_search():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify([])
    like = f'%{q}%'
    with get_db() as conn:
        customers = conn.execute(
            "SELECT 'client' as type, id, name, phone FROM customers WHERE name LIKE ? OR phone LIKE ? LIMIT 5",
            (like, like)).fetchall()
        cars = conn.execute(
            "SELECT 'voiture' as type, ca.id, ca.brand || ' ' || ca.model, ca.plate "
            "FROM cars ca WHERE ca.brand LIKE ? OR ca.model LIKE ? OR ca.plate LIKE ? LIMIT 5",
            (like, like, like)).fetchall()
        appointments = conn.execute(
            "SELECT 'rdv' as type, a.id, cu.name || ' — ' || a.service, a.date || ' (' || a.status || ')' "
            "FROM appointments a JOIN cars ca ON a.car_id=ca.id JOIN customers cu ON ca.customer_id=cu.id "
            "WHERE cu.name LIKE ? OR a.service LIKE ? LIMIT 4",
            (like, like)).fetchall()
        invoices = conn.execute(
            "SELECT 'facture' as type, i.id, 'Facture #' || i.id || ' — ' || cu.name, i.amount || ' DT (' || i.status || ')' "
            "FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
            "JOIN cars ca ON a.car_id=ca.id JOIN customers cu ON ca.customer_id=cu.id "
            "WHERE cu.name LIKE ? OR CAST(i.id AS TEXT) = ? LIMIT 4",
            (like, q)).fetchall()
    results = []
    icons = {'client': '👤', 'voiture': '🚗', 'rdv': '📅', 'facture': '📄'}
    urls = {'client': '/customer/', 'voiture': '/car/', 'rdv': '/edit_appointment/', 'facture': '/print_invoice/'}
    for row in (*customers, *cars, *appointments, *invoices):
        t = row[0]
        results.append({'type': t, 'icon': icons[t], 'id': row[1], 'label': row[2], 'sub': row[3], 'url': f'{urls[t]}{row[1]}'})
    return jsonify(results)



@api_bp.route("/api/chart_data")
@login_required
def chart_data():
    cached = cache.get('chart_data')
    if cached:
        return jsonify(cached)
    from datetime import date
    with get_db() as conn:
        today = date.today()
        months = []
        revenue_data = []
        expenses_data = []
        appointments_data = []
        for i in range(5, -1, -1):
            m = today.month - i
            y = today.year
            while m <= 0:
                m += 12
                y -= 1
            ms = f"{y}-{m:02d}-01"
            if m == 12:
                me = f"{y+1}-01-01"
            else:
                me = f"{y}-{m+1:02d}-01"
            rev = conn.execute(
                "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
            exp = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
            apt = conn.execute(
                "SELECT COUNT(*) FROM appointments a JOIN cars ca ON a.car_id = ca.id "
                "JOIN customers cu ON ca.customer_id = cu.id "
                "WHERE a.date >= ? AND a.date < ?", (ms, me)).fetchone()[0]
            month_names = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
            months.append(month_names[m-1])
            revenue_data.append(float(rev))
            expenses_data.append(float(exp))
            appointments_data.append(apt)
        # Service distribution
        services = conn.execute(
            "SELECT a.service, COUNT(*) FROM appointments a "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "GROUP BY a.service ORDER BY COUNT(*) DESC LIMIT 6"
        ).fetchall()
        # Expense categories distribution
        expense_cats = conn.execute(
            "SELECT category, COALESCE(SUM(amount),0) FROM expenses GROUP BY category ORDER BY SUM(amount) DESC LIMIT 6"
        ).fetchall()
    result = {
        'months': months,
        'revenue': revenue_data,
        'expenses': expenses_data,
        'appointments': appointments_data,
        'services': {'labels': [s[0][:20] for s in services], 'data': [s[1] for s in services]},
        'expense_categories': {'labels': [e[0] for e in expense_cats], 'data': [float(e[1]) for e in expense_cats]}
    }
    cache.set('chart_data', result, ttl=300)
    return jsonify(result)



@api_bp.route("/api/ratings/<int:customer_id>")
@login_required
def api_customer_ratings(customer_id):
    with get_db() as conn:
        ratings = conn.execute(
            "SELECT r.rating, r.comment, r.created_at, a.service, a.date "
            "FROM ratings r JOIN appointments a ON r.appointment_id = a.id "
            "WHERE r.customer_id = ? ORDER BY r.created_at DESC", (customer_id,)).fetchall()
        avg_rating = conn.execute(
            "SELECT AVG(rating), COUNT(*) FROM ratings WHERE customer_id = ?",
            (customer_id,)).fetchone()
    return jsonify({
        'average': round(avg_rating[0], 1) if avg_rating[0] else 0,
        'count': avg_rating[1],
        'ratings': [{'stars': r[0], 'comment': r[1], 'date': r[2], 'service': r[3], 'appt_date': r[4]} for r in ratings]
    })



# ─── Enhanced Dashboard API ───
@api_bp.route("/api/weekly_revenue")
@login_required
def weekly_revenue():
    cached = cache.get('weekly_revenue')
    if cached:
        return jsonify(cached)
    from datetime import date, timedelta
    today = date.today()
    data = []
    day_names = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    start_of_week = today - timedelta(days=today.weekday())
    with get_db() as conn:
        for i in range(7):
            day = start_of_week + timedelta(days=i)
            ds = day.isoformat()
            rev = conn.execute(
                "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                "WHERE a.date = ? AND i.status = 'paid'", (ds,)).fetchone()[0]
            appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE date = ?", (ds,)).fetchone()[0]
            data.append({'day': day_names[i], 'date': ds, 'revenue': float(rev), 'appointments': appts})
    cache.set('weekly_revenue', data, ttl=120)
    return jsonify(data)



@api_bp.route("/api/monthly_comparison")
@login_required
def monthly_comparison():
    cached = cache.get('monthly_comparison')
    if cached:
        return jsonify(cached)
    from datetime import date
    today = date.today()
    results = []
    with get_db() as conn:
        for i in range(11, -1, -1):
            m = today.month - i
            y = today.year
            while m <= 0:
                m += 12
                y -= 1
            ms = f"{y}-{m:02d}-01"
            me = f"{y+1}-01-01" if m == 12 else f"{y}-{m+1:02d}-01"
            rev = conn.execute(
                "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
            exp = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
            month_names = ["J","F","M","A","M","J","J","A","S","O","N","D"]
            results.append({
                'label': f"{month_names[m-1]}", 'month': f"{y}-{m:02d}",
                'revenue': float(rev), 'expenses': float(exp), 'profit': float(rev - exp)
            })
    cache.set('monthly_comparison', results, ttl=300)
    return jsonify(results)



@api_bp.route("/api/profit_forecast")
@login_required
def profit_forecast():
    cached = cache.get('profit_forecast')
    if cached:
        return jsonify(cached)
    from datetime import date
    today = date.today()
    with get_db() as conn:
        # Average daily revenue last 30 days
        from datetime import timedelta
        d30 = (today - timedelta(days=30)).isoformat()
        avg_rev = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0)/30.0 FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND i.status = 'paid'", (d30,)).fetchone()[0]
        avg_exp = conn.execute(
            "SELECT COALESCE(SUM(amount),0)/30.0 FROM expenses WHERE date >= ?", (d30,)).fetchone()[0]
        # Days remaining in month
        if today.month == 12:
            last_day = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(today.year, today.month + 1, 1) - timedelta(days=1)
        days_remaining = (last_day - today).days
        # Current month actual
        ms = f"{today.year}-{today.month:02d}-01"
        me = f"{today.year+1}-01-01" if today.month == 12 else f"{today.year}-{today.month+1:02d}-01"
        curr_rev = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
        curr_exp = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
    forecast_rev = float(curr_rev) + float(avg_rev) * days_remaining
    forecast_exp = float(curr_exp) + float(avg_exp) * days_remaining
    result = {
        'current_revenue': float(curr_rev),
        'current_expenses': float(curr_exp),
        'forecast_revenue': round(forecast_rev),
        'forecast_expenses': round(forecast_exp),
        'forecast_profit': round(forecast_rev - forecast_exp),
        'avg_daily_revenue': round(float(avg_rev), 1),
        'days_remaining': days_remaining
    }
    cache.set('profit_forecast', result, ttl=120)
    return jsonify(result)



@api_bp.route("/api/live_board")
@login_required
def api_live_board():
    from datetime import date
    today = str(date.today())
    with get_db() as conn:
        appointments = conn.execute(
            "SELECT a.id, cu.name, ca.brand || ' ' || ca.model, ca.plate, a.service, a.status, "
            "COALESCE(a.time,''), COALESCE(a.assigned_to,''), COALESCE(a.estimated_duration, 60) "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date = ? ORDER BY a.time, a.id", (today,)).fetchall()
    columns = {'pending': [], 'in_progress': [], 'completed': []}
    for a in appointments:
        item = {'id': a[0], 'customer': a[1], 'car': a[2], 'plate': a[3],
                'service': a[4], 'status': a[5], 'time': a[6], 'tech': a[7], 'duration': a[8]}
        if a[5] in columns:
            columns[a[5]].append(item)
        elif a[5] == 'cancelled':
            pass
        else:
            columns['pending'].append(item)
    return jsonify(columns)



@api_bp.route("/api/update_board_status", methods=["POST"])
@login_required
def update_board_status():
    data = request.get_json()
    if not data or 'id' not in data or 'status' not in data:
        return jsonify({'error': 'Données manquantes'}), 400
    new_status = data['status']
    if new_status not in ('pending', 'in_progress', 'completed'):
        return jsonify({'error': 'Statut invalide'}), 400
    appt_id = data['id']
    with get_db() as conn:
        conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (new_status, appt_id))
        # Auto-deduct inventory when completed
        if new_status == 'completed':
            appt = conn.execute("SELECT service FROM appointments WHERE id = ?", (appt_id,)).fetchone()
            if appt:
                service_name = appt[0].split(' - ')[0].strip()
                links = conn.execute(
                    "SELECT inventory_id, quantity_used FROM service_inventory WHERE service_name = ?",
                    (service_name,)).fetchall()
                for link in links:
                    conn.execute(
                        "UPDATE inventory SET quantity = MAX(0, quantity - ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (link[1], link[0]))
        conn.commit()
    log_activity('Board Update', f'Appointment #{appt_id} → {new_status}')
    return jsonify({'success': True})



# ─── Feature 2: QR Code for Invoices ───
@api_bp.route("/api/invoice_qr/<int:invoice_id>")
@login_required
def invoice_qr(invoice_id):
    """Generate QR code as SVG for an invoice"""
    with get_db() as conn:
        inv = conn.execute("SELECT qr_token FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not inv:
            return "Not found", 404
        token = inv[0]
        if not token:
            token = uuid.uuid4().hex
            conn.execute("UPDATE invoices SET qr_token = ? WHERE id = ?", (token, invoice_id))
            conn.commit()
    # Generate QR as simple SVG using manual encoding
    url = f"{request.host_url}invoice_view/{token}"
    # Use a simple QR code generation via HTML/JS approach
    return jsonify({'url': url, 'token': token})



# ─── Feature 3: Smart Scheduling ───
@api_bp.route("/api/available_slots")
@login_required
def available_slots():
    date_val = request.args.get('date', '')
    if not date_val:
        return jsonify([])
    max_daily = int(get_setting('max_daily_appointments', '10'))
    with get_db() as conn:
        booked = conn.execute(
            "SELECT COALESCE(time,''), COUNT(*) FROM appointments WHERE date = ? AND status != 'cancelled' GROUP BY time",
            (date_val,)).fetchall()
        total_booked = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE date = ? AND status != 'cancelled'",
            (date_val,)).fetchone()[0]
    booked_times = {b[0] for b in booked if b[0]}
    slots = []
    all_times = ['08:00', '08:30', '09:00', '09:30', '10:00', '10:30', '11:00', '11:30',
                 '12:00', '13:00', '13:30', '14:00', '14:30', '15:00', '15:30', '16:00', '16:30', '17:00']
    for t in all_times:
        slots.append({'time': t, 'available': t not in booked_times})
    return jsonify({
        'slots': slots,
        'total_booked': total_booked,
        'max_daily': max_daily,
        'full': total_booked >= max_daily
    })



@api_bp.route("/api/heatmap_data")
@login_required
def api_heatmap_data():
    with get_db() as conn:
        # Day of week analysis
        day_data = conn.execute(
            "SELECT CASE CAST(strftime('%w', date) AS INTEGER) "
            "WHEN 0 THEN 'Dim' WHEN 1 THEN 'Lun' WHEN 2 THEN 'Mar' WHEN 3 THEN 'Mer' "
            "WHEN 4 THEN 'Jeu' WHEN 5 THEN 'Ven' WHEN 6 THEN 'Sam' END as day_name, "
            "COUNT(*) FROM appointments WHERE status != 'cancelled' GROUP BY strftime('%w', date) "
            "ORDER BY CAST(strftime('%w', date) AS INTEGER)").fetchall()
        # Hour analysis
        hour_data = conn.execute(
            "SELECT COALESCE(time, ''), COUNT(*) FROM appointments "
            "WHERE time != '' AND status != 'cancelled' GROUP BY time ORDER BY time").fetchall()
        # Day x Hour matrix
        matrix = conn.execute(
            "SELECT strftime('%w', date) as dow, time, COUNT(*) "
            "FROM appointments WHERE time != '' AND status != 'cancelled' "
            "GROUP BY dow, time ORDER BY dow, time").fetchall()
        # Monthly trend
        monthly = conn.execute(
            "SELECT strftime('%Y-%m', date) as month, COUNT(*) "
            "FROM appointments WHERE status != 'cancelled' "
            "GROUP BY month ORDER BY month DESC LIMIT 12").fetchall()
        # Peak analysis
        busiest_day = conn.execute(
            "SELECT date, COUNT(*) as cnt FROM appointments WHERE status != 'cancelled' "
            "GROUP BY date ORDER BY cnt DESC LIMIT 5").fetchall()
    day_names = ['Dim', 'Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam']
    matrix_data = []
    for m in matrix:
        dow = int(m[0])
        hour = m[1][:2] if m[1] else '00'
        matrix_data.append({'day': dow, 'day_name': day_names[dow], 'hour': hour, 'time': m[1], 'count': m[2]})
    return jsonify({
        'by_day': [{'day': d[0], 'count': d[1]} for d in day_data],
        'by_hour': [{'time': h[0], 'count': h[1]} for h in hour_data],
        'matrix': matrix_data,
        'monthly': [{'month': m[0], 'count': m[1]} for m in reversed(monthly)],
        'busiest_days': [{'date': b[0], 'count': b[1]} for b in busiest_day]
    })



@api_bp.route("/api/time_report")
@login_required
def api_time_report():
    from datetime import date, timedelta, datetime
    period = request.args.get('period', 'week')
    today = date.today()
    if period == 'week':
        start = (today - timedelta(days=today.weekday())).isoformat()
    elif period == 'month':
        start = f"{today.year}-{today.month:02d}-01"
    else:
        start = (today - timedelta(days=30)).isoformat()
    with get_db() as conn:
        users = conn.execute("SELECT id, username, COALESCE(full_name, '') FROM users ORDER BY id").fetchall()
        results = []
        for u in users:
            logs = conn.execute(
                "SELECT action, timestamp FROM time_tracking WHERE user_id = ? AND date >= ? ORDER BY timestamp",
                (u[0], start)).fetchall()
            total_hours = 0
            clock_in_time = None
            for log in logs:
                if log[0] == 'clock_in':
                    try:
                        clock_in_time = datetime.fromisoformat(log[1])
                    except (ValueError, TypeError):
                        clock_in_time = None
                elif log[0] == 'clock_out' and clock_in_time:
                    try:
                        clock_out_time = datetime.fromisoformat(log[1])
                        total_hours += (clock_out_time - clock_in_time).total_seconds() / 3600
                        clock_in_time = None
                    except (ValueError, TypeError):
                        pass
            results.append({
                'username': u[1], 'full_name': u[2],
                'total_hours': round(total_hours, 1),
                'log_count': len(logs)
            })
    return jsonify(results)



# ─── Feature 8: Advanced Inventory Monitoring ───
@api_bp.route("/api/inventory_trends")
@login_required
def inventory_trends():
    with get_db() as conn:
        items = conn.execute("SELECT id, name, quantity, min_quantity, unit_price, category FROM inventory ORDER BY name").fetchall()
        # Consumption rate from service_inventory usage
        consumption = {}
        from datetime import date, timedelta
        d30 = (date.today() - timedelta(days=30)).isoformat()
        for item in items:
            used = conn.execute(
                "SELECT COALESCE(SUM(si.quantity_used),0) "
                "FROM service_inventory si JOIN appointments a ON si.service_name = a.service "
                "JOIN inventory inv ON si.inventory_id = inv.id "
                "WHERE inv.id = ? AND a.status = 'completed' AND a.date >= ?",
                (item[0], d30)).fetchone()[0]
            consumption[item[0]] = used
    results = []
    for item in items:
        usage_30d = consumption.get(item[0], 0)
        days_until_empty = round(item[2] / (usage_30d / 30)) if usage_30d > 0 else 999
        reorder_needed = item[2] <= item[3]
        results.append({
            'id': item[0], 'name': item[1], 'quantity': item[2], 'min_quantity': item[3],
            'unit_price': item[4], 'category': item[5],
            'usage_30d': round(usage_30d, 1), 'days_until_empty': min(days_until_empty, 999),
            'reorder': reorder_needed,
            'stock_value': round(item[2] * item[4], 1)
        })
    return jsonify(results)



@api_bp.route("/sw.js")
def service_worker():
    sw_content = """
const CACHE_NAME = 'amilcar-v7';
const STATIC_ASSETS = [
    '/',
    '/offline',
    '/static/style.css',
    '/static/logo.png',
    '/manifest.json'
];

self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(STATIC_ASSETS)));
    self.skipWaiting();
});

self.addEventListener('activate', e => {
    e.waitUntil(
        caches.keys().then(keys => Promise.all(
            keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
        ))
    );
    self.clients.claim();
});

self.addEventListener('fetch', e => {
    var req = e.request;
    if (req.method !== 'GET') return;
    /* Navigation requests: network-first with offline fallback */
    if (req.mode === 'navigate') {
        e.respondWith(
            fetch(req).then(r => {
                var clone = r.clone();
                caches.open(CACHE_NAME).then(c => c.put(req, clone));
                return r;
            }).catch(() => caches.match(req).then(r => r || caches.match('/offline')))
        );
        return;
    }
    /* Static assets: cache-first */
    if (req.url.match(/\\.(css|js|png|jpg|jpeg|gif|svg|ico|woff2?)$/)) {
        e.respondWith(
            caches.match(req).then(r => {
                if (r) return r;
                return fetch(req).then(resp => {
                    var clone = resp.clone();
                    caches.open(CACHE_NAME).then(c => c.put(req, clone));
                    return resp;
                });
            })
        );
        return;
    }
    /* API/other: network-first, fallback to cache */
    e.respondWith(
        fetch(req).then(r => {
            var clone = r.clone();
            caches.open(CACHE_NAME).then(c => c.put(req, clone));
            return r;
        }).catch(() => caches.match(req))
    );
});

self.addEventListener('push', e => {
    var data = e.data ? e.data.json() : {};
    var title = data.title || 'AMILCAR';
    var options = {
        body: data.body || '',
        icon: '/static/logo.png',
        badge: '/static/logo.png',
        tag: data.tag || 'amilcar-notif',
        data: {url: data.url || '/'}
    };
    e.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', e => {
    e.notification.close();
    e.waitUntil(clients.openWindow(e.notification.data.url || '/'));
});
"""
    response = make_response(sw_content)
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    return response



@api_bp.route("/api/reward_history/<int:customer_id>")
@login_required
def api_reward_history(customer_id):
    with get_db() as conn:
        history = conn.execute(
            "SELECT points, type, description, created_at FROM reward_history "
            "WHERE customer_id = ? ORDER BY created_at DESC LIMIT 50",
            (customer_id,)).fetchall()
        info = conn.execute(
            "SELECT points, total_earned, total_spent, tier FROM reward_points WHERE customer_id = ?",
            (customer_id,)).fetchone()
    return jsonify({
        'info': {'points': info[0], 'earned': info[1], 'spent': info[2], 'tier': info[3]} if info else None,
        'history': [{'points': h[0], 'type': h[1], 'desc': h[2], 'date': h[3]} for h in history]
    })



@api_bp.route("/api/validate_coupon")
@login_required
def validate_coupon():
    from datetime import date
    code = request.args.get('code', '').strip().upper()
    amount = float(request.args.get('amount', 0))
    with get_db() as conn:
        coupon = conn.execute("SELECT id, discount_type, discount_value, max_uses, used_count, expires_at, active, min_amount FROM coupons WHERE code = ?", (code,)).fetchone()
    if not coupon:
        return jsonify({'valid': False, 'error': 'Code invalide'})
    if not coupon[6]:
        return jsonify({'valid': False, 'error': 'Coupon désactivé'})
    if coupon[4] >= coupon[3]:
        return jsonify({'valid': False, 'error': 'Coupon épuisé'})
    if coupon[5] and coupon[5] < date.today().isoformat():
        return jsonify({'valid': False, 'error': 'Coupon expiré'})
    if amount < coupon[7]:
        return jsonify({'valid': False, 'error': f'Montant minimum: {coupon[7]} DT'})
    if coupon[1] == 'percent':
        discount = round(amount * coupon[2] / 100, 2)
    else:
        discount = min(coupon[2], amount)
    return jsonify({'valid': True, 'discount': discount, 'type': coupon[1], 'value': coupon[2]})



@api_bp.route("/api/mileage_alerts")
@login_required
def mileage_alerts():
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=14)).isoformat()
    today = date.today().isoformat()
    with get_db() as conn:
        due = conn.execute(
            "SELECT ca.id, cu.name, cu.phone, ca.brand, ca.model, ca.plate, ca.next_service_date, ca.mileage "
            "FROM cars ca JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE ca.next_service_date != '' AND ca.next_service_date <= ? ORDER BY ca.next_service_date",
            (soon,)).fetchall()
    alerts = []
    for d in due:
        overdue = d[6] < today
        alerts.append({'car_id': d[0], 'customer': d[1], 'phone': d[2], 'car': f"{d[3]} {d[4]}",
                       'plate': d[5], 'due_date': d[6], 'mileage': d[7], 'overdue': overdue})
    return jsonify(alerts)



@api_bp.route("/api/queue_status")
@login_required
def api_queue_status():
    with get_db() as conn:
        queue = conn.execute(
            "SELECT wq.id, cu.name, wq.service, wq.priority, wq.status, wq.estimated_wait, wq.created_at "
            "FROM waiting_queue wq JOIN customers cu ON wq.customer_id=cu.id "
            "WHERE wq.status IN ('waiting','serving') ORDER BY wq.priority DESC, wq.created_at").fetchall()
    position = 0
    total_wait = 0
    items = []
    for q in queue:
        if q[4] == 'waiting':
            position += 1
            total_wait += q[5]
        items.append({
            'id': q[0], 'customer': q[1], 'service': q[2], 'priority': q[3],
            'status': q[4], 'wait': q[5], 'since': q[6], 'position': position
        })
    return jsonify({'queue': items, 'total_waiting': position, 'est_total_wait': total_wait})



@api_bp.route("/api/get_price")
@login_required
def api_get_price():
    service = request.args.get('service', '')
    car_cat = request.args.get('car_category', 'sedan')
    tier = request.args.get('tier', '')
    with get_db() as conn:
        base_price = conn.execute("SELECT price FROM services WHERE name=?", (service,)).fetchone()
        base = base_price[0] if base_price else 0
        rule = conn.execute("SELECT price_modifier, fixed_price FROM dynamic_pricing WHERE service_name=? AND car_category=? AND active=1",
            (service, car_cat)).fetchone()
        if rule:
            if rule[1] > 0:
                return jsonify({'price': rule[1], 'base': base, 'modifier': 'fixed'})
            return jsonify({'price': round(base * rule[0], 2), 'base': base, 'modifier': rule[0]})
    return jsonify({'price': base, 'base': base, 'modifier': 1.0})



@api_bp.route("/api/push_subscribe", methods=["POST"])
def push_subscribe():
    data = request.get_json()
    if data:
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('push_sub_' || ?, ?)",
                        (data.get('endpoint', '')[:50], str(data)))
            conn.commit()
    return jsonify({'status': 'ok'})



@api_bp.route("/api/v1/customers", methods=["GET"])
@csrf.exempt
def api_customers():
    auth = check_api_key()
    if not auth:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as conn:
        rows = conn.execute("SELECT id, name, phone, email FROM customers ORDER BY name LIMIT 100").fetchall()
    return jsonify([{'id': r[0], 'name': r[1], 'phone': r[2], 'email': r[3]} for r in rows])



@api_bp.route("/api/v1/appointments", methods=["GET"])
@csrf.exempt
def api_appointments():
    auth = check_api_key()
    if not auth:
        return jsonify({'error': 'Unauthorized'}), 401
    date_filter = request.args.get('date', '')
    with get_db() as conn:
        if date_filter:
            rows = conn.execute("""SELECT a.id, a.date, a.time, a.service, a.status, c.name, ca.plate
                FROM appointments a JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
                WHERE a.date=? ORDER BY a.time""", (date_filter,)).fetchall()
        else:
            rows = conn.execute("""SELECT a.id, a.date, a.time, a.service, a.status, c.name, ca.plate
                FROM appointments a JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
                ORDER BY a.date DESC LIMIT 50""").fetchall()
    return jsonify([{'id': r[0], 'date': r[1], 'time': r[2], 'service': r[3], 'status': r[4],
                     'customer': r[5], 'plate': r[6]} for r in rows])



@api_bp.route("/api/v1/invoices", methods=["GET"])
@csrf.exempt
def api_invoices():
    auth = check_api_key()
    if not auth:
        return jsonify({'error': 'Unauthorized'}), 401
    status = request.args.get('status', '')
    with get_db() as conn:
        if status:
            rows = conn.execute("""SELECT i.id, i.amount, i.status, a.service, c.name
                FROM invoices i JOIN appointments a ON i.appointment_id=a.id
                JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
                WHERE i.status=? ORDER BY i.id DESC LIMIT 50""", (status,)).fetchall()
        else:
            rows = conn.execute("""SELECT i.id, i.amount, i.status, a.service, c.name
                FROM invoices i JOIN appointments a ON i.appointment_id=a.id
                JOIN cars ca ON a.car_id=ca.id JOIN customers c ON ca.customer_id=c.id
                ORDER BY i.id DESC LIMIT 50""").fetchall()
    return jsonify([{'id': r[0], 'amount': r[1], 'status': r[2], 'service': r[3], 'customer': r[4]} for r in rows])



@api_bp.route("/api/v1/stats", methods=["GET"])
@csrf.exempt
def api_stats():
    auth = check_api_key()
    if not auth:
        return jsonify({'error': 'Unauthorized'}), 401
    from datetime import date
    today = date.today().isoformat()
    month = date.today().strftime("%Y-%m")
    with get_db() as conn:
        today_rev = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
            JOIN appointments a ON i.appointment_id=a.id WHERE a.date=? AND i.status='paid'""", (today,)).fetchone()[0]
        month_rev = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
            JOIN appointments a ON i.appointment_id=a.id WHERE strftime('%%Y-%%m',a.date)=? AND i.status='paid'""", (month,)).fetchone()[0]
        total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        today_appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE date=?", (today,)).fetchone()[0]
    return jsonify({'today_revenue': today_rev, 'month_revenue': month_rev,
                    'total_customers': total_customers, 'today_appointments': today_appts})



@api_bp.route("/api/notifications/count")
@login_required
def api_notification_count():
    user_id = session.get('user_id')
    with get_db() as conn:
        count = conn.execute("""SELECT COUNT(*) FROM notifications_center 
            WHERE (user_id=? OR user_id=0) AND is_read=0""", (user_id,)).fetchone()[0]
    return jsonify({'count': count})



@api_bp.route('/api/convert_currency')
@login_required
def convert_currency_api():
    amount = float(request.args.get('amount', 0))
    from_curr = request.args.get('from', 'TND')
    to_curr = request.args.get('to', 'EUR')
    with get_db() as conn:
        if from_curr == 'TND':
            rate = conn.execute("SELECT rate_to_tnd FROM currency_rates WHERE currency_code=?",
                               (to_curr,)).fetchone()
            result = amount / rate['rate_to_tnd'] if rate and rate['rate_to_tnd'] > 0 else 0
        elif to_curr == 'TND':
            rate = conn.execute("SELECT rate_to_tnd FROM currency_rates WHERE currency_code=?",
                               (from_curr,)).fetchone()
            result = amount * rate['rate_to_tnd'] if rate else 0
        else:
            from_rate = conn.execute("SELECT rate_to_tnd FROM currency_rates WHERE currency_code=?",
                                    (from_curr,)).fetchone()
            to_rate = conn.execute("SELECT rate_to_tnd FROM currency_rates WHERE currency_code=?",
                                  (to_curr,)).fetchone()
            if from_rate and to_rate and to_rate['rate_to_tnd'] > 0:
                tnd = amount * from_rate['rate_to_tnd']
                result = tnd / to_rate['rate_to_tnd']
            else:
                result = 0
    return jsonify({'amount': amount, 'from': from_curr, 'to': to_curr, 'result': round(result, 2)})

# ── 10. Knowledge Base ──
@api_bp.route('/knowledge_base')
@login_required
def knowledge_base():
    category = request.args.get('category', 'all')
    search = request.args.get('q', '')
    with get_db() as conn:
        query = "SELECT * FROM knowledge_base WHERE 1=1"
        params = []
        if category != 'all':
            query += " AND category = ?"
            params.append(category)
        if search:
            query += " AND (title LIKE ? OR content LIKE ? OR tags LIKE ?)"
            params.extend([f"%{search}%"] * 3)
        query += " ORDER BY is_pinned DESC, created_at DESC"
        articles = conn.execute(query, params).fetchall()
        categories = conn.execute("SELECT DISTINCT category FROM knowledge_base ORDER BY category").fetchall()
    return render_template('knowledge_base.html', articles=articles,
                          categories=categories, category=category, search=search)


