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
if __name__ == '__main__':
    app.run(debug=True)