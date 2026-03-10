from database.db import create_tables
from models.customer import add_customer
from models.car import add_car
from models.appointment import add_appointment
from models.invoice import add_invoice
from models.report import print_report

create_tables()
add_customer("أحمد محمد", "0501234567")
add_car(1, "Toyota", "Corolla", "ABC-1234")
add_appointment(1, "2026-03-11", "تغيير زيت")
add_invoice(1, 150.0)

print_report()
