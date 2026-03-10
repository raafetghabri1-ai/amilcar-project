from database.db import connect

def total_revenue():
    conn = connect()
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(amount) FROM invoices')
    result = cursor.fetchone()[0]
    conn.close()
    return result or 0

def total_customers():
    conn = connect()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM customers')
    result = cursor.fetchone()[0]
    conn.close()
    return result

def total_appointments():
    conn = connect()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM appointments')
    result = cursor.fetchone()[0]
    conn.close()
    return result

def print_report():
    print("📊 تقرير مشروع amilcar")
    print("========================")
    print(f"👤 إجمالي العملاء   : {total_customers()}")
    print(f"📅 إجمالي المواعيد  : {total_appointments()}")
    print(f"💰 إجمالي الإيرادات : {total_revenue()} دينار")