"""
AMILCAR — Reports, KPIs & Dashboards
Blueprint: reports_bp
Routes: 28
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

reports_bp = Blueprint("reports_bp", __name__)


def generate_pdf(html_content):
    """Generate PDF from HTML using WeasyPrint."""
    from weasyprint import HTML
    pdf = HTML(string=html_content).write_pdf()
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    return response


@reports_bp.route("/report_pdf/<report_type>")
@login_required
def report_pdf(report_type):
    """Generate PDF for any report page."""
    allowed = ['daily', 'reports', 'end_of_day', 'advanced_report', 'ceo_dashboard']
    if report_type not in allowed:
        flash("Type de rapport invalide", "error")
        return redirect('/')
    from flask import current_app
    client = current_app.test_client()
    # Copy session to internal request
    with client.session_transaction() as sess:
        sess['user_id'] = session.get('user_id')
        sess['username'] = session.get('username')
        sess['role'] = session.get('role')
    resp = client.get(f'/{report_type}')
    if resp.status_code != 200:
        flash("Erreur lors de la génération du PDF", "error")
        return redirect(f'/{report_type}')
    html = resp.data.decode('utf-8')
    # Add print-friendly CSS
    html = html.replace('</head>', '<style>@page{size:A4;margin:1cm} body{background:#fff!important;color:#000!important} .no-print,.sidebar,.navbar{display:none!important}</style></head>')
    response = generate_pdf(html)
    response.headers['Content-Disposition'] = f'attachment; filename=rapport_{report_type}_{datetime.now().strftime("%Y%m%d")}.pdf'
    return response


@reports_bp.route("/daily")
@login_required
def daily():
    from datetime import date
    today = str(date.today())
    with get_db() as conn:
        appointments = conn.execute("SELECT a.id, cu.name, ca.brand, ca.model, a.date, a.service, a.status, COALESCE(a.time, '') FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id WHERE a.date = ?", (today,)).fetchall()
        revenue = conn.execute("SELECT SUM(amount) FROM invoices i JOIN appointments a ON i.appointment_id = a.id WHERE a.date = ? AND i.status = 'paid'", (today,)).fetchone()[0] or 0
        expenses_today = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date = ?", (today,)).fetchone()[0]
    return render_template("daily.html", appointments=appointments, revenue=revenue, today=today, expenses_today=expenses_today)



@reports_bp.route("/monthly")
@login_required
def monthly():
    from datetime import date, timedelta
    month_param = request.args.get("month")
    if month_param:
        year, mon = map(int, month_param.split("-"))
    else:
        today = date.today()
        year, mon = today.year, today.month
    month_start = f"{year}-{mon:02d}-01"
    if mon == 12:
        next_y, next_m = year + 1, 1
    else:
        next_y, next_m = year, mon + 1
    month_end = f"{next_y}-{next_m:02d}-01"
    if mon == 1:
        prev_y, prev_m = year - 1, 12
    else:
        prev_y, prev_m = year, mon - 1
    months_ar = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
    month_label = f"{months_ar[mon-1]} {year}"
    prev_label = f"{months_ar[prev_m-1]} {prev_y}"
    next_label = f"{months_ar[next_m-1]} {next_y}"
    with get_db() as conn:
        appointments = conn.execute(
            "SELECT a.id, cu.name, ca.brand, ca.model, a.date, a.service, a.status "
            "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date >= ? AND a.date < ? ORDER BY a.date",
            (month_start, month_end)).fetchall()
        invoices = conn.execute(
            "SELECT i.id, cu.name, a.service, i.amount, i.status "
            "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
            "WHERE a.date >= ? AND a.date < ? ORDER BY i.id",
            (month_start, month_end)).fetchall()
        revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'",
            (month_start, month_end)).fetchone()[0]
        unpaid = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'unpaid'",
            (month_start, month_end)).fetchone()[0]
        completed = sum(1 for a in appointments if a[6] == 'completed')
        month_expenses = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date >= ? AND date < ?",
            (month_start, month_end)).fetchone()[0]
    stats = {
        'appointments': len(appointments),
        'completed': completed,
        'revenue': revenue,
        'unpaid': unpaid,
        'expenses': month_expenses,
        'profit': revenue - month_expenses
    }
    return render_template("monthly.html",
        stats=stats, appointments=appointments, invoices=invoices,
        month_label=month_label,
        current_month=f"{year}-{mon:02d}",
        prev_month=f"{prev_y}-{prev_m:02d}", prev_label=prev_label,
        next_month=f"{next_y}-{next_m:02d}", next_label=next_label)



# ─── KPI Dashboard ───
@reports_bp.route("/kpi")
@login_required
def kpi_dashboard():
    from datetime import date, timedelta
    with get_db() as conn:
        today = date.today()
        # Current month boundaries
        ms = f"{today.year}-{today.month:02d}-01"
        if today.month == 12:
            me = f"{today.year+1}-01-01"
        else:
            me = f"{today.year}-{today.month+1:02d}-01"
        # Previous month boundaries
        if today.month == 1:
            pms = f"{today.year-1}-12-01"
            pme = ms
        else:
            pms = f"{today.year}-{today.month-1:02d}-01"
            pme = ms
        # Current month stats
        curr_revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (ms, me)).fetchone()[0]
        curr_appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
        curr_completed = conn.execute("SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ? AND status = 'completed'", (ms, me)).fetchone()[0]
        curr_new_customers = conn.execute(
            "SELECT COUNT(DISTINCT ca.customer_id) FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "WHERE a.date >= ? AND a.date < ? AND ca.customer_id NOT IN "
            "(SELECT DISTINCT ca2.customer_id FROM appointments a2 JOIN cars ca2 ON a2.car_id = ca2.id WHERE a2.date < ?)",
            (ms, me, ms)).fetchone()[0]
        # Previous month stats
        prev_revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
            "WHERE a.date >= ? AND a.date < ? AND i.status = 'paid'", (pms, pme)).fetchone()[0]
        prev_appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ?", (pms, pme)).fetchone()[0]
        # Completion rate
        completion_rate = round(curr_completed / curr_appts * 100) if curr_appts > 0 else 0
        # Average revenue per day (this month)
        days_elapsed = max(1, (today - date(today.year, today.month, 1)).days + 1)
        avg_daily = round(curr_revenue / days_elapsed, 1)
        # Average rating this month
        avg_rating = conn.execute(
            "SELECT AVG(r.rating), COUNT(*) FROM ratings r WHERE r.created_at >= ?", (ms,)).fetchone()
        # Returning customers rate
        total_customers_visited = conn.execute(
            "SELECT COUNT(DISTINCT ca.customer_id) FROM appointments a JOIN cars ca ON a.car_id = ca.id "
            "WHERE a.date >= ? AND a.date < ?", (ms, me)).fetchone()[0]
        returning = total_customers_visited - curr_new_customers if total_customers_visited > curr_new_customers else 0
        return_rate = round(returning / total_customers_visited * 100) if total_customers_visited > 0 else 0
        # Revenue growth
        revenue_growth = round((curr_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else 0
        # Top technicians this month
        top_techs = conn.execute(
            "SELECT a.assigned_to, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) "
            "FROM appointments a LEFT JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' "
            "WHERE a.date >= ? AND a.date < ? AND a.assigned_to != '' "
            "GROUP BY a.assigned_to ORDER BY cnt DESC LIMIT 5", (ms, me)).fetchall()
    kpi = {
        'revenue': curr_revenue, 'prev_revenue': prev_revenue, 'revenue_growth': revenue_growth,
        'appointments': curr_appts, 'prev_appointments': prev_appts,
        'completion_rate': completion_rate, 'avg_daily': avg_daily,
        'avg_rating': round(avg_rating[0], 1) if avg_rating[0] else 0,
        'rating_count': avg_rating[1],
        'new_customers': curr_new_customers, 'return_rate': return_rate,
        'top_techs': top_techs
    }
    month_names = ["Janvier","Février","Mars","Avril","Mai","Juin","Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
    return render_template("kpi.html", kpi=kpi, month_label=f"{month_names[today.month-1]} {today.year}")


# ─── CEO Dashboard ───
@reports_bp.route("/ceo_dashboard")
@login_required
def ceo_dashboard_v2():
    today = date.today()
    with get_db() as conn:
        # === Period boundaries ===
        ms = f"{today.year}-{today.month:02d}-01"
        me = f"{today.year+1}-01-01" if today.month == 12 else f"{today.year}-{today.month+1:02d}-01"
        if today.month == 1:
            pms = f"{today.year-1}-12-01"
        else:
            pms = f"{today.year}-{today.month-1:02d}-01"
        pme = ms
        today_str = today.isoformat()
        week_start = (today - timedelta(days=today.weekday())).isoformat()
        year_start = f"{today.year}-01-01"
        d30 = (today - timedelta(days=30)).isoformat()
        days_in_month = max(1, (today - date(today.year, today.month, 1)).days + 1)

        # === Revenue metrics ===
        today_revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
            "WHERE a.date=? AND i.status='paid'", (today_str,)).fetchone()[0]
        week_revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
            "WHERE a.date>=? AND i.status='paid'", (week_start,)).fetchone()[0]
        month_revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
            "WHERE a.date>=? AND a.date<? AND i.status='paid'", (ms, me)).fetchone()[0]
        prev_month_revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
            "WHERE a.date>=? AND a.date<? AND i.status='paid'", (pms, pme)).fetchone()[0]
        year_revenue = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
            "WHERE a.date>=? AND i.status='paid'", (year_start,)).fetchone()[0]
        revenue_growth = round((month_revenue - prev_month_revenue) / prev_month_revenue * 100) if prev_month_revenue > 0 else 0

        # === Expense metrics ===
        month_expenses = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date>=? AND date<?", (ms, me)).fetchone()[0]
        prev_month_expenses = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date>=? AND date<?", (pms, pme)).fetchone()[0]
        year_expenses = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date>=?", (year_start,)).fetchone()[0]

        # === Profit ===
        month_profit = month_revenue - month_expenses
        prev_month_profit = prev_month_revenue - prev_month_expenses
        profit_growth = round((month_profit - prev_month_profit) / abs(prev_month_profit) * 100) if prev_month_profit != 0 else 0
        profit_margin = round(month_profit / month_revenue * 100) if month_revenue > 0 else 0
        year_profit = year_revenue - year_expenses

        # === Appointment metrics ===
        today_appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE date=?", (today_str,)).fetchone()[0]
        month_appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE date>=? AND date<?", (ms, me)).fetchone()[0]
        month_completed = conn.execute("SELECT COUNT(*) FROM appointments WHERE date>=? AND date<? AND status='completed'", (ms, me)).fetchone()[0]
        completion_rate = round(month_completed / month_appts * 100) if month_appts > 0 else 0

        # === Customer metrics ===
        total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        new_customers_month = conn.execute(
            "SELECT COUNT(DISTINCT ca.customer_id) FROM appointments a JOIN cars ca ON a.car_id=ca.id "
            "WHERE a.date>=? AND a.date<? AND ca.customer_id NOT IN "
            "(SELECT DISTINCT ca2.customer_id FROM appointments a2 JOIN cars ca2 ON a2.car_id=ca2.id WHERE a2.date<?)",
            (ms, me, ms)).fetchone()[0]
        active_customers = conn.execute(
            "SELECT COUNT(DISTINCT ca.customer_id) FROM appointments a JOIN cars ca ON a.car_id=ca.id "
            "WHERE a.date>=?", (d30,)).fetchone()[0]

        # === Unpaid / Outstanding ===
        unpaid_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status='unpaid'").fetchone()[0]
        unpaid_count = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='unpaid'").fetchone()[0]

        # === Average ticket ===
        avg_ticket = conn.execute(
            "SELECT AVG(amount) FROM invoices WHERE status='paid' AND amount>0").fetchone()[0] or 0

        # === Forecast ===
        avg_daily_rev = conn.execute(
            "SELECT COALESCE(SUM(i.amount),0)/30.0 FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
            "WHERE a.date>=? AND i.status='paid'", (d30,)).fetchone()[0]
        avg_daily_exp = conn.execute(
            "SELECT COALESCE(SUM(amount),0)/30.0 FROM expenses WHERE date>=?", (d30,)).fetchone()[0]
        if today.month == 12:
            last_day = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(today.year, today.month + 1, 1) - timedelta(days=1)
        days_remaining = (last_day - today).days
        forecast_revenue = float(month_revenue) + float(avg_daily_rev) * days_remaining
        forecast_profit = forecast_revenue - (float(month_expenses) + float(avg_daily_exp) * days_remaining)

        # === Top services (this month) ===
        top_services = conn.execute(
            "SELECT a.service, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) "
            "FROM appointments a LEFT JOIN invoices i ON i.appointment_id=a.id AND i.status='paid' "
            "WHERE a.date>=? AND a.date<? GROUP BY a.service ORDER BY cnt DESC LIMIT 5", (ms, me)).fetchall()

        # === Top customers (this month) ===
        top_customers = conn.execute(
            "SELECT cu.name, COUNT(*) as visits, COALESCE(SUM(i.amount),0) as total "
            "FROM customers cu JOIN cars ca ON ca.customer_id=cu.id "
            "JOIN appointments a ON a.car_id=ca.id LEFT JOIN invoices i ON i.appointment_id=a.id AND i.status='paid' "
            "WHERE a.date>=? AND a.date<? GROUP BY cu.id ORDER BY total DESC LIMIT 5", (ms, me)).fetchall()

        # === Revenue by day of week (last 30 days) ===
        dow_revenue = conn.execute(
            "SELECT CASE CAST(strftime('%%w', a.date) AS INTEGER) "
            "WHEN 0 THEN 'Dim' WHEN 1 THEN 'Lun' WHEN 2 THEN 'Mar' WHEN 3 THEN 'Mer' "
            "WHEN 4 THEN 'Jeu' WHEN 5 THEN 'Ven' WHEN 6 THEN 'Sam' END as dow, "
            "COALESCE(SUM(i.amount),0) "
            "FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
            "WHERE a.date>=? AND i.status='paid' GROUP BY strftime('%%w', a.date) ORDER BY strftime('%%w', a.date)", (d30,)).fetchall()

        # === Payment methods breakdown ===
        payment_methods = conn.execute(
            "SELECT method, COUNT(*), COALESCE(SUM(amount),0) FROM payments "
            "WHERE paid_at>=? GROUP BY method ORDER BY SUM(amount) DESC", (ms,)).fetchall()

        # === Expense categories (this month) ===
        expense_cats = conn.execute(
            "SELECT category, COALESCE(SUM(amount),0) FROM expenses "
            "WHERE date>=? AND date<? GROUP BY category ORDER BY SUM(amount) DESC LIMIT 6", (ms, me)).fetchall()

        # === Daily revenue trend (last 14 days) ===
        daily_trend = []
        for i in range(13, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            rev = conn.execute(
                "SELECT COALESCE(SUM(i.amount),0) FROM invoices i JOIN appointments a ON i.appointment_id=a.id "
                "WHERE a.date=? AND i.status='paid'", (d,)).fetchone()[0]
            daily_trend.append({'date': d, 'revenue': float(rev)})

        # === Online bookings ===
        pending_bookings = conn.execute("SELECT COUNT(*) FROM online_bookings WHERE status='pending'").fetchone()[0]
        month_bookings = conn.execute(
            "SELECT COUNT(*) FROM online_bookings WHERE date(created_at)>=?", (ms,)).fetchone()[0]

        # === Inventory alerts ===
        low_stock = conn.execute(
            "SELECT COUNT(*) FROM inventory WHERE quantity <= min_quantity").fetchone()[0]

    ceo = {
        'today_revenue': float(today_revenue),
        'week_revenue': float(week_revenue),
        'month_revenue': float(month_revenue),
        'prev_month_revenue': float(prev_month_revenue),
        'year_revenue': float(year_revenue),
        'revenue_growth': revenue_growth,
        'month_expenses': float(month_expenses),
        'year_expenses': float(year_expenses),
        'month_profit': float(month_profit),
        'profit_growth': profit_growth,
        'profit_margin': profit_margin,
        'year_profit': float(year_profit),
        'today_appts': today_appts,
        'month_appts': month_appts,
        'completion_rate': completion_rate,
        'total_customers': total_customers,
        'new_customers': new_customers_month,
        'active_customers': active_customers,
        'unpaid_total': float(unpaid_total),
        'unpaid_count': unpaid_count,
        'avg_ticket': round(float(avg_ticket), 1),
        'avg_daily_revenue': round(float(avg_daily_rev), 1),
        'forecast_revenue': round(forecast_revenue),
        'forecast_profit': round(forecast_profit),
        'days_remaining': days_remaining,
        'top_services': [{'name': s[0], 'count': s[1], 'revenue': float(s[2])} for s in top_services],
        'top_customers': [{'name': c[0], 'visits': c[1], 'revenue': float(c[2])} for c in top_customers],
        'dow_revenue': [{'day': d[0], 'revenue': float(d[1])} for d in dow_revenue],
        'payment_methods': [{'method': p[0] or 'Espèces', 'count': p[1], 'amount': float(p[2])} for p in payment_methods],
        'expense_cats': [{'name': c[0] or 'Autre', 'amount': float(c[1])} for c in expense_cats],
        'daily_trend': daily_trend,
        'pending_bookings': pending_bookings,
        'month_bookings': month_bookings,
        'low_stock': low_stock,
    }
    month_names = ["Janvier","Février","Mars","Avril","Mai","Juin","Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
    return render_template("ceo_dashboard.html", ceo=ceo, month_label=f"{month_names[today.month-1]} {today.year}")


# ─── Advanced Financial Reports ───
@reports_bp.route("/reports")
@login_required
def reports():
    from datetime import date
    with get_db() as conn:
        today = date.today()
        # Monthly comparison - last 6 months
        months_data = []
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
            appts = conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE date >= ? AND date < ?", (ms, me)).fetchone()[0]
            month_names = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
            months_data.append({
                'label': f"{month_names[m-1]} {y}", 'revenue': rev,
                'expenses': exp, 'profit': rev - exp, 'appointments': appts
            })
        # Top 5 most profitable services
        top_services = conn.execute(
            "SELECT a.service, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) as total "
            "FROM appointments a LEFT JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' "
            "GROUP BY a.service ORDER BY total DESC LIMIT 5"
        ).fetchall()
        # Top 5 spending customers
        top_customers = conn.execute(
            "SELECT cu.id, cu.name, COALESCE(SUM(i.amount),0) as total, COUNT(DISTINCT a.id) as visits "
            "FROM customers cu JOIN cars ca ON ca.customer_id = cu.id "
            "JOIN appointments a ON a.car_id = ca.id "
            "LEFT JOIN invoices i ON i.appointment_id = a.id AND i.status = 'paid' "
            "GROUP BY cu.id ORDER BY total DESC LIMIT 5"
        ).fetchall()
        # Payment method breakdown
        payment_methods = conn.execute(
            "SELECT COALESCE(payment_method, 'N/A'), COUNT(*), COALESCE(SUM(amount),0) "
            "FROM invoices WHERE status = 'paid' GROUP BY payment_method ORDER BY SUM(amount) DESC"
        ).fetchall()
        # Invoice stats
        total_paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status = 'paid'").fetchone()[0]
        total_unpaid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status = 'unpaid'").fetchone()[0]
        total_partial = conn.execute("SELECT COALESCE(SUM(amount - COALESCE(paid_amount,0)),0) FROM invoices WHERE status = 'partial'").fetchone()[0]
    return render_template("reports.html", months_data=months_data, top_services=top_services,
                           top_customers=top_customers, payment_methods=payment_methods,
                           total_paid=total_paid, total_unpaid=total_unpaid, total_partial=total_partial)



# ─── Phase 6 Feature 1: Advanced PDF Reports ───
@reports_bp.route("/advanced_report")
@login_required
def advanced_report():
    from datetime import date, timedelta
    period = request.args.get('period', 'month')
    today = date.today()
    if period == 'year':
        start = f"{today.year}-01-01"
        title = f"Rapport Annuel {today.year}"
    else:
        start = f"{today.year}-{today.month:02d}-01"
        title = f"Rapport Mensuel {today.strftime('%B %Y')}"
    end = today.isoformat()
    with get_db() as conn:
        revenue = conn.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status='paid' AND created_at BETWEEN ? AND ?", (start, end)).fetchone()[0]
        expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date BETWEEN ? AND ?", (start, end)).fetchone()[0]
        appt_count = conn.execute("SELECT COUNT(*) FROM appointments WHERE date BETWEEN ? AND ?", (start, end)).fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM appointments WHERE date BETWEEN ? AND ? AND status='completed'", (start, end)).fetchone()[0]
        new_customers = conn.execute("SELECT COUNT(*) FROM customers WHERE id IN (SELECT DISTINCT ca.customer_id FROM cars ca JOIN appointments a ON a.car_id=ca.id WHERE a.date BETWEEN ? AND ?)", (start, end)).fetchone()[0]
        top_services = conn.execute("SELECT service, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) FROM appointments a LEFT JOIN invoices i ON i.appointment_id=a.id AND i.status='paid' WHERE a.date BETWEEN ? AND ? GROUP BY a.service ORDER BY cnt DESC LIMIT 10", (start, end)).fetchall()
        top_customers = conn.execute("SELECT cu.name, COUNT(*) as cnt, COALESCE(SUM(i.amount),0) FROM appointments a JOIN cars ca ON a.car_id=ca.id JOIN customers cu ON ca.customer_id=cu.id LEFT JOIN invoices i ON i.appointment_id=a.id AND i.status='paid' WHERE a.date BETWEEN ? AND ? GROUP BY cu.id ORDER BY cnt DESC LIMIT 10", (start, end)).fetchall()
        monthly_rev = conn.execute("SELECT strftime('%Y-%m', created_at) as m, SUM(amount) FROM invoices WHERE status='paid' AND created_at >= date(?, '-12 months') GROUP BY m ORDER BY m", (end,)).fetchall()
    data = {
        'title': title, 'period': period, 'start': start, 'end': end,
        'revenue': revenue, 'expenses': expenses, 'profit': revenue - expenses,
        'appt_count': appt_count, 'completed': completed, 'new_customers': new_customers,
        'completion_rate': round(completed/appt_count*100) if appt_count else 0,
        'top_services': [tuple(r) for r in top_services], 'top_customers': [tuple(r) for r in top_customers], 'monthly_rev': [tuple(r) for r in monthly_rev]
    }
    fmt = request.args.get('format', 'html')
    if fmt == 'pdf':
        from xhtml2pdf import pisa
        html = render_template("advanced_report.html", data=data, pdf_mode=True)
        result = io.BytesIO()
        pisa.CreatePDF(io.BytesIO(html.encode('utf-8')), dest=result)
        result.seek(0)
        response = make_response(result.read())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=rapport_{period}_{end}.pdf'
        return response
    return render_template("advanced_report.html", data=data, pdf_mode=False)




@reports_bp.route("/end_of_day")
@login_required
def end_of_day_report():
    from datetime import date as d, timedelta
    day = request.args.get("date", str(d.today()))
    with get_db() as conn:
        appointments = conn.execute("""SELECT a.id, a.service, a.status, a.time, 
            c.name as customer_name, car.brand, car.model, car.plate
            FROM appointments a
            LEFT JOIN cars car ON a.car_id=car.id
            LEFT JOIN customers c ON car.customer_id=c.id
            WHERE a.date=? ORDER BY a.time""", (day,)).fetchall()
        
        revenue_paid = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
            JOIN appointments a ON i.appointment_id=a.id
            WHERE a.date=? AND i.status='paid'""", (day,)).fetchone()[0]
        revenue_unpaid = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
            JOIN appointments a ON i.appointment_id=a.id
            WHERE a.date=? AND i.status='unpaid'""", (day,)).fetchone()[0]
        expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date=?", (day,)).fetchone()[0]
        
        stats = {
            'total_appointments': len(appointments),
            'completed': sum(1 for a in appointments if a['status'] == 'completed'),
            'in_progress': sum(1 for a in appointments if a['status'] == 'in_progress'),
            'pending': sum(1 for a in appointments if a['status'] == 'pending'),
            'cancelled': sum(1 for a in appointments if a['status'] == 'cancelled'),
            'revenue_paid': revenue_paid,
            'revenue_unpaid': revenue_unpaid,
            'expenses': expenses,
            'profit': revenue_paid - expenses,
        }
        
        by_service = conn.execute("""SELECT a.service, COUNT(*) as count,
            COALESCE(SUM(CASE WHEN i.status='paid' THEN i.amount ELSE 0 END),0) as revenue
            FROM appointments a LEFT JOIN invoices i ON a.id=i.appointment_id
            WHERE a.date=? GROUP BY a.service ORDER BY revenue DESC""", (day,)).fetchall()
        
        by_employee = conn.execute("""SELECT COALESCE(u.full_name, u.username, 'Non assigné') as name,
            COUNT(*) as count, SUM(CASE WHEN a.status='completed' THEN 1 ELSE 0 END) as completed
            FROM appointments a LEFT JOIN users u ON a.assigned_employee_id=u.id
            WHERE a.date=? GROUP BY a.assigned_employee_id""", (day,)).fetchall()
        
        payment_methods = conn.execute("""SELECT COALESCE(i.payment_method,'cash') as method,
            COUNT(*) as count, SUM(i.amount) as total
            FROM invoices i JOIN appointments a ON i.appointment_id=a.id
            WHERE a.date=? AND i.status='paid' GROUP BY i.payment_method""", (day,)).fetchall()
        
        shop = get_all_settings()
    return render_template("end_of_day.html", appointments=appointments, stats=stats,
                          by_service=by_service, by_employee=by_employee, 
                          payment_methods=payment_methods, day=day, shop=shop)





# ─── 18.6 Daily Technician Work Summary ─────────────────────────────────────
