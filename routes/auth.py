"""
AMILCAR — Authentication & User Management
Blueprint: auth_bp
Routes: 10
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

auth_bp = Blueprint("auth_bp", __name__)

_login_attempts = {}
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300

PERMISSIONS = {
    'admin': ['all'],
    'manager': ['customers', 'appointments', 'invoices', 'reports', 'inventory', 'services', 'expenses', 'team', 'calendar', 'settings'],
    'receptionist': ['customers', 'appointments', 'invoices', 'calendar', 'quotes'],
    'technician': ['appointments', 'live_board', 'time_tracking', 'gallery', 'inspections'],
    'employee': ['appointments', 'customers'],
}


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect('/')
    if request.method == 'POST':
        ip = request.remote_addr
        now = time_module.time()
        # Rate limiting check
        if ip in _login_attempts:
            attempts, first_time = _login_attempts[ip]
            if now - first_time > LOGIN_LOCKOUT_SECONDS:
                _login_attempts.pop(ip, None)
            elif attempts >= LOGIN_MAX_ATTEMPTS:
                remaining = int(LOGIN_LOCKOUT_SECONDS - (now - first_time))
                flash(f'Trop de tentatives. Réessayez dans {remaining}s', 'error')
                return render_template('login.html')
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE LOWER(username) = ?", (username,)).fetchone()
        if user and check_password_hash(user[2], password):
            _login_attempts.pop(ip, None)
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['role'] = user[3] if len(user) > 3 and user[3] else 'employee'
            # Set branch_id from users table
            with get_db() as conn:
                bid = conn.execute("SELECT COALESCE(branch_id, 0) FROM users WHERE id=?", (user[0],)).fetchone()
                session['branch_id'] = bid[0] if bid else 0
            return redirect('/')
        # Track failed attempt
        if ip in _login_attempts:
            _login_attempts[ip] = (_login_attempts[ip][0] + 1, _login_attempts[ip][1])
        else:
            _login_attempts[ip] = (1, now)
        flash('Nom d\'utilisateur ou mot de passe invalide', 'error')
    return render_template('login.html')



@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect('/login')



@auth_bp.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new_pass = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
            if not check_password_hash(user[2], current):
                flash('Mot de passe actuel incorrect', 'error')
            elif len(new_pass) < 6:
                flash('Le nouveau mot de passe doit contenir au moins 6 caractères', 'error')
            elif new_pass != confirm:
                flash('Les mots de passe ne correspondent pas', 'error')
            else:
                conn.execute("UPDATE users SET password = ? WHERE id = ?",
                    (generate_password_hash(new_pass), session['user_id']))
                conn.commit()
                flash('Mot de passe modifié avec succès', 'success')
    return render_template('change_password.html')



# ─── User Management (Admin) ───
@auth_bp.route("/users")
@admin_required
def users_list():
    with get_db() as conn:
        users = conn.execute("SELECT id, username, role, COALESCE(full_name, '') FROM users ORDER BY id").fetchall()
    return render_template("users.html", users=users)



@auth_bp.route("/add_user", methods=["GET", "POST"])
@admin_required
def add_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "employee")
        if not username or len(username) < 3:
            flash("Le nom d'utilisateur doit contenir au moins 3 caractères", "error")
            return render_template("add_user.html")
        if not password or len(password) < 6:
            flash("Le mot de passe doit contenir au moins 6 caractères", "error")
            return render_template("add_user.html")
        if role not in ('admin', 'manager', 'receptionist', 'technician', 'employee'):
            role = 'employee'
        full_name = request.form.get("full_name", "").strip()
        with get_db() as conn:
            exists = conn.execute("SELECT id FROM users WHERE LOWER(username) = ?", (username,)).fetchone()
            if exists:
                flash("Ce nom d'utilisateur existe déjà", "error")
                return render_template("add_user.html")
            conn.execute("INSERT INTO users (username, password, role, full_name) VALUES (?,?,?,?)",
                (username, generate_password_hash(password), role, full_name))
            conn.commit()
        log_activity('Add User', f'User: {username} ({role})')
        flash(f"Utilisateur {username} ajouté avec succès", "success")
        return redirect("/users")
    return render_template("add_user.html")



@auth_bp.route("/delete_user/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    if user_id == session.get('user_id'):
        flash("Impossible de supprimer votre propre compte", "error")
        return redirect("/users")
    with get_db() as conn:
        user = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    log_activity('Delete User', f'User: {user[0] if user else user_id}')
    flash("Utilisateur supprimé", "success")
    return redirect("/users")



@auth_bp.route("/manage_roles")
@login_required
def manage_roles():
    if session.get('role') != 'admin':
        flash("Accès refusé", "error")
        return redirect("/")
    with get_db() as conn:
        users = conn.execute("SELECT id, username, role, COALESCE(full_name,'') FROM users ORDER BY id").fetchall()
    return render_template("manage_roles.html", users=users, roles=PERMISSIONS)



@auth_bp.route("/client/login", methods=["POST"])
def customer_login():
    phone = request.form.get("phone", "").strip()
    if not phone:
        flash("Numéro de téléphone requis", "error")
        return redirect("/client")
    with get_db() as conn:
        customer = conn.execute("SELECT id, name, phone FROM customers WHERE phone = ?", (phone,)).fetchone()
    if not customer:
        flash("Numéro non trouvé. Contactez-nous pour créer votre compte.", "error")
        return redirect("/client")
    session['client_id'] = customer[0]
    session['client_name'] = customer[1]
    session['client_phone'] = customer[2]
    return redirect("/client/dashboard")



@auth_bp.route("/client/logout")
def customer_logout():
    session.pop('client_id', None)
    session.pop('client_name', None)
    session.pop('client_phone', None)
    return redirect("/client")



@auth_bp.route("/client_app/login", methods=["POST"])
def client_app_login():
    phone = request.form.get("phone", "").strip()
    with get_db() as conn:
        customer = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
        if not customer:
            flash("Numéro non trouvé", "danger")
            return redirect("/client_app")
        session['client_id'] = customer['id']
        session['client_name'] = customer['name']
    return redirect("/client_app/dashboard")


