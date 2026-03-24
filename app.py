import os
import uuid
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)
app.secret_key = "AK_ELECTRONICS_2026_SECURE"

# --- ☁️ CLOUD DATABASE CONFIGURATION ---
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///ak_electronics.db"
)

uri = app.config.get("SQLALCHEMY_DATABASE_URI")
if uri and uri.startswith("postgres://"):
    app.config["SQLALCHEMY_DATABASE_URI"] = uri.replace(
        "postgres://", "postgresql://", 1
    )

app.config["UPLOAD_FOLDER"] = "static/uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db = SQLAlchemy(app)

# --- 🎯 FIREBASE ADMIN SDK ---
import json
firebase_key_path = "firebase_key.json"
if os.path.exists(firebase_key_path):
    cred = credentials.Certificate(firebase_key_path)
    firebase_admin.initialize_app(cred)
elif os.environ.get("FIREBASE_KEY"):
    key_dict = json.loads(os.environ.get("FIREBASE_KEY"))
    cred = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(cred)
else:
    print("⚠️ Firebase Key not found. Backend features may fail.")

fb_db = firestore.client()
print("✅ Firebase Admin initialized successfully!")


# --- DATABASE MODELS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), default="user")
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    cart_items = db.relationship("CartItem", backref="buyer", lazy=True)
    orders = db.relationship("Order", backref="customer", lazy=True)
    requests = db.relationship("ServiceRequest", backref="user", lazy=True)


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    subcategories = db.relationship("SubCategory", backref="parent_category", lazy=True)


class SubCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(50))
    sub_category = db.Column(db.String(50))
    image_url = db.Column(db.Text)


class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    product = db.relationship("Product")


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default="Processing")
    tracking_number = db.Column(db.String(50), unique=True)
    delivery_address = db.Column(db.Text, nullable=False)


class ServiceRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    request_type = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="Pending")
    admin_reply = db.Column(db.Text)
    reply_image = db.Column(db.String(300))


with app.app_context():
    db.create_all()


# --- PUBLIC STOREFRONT (FIRESTORE) ---
@app.route("/")
def index():
    search_query = request.args.get("search", "").strip()
    
    # Fetch from Firestore
    products_ref = fb_db.collection("products")
    if search_query:
        # Note: Firestore text search is limited, using basic filter for now
        # For full text search, Algolia or similar is usually used.
        # Here we'll just fetch all and filter in Python for simplicity.
        all_docs = products_ref.stream()
        products = []
        for doc in all_docs:
            p = doc.to_dict()
            p['id'] = doc.id
            if (search_query.lower() in p.get('name', '').lower() or 
                search_query.lower() in p.get('category', '').lower() or
                search_query.lower() in p.get('description', '').lower()):
                products.append(p)
    else:
        all_docs = products_ref.stream()
        products = []
        for doc in all_docs:
            p = doc.to_dict()
            p['id'] = doc.id
            products.append(p)

    categories = {}
    for p in products:
        categories.setdefault(p.get('category', 'Other'), []).append(p)

    cart_dict = {}
    cart_count = 0
    if "user_id" in session:
        # Cart still using SQLAlchemy for now to avoid complete schema overhaul in one go
        cart_items = CartItem.query.filter_by(user_id=session["user_id"]).all()
        cart_count = sum(item.quantity for item in cart_items)
        cart_dict = {item.product_id: item.quantity for item in cart_items}

    return render_template(
        "index.html",
        categories=categories,
        search_query=search_query,
        cart_dict=cart_dict,
        cart_count=cart_count,
    )


# --- AUTHENTICATION ROUTES (FIREBASE) ---
from firebase_admin import auth as fb_auth

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        id_token = request.json.get("idToken")
        if not id_token:
            # Fallback for traditional form login if needed (e.g. dev accounts)
            email, password = request.form.get("email"), request.form.get("password")
            if email == "admin@ak.com" and password == "ak@2026":
                session["user_id"], session["role"] = "admin", "admin"
                return redirect(url_for("index"))
            if email == "dev@ak.com" and password == "dev@2026":
                session["user_id"], session["role"] = "dev", "developer"
                return redirect(url_for("index"))
            return "Missing Token", 400

        try:
            decoded_token = fb_auth.verify_id_token(id_token)
            uid = decoded_token['uid']
            email = decoded_token.get('email')
            
            # Simple role mapping based on email
            role = "user"
            if email == "admin@ak.com": role = "admin"
            elif email == "dev@ak.com": role = "developer"
            
            session["user_id"] = uid
            session["role"] = role
            return {"status": "success"}, 200
        except Exception as e:
            return str(e), 401
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    # Registration is handled primarily on the frontend with Firebase SDK
    # Here we just serve the page or handle post-registration logic if needed.
    if request.method == "POST":
        # Process Firestore user profile creation if needed
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# --- INFORMATIONAL ROUTES ---
@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


# --- CART & CHECKOUT ROUTES ---
@app.route("/add_to_cart/<int:product_id>")
def add_to_cart(product_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    item = CartItem.query.filter_by(
        user_id=session["user_id"], product_id=product_id
    ).first()
    if item:
        item.quantity += 1
    else:
        db.session.add(CartItem(user_id=session["user_id"], product_id=product_id))
    db.session.commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/decrease_cart/<int:product_id>")
def decrease_cart(product_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    item = CartItem.query.filter_by(
        user_id=session["user_id"], product_id=product_id
    ).first()
    if item:
        item.quantity -= 1
        if item.quantity <= 0:
            db.session.delete(item)
        db.session.commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/cart")
def cart():
    if "user_id" not in session:
        return redirect(url_for("login"))
    items = CartItem.query.filter_by(user_id=session["user_id"]).all()
    total = sum(i.product.price * i.quantity for i in items)
    return render_template("cart.html", items=items, total=total)


@app.route("/remove_cart/<int:item_id>")
def remove_cart(item_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    item = CartItem.query.get(item_id)
    if item and item.user_id == session["user_id"]:
        db.session.delete(item)
        db.session.commit()
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user_id = session["user_id"]
    items = CartItem.query.filter_by(user_id=user_id).all()
    if not items:
        return redirect(url_for("index"))
    total = sum(i.product.price * i.quantity for i in items)
    if request.method == "POST":
        u = User.query.get(user_id)
        u.phone, u.address = request.form.get("phone"), request.form.get("address")
        db.session.add(
            Order(
                user_id=user_id,
                total_amount=total,
                delivery_address=f"{u.phone} | {u.address}",
                tracking_number="AK-" + str(uuid.uuid4())[:8].upper(),
            )
        )
        for i in items:
            db.session.delete(i)
        db.session.commit()
        return redirect(url_for("my_orders"))
    return render_template("checkout.html", total=total)


@app.route("/my_orders")
def my_orders():
    if "user_id" not in session:
        return redirect(url_for("login"))
    orders = (
        Order.query.filter_by(user_id=session["user_id"])
        .order_by(Order.id.desc())
        .all()
    )
    return render_template("orders.html", orders=orders)


# --- ADMIN & SERVICE ROUTES ---
@app.route("/submit_request", methods=["POST"])
def submit_request():
    if "user_id" in session:
        db.session.add(
            ServiceRequest(
                user_id=session["user_id"],
                request_type=request.form.get("request_type"),
                message=request.form.get("message"),
            )
        )
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/admin")
def admin_dashboard():
    if session.get("role") not in ["admin", "developer"]:
        return redirect(url_for("login"))
    
    # Fetch from Firestore
    products = []
    for doc in fb_db.collection("products").stream():
        p = doc.to_dict()
        p['id'] = doc.id
        products.append(p)
        
    cats = []
    cat_dict = {}
    for doc in fb_db.collection("categories").stream():
        c_data = doc.to_dict()
        c_data['id'] = doc.id
        cats.append(c_data)
        cat_dict[c_data['name']] = c_data.get('subcategories', [])

    return render_template(
        "admin.html",
        products=products,
        orders=Order.query.order_by(Order.id.desc()).all(),
        categories=cats,
        category_dict=cat_dict,
        notifications=ServiceRequest.query.order_by(ServiceRequest.id.desc()).all(),
    )


@app.route("/add_category", methods=["POST"])
def add_category():
    if session.get("role") in ["admin", "developer"]:
        name = request.form.get("name")
        # Check if exists in Firestore
        existing = fb_db.collection("categories").where("name", "==", name).get()
        if not existing:
            fb_db.collection("categories").add({"name": name, "subcategories": []})
    return redirect(url_for("admin_dashboard"))


@app.route("/add_subcategory", methods=["POST"])
def add_subcategory():
    if session.get("role") in ["admin", "developer"]:
        name = request.form.get("name")
        category_id = request.form.get("category_id")
        cat_ref = fb_db.collection("categories").document(category_id)
        cat_doc = cat_ref.get()
        if cat_doc.exists:
            subs = cat_doc.to_dict().get("subcategories", [])
            if name not in subs:
                subs.append(name)
                cat_ref.update({"subcategories": subs})
    return redirect(url_for("admin_dashboard"))


@app.route("/add_product", methods=["POST"])
def add_product():
    if session.get("role") in ["admin", "developer"]:
        files = request.files.getlist("images")
        filenames = []
        for f in files:
            if f and f.filename:
                fname = secure_filename(f.filename)
                f.save(os.path.join(app.config["UPLOAD_FOLDER"], fname))
                filenames.append(fname)
        image_urls = ",".join(filenames) if filenames else "default.png"
        
        fb_db.collection("products").add({
            "name": request.form.get("name"),
            "price": float(request.form.get("price")),
            "category": request.form.get("category"),
            "sub_category": request.form.get("sub_category"),
            "description": request.form.get("description"),
            "image_url": image_urls,
        })
    return redirect(url_for("admin_dashboard"))


@app.route("/delete_product/<string:id>")
def delete_product(id):
    if session.get("role") in ["admin", "developer"]:
        fb_db.collection("products").document(id).delete()
    return redirect(url_for("admin_dashboard"))


@app.route("/update_order/<int:order_id>/<string:status>")
def update_order(order_id, status):
    if session.get("role") in ["admin", "developer"]:
        o = Order.query.get(order_id)
        if o:
            o.status = status
            db.session.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/queries")
def view_queries():
    if session.get("role") not in ["admin", "developer"]:
        return redirect(url_for("login"))
    queries = ServiceRequest.query.order_by(ServiceRequest.id.desc()).all()
    return render_template("admin_queries.html", queries=queries)


@app.route("/delete_request/<int:req_id>")
def delete_request(req_id):
    if session.get("role") not in ["admin", "developer"]:
        return redirect(url_for("login"))
    req = ServiceRequest.query.get(req_id)
    if req:
        db.session.delete(req)
        db.session.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/resolve_request/<int:req_id>", methods=["GET", "POST"])
def resolve_request(req_id):
    if session.get("role") not in ["admin", "developer"]:
        return redirect(url_for("login"))
    req = ServiceRequest.query.get_or_404(req_id)
    if request.method == "POST":
        req.admin_reply = request.form.get("admin_reply")
        req.status = "Resolved"
        f = request.files.get("reply_image")
        if f and f.filename:
            fname = secure_filename(f.filename)
            f.save(os.path.join(app.config["UPLOAD_FOLDER"], fname))
            req.reply_image = fname
        db.session.commit()
        return redirect(url_for("admin_dashboard"))
    return render_template("resolve.html", req=req)


# --- DEVELOPER ROUTES ---
@app.route("/developer_console")
def developer_console():
    if session.get("role") != "developer":
        return redirect(url_for("login"))
    try:
        with open("templates/index.html", "r", encoding="utf-8") as f:
            home_code = f.read()
    except:
        home_code = "Error reading file."
    return render_template(
        "developer.html",
        users=User.query.all(),
        orders=Order.query.all(),
        home_code=home_code,
    )


@app.route("/edit_file", methods=["POST"])
def edit_file():
    if session.get("role") != "developer":
        return redirect(url_for("login"))
    filename = request.form.get("filename")
    new_code = request.form.get("code")
    if filename in ["templates/index.html", "static/style.css"]:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(new_code)
    return redirect(url_for("developer_console"))


if __name__ == "__main__":
    app.run(debug=True)
