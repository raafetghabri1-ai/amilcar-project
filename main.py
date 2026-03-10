from database.db import create_tables
from models.customer import add_customer
from models.car import add_car
from models.appointment import add_appointment, get_appointments

create_tables()

add_customer("أحمد محمد", "0501234567")
add_car(1, "Toyota", "Corolla", "ABC-1234")
add_appointment(1, "2026-03-11", "تغيير زيت")

print("\n--- المواعيد ---")
appointments = get_appointments()
for a in appointments:
    print(a)
