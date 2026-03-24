"""
AMILCAR — Analytics, Quality & Advanced Tools
Blueprint: analytics_bp
"""
from flask import Blueprint, render_template, request, redirect, flash, make_response, jsonify, session
from helpers import login_required, admin_required, get_db, get_services, get_setting, get_all_settings
from helpers import log_activity, build_wa_url, PER_PAGE
from database.db import get_db
from datetime import datetime, date, timedelta
import os, re, io
import time as time_module

analytics_bp = Blueprint("analytics_bp", __name__)

# ─── Feature 7: Service Profitability Analysis ───
@analytics_bp.route("/profitability")
@login_required
def service_profitability():
    with get_db() as conn:
        # Get services with revenue and material costs
        services = conn.execute(
            "SELECT a.service, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) as revenue "
            "FROM appointments a LEFT JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' "
            "WHERE a.status = 'completed' GROUP BY a.service ORDER BY revenue DESC").fetchall()
        # Material cost per service from service_inventory
        cost_data = {}
        links = conn.execute(
            "SELECT si.service_name, SUM(si.quantity_used * inv.unit_price) as cost "
            "FROM service_inventory si JOIN inventory inv ON si.inventory_id = inv.id "
            "GROUP BY si.service_name").fetchall()
        for l in links:
            cost_data[l[0]] = l[1]
    results = []
    for s in services:
        service_name = s[0].split(' - ')[0].strip()
        material_cost = cost_data.get(service_name, 0) * s[1]
        profit = s[2] - material_cost
        margin = round(profit / s[2] * 100) if s[2] > 0 else 0
        results.append({
            'service': s[0], 'count': s[1], 'revenue': s[2],
            'material_cost': round(material_cost, 1),
            'profit': round(profit, 1), 'margin': margin
        })
    return render_template("profitability.html", services=results)



# ─── Phase 8 Feature 8: Retention Analysis ───
@analytics_bp.route("/retention_analysis")
@login_required
def retention_analysis():
    with get_db() as conn:
        from datetime import date, timedelta
        today = date.today()
        # All customers with visits
        customers_data = conn.execute("""
            SELECT c.id, c.name, c.phone, COUNT(a.id) as visits,
                   MIN(a.date) as first_visit, MAX(a.date) as last_visit,
                   COALESCE(SUM(i.amount),0) as total_spent
            FROM customers c
            LEFT JOIN cars cr ON cr.customer_id=c.id
            LEFT JOIN appointments a ON a.car_id=cr.id
            LEFT JOIN invoices i ON i.appointment_id=a.id AND i.status='Payée'
            GROUP BY c.id ORDER BY last_visit DESC
        """).fetchall()
        total = len(customers_data)
        active_30 = len([c for c in customers_data if c[5] and c[5] >= (today - timedelta(days=30)).isoformat()])
        active_90 = len([c for c in customers_data if c[5] and c[5] >= (today - timedelta(days=90)).isoformat()])
        churned = len([c for c in customers_data if c[5] and c[5] < (today - timedelta(days=90)).isoformat()])
        new_30 = len([c for c in customers_data if c[4] and c[4] >= (today - timedelta(days=30)).isoformat()])
        returning = len([c for c in customers_data if c[3] and c[3] > 1])
        retention_rate = round(returning / total * 100, 1) if total > 0 else 0
        churn_rate = round(churned / total * 100, 1) if total > 0 else 0
        # Monthly retention
        monthly_retention = []
        for i in range(5, -1, -1):
            m = today.replace(day=1) - timedelta(days=i*30)
            ms = m.replace(day=1).isoformat()
            me = (m.replace(day=28) + timedelta(days=4)).replace(day=1).isoformat()
            active = conn.execute("SELECT COUNT(DISTINCT cr.customer_id) FROM appointments a JOIN cars cr ON a.car_id=cr.id WHERE a.date >= ? AND a.date < ?", (ms, me)).fetchone()[0]
            monthly_retention.append({'month': ms[:7], 'active': active})
        # At risk (visited 2+ times, last visit 30-90 days ago)
        at_risk = [c for c in customers_data if c[3] >= 2 and c[5] and
                   (today - timedelta(days=90)).isoformat() <= c[5] < (today - timedelta(days=30)).isoformat()]
    return render_template("retention_analysis.html",
        total=total, active_30=active_30, active_90=active_90, churned=churned,
        new_30=new_30, returning=returning, retention_rate=retention_rate,
        churn_rate=churn_rate, monthly_retention=monthly_retention,
        at_risk=at_risk[:20], customers=customers_data[:50])



@analytics_bp.route("/quality_check/<int:appt_id>", methods=["GET", "POST"])
@login_required
def quality_check(appt_id):
    import json
    with get_db() as conn:
        appt = conn.execute("""SELECT a.*, c.plate, cu.name as customer_name 
            FROM appointments a JOIN cars c ON a.car_id=c.id 
            JOIN customers cu ON c.customer_id=cu.id WHERE a.id=?""", (appt_id,)).fetchone()
        if not appt:
            flash("RDV introuvable", "danger")
            return redirect("/appointments")
        existing = conn.execute("SELECT * FROM quality_checks WHERE appointment_id=?", (appt_id,)).fetchone()
        if request.method == "POST":
            checklist = []
            for item in QUALITY_CHECKLIST:
                status = request.form.get(f"check_{item['id']}", "pending")
                note = request.form.get(f"note_{item['id']}", "").strip()
                checklist.append({'id': item['id'], 'label': item['label'], 'category': item['category'],
                                'status': status, 'note': note})
            passed = sum(1 for c in checklist if c['status'] == 'pass')
            total = len(checklist)
            score = int((passed / total) * 100) if total else 0
            nps = request.form.get("nps_score", 0, type=int)
            nps_comment = request.form.get("nps_comment", "").strip()
            checklist_json = json.dumps(checklist)
            if existing:
                conn.execute("""UPDATE quality_checks SET checklist=?, overall_score=?, nps_score=?, 
                    nps_comment=?, status=? WHERE appointment_id=?""",
                    (checklist_json, score, nps, nps_comment, 'completed' if score >= 80 else 'needs_review', appt_id))
            else:
                conn.execute("""INSERT INTO quality_checks 
                    (appointment_id, inspector_id, checklist, overall_score, nps_score, nps_comment, status)
                    VALUES (?,?,?,?,?,?,?)""",
                    (appt_id, session.get('user_id', 0), checklist_json, score, nps, nps_comment,
                     'completed' if score >= 80 else 'needs_review'))
            conn.commit()
            flash(f"Contrôle qualité enregistré — Score: {score}%", "success")
            return redirect(f"/quality_check/{appt_id}")
        parsed_checklist = json.loads(existing['checklist']) if existing and existing['checklist'] else []
    return render_template("quality_check.html", appt=appt, existing=existing,
                          checklist=QUALITY_CHECKLIST, parsed=parsed_checklist)



@analytics_bp.route("/quality_dashboard")
@login_required
def quality_dashboard():
    import json
    with get_db() as conn:
        checks = conn.execute("""SELECT qc.*, a.service, a.date, c.plate, cu.name as customer_name
            FROM quality_checks qc 
            JOIN appointments a ON qc.appointment_id=a.id 
            JOIN cars c ON a.car_id=c.id 
            JOIN customers cu ON c.customer_id=cu.id 
            ORDER BY qc.created_at DESC LIMIT 100""").fetchall()
        avg_score = conn.execute("SELECT AVG(overall_score) FROM quality_checks").fetchone()[0] or 0
        avg_nps = conn.execute("SELECT AVG(nps_score) FROM quality_checks WHERE nps_score > 0").fetchone()[0] or 0
        total = conn.execute("SELECT COUNT(*) FROM quality_checks").fetchone()[0]
        passed = conn.execute("SELECT COUNT(*) FROM quality_checks WHERE overall_score >= 80").fetchone()[0]
    return render_template("quality_dashboard.html", checks=checks, avg_score=avg_score, 
                          avg_nps=avg_nps, total=total, passed=passed)



# ─── 9. Tableau Comparatif Mensuel ───

@analytics_bp.route("/monthly_comparison")
@login_required
@admin_required
def monthly_comparison_view():
    from datetime import date
    today = date.today()
    current_month = today.strftime("%Y-%m")
    if today.month == 1:
        prev_month = f"{today.year - 1}-12"
    else:
        prev_month = f"{today.year}-{today.month - 1:02d}"
    with get_db() as conn:
        def month_stats(m):
            revenue = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE strftime('%%Y-%%m',created_at)=? AND status='paid'", (m,)).fetchone()[0]
            appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE strftime('%%Y-%%m',date)=?", (m,)).fetchone()[0]
            completed = conn.execute("SELECT COUNT(*) FROM appointments WHERE strftime('%%Y-%%m',date)=? AND status='completed'", (m,)).fetchone()[0]
            new_customers = conn.execute("SELECT COUNT(*) FROM customers WHERE last_visit LIKE ?", (m + '%',)).fetchone()[0]
            expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE strftime('%%Y-%%m',date)=?", (m,)).fetchone()[0]
            avg_ticket = revenue / completed if completed else 0
            profit = revenue - expenses
            return {'month': m, 'revenue': revenue, 'appointments': appts, 'completed': completed,
                    'new_customers': new_customers, 'expenses': expenses, 'avg_ticket': avg_ticket, 'profit': profit}
        current = month_stats(current_month)
        previous = month_stats(prev_month)
        # Calculate deltas
        def delta(curr, prev):
            if prev == 0:
                return 100 if curr > 0 else 0
            return ((curr - prev) / prev) * 100
        deltas = {}
        for key in ['revenue', 'appointments', 'completed', 'new_customers', 'expenses', 'avg_ticket', 'profit']:
            deltas[key] = delta(current[key], previous[key])
        # Last 6 months for chart
        months_data = []
        for i in range(5, -1, -1):
            m = today.month - i
            y = today.year
            while m <= 0:
                m += 12; y -= 1
            ms = f"{y}-{m:02d}"
            months_data.append(month_stats(ms))
    return render_template("monthly_comparison.html", current=current, previous=previous,
                          deltas=deltas, months_data=months_data)



# ─── 6. Rentabilité par Type Véhicule ───

@analytics_bp.route("/profitability_vehicle_type")
@login_required
@admin_required
def profitability_vehicle_type():
    month = request.args.get("month", "")
    from datetime import date
    if not month:
        month = date.today().strftime("%Y-%m")
    with get_db() as conn:
        data = conn.execute("""SELECT c.vehicle_type,
            COUNT(DISTINCT a.id) as appointments,
            COALESCE(SUM(CASE WHEN i.status='paid' THEN i.amount ELSE 0 END),0) as revenue,
            COALESCE(SUM(pu.total_cost),0) as product_cost,
            COUNT(DISTINCT c.id) as vehicles
            FROM appointments a
            JOIN cars c ON a.car_id=c.id
            LEFT JOIN invoices i ON i.appointment_id=a.id
            LEFT JOIN product_usage pu ON pu.appointment_id=a.id
            WHERE strftime('%%Y-%%m', a.date) = ?
            GROUP BY c.vehicle_type""", (month,)).fetchall()
        # Service breakdown by vehicle type
        service_data = conn.execute("""SELECT c.vehicle_type, a.service,
            COUNT(*) as cnt, COALESCE(SUM(CASE WHEN i.status='paid' THEN i.amount ELSE 0 END),0) as rev
            FROM appointments a JOIN cars c ON a.car_id=c.id LEFT JOIN invoices i ON i.appointment_id=a.id
            WHERE strftime('%%Y-%%m', a.date)=?
            GROUP BY c.vehicle_type, a.service ORDER BY rev DESC""", (month,)).fetchall()
    return render_template("profitability_vehicle_type.html", data=data, service_data=service_data, month=month)



@analytics_bp.route("/efficiency_report")
@login_required
def efficiency_report():
    from datetime import date, timedelta
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    with get_db() as conn:
        by_service = conn.execute("""SELECT service_name, COUNT(*) as count,
            AVG(estimated_minutes) as avg_est, AVG(actual_minutes) as avg_actual,
            AVG(efficiency_pct) as avg_eff
            FROM service_timer WHERE strftime('%%Y-%%m', created_at)=? AND actual_minutes > 0
            GROUP BY service_name ORDER BY avg_eff""", (month,)).fetchall()
        by_employee = conn.execute("""SELECT e.full_name as name, COUNT(*) as count,
            AVG(st.actual_minutes) as avg_time, AVG(st.efficiency_pct) as avg_eff
            FROM service_timer st LEFT JOIN users e ON st.employee_id=e.id
            WHERE strftime('%%Y-%%m', st.created_at)=? AND st.actual_minutes > 0
            GROUP BY st.employee_id ORDER BY avg_eff DESC""", (month,)).fetchall()
        bottlenecks = conn.execute("""SELECT service_name, employee_id, actual_minutes, estimated_minutes, efficiency_pct
            FROM service_timer WHERE efficiency_pct < 70 AND efficiency_pct > 0
            AND strftime('%%Y-%%m', created_at)=? ORDER BY efficiency_pct LIMIT 20""", (month,)).fetchall()
    return render_template("efficiency_report.html", by_service=by_service, by_employee=by_employee,
        bottlenecks=bottlenecks, month=month)



# ─── 8. Objectifs & Budget Mensuel ───

@analytics_bp.route("/monthly_goals_view")
@login_required
def monthly_goals_view():
    from datetime import date
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    with get_db() as conn:
        goals = conn.execute("SELECT * FROM monthly_goals WHERE month=? ORDER BY goal_type", (month,)).fetchall()
        actuals = {}
        actuals['revenue'] = conn.execute("""SELECT COALESCE(SUM(amount), 0) FROM invoices
            WHERE status='paid' AND strftime('%%Y-%%m', created_at)=?""", (month,)).fetchone()[0]
        actuals['appointments'] = conn.execute("""SELECT COUNT(*) FROM appointments
            WHERE strftime('%%Y-%%m', date)=?""", (month,)).fetchone()[0]
        actuals['new_customers'] = conn.execute("""SELECT COUNT(*) FROM customers
            WHERE strftime('%%Y-%%m', last_visit)=?""", (month,)).fetchone()[0]
        actuals['avg_ticket'] = conn.execute("""SELECT AVG(amount) FROM invoices
            WHERE status='paid' AND strftime('%%Y-%%m', created_at)=?""", (month,)).fetchone()[0] or 0
    return render_template("monthly_goals.html", goals=goals, actuals=actuals, month=month)



@analytics_bp.route("/monthly_goal/add", methods=["POST"])
@login_required
def monthly_goal_add():
    with get_db() as conn:
        conn.execute("""INSERT INTO monthly_goals (month, goal_type, target_value, unit, notes)
            VALUES (?,?,?,?,?)""",
            (request.form["month"], request.form["goal_type"],
             request.form.get("target_value", 0, type=float),
             request.form.get("unit", ""), request.form.get("notes", "")))
        conn.commit()
    flash("Objectif ajouté", "success")
    return redirect(f"/monthly_goals_view?month={request.form['month']}")



@analytics_bp.route('/smart_scheduling/suggest', methods=['POST'])
@login_required
def smart_scheduling_suggest():
    date = request.form['date']
    service_id = int(request.form.get('service_id', 0))
    duration = int(request.form.get('duration', 60))
    with get_db() as conn:
        # Find busy times
        busy = conn.execute("""
            SELECT time, estimated_duration FROM appointments WHERE date = ? AND status != 'cancelled'
        """, (date,)).fetchall()
        busy_times = set()
        for b in busy:
            if b['time']:
                hour = int(b['time'].split(':')[0]) if ':' in b['time'] else 8
                dur = b['estimated_duration'] or 60
                for h in range(hour, min(hour + (dur // 60) + 1, 19)):
                    busy_times.add(h)
        # Suggest free slots
        suggestions = []
        for hour in range(8, 18):
            slots_needed = max(1, duration // 60)
            if all(h not in busy_times for h in range(hour, min(hour + slots_needed, 19))):
                suggestions.append({'time': f"{hour:02d}:00", 'score': 100 - len(busy_times) * 5})
        # Sort by score
        suggestions.sort(key=lambda x: x['score'], reverse=True)
    return jsonify({'suggestions': suggestions[:5]})

# ── 8. Data Import Center ──
@analytics_bp.route('/import_center')
@login_required
def import_center():
    with get_db() as conn:
        history = conn.execute("SELECT * FROM import_history ORDER BY created_at DESC LIMIT 20").fetchall()
    return render_template('import_center.html', history=history)



@analytics_bp.route('/report_builder/create', methods=['POST'])
@login_required
def report_builder_create():
    sections = request.form.getlist('sections')
    with get_db() as conn:
        conn.execute("""INSERT INTO report_builder (name, report_type, sections, schedule)
            VALUES (?,?,?,?)""",
            (request.form['name'], request.form['report_type'],
             ','.join(sections), request.form.get('schedule', '')))
        conn.commit()
    flash("Rapport créé avec succès", "success")
    return redirect("/report_builder")



@analytics_bp.route('/report_builder/generate/<int:report_id>')
@login_required
def report_builder_generate(report_id):
    with get_db() as conn:
        report = conn.execute("SELECT * FROM report_builder WHERE id=?", (report_id,)).fetchone()
        if not report:
            flash("Rapport non trouvé", "danger")
            return redirect("/report_builder")
        sections = (report['sections'] or '').split(',')
        data = {}
        month_start = datetime.now().strftime('%Y-%m-01')
        today = datetime.now().strftime('%Y-%m-%d')

        if 'revenue' in sections:
            data['revenue'] = conn.execute("""
                SELECT COALESCE(SUM(total), 0) as total, COUNT(*) as count,
                       AVG(total) as avg_ticket
                FROM invoices WHERE date >= ? AND status != 'cancelled'
            """, (month_start,)).fetchone()
        if 'appointments' in sections:
            data['appointments'] = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed,
                       SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) as cancelled
                FROM appointments WHERE date >= ?
            """, (month_start,)).fetchone()
        if 'customers' in sections:
            data['customers'] = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) as new_this_month
                FROM customers
            """, (month_start,)).fetchone()
        if 'services' in sections:
            data['services'] = conn.execute("""
                SELECT service, COUNT(*) as count, SUM(i.total) as revenue
                FROM appointments a LEFT JOIN invoices i ON a.id = i.appointment_id
                WHERE a.date >= ? GROUP BY a.service ORDER BY count DESC LIMIT 10
            """, (month_start,)).fetchall()
        if 'employees' in sections:
            data['employees'] = conn.execute("""
                SELECT assigned_to, COUNT(*) as count
                FROM appointments WHERE date >= ? AND assigned_to != ''
                GROUP BY assigned_to ORDER BY count DESC
            """, (month_start,)).fetchall()
        if 'inventory' in sections:
            data['inventory'] = conn.execute("""
                SELECT name, quantity, min_quantity FROM inventory
                WHERE quantity <= min_quantity ORDER BY quantity ASC LIMIT 10
            """).fetchall()

        conn.execute("UPDATE report_builder SET last_generated=? WHERE id=?", (today, report_id))
        conn.commit()

    # Generate PDF
    settings = {}
    with get_db() as conn:
        for s in conn.execute("SELECT key, value FROM settings").fetchall():
            settings[s['key']] = s['value']

    html = render_template('report_builder_pdf.html', report=report, data=data,
                          sections=sections, settings=settings,
                          generated_at=datetime.now().strftime('%d/%m/%Y %H:%M'))
    from xhtml2pdf import pisa
    pdf_buffer = io.BytesIO()
    pisa.CreatePDF(io.BytesIO(html.encode('utf-8')), dest=pdf_buffer)
    pdf_buffer.seek(0)
    return send_file(pdf_buffer, mimetype='application/pdf',
                    download_name=f"rapport_{report['name']}_{today}.pdf", as_attachment=True)



@analytics_bp.route('/report_builder/delete/<int:report_id>', methods=["POST"])
@login_required
def report_builder_delete(report_id):
    with get_db() as conn:
        conn.execute("DELETE FROM report_builder WHERE id=?", (report_id,))
        conn.commit()
    flash("Rapport supprimé", "success")
    return redirect("/report_builder")

# ── 10. Customer 360 View ──
@analytics_bp.route('/customer_360/<int:customer_id>')
@login_required
def customer_360(customer_id):
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not customer:
            flash("Client non trouvé", "danger")
            return redirect("/customers")
        cars = conn.execute("SELECT * FROM cars WHERE customer_id=?", (customer_id,)).fetchall()
        appointments = conn.execute("""
            SELECT a.*, c.brand, c.model, c.plate FROM appointments a
            LEFT JOIN cars c ON a.car_id = c.id
            WHERE a.customer_id=? ORDER BY a.date DESC LIMIT 20
        """, (customer_id,)).fetchall()
        invoices = conn.execute("""
            SELECT * FROM invoices WHERE customer_id=? ORDER BY date DESC LIMIT 20
        """, (customer_id,)).fetchall()
        total_spent = conn.execute(
            "SELECT COALESCE(SUM(total), 0) FROM invoices WHERE customer_id=? AND status != 'cancelled'",
            (customer_id,)).fetchone()[0]
        total_visits = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE customer_id=?", (customer_id,)).fetchone()[0]
        # Wallet
        wallet = conn.execute(
            "SELECT * FROM wallet_transactions WHERE customer_id=? ORDER BY created_at DESC LIMIT 10",
            (customer_id,)).fetchall()
        # NPS
        nps = conn.execute(
            "SELECT * FROM nps_surveys WHERE customer_id=? ORDER BY created_at DESC LIMIT 5",
            (customer_id,)).fetchall()
        # Communications
        comms = conn.execute(
            "SELECT * FROM channel_inbox WHERE customer_id=? ORDER BY created_at DESC LIMIT 10",
            (customer_id,)).fetchall()
        # Loyalty
        loyalty = conn.execute(
            "SELECT * FROM loyalty WHERE customer_id=?", (customer_id,)).fetchone()
        # Treatments
        treatments = conn.execute("""
            SELECT t.* FROM treatments t
            LEFT JOIN cars c ON t.car_id = c.id
            WHERE c.customer_id=? ORDER BY t.applied_date DESC LIMIT 10
        """, (customer_id,)).fetchall()
        # Timeline
        timeline = conn.execute(
            "SELECT * FROM customer_timeline WHERE customer_id=? ORDER BY created_at DESC LIMIT 20",
            (customer_id,)).fetchall()
        # Reviews
        reviews = conn.execute(
            "SELECT * FROM client_reviews WHERE customer_id=? ORDER BY created_at DESC LIMIT 5",
            (customer_id,)).fetchall()
        # Referrals
        referrals = conn.execute(
            "SELECT * FROM referrals WHERE referrer_id=? OR referred_id=? ORDER BY created_at DESC",
            (customer_id, customer_id)).fetchall()
    return render_template('customer_360.html', customer=customer, cars=cars,
                          appointments=appointments, invoices=invoices,
                          total_spent=total_spent, total_visits=total_visits,
                          wallet=wallet, nps=nps, comms=comms, loyalty=loyalty,
                          treatments=treatments, timeline=timeline, reviews=reviews,
                          referrals=referrals)



# ─── 18.2 End of Day Report ──────────────────────────────────────────────────

@analytics_bp.route("/tech_summary")
@login_required
def tech_daily_summary():
    from datetime import date as d
    day = request.args.get("date", str(d.today()))
    with get_db() as conn:
        employees = conn.execute("SELECT id, full_name, username FROM users WHERE role IN ('employee','admin') ORDER BY full_name").fetchall()
        summaries = []
        for emp in employees:
            tasks = conn.execute("""SELECT a.service, a.status, a.time, 
                c.name as customer_name, car.brand, car.model, car.plate,
                COALESCE(i.amount,0) as revenue
                FROM appointments a
                LEFT JOIN cars car ON a.car_id=car.id
                LEFT JOIN customers c ON car.customer_id=c.id
                LEFT JOIN invoices i ON a.id=i.appointment_id
                WHERE a.date=? AND a.assigned_employee_id=?
                ORDER BY a.time""", (day, emp['id'])).fetchall()
            
            completed = sum(1 for t in tasks if t['status'] == 'completed')
            total_revenue = sum(t['revenue'] for t in tasks if t['status'] == 'completed')
            
            timer = conn.execute("""SELECT COUNT(*) as count, 
                COALESCE(AVG(efficiency_pct),0) as avg_eff,
                COALESCE(SUM(actual_minutes),0) as total_minutes
                FROM service_timer WHERE employee_id=? AND date(created_at)=?""",
                (emp['id'], day)).fetchone()
            
            summaries.append({
                'employee': emp,
                'tasks': tasks,
                'completed': completed,
                'total_tasks': len(tasks),
                'revenue': total_revenue,
                'avg_efficiency': round(timer['avg_eff'], 1) if timer else 0,
                'total_minutes': timer['total_minutes'] if timer else 0,
            })
    return render_template("tech_summary.html", summaries=summaries, day=day)


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT: Excel & PDF
# ═══════════════════════════════════════════════════════════════════════════════


@analytics_bp.route("/pnl_report")
@login_required
def pnl_report():
    """Formal Profit & Loss statement with year-over-year comparison."""
    year = request.args.get('year', date.today().year, type=int)
    prev_year = year - 1
    month_names = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]

    with get_db() as conn:
        months = []
        for m in range(1, 13):
            ms = f"{year}-{m:02d}-01"
            me = f"{year}-{m+1:02d}-01" if m < 12 else f"{year+1}-01-01"
            pms = f"{prev_year}-{m:02d}-01"
            pme = f"{prev_year}-{m+1:02d}-01" if m < 12 else f"{prev_year+1}-01-01"

            rev = conn.execute(
                "SELECT COALESCE(SUM(i.amount),0) FROM invoices i "
                "JOIN appointments a ON i.appointment_id=a.id "
                "WHERE a.date>=? AND a.date<? AND i.status='paid'", (ms, me)).fetchone()[0]
            exp = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date>=? AND date<?", (ms, me)).fetchone()[0]
            prev_rev = conn.execute(
                "SELECT COALESCE(SUM(i.amount),0) FROM invoices i "
                "JOIN appointments a ON i.appointment_id=a.id "
                "WHERE a.date>=? AND a.date<? AND i.status='paid'", (pms, pme)).fetchone()[0]
            prev_exp = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date>=? AND date<?", (pms, pme)).fetchone()[0]

            months.append({
                'label': month_names[m-1],
                'revenue': rev, 'expenses': exp, 'profit': rev - exp,
                'prev_revenue': prev_rev, 'prev_expenses': prev_exp, 'prev_profit': prev_rev - prev_exp,
                'rev_change': round(((rev - prev_rev) / prev_rev * 100) if prev_rev > 0 else 0, 1),
                'exp_change': round(((exp - prev_exp) / prev_exp * 100) if prev_exp > 0 else 0, 1),
            })

        # Expense breakdown by category
        expense_cats = conn.execute(
            "SELECT COALESCE(category,'Autre'), COALESCE(SUM(amount),0) "
            "FROM expenses WHERE strftime('%%Y',date)=? GROUP BY category ORDER BY SUM(amount) DESC",
            (str(year),)).fetchall()

        # Totals
        total_rev = sum(m['revenue'] for m in months)
        total_exp = sum(m['expenses'] for m in months)
        prev_total_rev = sum(m['prev_revenue'] for m in months)
        prev_total_exp = sum(m['prev_expenses'] for m in months)

    return render_template("pnl_report.html", months=months, year=year, prev_year=prev_year,
                           expense_cats=expense_cats, total_rev=total_rev, total_exp=total_exp,
                           total_profit=total_rev - total_exp,
                           prev_total_rev=prev_total_rev, prev_total_exp=prev_total_exp,
                           prev_total_profit=prev_total_rev - prev_total_exp)



@analytics_bp.route("/auto_reminders", methods=["GET", "POST"])
@login_required
def auto_reminders():
    """Send automatic WhatsApp reminders for tomorrow's appointments."""
    if request.method == "POST":
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        shop_name = get_setting('shop_name', 'AMILCAR')
        sent_count = 0
        with get_db() as conn:
            appts = conn.execute("""
                SELECT a.id, a.date, a.time, a.service, c.name, c.phone, car.brand, car.model
                FROM appointments a
                JOIN cars car ON a.car_id=car.id
                JOIN customers c ON car.customer_id=c.id
                WHERE a.date=? AND a.status IN ('pending','confirmed')
                AND c.phone IS NOT NULL AND c.phone != ''
            """, (tomorrow,)).fetchall()

            reminder_urls = []
            for appt in appts:
                time_str = appt['time'] or ''
                msg = (f"Bonjour {appt['name']},\n\n"
                       f"Rappel de votre RDV demain{' à ' + time_str if time_str else ''} "
                       f"chez {shop_name}.\n"
                       f"Service : {appt['service']}\n"
                       f"Véhicule : {appt['brand']} {appt['model']}\n\n"
                       f"À demain ! 🚗")
                url = build_wa_url(appt['phone'], msg)
                reminder_urls.append({'name': appt['name'], 'phone': appt['phone'],
                                      'service': appt['service'], 'url': url})
                conn.execute(
                    "INSERT INTO communication_log (customer_id, type, subject, message) "
                    "SELECT cu.id, 'whatsapp', 'Rappel RDV', ? FROM customers cu WHERE cu.phone=?",
                    (msg, appt['phone']))
                sent_count += 1
            conn.commit()

        flash(f"{sent_count} rappel(s) préparé(s) pour demain", "success")
        return render_template("auto_reminders.html", reminder_urls=reminder_urls, sent=True)

    # GET: show tomorrow's appointments that need reminders
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with get_db() as conn:
        appts = conn.execute("""
            SELECT a.id, a.date, a.time, a.service, c.name, c.phone, car.brand, car.model
            FROM appointments a
            JOIN cars car ON a.car_id=car.id
            JOIN customers c ON car.customer_id=c.id
            WHERE a.date=? AND a.status IN ('pending','confirmed')
            AND c.phone IS NOT NULL AND c.phone != ''
        """, (tomorrow,)).fetchall()
    return render_template("auto_reminders.html", appointments=appts, sent=False,
                           tomorrow=tomorrow)


@analytics_bp.route("/notify_car_ready/<int:appointment_id>")
@login_required
def notify_car_ready(appointment_id):
    """Send WhatsApp notification that car is ready for pickup."""
    with get_db() as conn:
        data = conn.execute("""
            SELECT c.name, c.phone, car.brand, car.model, a.service
            FROM appointments a JOIN cars car ON a.car_id=car.id
            JOIN customers c ON car.customer_id=c.id WHERE a.id=?
        """, (appointment_id,)).fetchone()
    if not data or not data['phone']:
        flash("Numéro de téléphone introuvable", "warning")
        return redirect("/appointments")

    shop_name = get_setting('shop_name', 'AMILCAR')
    msg = (f"Bonjour {data['name']},\n\n"
           f"Votre {data['brand']} {data['model']} est prête ! ✅\n"
           f"Service effectué : {data['service']}\n\n"
           f"Vous pouvez passer la récupérer chez {shop_name}.\n"
           f"Merci de votre confiance ! 🙏")
    url = build_wa_url(data['phone'], msg)
    log_activity('WhatsApp', f'Car ready notification for appointment #{appointment_id}')
    return redirect(url)
