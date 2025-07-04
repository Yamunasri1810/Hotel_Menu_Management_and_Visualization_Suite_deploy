from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response, make_response
from pymongo import MongoClient
from datetime import datetime, timedelta
from collections import defaultdict
import os
import io
import csv
from fpdf import FPDF
from base64 import b64decode
from PIL import Image
import tempfile
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

app = Flask(__name__)
app.secret_key = 'secret123456123456'
uri = os.environ.get('URI')


client = MongoClient(uri, server_api=ServerApi('1'))

try:
    
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(e)



db = client['hotel_orders']
users_col = db['users']
orders_col = db['orders']

menu = [
    {"id": 1, "name": "Margherita Pizza", "price": 10, "image": "/static/images/pizza.jpg"},
    {"id": 3, "name": "Caesar Salad", "price": 8, "image": "/static/images/salad.jpg"},
    {"id": 4, "name": "Tiramisu", "price": 6, "image": "/static/images/tiramisu.jpg"},
    {"id": 5, "name": "Minestrone Soup", "price": 7, "image": "/static/images/soup.jpg"},
]


@app.route('/')
def home():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    phone = request.form['phone']
    user = users_col.find_one({"phone": phone})
    if user:
        session['user'] = {"name": user['name'], "role": user['role'], "phone": user['phone']}
        return redirect(url_for("owner_dashboard") if user['role'] == 'owner' else url_for(f"{user['role']}_page"))
    else:
        return render_template('register.html', phone=phone)

@app.route('/register', methods=['POST'])
def register():
    name = request.form['name']
    phone = request.form['phone']
    users_col.insert_one({"name": name, "phone": phone, "role": "customer"})
    session['user'] = {"name": name, "phone": phone, "role": "customer"}
    return redirect(url_for("customer_page"))

@app.route('/menu')
def customer_page():
    return render_template('menu.html', dishes=menu, datetime=datetime)

@app.route('/submit_order', methods=['POST'])
def submit_order():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Not logged in."})

    data = request.get_json()
    items = data.get("items", [])
    table_number = data.get("table_number")

    if not items or not table_number:
        return jsonify({"success": False, "message": "Incomplete order."})

    orders_col.insert_one({
        "user_name": session['user']['name'],
        "user_phone": session['user']['phone'],
        "items": items,
        "table_number": table_number,
        "timestamp": datetime.now(),
        "status": "pending"
    })
    return jsonify({"success": True, "message": "Order sent to kitchen!"})

@app.route('/get_bill')
def get_bill():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Not logged in."})

    orders = list(orders_col.find({"user_phone": session['user']['phone'], "status": "pending"}))

    if not orders:
        return jsonify({"success": False, "message": "No pending orders found."})

    total = 0
    bill_items = []

    for order in orders:
        for item in order['items']:
            dish = next((d for d in menu if d['id'] == item['id']), None)
            if dish:
                item_total = item['quantity'] * dish['price']
                total += item_total
                bill_items.append({
                    "name": dish['name'],
                    "quantity": item['quantity'],
                    "total_price": item_total
                })

    orders_col.update_many({"user_phone": session['user']['phone'], "status": "pending"}, {"$set": {"status": "completed"}})

    return jsonify({"success": True, "orders": bill_items, "total": total})

@app.route('/chef')
def chef_page():
    chef_orders = list(orders_col.find({"status": "pending"}).sort("timestamp", -1))
    return render_template("chef.html", orders=chef_orders, menu=menu)

@app.route('/owner')
def owner_dashboard():
    period = request.args.get('period', 'week')

    if period == 'year':
        start_date = datetime.now() - timedelta(days=365)
    elif period == 'month':
        start_date = datetime.now() - timedelta(days=30)
    else:
        start_date = datetime.now() - timedelta(days=7)

    query = {"timestamp": {"$gte": start_date}}
    orders = list(orders_col.find(query))

    dish_count = defaultdict(int)
    sales_trend = defaultdict(float)
    customer_freq = defaultdict(int)

    for order in orders:
        user_phone = order.get('user_phone')
        customer_freq[user_phone] += 1
        order_total = 0

        timestamp = order.get("timestamp", datetime.now())
        time_label = timestamp.strftime('%A') if period == 'week' else timestamp.strftime('%B')

        for item in order.get("items", []):
            dish = next((d for d in menu if d['id'] == item['id']), None)
            if dish:
                dish_count[dish['name']] += item['quantity']
                order_total += item['quantity'] * dish['price']

        sales_trend[time_label] += order_total

    most_ordered = max(dish_count.items(), key=lambda x: x[1], default=("None", 0))
    least_ordered = min(dish_count.items(), key=lambda x: x[1], default=("None", 0))
    weekly_profits = dict(sales_trend)

    sorted_customers = sorted(customer_freq.items(), key=lambda x: x[1], reverse=True)[:5]
    regular_customers = []
    for phone, visits in sorted_customers:
        user = users_col.find_one({"phone": phone})
        if user:
            regular_customers.append({"name": user['name'], "visits": visits})

    return render_template("owner.html",
        most_ordered=most_ordered,
        least_ordered=least_ordered,
        weekly_profits=weekly_profits,
        regular_customers=regular_customers
    )

@app.route('/api/owner_data')
def owner_data():
    period = request.args.get('period', 'week')
    if period == 'year':
        start_date = datetime.now() - timedelta(days=365)
    elif period == 'month':
        start_date = datetime.now() - timedelta(days=30)
    else:
        start_date = datetime.now() - timedelta(days=7)

    orders = list(orders_col.find({"timestamp": {"$gte": start_date}}))

    dish_count = defaultdict(int)
    customer_freq = defaultdict(int)
    customer_daily = defaultdict(set)
    sales_trend = defaultdict(float)
    export_rows = []
    total_profit = 0

    for order in orders:
        user_phone = order['user_phone']
        customer_freq[user_phone] += 1

        order_date = order.get("timestamp", datetime.now())
        time_label = order_date.strftime('%Y') if period == 'year' else order_date.strftime('%B') if period == 'month' else order_date.strftime('%A')
        customer_daily[time_label].add(user_phone)
        order_total = 0

        for item in order['items']:
            dish = next((d for d in menu if d['id'] == item['id']), None)
            if dish:
                item_total = item['quantity'] * dish['price']
                dish_count[dish['name']] += item['quantity']
                order_total += item_total
                total_profit += item_total

                export_rows.append({
                    "Date": order_date.strftime('%Y-%m-%d'),
                    "Customer": user_phone,
                    "Dish": dish['name'],
                    "Quantity": item['quantity'],
                    "Price": dish['price'],
                    "Total": item_total
                })

        sales_trend[time_label] += order_total

    if request.args.get("export") == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=export_rows[0].keys())
        writer.writeheader()
        writer.writerows(export_rows)
        return Response(output.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment;filename=dashboard_export.csv"})

    sorted_customers = sorted(customer_freq.items(), key=lambda x: x[1], reverse=True)[:5]
    top_customers = []
    for phone, visits in sorted_customers:
        user = users_col.find_one({"phone": phone})
        if user:
            top_customers.append({"name": user['name'], "visits": visits})

    return jsonify({
        "total_profit": total_profit,
        "total_customers": len(set(customer_freq.keys())),
        "avg_customers": round(sum(len(v) for v in customer_daily.values()) / len(customer_daily), 2) if customer_daily else 0,
        "dish_labels": list(dish_count.keys()),
        "dish_data": list(dish_count.values()),
        "sales_labels": list(sales_trend.keys()),
        "sales_data": list(sales_trend.values()),
        "top_customers": top_customers
    })

@app.route('/export_report', methods=['POST'])
def export_report():
    data = request.get_json()
    dish_image_data = data['dishImage'].split(',')[1]
    sales_image_data = data['salesImage'].split(',')[1]

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(200, 10, "Sales Dashboard Report", ln=True, align="C")
    pdf.ln(10)

    # Save and insert Dish Chart
    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmpfile1:
        tmpfile1.write(b64decode(dish_image_data))
        tmpfile1.flush()
        pdf.cell(0, 10, "Dish Frequency Chart", ln=True)
        pdf.image(tmpfile1.name, x=10, w=180)

    pdf.add_page()

    # Save and insert Sales Chart
    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmpfile2:
        tmpfile2.write(b64decode(sales_image_data))
        tmpfile2.flush()
        pdf.cell(0, 10, "Sales Trend Chart", ln=True)
        pdf.image(tmpfile2.name, x=10, w=180)

    # Send PDF
    response = make_response(pdf.output(dest='S').encode('latin1'))
    response.headers.set('Content-Disposition', 'attachment', filename='dashboard_report.pdf')
    response.headers.set('Content-Type', 'application/pdf')
    return response


if __name__ == '__main__':
    app.run(debug=True,host='0.0.0.0')