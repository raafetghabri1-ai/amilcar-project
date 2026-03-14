from flask import Flask, render_template, request, redirect, url_for
from models.customer import get_all_customers, add_customer
from models.report import total_customers, total_appointments, total_revenue
from models.appointment import get_appointments
from models.invoice import get_all_invoices
from database.db import create_tables

app = Flask(__name__)
create_tables()

@app.route('/')
def index():
    from database.db import connect
    conn = connect()
    pending_quotes = conn.execute("SELECT COUNT(*) FROM quotes WHERE status = 'pending'").fetchone()[0]
    pending_appointments = conn.execute(
        "SELECT a.id, cu.name, ca.brand, ca.model, a.date, a.service "
        "FROM appointments a JOIN cars ca ON a.car_id = ca.id "
        "JOIN customers cu ON ca.customer_id = cu.id "
        "WHERE a.status = 'pending' ORDER BY a.date"
    ).fetchall()
    conn.close()
    stats = {
        'customers': total_customers(),
        'appointments': total_appointments(),
        'revenue': total_revenue(),
        'quotes': pending_quotes
    }
    return render_template('index.html', stats=stats, pending_appointments=pending_appointments)

@app.route('/customers')
def customers():
    search = request.args.get('q', '').strip()
    if search:
        from database.db import connect
        conn = connect()
        all_customers = conn.execute(
            "SELECT * FROM customers WHERE name LIKE ? OR phone LIKE ?",
            (f'%{search}%', f'%{search}%')
        ).fetchall()
        conn.close()
    else:
        all_customers = get_all_customers()
    return render_template('customers.html', customers=all_customers, search=search)

@app.route('/appointments')
def appointments():
    all_appointments = get_appointments()
    return render_template('appointments.html', appointments=all_appointments)

@app.route('/invoices')
def invoices():
    all_invoices = get_all_invoices()
    return render_template('invoices.html', invoices=all_invoices)
@app.route('/add_customer', methods=['GET', 'POST'])
def new_customer():
    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        add_customer(name, phone)
        return redirect(url_for('customers'))
    return render_template('add_customer.html')

@app.route("/add_appointment", methods=["GET", "POST"])
def new_appointment():
    if request.method == "POST":
        car_id = request.form["car_id"]
        date = request.form["date"]
        service = request.form["service"]
        from models.appointment import add_appointment
        add_appointment(car_id, date, service)
        return redirect("/appointments")
    from models.car import get_customer_cars
    from models.customer import get_all_customers
    all_customers = get_all_customers()
    return render_template("add_appointment.html", customers=all_customers)

@app.route("/add_invoice", methods=["GET", "POST"])
def new_invoice():
    if request.method == "POST":
        appointment_id = request.form.get("appointment_id")
        amount = request.form.get("amount")
        if appointment_id and amount:
            from models.invoice import add_invoice
            add_invoice(appointment_id, float(amount))
        return redirect("/invoices")
    from models.appointment import get_appointments
    all_appointments = get_appointments()
    return render_template("add_invoice.html", appointments=all_appointments)

@app.route("/pay_invoice/<int:invoice_id>")
def pay_invoice(invoice_id):
    from database.db import connect
    conn = connect()
    conn.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()
    return redirect("/invoices")

import os
from werkzeug.utils import secure_filename
UPLOAD_FOLDER = 'static/uploads'

@app.route("/request_quote", methods=["GET", "POST"])
def request_quote():
    if request.method == "POST":
        name = request.form["name"]
        phone = request.form["phone"]
        service = request.form["service"]
        files = request.files.getlist("photos")
        saved = []
        for f in files:
            if f.filename:
                filename = secure_filename(f.filename)
                f.save(os.path.join(UPLOAD_FOLDER, filename))
                saved.append(filename)
        from database.db import connect
        conn = connect()
        conn.execute("INSERT INTO quotes (name, phone, service, photos) VALUES (?,?,?,?)",
            (name, phone, service, ",".join(saved)))
        conn.commit()
        conn.close()
        return render_template("quote_success.html")
    return render_template("request_quote.html")

@app.route("/quotes")
def quotes():
    from database.db import connect
    conn = connect()
    all_quotes = conn.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("quotes.html", quotes=all_quotes)

@app.route("/add_car", methods=["GET", "POST"])
def new_car():
    if request.method == "POST":
        customer_id = request.form["customer_id"]
        brand = request.form["brand"]
        model = request.form["model"]
        plate = request.form["plate"]
        from models.car import add_car
        add_car(customer_id, brand, model, plate)
        return redirect("/customers")
    from models.customer import get_all_customers
    all_customers = get_all_customers()
    return render_template("add_car.html", customers=all_customers)

@app.route("/set_price/<int:quote_id>", methods=["POST"])
def set_price(quote_id):
    price = request.form["price"]
    from database.db import connect
    conn = connect()
    conn.execute("UPDATE quotes SET price = ?, status = 'priced' WHERE id = ?", (float(price), quote_id))
    conn.commit()
    conn.close()
    return redirect("/quotes")

@app.route("/convert_quote/<int:quote_id>")
def convert_quote(quote_id):
    from database.db import connect
    from datetime import date
    conn = connect()
    quote = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    if not quote:
        conn.close()
        return redirect("/quotes")
    name, phone, service, price = quote[1], quote[2], quote[3], quote[6] or 0
    # البحث عن عميل موجود بنفس رقم الهاتف أو إنشاء جديد
    customer = conn.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
    if customer:
        customer_id = customer[0]
    else:
        cursor = conn.execute("INSERT INTO customers (name, phone) VALUES (?, ?)", (name, phone))
        customer_id = cursor.lastrowid
    # البحث عن سيارة للعميل أو إنشاء واحدة افتراضية
    car = conn.execute("SELECT id FROM cars WHERE customer_id = ?", (customer_id,)).fetchone()
    if car:
        car_id = car[0]
    else:
        cursor = conn.execute("INSERT INTO cars (customer_id, brand, model, plate) VALUES (?, ?, ?, ?)",
            (customer_id, "-", "-", "-"))
        car_id = cursor.lastrowid
    # إنشاء الموعد
    service_text = f"{service} - {price} DT" if price else service
    conn.execute("INSERT INTO appointments (car_id, date, service) VALUES (?, ?, ?)",
        (car_id, str(date.today()), service_text))
    conn.execute("UPDATE quotes SET status = 'converted' WHERE id = ?", (quote_id,))
    conn.commit()
    conn.close()
    return redirect("/appointments")

@app.route("/update_appointment/<int:appointment_id>/<status>")
def update_appointment(appointment_id, status):
    from database.db import connect
    conn = connect()
    conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id))
    conn.commit()
    conn.close()
    return redirect("/appointments")

@app.route("/customer/<int:customer_id>")
def customer_detail(customer_id):
    from database.db import connect
    conn = connect()
    customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    cars = conn.execute("SELECT * FROM cars WHERE customer_id = ?", (customer_id,)).fetchall()
    appointments = conn.execute("SELECT a.* FROM appointments a JOIN cars c ON a.car_id = c.id WHERE c.customer_id = ? ORDER BY a.id DESC", (customer_id,)).fetchall()
    conn.close()
    return render_template("customer_detail.html", customer=customer, cars=cars, appointments=appointments)

@app.route("/edit_customer/<int:customer_id>", methods=["GET", "POST"])
def edit_customer(customer_id):
    from database.db import connect
    conn = connect()
    if request.method == "POST":
        name = request.form["name"]
        phone = request.form["phone"]
        conn.execute("UPDATE customers SET name = ?, phone = ? WHERE id = ?", (name, phone, customer_id))
        conn.commit()
        conn.close()
        return redirect(f"/customer/{customer_id}")
    customer = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    conn.close()
    return render_template("edit_customer.html", customer=customer)

@app.route("/edit_car/<int:car_id>", methods=["GET", "POST"])
def edit_car(car_id):
    from database.db import connect
    conn = connect()
    if request.method == "POST":
        brand = request.form["brand"]
        model = request.form["model"]
        plate = request.form["plate"]
        conn.execute("UPDATE cars SET brand = ?, model = ?, plate = ? WHERE id = ?", (brand, model, plate, car_id))
        conn.commit()
        customer_id = conn.execute("SELECT customer_id FROM cars WHERE id = ?", (car_id,)).fetchone()[0]
        conn.close()
        return redirect(f"/customer/{customer_id}")
    car = conn.execute("SELECT * FROM cars WHERE id = ?", (car_id,)).fetchone()
    conn.close()
    return render_template("edit_car.html", car=car)

@app.route("/delete_car/<int:car_id>")
def delete_car(car_id):
    from database.db import connect
    conn = connect()
    customer_id = conn.execute("SELECT customer_id FROM cars WHERE id = ?", (car_id,)).fetchone()[0]
    conn.execute("DELETE FROM cars WHERE id = ?", (car_id,))
    conn.commit()
    conn.close()
    return redirect(f"/customer/{customer_id}")

@app.route("/delete_customer/<int:customer_id>")
def delete_customer(customer_id):
    from database.db import connect
    conn = connect()
    conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    conn.commit()
    conn.close()
    return redirect("/customers")

@app.route("/delete_appointment/<int:appointment_id>")
def delete_appointment(appointment_id):
    from database.db import connect
    conn = connect()
    conn.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()
    return redirect("/appointments")

@app.route("/delete_invoice/<int:invoice_id>")
def delete_invoice(invoice_id):
    from database.db import connect
    conn = connect()
    conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()
    return redirect("/invoices")

@app.route("/daily")
def daily():
    from database.db import connect
    from datetime import date
    today = str(date.today())
    conn = connect()
    appointments = conn.execute("SELECT a.id, cu.name, ca.brand, ca.model, a.date, a.service, a.status FROM appointments a JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id WHERE a.date = ?", (today,)).fetchall()
    revenue = conn.execute("SELECT SUM(amount) FROM invoices i JOIN appointments a ON i.appointment_id = a.id WHERE a.date = ? AND i.status = 'paid'", (today,)).fetchone()[0] or 0
    conn.close()
    return render_template("daily.html", appointments=appointments, revenue=revenue, today=today)

@app.route("/print_invoice/<int:invoice_id>")
def print_invoice(invoice_id):
    from database.db import connect
    conn = connect()
    inv = conn.execute(
        "SELECT i.*, a.date, a.service, cu.name, cu.phone, ca.brand, ca.model, ca.plate "
        "FROM invoices i JOIN appointments a ON i.appointment_id = a.id "
        "JOIN cars ca ON a.car_id = ca.id JOIN customers cu ON ca.customer_id = cu.id "
        "WHERE i.id = ?", (invoice_id,)).fetchone()
    conn.close()
    return render_template("print_invoice.html", inv=inv)

@app.route("/monthly")
def monthly():
    from database.db import connect
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
    conn = connect()
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
    conn.close()
    stats = {
        'appointments': len(appointments),
        'completed': completed,
        'revenue': revenue,
        'unpaid': unpaid
    }
    return render_template("monthly.html",
        stats=stats, appointments=appointments, invoices=invoices,
        month_label=month_label,
        prev_month=f"{prev_y}-{prev_m:02d}", prev_label=prev_label,
        next_month=f"{next_y}-{next_m:02d}", next_label=next_label)
if __name__ == '__main__':
    app.run(debug=True)