# -*- coding: utf-8 -*-
"""
Database Manager - Phase 2: Security Logic
إنشاء وإدارة قاعدة بيانات SQLite
"""
import sqlite3
import os
from datetime import datetime
import hashlib

# ==================== إعدادات قاعدة البيانات ====================
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Data",
    "usb_security.db"
)

# ==================== دالة إنشاء قاعدة البيانات ====================
def create_database():
    """
    إنشاء قاعدة بيانات إذا لم تكن موجودة
    """
    try:
        # التأكد من وجود مجلد Data
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

        # الاتصال بقاعدة البيانات
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 1️⃣ جدول القائمة البيضاء (Whitelist)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS whitelist(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE NOT NULL,
                model TEXT,
                serial_number TEXT,
                vid TEXT,
                pid TEXT,
                size_gb REAL,
                date_added TEXT, 
                added_by TEXT,
                status TEXT DEFAULT 'Active',
                notes TEXT 
            )
        ''')

        # 2️⃣ جدول القائمة السوداء (Blacklist)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blacklist(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE NOT NULL,
                model TEXT,
                serial_number TEXT,
                vid TEXT,
                pid TEXT,
                size_gb REAL,
                date_blocked TEXT, 
                blocked_by TEXT,
                block_reason TEXT,
                status TEXT DEFAULT 'Active',
                notes TEXT
            )
        ''')

        # 3️⃣ جدول سجل التدقيق (Audit Log)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                event_type TEXT,
                device_fingerprint TEXT,
                device_model TEXT,
                result TEXT,
                user TEXT,
                details TEXT
            )
        ''')

        # 4️⃣ جدول الإعدادات (Settings)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        # 5️⃣ جدول المسؤولين (Admins)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admins( 
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_date TEXT,
                last_login TEXT,
                status TEXT DEFAULT 'Active',
                is_master INTEGER DEFAULT 0
            )
        ''')




        # 6️⃣ جدول الأجهزة المحظورة تلقائياً (Auto-Blocked)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auto_blocked(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE NOT NULL,
                model TEXT,
                serial_number TEXT,
                vid TEXT DEFAULT 'N/A',
                pid TEXT DEFAULT 'N/A',
                size_gb REAL,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                block_reason TEXT,
                blocked_by TEXT DEFAULT 'SYSTEM'
            )
        ''')

        # إضافة كلمة مرور افتراضية في جدول settings
        default_password = "admin"
        password_hash = hashlib.sha256(default_password.encode()).hexdigest()
        
        cursor.execute('''
            INSERT OR IGNORE INTO settings (key, value)
            VALUES ('admin_password_hash', ?)
        ''', (password_hash,))

        # إضافة مسؤول افتراضي (admin / admin) في جدول admins
        created_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute('''
            INSERT OR IGNORE INTO admins(username, password_hash, created_date, is_master)
            VALUES (?, ?, ?, ?)
        ''', ("admin", password_hash, created_date, 1))

        # حفظ التغييرات
        conn.commit()
        conn.close()

        print(f"✅ Database created successfully at: {DB_PATH}")
        return True
                   

    except Exception as e:
        print(f"❌ Error creating database: {e}")
        return False

# ==================== دوال مساعدة ====================
def get_connection():
    """
    الحصول على اتصال بقاعدة البيانات
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"❌ Error connecting to database: {e}")
        return None

def table_exists(table_name):
    """التحقق من وجود جدول"""
    try:
        conn = get_connection()
        if not conn:
            return False
        
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name FROM sqlite_master
            WHERE type='table' AND name=?
        ''', (table_name,))

        result = cursor.fetchone()
        conn.close()

        return result is not None
    except Exception as e:
        print(f"❌ Error checking table: {e}")
        return False

def get_table_count(table_name):
    """الحصول على عدد الصفوف في الجدول"""
    try:
        conn = get_connection()
        if not conn:
            return 0
        
        cursor = conn.cursor()
        cursor.execute(f'SELECT COUNT(*) FROM {table_name}')
        result = cursor.fetchone()
        conn.close()

        return result[0] if result else 0

    except Exception as e:
        print(f"❌ Error getting table count: {e}")
        return 0

# ==================== دوال سجل التدقيق ====================
def log_event(event_type, device_fingerprint="", device_model="", result="", user="System", details=""):
    """تسجيل حدث في سجل التدقيق"""
    try:
        conn = get_connection()
        if not conn:
            return False
        
        cursor = conn.cursor()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute('''
            INSERT INTO audit_log (timestamp, event_type, device_fingerprint, device_model, result, user, details)
            VALUES(?, ?, ?, ?, ?, ?, ?)
        ''', (timestamp, event_type, device_fingerprint, device_model, result, user, details))

        conn.commit()
        conn.close()

        return True

    except Exception as e:
        print(f"❌ Error logging event: {e}")
        return False

def get_audit_logs(limit=50):
    """الحصول على آخر أحداث سجل التدقيق"""
    try:
        conn = get_connection()
        if not conn:
            return []

        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM audit_log 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (limit,))

        results = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return results

    except Exception as e:
        print(f"❌ Error getting audit logs: {e}")
        return []

# ==================== دوال الإعدادات ====================
def get_setting(key, default=""):
    """الحصول على قيمة إعداد"""
    try:
        conn = get_connection()
        if not conn:
            return default
        
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        result = cursor.fetchone()
        conn.close()
        
        return result[0] if result else default
        
    except Exception as e:
        print(f"❌ Error getting setting: {e}")
        return default

def set_setting(key, value):
    """تعيين قيمة إعداد"""
    try:
        conn = get_connection()
        if not conn:
            return False
        
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO settings (key, value) 
            VALUES (?, ?)
        ''', (key, value))
        
        conn.commit()
        conn.close()
        
        return True
        
    except Exception as e:
        print(f"❌ Error setting setting: {e}")
        return False

# ==================== دوال المسؤولين ====================
def get_default_admin():
    """الحصول على معلومات المسؤول الافتراضي"""
    try:
        conn = get_connection()
        if not conn:
            return None
        
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, username, created_date, status, is_master 
            FROM admins WHERE username = ?
        ''', ("admin",))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return dict(result)
        return None
        
    except Exception as e:
        print(f"❌ Error getting default admin: {e}")
        return None

# ==================== الدالة الرئيسية للاختبار ====================
def main():
    """اختبار إنشاء قاعدة البيانات"""
    print("\n" + "="*70)
    print("🗄️  Database Manager - Phase 2: Security Logic")
    print("="*70 + "\n")

    # إنشاء قاعدة البيانات
    if create_database():
        print("\n📊 Database Statistics:")
        print(f"   Whitelist entries: {get_table_count('whitelist')}")
        print(f"   Blacklist entries: {get_table_count('blacklist')}")
        print(f"   Audit log entries: {get_table_count('audit_log')}")
        print(f"   Admin users: {get_table_count('admins')}")
        
        # عرض معلومات المسؤول الافتراضي
        admin = get_default_admin()
        if admin:
            print(f"\n👤 Default Admin:")
            print(f"   Username: {admin['username']}")
            print(f"   Status: {admin['status']}")
            print(f"   Is Master: {'Yes' if admin['is_master'] else 'No'}")
            print(f"   Created: {admin['created_date']}")
        
        # تسجيل حدث إنشاء قاعدة البيانات
        log_event(
            event_type="DATABASE_CREATED",
            device_fingerprint="",
            device_model="",
            result="Success",
            user="System",
            details="Database created successfully with admins table"
        )
        
        print("\n✅ Database is ready!")
        print("\n💡 Default Login Credentials:")
        print("   Username: admin")
        print("   Password: admin")
        print("   ⚠️  Please change after first login!")
    else:
        print("\n❌ Failed to create database!")

    print("\nPress Enter to exit...")
    input()

if __name__ == "__main__":
    main()