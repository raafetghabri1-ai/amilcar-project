from flask import Flask, render_template
from models.report import total_customers, total_appointments, total_revenue
from models.customer import get_all_customers
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

if __name__ == '__main__':
    app.run(debug=True)