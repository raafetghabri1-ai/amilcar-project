"""
AMILCAR — Team, Employees & Performance
Blueprint: team_bp
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

team_bp = Blueprint("team_bp", __name__)


# ─── Technician Performance ───
@team_bp.route("/technician_performance")
@admin_required
def technician_performance():
    with get_db() as conn:
        users = conn.execute("SELECT id, username, COALESCE(full_name, '') FROM users ORDER BY id").fetchall()
        performance = []
        for u in users:
            username = u[1]
            full_name = u[2]
            total_jobs = conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE assigned_to = ?", (username,)).fetchone()[0]
            completed_jobs = conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE assigned_to = ? AND status = 'completed'", (username,)).fetchone()[0]
            in_progress = conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE assigned_to = ? AND status = 'in_progress'", (username,)).fetchone()[0]
            revenue = conn.execute(
                "SELECT COALESCE(SUM(i.amount), 0) FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
                "WHERE a.assigned_to = ? AND i.status = 'paid'", (username,)).fetchone()[0]
            performance.append({
                'username': username, 'full_name': full_name,
                'total': total_jobs, 'completed': completed_jobs,
                'in_progress': in_progress, 'revenue': revenue
            })
    return render_template("technician_performance.html", performance=performance)



# ─── Feature 6: Employee Time Tracking ───
@team_bp.route("/time_tracking")
@login_required
def time_tracking():
    from datetime import date
    today = str(date.today())
    with get_db() as conn:
        users = conn.execute("SELECT id, username, COALESCE(full_name, '') FROM users ORDER BY id").fetchall()
        today_logs = conn.execute(
            "SELECT t.id, t.username, t.action, t.timestamp, t.date "
            "FROM time_tracking t WHERE t.date = ? ORDER BY t.timestamp DESC", (today,)).fetchall()
        # Current status for each user
        user_status = {}
        for u in users:
            last = conn.execute(
                "SELECT action, timestamp FROM time_tracking WHERE user_id = ? AND date = ? ORDER BY timestamp DESC LIMIT 1",
                (u[0], today)).fetchone()
            if last:
                user_status[u[0]] = {'action': last[0], 'time': last[1]}
            else:
                user_status[u[0]] = {'action': 'out', 'time': None}
    return render_template("time_tracking.html", users=users, today_logs=today_logs,
                           user_status=user_status, today=today)



@team_bp.route("/clock_in_out", methods=["POST"])
@login_required
def clock_in_out():
    from datetime import date, datetime
    user_id = request.form.get("user_id", session.get('user_id'))
    action = request.form.get("action", "clock_in")
    if action not in ('clock_in', 'clock_out', 'break_start', 'break_end'):
        action = 'clock_in'
    today = str(date.today())
    with get_db() as conn:
        username = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        if username:
            conn.execute("INSERT INTO time_tracking (user_id, username, action, date) VALUES (?,?,?,?)",
                (user_id, username[0], action, today))
            conn.commit()
    action_labels = {'clock_in': 'Entrée', 'clock_out': 'Sortie', 'break_start': 'Pause début', 'break_end': 'Pause fin'}
    flash(f"{action_labels.get(action, action)} enregistré(e)", "success")
    return redirect("/time_tracking")



@team_bp.route("/manage_roles/update/<int:user_id>", methods=["POST"])
@login_required
def update_user_role(user_id):
    if session.get('role') != 'admin':
        flash("Accès refusé", "error")
        return redirect("/")
    new_role = request.form.get("role", "employee")
    if new_role not in PERMISSIONS and new_role != 'employee':
        new_role = 'employee'
    with get_db() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        conn.commit()
    log_activity('Role Updated', f'User #{user_id} → {new_role}')
    flash(f"Rôle mis à jour: {new_role}", "success")
    return redirect("/manage_roles")



# ─── 4. Employee Shifts Management ───
@team_bp.route("/employee_shifts")
@login_required
@admin_required
def employee_shifts():
    week_offset = request.args.get("week", 0, type=int)
    from datetime import date, timedelta
    today = date.today()
    start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    end = start + timedelta(days=6)
    with get_db() as conn:
        users = conn.execute("SELECT id, username, full_name FROM users WHERE role != 'admin' ORDER BY username").fetchall()
        shifts = conn.execute("SELECT * FROM employee_shifts WHERE shift_date BETWEEN ? AND ? ORDER BY shift_date, start_time",
                             (start.isoformat(), end.isoformat())).fetchall()
        leaves = conn.execute("SELECT * FROM employee_leaves WHERE start_date <= ? AND end_date >= ? ORDER BY start_date",
                             (end.isoformat(), start.isoformat())).fetchall()
    days = [(start + timedelta(days=i)) for i in range(7)]
    return render_template("employee_shifts.html", users=users, shifts=shifts, leaves=leaves,
                          days=days, start=start, end=end, week_offset=week_offset)



@team_bp.route("/employee_shifts/add", methods=["POST"])
@login_required
@admin_required
def employee_shift_add():
    uid = request.form.get("user_id", type=int)
    shift_date = request.form.get("shift_date", "")
    start_time = request.form.get("start_time", "08:00")
    end_time = request.form.get("end_time", "17:00")
    shift_type = request.form.get("shift_type", "normal")
    notes = request.form.get("notes", "")
    if uid and shift_date:
        with get_db() as conn:
            uname = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            conn.execute("INSERT INTO employee_shifts (user_id, username, shift_date, start_time, end_time, shift_type, notes) VALUES (?,?,?,?,?,?,?)",
                        (uid, uname[0] if uname else '', shift_date, start_time, end_time, shift_type, notes))
            conn.commit()
        flash("Shift ajouté !", "success")
    return redirect("/employee_shifts")



@team_bp.route("/employee_shifts/delete/<int:sid>", methods=["POST"])
@login_required
@admin_required
def employee_shift_delete(sid):
    with get_db() as conn:
        conn.execute("DELETE FROM employee_shifts WHERE id=?", (sid,))
        conn.commit()
    flash("Shift supprimé", "success")
    return redirect("/employee_shifts")



@team_bp.route("/employee_leave/add", methods=["POST"])
@login_required
@admin_required
def employee_leave_add():
    uid = request.form.get("user_id", type=int)
    leave_type = request.form.get("leave_type", "annual")
    start_date = request.form.get("start_date", "")
    end_date = request.form.get("end_date", "")
    reason = request.form.get("reason", "")
    if uid and start_date and end_date:
        with get_db() as conn:
            uname = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            conn.execute("INSERT INTO employee_leaves (user_id, username, leave_type, start_date, end_date, reason) VALUES (?,?,?,?,?,?)",
                        (uid, uname[0] if uname else '', leave_type, start_date, end_date, reason))
            conn.commit()
        flash("Congé enregistré !", "success")
    return redirect("/employee_shifts")



@team_bp.route("/employee_leave/approve/<int:lid>/<action>", methods=["POST"])
@login_required
@admin_required
def employee_leave_action(lid, action):
    if action in ('approved', 'rejected'):
        with get_db() as conn:
            conn.execute("UPDATE employee_leaves SET status=? WHERE id=?", (action, lid))
            conn.commit()
    return redirect("/employee_shifts")



# ─── 9. Staff Notes ───
@team_bp.route("/staff_notes")
@login_required
def staff_notes():
    entity_type = request.args.get("type", "customer")
    entity_id = request.args.get("id", type=int)
    with get_db() as conn:
        notes = []
        entity_name = ""
        if entity_type and entity_id:
            notes = conn.execute("""SELECT * FROM staff_notes WHERE entity_type=? AND entity_id=?
                ORDER BY created_at DESC""", (entity_type, entity_id)).fetchall()
            if entity_type == 'customer':
                row = conn.execute("SELECT name FROM customers WHERE id=?", (entity_id,)).fetchone()
                entity_name = row[0] if row else ""
            elif entity_type == 'car':
                row = conn.execute("SELECT brand, model, plate FROM cars WHERE id=?", (entity_id,)).fetchone()
                entity_name = f"{row[0]} {row[1]} ({row[2]})" if row else ""
        recent = conn.execute("""SELECT sn.*, CASE sn.entity_type
            WHEN 'customer' THEN (SELECT name FROM customers WHERE id=sn.entity_id)
            WHEN 'car' THEN (SELECT brand||' '||model||' ('||plate||')' FROM cars WHERE id=sn.entity_id)
            ELSE '' END as entity_name
            FROM staff_notes sn ORDER BY sn.created_at DESC LIMIT 50""").fetchall()
    return render_template("staff_notes.html", notes=notes, recent=recent,
                          entity_type=entity_type, entity_id=entity_id, entity_name=entity_name)



@team_bp.route("/staff_note/add", methods=["POST"])
@login_required
def staff_note_add():
    entity_type = request.form.get("entity_type", "customer")
    entity_id = request.form.get("entity_id", type=int)
    note = request.form.get("note", "").strip()
    priority = request.form.get("priority", "normal")
    if entity_id and note:
        with get_db() as conn:
            conn.execute("INSERT INTO staff_notes (entity_type, entity_id, user_id, username, note, priority) VALUES (?,?,?,?,?,?)",
                        (entity_type, entity_id, session.get('user_id', 0),
                         session.get('username', ''), note, priority))
            conn.commit()
        flash("Note ajoutée !", "success")
    return redirect(f"/staff_notes?type={entity_type}&id={entity_id}")



@team_bp.route("/staff_note/delete/<int:nid>", methods=["POST"])
@login_required
def staff_note_delete(nid):
    with get_db() as conn:
        note = conn.execute("SELECT entity_type, entity_id FROM staff_notes WHERE id=?", (nid,)).fetchone()
        conn.execute("DELETE FROM staff_notes WHERE id=?", (nid,))
        conn.commit()
    if note:
        return redirect(f"/staff_notes?type={note[0]}&id={note[1]}")
    return redirect("/staff_notes")



# ─── 5. Employee Targets & Commissions ───
@team_bp.route("/employee_targets")
@login_required
@admin_required
def employee_targets_page():
    from datetime import date
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    with get_db() as conn:
        users = conn.execute("SELECT id, username, full_name, commission_rate FROM users WHERE role != 'admin' ORDER BY username").fetchall()
        targets = conn.execute("SELECT * FROM employee_targets WHERE month=?", (month,)).fetchall()
        # Calculate actuals
        for u in users:
            actual_rev = conn.execute("""SELECT COALESCE(SUM(i.amount),0) FROM invoices i
                JOIN appointments a ON i.appointment_id=a.id
                WHERE a.assigned_to=? AND strftime('%%Y-%%m', a.date)=? AND i.status='paid'""",
                (u[1], month)).fetchone()[0]
            actual_jobs = conn.execute("""SELECT COUNT(*) FROM appointments
                WHERE assigned_to=? AND strftime('%%Y-%%m', date)=? AND status='completed'""",
                (u[1], month)).fetchone()[0]
            existing = conn.execute("SELECT id FROM employee_targets WHERE user_id=? AND month=?", (u[0], month)).fetchone()
            if existing:
                conn.execute("UPDATE employee_targets SET actual_revenue=?, actual_jobs=?, commission_earned=actual_revenue*commission_rate/100 WHERE id=?",
                            (actual_rev, actual_jobs, existing[0]))
            else:
                rate = u[3] or 0
                conn.execute("INSERT INTO employee_targets (user_id, username, month, actual_revenue, actual_jobs, commission_rate) VALUES (?,?,?,?,?,?)",
                            (u[0], u[1], month, actual_rev, actual_jobs, rate))
        conn.commit()
        targets = conn.execute("SELECT * FROM employee_targets WHERE month=? ORDER BY actual_revenue DESC", (month,)).fetchall()
    return render_template("employee_targets.html", users=users, targets=targets, month=month)



@team_bp.route("/employee_target/set", methods=["POST"])
@login_required
@admin_required
def employee_target_set():
    uid = request.form.get("user_id", type=int)
    month = request.form.get("month", "")
    target_rev = request.form.get("target_revenue", 0, type=float)
    target_jobs = request.form.get("target_jobs", 0, type=int)
    comm_rate = request.form.get("commission_rate", 0, type=float)
    bonus = request.form.get("bonus", 0, type=float)
    if uid and month:
        with get_db() as conn:
            uname = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            conn.execute("""INSERT INTO employee_targets (user_id, username, month, target_revenue, target_jobs, commission_rate, bonus)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id, month) DO UPDATE SET
                target_revenue=?, target_jobs=?, commission_rate=?, bonus=?""",
                (uid, uname[0] if uname else '', month, target_rev, target_jobs, comm_rate, bonus,
                 target_rev, target_jobs, comm_rate, bonus))
            conn.execute("UPDATE users SET commission_rate=? WHERE id=?", (comm_rate, uid))
            conn.commit()
        flash("Objectif mis à jour !", "success")
    return redirect(f"/employee_targets?month={month}")



# ─── 9. Vue Mobile Technicien ───

@team_bp.route("/tech_mobile")
@login_required
def tech_mobile():
    from datetime import date, timedelta
    today = date.today().isoformat()
    user_id = session.get('user_id')
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        user_name = user['full_name'] or user['username'] if user else ''
        # Today's work orders
        orders = conn.execute("""SELECT a.*, c.plate, c.brand, c.model, cu.name as customer_name, cu.phone
            FROM appointments a 
            JOIN cars c ON a.car_id=c.id 
            JOIN customers cu ON c.customer_id=cu.id 
            WHERE a.date=? AND (a.assigned_to=? OR a.assigned_to=?)
            ORDER BY CASE a.status WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END, a.time ASC""",
            (today, user_name, str(user_id))).fetchall()
        # Time tracking
        time_entry = conn.execute("SELECT * FROM time_tracking WHERE user_id=? AND date=?", (user_id, today)).fetchone()
        # Stats
        completed_today = sum(1 for o in orders if o['status'] == 'completed')
        in_progress = sum(1 for o in orders if o['status'] == 'in_progress')
        pending = sum(1 for o in orders if o['status'] == 'pending')
        # Weekly stats
        week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        week_completed = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE (assigned_to=? OR assigned_to=?) AND date>=? AND status='completed'",
            (user_name, str(user_id), week_start)).fetchone()[0]
        week_hours = conn.execute(
            "SELECT SUM(CASE WHEN clock_in IS NOT NULL AND clock_out IS NOT NULL "
            "THEN (CAST(SUBSTR(clock_out,1,2) AS REAL)*60+CAST(SUBSTR(clock_out,4,2) AS REAL) "
            "- CAST(SUBSTR(clock_in,1,2) AS REAL)*60-CAST(SUBSTR(clock_in,4,2) AS REAL))/60 ELSE 0 END) "
            "FROM time_tracking WHERE user_id=? AND date>=?", (user_id, week_start)).fetchone()[0] or 0
    return render_template("tech_mobile.html", orders=orders, user=user, time_entry=time_entry,
                          completed=completed_today, in_progress=in_progress, pending=pending,
                          today=today, weekly_stats={'completed': week_completed, 'hours': f'{week_hours:.1f}'})



@team_bp.route("/tech_mobile/update_status/<int:appt_id>", methods=["POST"])
@login_required
def tech_update_status(appt_id):
    status = request.form.get("status", "")
    valid_statuses = ['pending', 'in_progress', 'completed', 'cancelled']
    if status in valid_statuses:
        with get_db() as conn:
            conn.execute("UPDATE appointments SET status=? WHERE id=?", (status, appt_id))
            conn.commit()
        flash(f"Statut mis à jour: {status}", "success")
    return redirect("/tech_mobile")



@team_bp.route("/tech_mobile/clock", methods=["POST"])
@login_required
def tech_clock():
    from datetime import date, datetime
    action = request.form.get("action", "")
    user_id = session.get('user_id')
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    with get_db() as conn:
        entry = conn.execute("SELECT * FROM time_tracking WHERE user_id=? AND date=?", (user_id, today)).fetchone()
        if action == "clock_in":
            if not entry:
                conn.execute("INSERT INTO time_tracking (user_id, date, clock_in) VALUES (?,?,?)", (user_id, today, now))
            else:
                conn.execute("UPDATE time_tracking SET clock_in=? WHERE id=?", (now, entry['id']))
        elif action == "clock_out" and entry:
            conn.execute("UPDATE time_tracking SET clock_out=? WHERE id=?", (now, entry['id']))
        conn.commit()
    return redirect("/tech_mobile")


@team_bp.route("/tech_mobile/note/<int:appt_id>", methods=["POST"])
@login_required
def tech_add_note(appt_id):
    note = request.form.get("tech_note", "").strip()
    if note:
        with get_db() as conn:
            existing = conn.execute("SELECT notes FROM appointments WHERE id=?", (appt_id,)).fetchone()
            current = existing['notes'] or '' if existing else ''
            prefix = f"[{session.get('username', 'tech')}] "
            new_notes = f"{current}\n{prefix}{note}".strip() if current else f"{prefix}{note}"
            conn.execute("UPDATE appointments SET notes=? WHERE id=?", (new_notes, appt_id))
            conn.commit()
        flash("Note ajoutée", "success")
    return redirect("/tech_mobile")



# ─── 1. Système de Réclamations & Tickets ───

@team_bp.route("/tickets")
@login_required
def tickets():
    from datetime import datetime
    with get_db() as conn:
        all_tickets = conn.execute("""SELECT t.*, cu.name as customer_name, cu.phone,
            u.full_name as assigned_name
            FROM tickets t 
            JOIN customers cu ON t.customer_id=cu.id 
            LEFT JOIN users u ON t.assigned_to=u.id
            ORDER BY CASE t.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, 
            t.created_at DESC""").fetchall()
        now = datetime.now().isoformat()
        stats = {
            'total': len(all_tickets),
            'open': sum(1 for t in all_tickets if t['status'] == 'open'),
            'in_progress': sum(1 for t in all_tickets if t['status'] == 'in_progress'),
            'resolved': sum(1 for t in all_tickets if t['status'] in ('resolved', 'closed')),
            'overdue': sum(1 for t in all_tickets if t['sla_deadline'] and t['sla_deadline'] < now and t['status'] not in ('resolved', 'closed')),
            'avg_satisfaction': 0,
        }
        sat = conn.execute("SELECT AVG(satisfaction_score) FROM tickets WHERE satisfaction_score > 0").fetchone()[0]
        stats['avg_satisfaction'] = sat or 0
        customers = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
        staff = conn.execute("SELECT id, full_name, username FROM users WHERE role IN ('admin','employee')").fetchall()
    return render_template("tickets.html", tickets=all_tickets, stats=stats, customers=customers, staff=staff)



@team_bp.route("/ticket/add", methods=["POST"])
@login_required
def add_ticket():
    from datetime import datetime, timedelta
    customer_id = request.form.get("customer_id", type=int)
    subject = request.form.get("subject", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "general")
    priority = request.form.get("priority", "medium")
    assigned_to = request.form.get("assigned_to", 0, type=int)
    sla_map = {'urgent': 24, 'high': 48, 'medium': 72, 'low': 120}
    sla_hours = sla_map.get(priority, 72)
    sla_deadline = (datetime.now() + timedelta(hours=sla_hours)).isoformat()
    if customer_id and subject:
        with get_db() as conn:
            conn.execute("""INSERT INTO tickets 
                (customer_id, subject, description, category, priority, sla_hours, sla_deadline, assigned_to)
                VALUES (?,?,?,?,?,?,?,?)""",
                (customer_id, subject, description, category, priority, sla_hours, sla_deadline, assigned_to))
            conn.commit()
        flash("Ticket créé !", "success")
    return redirect("/tickets")



@team_bp.route("/ticket/<int:tid>")
@login_required
def view_ticket(tid):
    with get_db() as conn:
        ticket = conn.execute("""SELECT t.*, cu.name as customer_name, cu.phone, cu.email,
            u.full_name as assigned_name
            FROM tickets t JOIN customers cu ON t.customer_id=cu.id 
            LEFT JOIN users u ON t.assigned_to=u.id WHERE t.id=?""", (tid,)).fetchone()
        if not ticket:
            flash("Ticket introuvable", "danger")
            return redirect("/tickets")
        messages = conn.execute("""SELECT tm.*, 
            CASE WHEN tm.sender_type='staff' THEN u.full_name ELSE cu.name END as sender_name
            FROM ticket_messages tm 
            LEFT JOIN users u ON tm.sender_type='staff' AND tm.sender_id=u.id
            LEFT JOIN customers cu ON tm.sender_type='customer' AND tm.sender_id=cu.id
            WHERE tm.ticket_id=? ORDER BY tm.created_at ASC""", (tid,)).fetchall()
        staff = conn.execute("SELECT id, full_name, username FROM users WHERE role IN ('admin','employee')").fetchall()
    return render_template("ticket_detail.html", ticket=ticket, messages=messages, staff=staff)



@team_bp.route("/ticket/<int:tid>/reply", methods=["POST"])
@login_required
def reply_ticket(tid):
    message = request.form.get("message", "").strip()
    if message:
        with get_db() as conn:
            conn.execute("INSERT INTO ticket_messages (ticket_id, sender_type, sender_id, message) VALUES (?,?,?,?)",
                        (tid, 'staff', session.get('user_id', 0), message))
            conn.execute("UPDATE tickets SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (tid,))
            conn.commit()
    return redirect(f"/ticket/{tid}")



@team_bp.route("/ticket/<int:tid>/update", methods=["POST"])
@login_required
def update_ticket(tid):
    from datetime import datetime
    status = request.form.get("status", "")
    assigned = request.form.get("assigned_to", 0, type=int)
    satisfaction = request.form.get("satisfaction_score", 0, type=int)
    resolution = request.form.get("resolution", "").strip()
    with get_db() as conn:
        updates = ["updated_at=CURRENT_TIMESTAMP"]
        params = []
        if status:
            updates.append("status=?"); params.append(status)
            if status in ('resolved', 'closed'):
                updates.append("closed_at=?"); params.append(datetime.now().isoformat())
        if assigned:
            updates.append("assigned_to=?"); params.append(assigned)
        if satisfaction:
            updates.append("satisfaction_score=?"); params.append(satisfaction)
        if resolution:
            updates.append("resolution=?"); params.append(resolution)
        params.append(tid)
        conn.execute(f"UPDATE tickets SET {','.join(updates)} WHERE id=?", params)
        conn.commit()
    flash("Ticket mis à jour", "success")
    return redirect(f"/ticket/{tid}")



# ─── 8. Chat Interne ───

@team_bp.route("/team_chat")
@login_required
def team_chat():
    channel = request.args.get("channel", "general")
    with get_db() as conn:
        messages = conn.execute("""SELECT tm.*, u.full_name, u.username 
            FROM team_messages tm JOIN users u ON tm.sender_id=u.id 
            WHERE tm.channel=? ORDER BY tm.created_at DESC LIMIT 100""", (channel,)).fetchall()
        messages = list(reversed(messages))
        users = conn.execute("SELECT id, full_name, username FROM users ORDER BY full_name").fetchall()
        channels = ['general', 'technique', 'admin', 'urgent']
        # Mark as read
        conn.execute("""UPDATE team_messages SET is_read=1 
            WHERE channel=? AND recipient_id IN (0, ?)""", (channel, session.get('user_id', 0)))
        conn.commit()
    return render_template("team_chat.html", messages=messages, users=users,
                          channels=channels, current_channel=channel)



@team_bp.route("/team_chat/send", methods=["POST"])
@login_required
def send_team_message():
    channel = request.form.get("channel", "general")
    message = request.form.get("message", "").strip()
    if message:
        with get_db() as conn:
            conn.execute("INSERT INTO team_messages (sender_id, channel, message) VALUES (?,?,?)",
                        (session.get('user_id', 0), channel, message))
            conn.commit()
    return redirect(f"/team_chat?channel={channel}")



# ─── 3. Gamification Employés ───

@team_bp.route("/employee_gamification")
@login_required
def employee_gamification_view():
    from datetime import date
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    with get_db() as conn:
        employees = conn.execute("SELECT * FROM users WHERE role IN ('employee','admin') ORDER BY full_name").fetchall()
        leaderboard = []
        for emp in employees:
            stats = conn.execute("""SELECT COUNT(*) as completed,
                COALESCE(SUM(i.amount), 0) as revenue
                FROM appointments a
                LEFT JOIN invoices i ON a.id = i.appointment_id AND i.status='paid'
                WHERE a.assigned_employee_id=? AND a.status='Terminé'
                AND strftime('%%Y-%%m', a.date)=?""", (emp['id'], month)).fetchone()
            avg_rating = conn.execute("""SELECT AVG(n.score) FROM nps_surveys n
                JOIN appointments a ON n.appointment_id=a.id
                WHERE a.assigned_employee_id=? AND strftime('%%Y-%%m', n.created_at)=?""",
                (emp['id'], month)).fetchone()[0] or 0
            timer_stats = conn.execute("""SELECT AVG(efficiency_pct) as avg_eff
                FROM service_timer WHERE employee_id=? AND strftime('%%Y-%%m', created_at)=?""",
                (emp['id'], month)).fetchone()
            efficiency = timer_stats['avg_eff'] if timer_stats and timer_stats['avg_eff'] else 0
            points = (stats['completed'] * 10) + int(stats['revenue'] / 100) + int(avg_rating * 5) + int(efficiency / 2)
            leaderboard.append({
                'id': emp['id'], 'name': emp['full_name'] or emp['username'], 'role': emp['role'] if 'role' in emp.keys() else '',
                'completed': stats['completed'], 'revenue': stats['revenue'],
                'avg_rating': round(avg_rating, 1), 'efficiency': round(efficiency, 1),
                'points': points, 'badges': '',
                'commission_rate': 0,
                'commission': 0,
            })
        leaderboard.sort(key=lambda x: x['points'], reverse=True)
        for i, e in enumerate(leaderboard):
            e['rank'] = i + 1
    return render_template("employee_gamification.html", leaderboard=leaderboard, month=month)



@team_bp.route("/employee_badge/<int:emp_id>", methods=["POST"])
@login_required
def employee_badge_add(emp_id):
    badge = request.form.get("badge", "")
    with get_db() as conn:
        emp = conn.execute("SELECT id FROM users WHERE id=?", (emp_id,)).fetchone()
        if emp:
            conn.commit()
    flash(f"Badge '{badge}' attribué !", "success")
    return redirect("/employee_gamification")



@team_bp.route('/commission/generate', methods=['POST'])
@login_required
def commission_generate():
    month = request.form['month']
    with get_db() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM commission_log WHERE month=?", (month,)).fetchone()[0]
        if existing > 0:
            flash("Commissions déjà générées pour ce mois", "warning")
            return redirect(f"/commission_tracker?month={month}")
        employees = conn.execute("SELECT id, full_name, commission_rate FROM users WHERE role != 'admin' AND commission_rate > 0").fetchall()
        for emp in employees:
            invoices = conn.execute("""
                SELECT i.id, i.total, a.service, a.id as appt_id
                FROM invoices i LEFT JOIN appointments a ON i.appointment_id = a.id
                WHERE a.assigned_to = ? AND i.date LIKE ? AND i.status != 'cancelled'
            """, (emp['full_name'], f"{month}%")).fetchall()
            for inv in invoices:
                commission = inv['total'] * (emp['commission_rate'] / 100)
                conn.execute("""INSERT INTO commission_log
                    (employee_id, employee_name, month, appointment_id, invoice_id,
                     service_name, invoice_total, commission_rate, commission_amount)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (emp['id'], emp['full_name'], month, inv['appt_id'] or 0, inv['id'],
                     inv['service'] or '', inv['total'], emp['commission_rate'], commission))
        conn.commit()
    flash("Commissions générées avec succès", "success")
    return redirect(f"/commission_tracker?month={month}")



@team_bp.route('/commission/pay/<int:emp_id>', methods=['POST'])
@login_required
def commission_pay(emp_id):
    month = request.form['month']
    with get_db() as conn:
        conn.execute("UPDATE commission_log SET status='paid', paid_at=? WHERE employee_id=? AND month=? AND status='pending'",
                    (datetime.now().strftime('%Y-%m-%d %H:%M'), emp_id, month))
        conn.commit()
    flash("Commissions marquées comme payées", "success")
    return redirect(f"/commission_tracker?month={month}")

# ── 4. Campaign Analytics ──
@team_bp.route('/campaign_analytics')
@login_required
def campaign_analytics():
    with get_db() as conn:
        campaigns = conn.execute("""
            SELECT mc.*, COUNT(cl.id) as message_count,
                   SUM(CASE WHEN cl.status='sent' THEN 1 ELSE 0 END) as sent_count,
                   SUM(CASE WHEN cl.status='opened' THEN 1 ELSE 0 END) as opened_count,
                   SUM(CASE WHEN cl.status='clicked' THEN 1 ELSE 0 END) as clicked_count
            FROM marketing_campaigns mc
            LEFT JOIN campaign_log cl ON mc.id = cl.campaign_id
            GROUP BY mc.id ORDER BY mc.created_at DESC
        """).fetchall()
        total_campaigns = len(campaigns)
        total_sent = sum(c['sent_count'] or 0 for c in campaigns)
        total_opened = sum(c['opened_count'] or 0 for c in campaigns)
        avg_open_rate = (total_opened / total_sent * 100) if total_sent > 0 else 0
        recent_logs = conn.execute("""
            SELECT cl.*, mc.name as campaign_name, c.name as customer_name
            FROM campaign_log cl
            LEFT JOIN marketing_campaigns mc ON cl.campaign_id = mc.id
            LEFT JOIN customers c ON cl.customer_id = c.id
            ORDER BY cl.sent_at DESC LIMIT 50
        """).fetchall()
    return render_template('campaign_analytics.html', campaigns=campaigns,
                          total_campaigns=total_campaigns, total_sent=total_sent,
                          avg_open_rate=avg_open_rate, recent_logs=recent_logs)

# ── 5. Multi-Channel Inbox ──
@team_bp.route('/channel_inbox')
@login_required
def channel_inbox():
    channel = request.args.get('channel', 'all')
    status = request.args.get('status', 'all')
    with get_db() as conn:
        query = "SELECT * FROM channel_inbox WHERE 1=1"
        params = []
        if channel != 'all':
            query += " AND channel = ?"
            params.append(channel)
        if status != 'all':
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT 200"
        messages = conn.execute(query, params).fetchall()
        stats = conn.execute("""
            SELECT channel, COUNT(*) as total,
                   SUM(CASE WHEN direction='outgoing' THEN 1 ELSE 0 END) as sent,
                   SUM(CASE WHEN direction='incoming' THEN 1 ELSE 0 END) as received,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed
            FROM channel_inbox GROUP BY channel
        """).fetchall()
        # Sync existing logs into unified inbox
        existing_count = conn.execute("SELECT COUNT(*) FROM channel_inbox").fetchone()[0]
        if existing_count == 0:
            # Import from whatsapp_logs
            wa_logs = conn.execute("SELECT * FROM whatsapp_logs LIMIT 500").fetchall()
            for log in wa_logs:
                conn.execute("""INSERT INTO channel_inbox
                    (customer_id, customer_name, channel, direction, message, status, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (log['customer_id'], '', 'whatsapp', 'outgoing',
                     log['message_text'], log['status'], log['created_at']))
            # Import from email_log
            em_logs = conn.execute("SELECT * FROM email_log LIMIT 500").fetchall()
            for log in em_logs:
                conn.execute("""INSERT INTO channel_inbox
                    (customer_id, customer_name, channel, direction, message, status, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (log['customer_id'], '', 'email', 'outgoing',
                     log['subject'], log['status'], log['sent_at']))
            conn.commit()
            messages = conn.execute(query, params).fetchall()
            stats = conn.execute("""
                SELECT channel, COUNT(*) as total,
                       SUM(CASE WHEN direction='outgoing' THEN 1 ELSE 0 END) as sent,
                       SUM(CASE WHEN direction='incoming' THEN 1 ELSE 0 END) as received,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed
                FROM channel_inbox GROUP BY channel
            """).fetchall()
    return render_template('channel_inbox.html', messages=messages, stats=stats,
                          channel=channel, status=status)


