import os
from app import app, db, Product, Category, SubCategory, fb_db

def migrate():
    with app.app_context():
        # Migrate Categories
        print("Migrating Categories...")
        categories = Category.query.all()
        for cat in categories:
            cat_ref = fb_db.collection("categories").document(str(cat.id))
            cat_ref.set({
                "name": cat.name,
                "subcategories": [sub.name for sub in cat.subcategories]
            })
            print(f"  - {cat.name} migrated.")

        # Migrate Products
        print("\nMigrating Products...")
        products = Product.query.all()
        for prod in products:
            prod_ref = fb_db.collection("products").document(str(prod.id))
            prod_ref.set({
                "name": prod.name,
                "price": prod.price,
                "description": prod.description,
                "category": prod.category,
                "sub_category": prod.sub_category,
                "image_url": prod.image_url
            })
            print(f"  - {prod.name} migrated.")

if __name__ == "__main__":
    migrate()
    print("\n✅ Migration complete!")
