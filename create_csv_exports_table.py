import mysql.connector
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME

# Connect to database
conn = mysql.connector.connect(
    host=DB_HOST,
    user=DB_USER,
    password=DB_PASSWORD,
    database=DB_NAME
)

cursor = conn.cursor()

# Create the csv_exports table
create_table_query = """
CREATE TABLE IF NOT EXISTS csv_exports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    csv_file VARCHAR(255),
    export_type VARCHAR(20),
    exported_at DATETIME,
    INDEX idx_order_id (order_id),
    INDEX idx_export_type (export_type)
)
"""

cursor.execute(create_table_query)
conn.commit()

print("Created csv_exports table successfully")

cursor.close()
conn.close()
