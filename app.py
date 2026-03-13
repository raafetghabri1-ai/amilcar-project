from flask import Flask, render_template, request, redirect, url_for
from models.customer import get_all_customers
from models.report import total_customers, total_appointments, total_revenue
from models.appointment import get_appointments
from models.invoice import get_all_invoices
from database.db import create_tables

app = Flask(__name__)
create_tables()

@app.route('/')
def index():
    stats = {
        'customers': total_customers(),
        'appointments': total_appointments(),
        'revenue': total_revenue()
    }
    return render_template('index.html', stats=stats)

@app.route('/customers')
def customers():
    all_customers = get_all_customers()
    return render_template('customers.html', customers=all_customers)

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
from flask import Flask, render_template, request, redirect, url_for

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
        appointment_id = request.form["appointment_id"]
        amount = request.form["amount"]
        from models.invoice import add_invoice
        add_invoice(appointment_id, float(amount))
        return redirect("/invoices")
    return render_template("add_invoice.html")

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
if __name__ == '__main__':
    app.run(debug=True)