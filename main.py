from database.db import create_tables
from models.customer import add_customer, get_all_customers
from models.car import add_car, get_customer_cars

# إنشاء قاعدة البيانات
create_tables()

# إضافة سيارة للعميل رقم 1
add_car(1, "Toyota", "Corolla", "ABC-1234")

# عرض سيارات العميل رقم 1
cars = get_customer_cars(1)
for car in cars:
    print(car)