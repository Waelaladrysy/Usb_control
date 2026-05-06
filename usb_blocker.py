# -*- coding: utf-8 -*-
"""
USB Blocker - آلية المنع الفعلية المعززة
تستخدم Device Installation Restrictions لمنع تثبيت التعريف من الأساس
"""
import winreg
import subprocess
import os
import sys
import ctypes
from datetime import datetime
import time 
import wmi 

# إضافة مسار الجذر للمشروع لضمان عمل الاستيرادات في كل البيئات
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from security_logic.database_manager import get_connection, log_event
from security_logic.whitelist_manager import is_device_whitelisted
from security_logic.blacklist_manager import add_to_blacklist, device_exists_in_blacklist

POLICY_KEY = r"SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions"
DENY_KEY = r"SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions\DenyDeviceIDs"

def ensure_policy_keys():
    """إنشاء مفاتيح السياسة إذا لم تكن موجودة"""
    try:
        winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY)
        winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, DENY_KEY)
        
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, POLICY_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "DenyDeviceIDs", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "DenyDeviceIDsRetroactive", 0, winreg.REG_DWORD, 1)
        return True
    except Exception as e:
        print(f"⚠️ Policy key setup warning: {e}")
        return False

def add_to_deny_list(pnp_id: str):
    """حظر معزول تماماً باستخدام Device Instance ID الكامل"""
    if not pnp_id or not pnp_id.startswith("USB\\"):
        return False
    try:
        ensure_policy_keys()
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DENY_KEY, 0, winreg.KEY_SET_VALUE) as k:
            timestamp = str(int(datetime.now().timestamp()))
            winreg.SetValueEx(k, timestamp, 0, winreg.REG_SZ, pnp_id)
        return True
    except Exception as e:
        print(f"❌ Failed to add to deny list: {e}")
        return False

def remove_from_deny_list(pnp_id: str):
    """حذف المعرف الكامل من سياسة الحظر"""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DENY_KEY, 0, winreg.KEY_ALL_ACCESS) as k:
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(k, i)
                    if val == pnp_id:
                        winreg.DeleteValue(k, name)
                        return True
                    i += 1
                except OSError:
                    break
        return False
    except Exception as e:
        print(f"❌ Failed to remove from deny list: {e}")
        return False

def disable_via_pnputil(pnp_id: str):
    """تعطيل فوري عبر pnputil كطبقة حماية إضافية"""
    if not pnp_id: return False
    try:
        cmd = f'pnputil /disable-device "{pnp_id}"'
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return res.returncode == 0
    except:
        return False

def enable_via_pnputil(pnp_id: str):
    """تمكين فوري عبر pnputil"""
    if not pnp_id: return False
    try:
        cmd = f'pnputil /enable-device "{pnp_id}"'
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return res.returncode == 0
    except:
        return False

def check_and_block_device(device_data: dict):
    """
    الدالة الرئيسية:
      - مسموح (whitelist)  → يمر بدون حظر
      - محظور (blacklist)  → يُحظر فوراً بدون إضافة لـ auto_blocked
      - غير معروف          → يُحظر ويُضاف لـ auto_blocked
    """
    serial  = device_data.get('serial', 'N/A')
    hw_id   = device_data.get('hardware_id', '')
    pnp_id  = device_data.get('pnp_id', '')
    model   = device_data.get('model', 'Unknown')
    vid     = device_data.get('vid', 'N/A')
    pid     = device_data.get('pid', 'N/A')
    size    = device_data.get('size_gb', 0)

    import hashlib
    raw         = f"{serial}{vid}{pid}_{size}"
    fingerprint = hashlib.sha256(raw.encode()).hexdigest()
    now_local   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 1. القائمة البيضاء: يُسمح بالمرور ──────────────────────────────────
    if is_device_whitelisted(fingerprint):
        print(f"✅ Allowed (Whitelisted): {model}")
        log_event("DEVICE_ALLOWED", fingerprint, model, "Allowed", "System",
                  "Whitelisted device connected")
        return

    # ── 2. الحظر الفعلي عبر Registry + pnputil ──────────────────────────────
    print(f"🚫 Blocking device: {model}")
    if pnp_id:
        add_to_deny_list(pnp_id)
    disable_via_pnputil(pnp_id)

    # ── 3. القائمة السوداء: يُحظر ولا يُضاف لـ auto_blocked ──────────────────
    if device_exists_in_blacklist(fingerprint):
        print(f"⛔ Already in Blacklist: {model} — blocked, not added to auto_blocked")
        log_event("DEVICE_BLOCKED", fingerprint, model, "Blocked", "System",
                  f"Blacklisted device | HW_ID: {hw_id} | PNP: {pnp_id}")
        return

    # ── 4. جهاز غير معروف: يُضاف لـ auto_blocked ────────────────────────────
    conn = get_connection()
    if conn:
        cur = conn.cursor()
        # إضافة عمود pnp_device_id إن لم يكن موجوداً (توافق مع قواعد قديمة)
        try:
            cur.execute("ALTER TABLE auto_blocked ADD COLUMN pnp_device_id TEXT")
            conn.commit()
        except Exception:
            pass

        # تحقق هل موجود مسبقاً
        cur.execute("SELECT first_seen FROM auto_blocked WHERE fingerprint = ?", (fingerprint,))
        existing = cur.fetchone()

        if existing:
            # تحديث last_seen و pnp_device_id فقط
            cur.execute('''
                UPDATE auto_blocked
                SET last_seen = ?, pnp_device_id = ?
                WHERE fingerprint = ?
            ''', (now_local, pnp_id, fingerprint))
        else:
            cur.execute('''
                INSERT INTO auto_blocked
                (fingerprint, model, serial_number, vid, pid, size_gb,
                 pnp_device_id, block_reason, blocked_by, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'Auto-blocked by System Policy', 'SYSTEM', ?, ?)
            ''', (fingerprint, model, serial, vid, pid, size, pnp_id, now_local, now_local))

        conn.commit()
        conn.close()

    log_event("DEVICE_BLOCKED", fingerprint, model, "Blocked", "System",
              f"Unknown device | HW_ID: {hw_id} | PNP: {pnp_id}")


# ==================== كلاس USBBlocker (المطلوب لـ gui_main.py) ====================
class USBBlocker:
    """فئة للمنعة الفعلية لأجهزة USB عبر Registry"""
    @staticmethod
    def disable_usb_storage():
        """تعطيل جميع أجهزة التخزين USB عبر Registry"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Services\USBSTOR",
                0,
                winreg.KEY_SET_VALUE
            )
            winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, 4)
            winreg.CloseKey(key)
            
            log_event(
                event_type="USB_STORAGE_DISABLED",
                result="Success",
                user="System",
                details="All USB storage disabled via Registry"
            )
            return True
        except Exception as e:
            print(f"❌ Error disabling USB storage: {e}")
            return False

    @staticmethod
    def enable_usb_storage():
        """تمكين جميع أجهزة التخزين USB عبر Registry"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Services\USBSTOR",
                0,
                winreg.KEY_SET_VALUE
            )
            winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, 3)
            winreg.CloseKey(key)
            
            log_event(
                event_type="USB_STORAGE_ENABLED",
                result="Success",
                user="System",
                details="All USB storage enabled via Registry"
            )
            return True
        except Exception as e:
            print(f"❌ Error enabling USB storage: {e}")
            return False


    @staticmethod
    def get_usb_storage_status():
        """الحصول على حالة تخزين USB"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Services\USBSTOR",
                0,
                winreg.KEY_READ
            )
            value, _ = winreg.QueryValueEx(key, "Start")
            winreg.CloseKey(key)
            
            return "Disabled" if value == 4 else "Enabled"
        except:
            return "Unknown"
    
    @staticmethod
    def disable_specific_device(pnp_device_id):
        """تعطيل جهاز USB محدد بعينه تلقائياً"""
        if not pnp_device_id: return False
        try:
            cmd = f'pnputil /disable-device "{pnp_device_id}"'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode == 0 or "Disabled" in result.stdout or "تم التعطيل" in result.stdout:
                return True
            return False
        except Exception as e:
            print(f"❌ Error disabling specific device: {e}")
            return False

    @staticmethod
    def enable_specific_device(pnp_device_id):
        """تمكين جهاز USB محدد بعينه تلقائياً"""
        if not pnp_device_id: return False
        try:
            cmd = f'pnputil /enable-device "{pnp_device_id}"'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
             # 2. (مهم جداً) أمر إعادة مسح الأجهزة لتطبيق التغيير فوراً
            # هذا يحاكي عملية "Scan for hardware changes" في مدير الأجهزة
            cmd_scan = 'pnputil /scan-devices'
            subprocess.run(cmd_scan, shell=True, capture_output=True, text=True)
            if result.returncode == 0 or "Enabled" in result.stdout or "تم التمكين" in result.stdout:
                return True
            return False
        except Exception as e:
            print(f"❌ Error enabling specific device: {e}")
            return False
        


    # ══════════════════════════════════════════════════════════════════════
    # AutoPlay — المفاتيح التي يقرأ منها إعداد الويندوز (Settings app)
    # ══════════════════════════════════════════════════════════════════════
    #
    # المفتاح الوحيد الذي يتحكم بزر ON/OFF في Settings:
    #   HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\AutoplayHandlers
    #   القيمة: DisableAutoplay  (DWORD)  1=OFF  0=ON
    #
    # المفاتيح الإضافية (تمنع AutoRun على مستوى Policy):
    #   HKCU\...\Policies\Explorer  → NoDriveTypeAutoRun  0xFF=كل الأقراص
    #   HKLM\...\Policies\Explorer  → NoDriveTypeAutoRun  0xFF=كل الأقراص
    #
    # بعد أي تغيير Registry يجب إرسال WM_SETTINGCHANGE لتحديث الواجهة فوراً
    # ══════════════════════════════════════════════════════════════════════

    # tuple: (hive, subkey, value_name, reg_type, disable_value, enable_value)
    _AP_ENTRIES = [
        # المفتاح الرئيسي — يتحكم بزر ON/OFF في Settings
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\AutoplayHandlers",
         "DisableAutoplay", winreg.REG_DWORD, 1, 0),

        # Policy HKCU — يمنع AutoRun حتى لو كان الزر ON
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer",
         "NoDriveTypeAutoRun", winreg.REG_DWORD, 0xFF, 0x91),

        # Policy HKLM — يحتاج Admin (نتجاهل الفشل)
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer",
         "NoDriveTypeAutoRun", winreg.REG_DWORD, 0xFF, 0x91),
    ]

    # الحالة الأصلية قبل تدخل البرنامج
    _saved_autoplay_state: dict | None = None

    # ── دوال Registry المساعدة ────────────────────────────────────────────
    @staticmethod
    def _read_reg_value(hive, subkey, value_name):
        """يُرجع (value, reg_type) أو (None, None) إذا لم تكن موجودة."""
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as k:
                return winreg.QueryValueEx(k, value_name)
        except FileNotFoundError:
            return None, None
        except Exception as e:
            print(f"⚠️ _read_reg_value {subkey}\\{value_name}: {e}")
            return None, None

    @staticmethod
    def _write_reg_value(hive, subkey, value_name, reg_type, value) -> bool:
        """يكتب قيمة، يُنشئ المفتاح إن لزم."""
        try:
            with winreg.CreateKey(hive, subkey) as k:
                winreg.SetValueEx(k, value_name, 0, reg_type, value)
            return True
        except Exception as e:
            print(f"⚠️ _write_reg_value {subkey}\\{value_name}: {e}")
            return False

    @staticmethod
    def _delete_reg_value(hive, subkey, value_name) -> bool:
        """يحذف قيمة (لا يُخطئ إذا غير موجودة)."""
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, value_name)
            return True
        except FileNotFoundError:
            return True
        except Exception as e:
            print(f"⚠️ _delete_reg_value {subkey}\\{value_name}: {e}")
            return False

    @staticmethod
    def _broadcast_settings_change():
        """
        يُرسل WM_SETTINGCHANGE للويندوز حتى تتحدث نافذة Settings فوراً
        وتعكس التغيير الصحيح في زر AutoPlay ON/OFF.
        """
        try:
            HWND_BROADCAST   = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            result = ctypes.c_long()
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE,
                0, "AutoPlay",
                SMTO_ABORTIFHUNG, 3000,
                ctypes.byref(result)
            )
            print("📡 Settings broadcast sent → Windows notified")
        except Exception as e:
            print(f"⚠️ Broadcast failed (non-critical): {e}")

    # ── دوال AutoPlay الرئيسية ────────────────────────────────────────────
    @staticmethod
    def get_autoplay_status() -> str:
        """
        يفحص ما إذا كان AutoPlay مُعطَّلاً فعلياً.
        يُرجع "Disabled" أو "Enabled".
        المرجع الأساسي: DisableAutoplay في AutoplayHandlers (هو ما تعرضه Settings).
        """
        val, _ = USBBlocker._read_reg_value(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\AutoplayHandlers",
            "DisableAutoplay"
        )
        if val == 1:
            return "Disabled"

        val, _ = USBBlocker._read_reg_value(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer",
            "NoDriveTypeAutoRun"
        )
        if val == 0xFF:
            return "Disabled"

        return "Enabled"

    @staticmethod
    def _save_autoplay_state():
        """
        يحفظ القيم الحالية لجميع مفاتيح AutoPlay قبل أي تعديل.
        يُستدعى مرة واحدة فقط في عمر البرنامج.
        """
        if USBBlocker._saved_autoplay_state is not None:
            return

        state = {}
        for hive, subkey, name, rtype, _, _ in USBBlocker._AP_ENTRIES:
            val, _ = USBBlocker._read_reg_value(hive, subkey, name)
            state[f"{hive}|{subkey}|{name}"] = {
                'hive': hive, 'subkey': subkey,
                'name': name,  'rtype': rtype,
                'original': val,   # None = لم يكن موجوداً أصلاً
            }

        USBBlocker._saved_autoplay_state = state
        print("💾 AutoPlay original state saved")

    @staticmethod
    def disable_autoplay() -> bool:
        """
        يُعطِّل AutoPlay ويُحدِّث زر Settings فوراً.
          1. يحفظ الحالة الأصلية
          2. يكتب قيم التعطيل في كل المفاتيح
          3. يُرسل broadcast لتحديث الواجهة
        """
        USBBlocker._save_autoplay_state()

        ok = True
        for hive, subkey, name, rtype, disable_val, _ in USBBlocker._AP_ENTRIES:
            result = USBBlocker._write_reg_value(hive, subkey, name, rtype, disable_val)
            if hive != winreg.HKEY_LOCAL_MACHINE:   # HKLM فشله غير مقلق
                ok &= result

        # أخبر الويندوز بالتغيير → يتحدث زر Settings فوراً
        USBBlocker._broadcast_settings_change()

        if ok:
            log_event("AUTOPLAY_DISABLED", "", "", "Success", "System",
                      "AutoPlay disabled — Settings UI updated via broadcast")
            print("✅ AutoPlay disabled — Settings toggle now shows OFF")
        else:
            print("❌ AutoPlay disable partially failed")
        return ok

    @staticmethod
    def restore_autoplay() -> bool:
        """
        يُعيد AutoPlay لحالته الأصلية تماماً ويُحدِّث زر Settings.
          - مفتاح كان موجوداً → يُعيد قيمته
          - مفتاح لم يكن موجوداً → يحذفه (كأننا لم نلمسه)
          - يُرسل broadcast في النهاية
        """
        if USBBlocker._saved_autoplay_state is None:
            print("ℹ️ No AutoPlay state to restore")
            return True

        print("🔄 Restoring AutoPlay original state...")

        for key_id, info in USBBlocker._saved_autoplay_state.items():
            hive     = info['hive']
            subkey   = info['subkey']
            name     = info['name']
            rtype    = info['rtype']
            original = info['original']

            if original is None:
                USBBlocker._delete_reg_value(hive, subkey, name)
                print(f"  🗑️  Deleted (was absent): ...\\{name}")
            else:
                USBBlocker._write_reg_value(hive, subkey, name, rtype, original)
                print(f"  ✅ Restored: ...\\{name} = {original}")

        USBBlocker._saved_autoplay_state = None

        # أخبر الويندوز بالتغيير → يتحدث زر Settings فوراً
        USBBlocker._broadcast_settings_change()

        log_event("AUTOPLAY_RESTORED", "", "", "Success", "System",
                  "AutoPlay restored to original — Settings UI updated")
        print("✅ AutoPlay restored — Settings toggle reflects original state")
        return True

    @staticmethod
    def disable_autoplay_if_enabled() -> str:
        """
        يفحص AutoPlay عند التشغيل ويُعطّله إذا كان مُفعَّلاً.
        القيم المُرجَعة:
          "DISABLED_NOW" — كان مُفعَّلاً وتم إيقافه (زر Settings أصبح OFF)
          "ALREADY_OFF"  — كان مُطفأً مسبقاً
          "FAILED"       — فشلت العملية
        """
        USBBlocker._save_autoplay_state()   # دائماً احفظ أولاً

        status = USBBlocker.get_autoplay_status()
        if status == "Enabled":
            print("⚠️ AutoPlay is ON — disabling...")
            success = USBBlocker.disable_autoplay()
            return "DISABLED_NOW" if success else "FAILED"
        else:
            print("✅ AutoPlay already OFF — state saved for restore on exit")
            return "ALREADY_OFF"

    @staticmethod
    def unblock_and_reenable(pnp_id):
        """
        إلغاء الحظر الكامل وإعادة تفعيل الجهاز تلقائياً.

        المشكلة الجوهرية:
          - الـ pnp_id قد يكون USB\\VID_... (الأب) أو USBSTOR\\... (القرص)
          - تمكين الأب وحده لا يُظهر الفلاش في This PC
          - يجب تمكين جميع العقد المرتبطة بنفس الـ serial

        الحل: استخراج الـ serial من pnp_id ثم تمكين كل الأجهزة التي تحتويه
        """
        if not pnp_id:
            print("⚠️ PnPDeviceID not provided.")
            return False

        print(f"🔓 Unblocking: {pnp_id}")

        # ═══ الخطوة 1: استخراج الـ serial من pnp_id ═══
        # صيغة pnp_id: USB\VID_XXXX&PID_XXXX\SERIAL  أو  USBSTOR\...\SERIAL&0
        # أو: قد يكون serial مباشراً إذا لم يُعثر على pnp_id
        if '\\' in pnp_id:
            serial_part = pnp_id.split('\\')[-1]
            serial_clean = serial_part.split('&')[0].strip()
        else:
            # مُرِّر serial مباشرة
            serial_clean = pnp_id.strip()
            pnp_id = ''  # لا يوجد pnp_id فعلي
        print(f"  Serial for bulk-enable: {serial_clean}")

        # ═══ الخطوة 2: حذف من قائمة الحظر في Registry ═══
        if pnp_id:
            removed = remove_from_deny_list(pnp_id)
            print(f"  Registry removal: {'✅' if removed else '⚠️ not found (ok)'}")
        else:
            print("  Registry removal: skipped (serial-only mode)")

        # ═══ الخطوة 3: تعطيل السياسة الاسترجاعية مؤقتاً ═══
        try:
            winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions")
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions",
                                0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "DenyDeviceIDsRetroactive", 0, winreg.REG_DWORD, 0)
            print("  ✅ Retroactive policy paused")
        except Exception as e:
            print(f"  ⚠️ Policy note: {e}")

        time.sleep(0.3)

        # ═══ الخطوة 4: تمكين كل الأجهزة المرتبطة بالـ serial ═══
        # هذا يشمل: USB\\VID_...\\SERIAL (الأب) + USBSTOR\\...\\SERIAL (القرص)
        # نستخدم PowerShell مع Get-PnpDevice لإيجاد كل العقد وتمكينها
        ps_ok = False

        if serial_clean:
            # تمكين كل الأجهزة التي تحتوي الـ serial في InstanceId
            ps_all = f'''
$serial = "{serial_clean}"
$devices = Get-PnpDevice | Where-Object {{ $_.InstanceId -like "*$serial*" }}
foreach ($dev in $devices) {{
    Write-Host "Enabling: $($dev.InstanceId) [$($dev.Status)]"
    Enable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
}}
Write-Host "Done enabling $($devices.Count) device(s)"
'''
            try:
                res = subprocess.run(
                    ['powershell', '-NonInteractive', '-Command', ps_all],
                    capture_output=True, text=True, timeout=30
                )
                ps_ok = res.returncode == 0
                if res.stdout.strip():
                    print(f"  PS output: {res.stdout.strip()[:200]}")
                print(f"  ✅ PowerShell bulk enable: {'OK' if ps_ok else res.stderr.strip()[:80]}")
            except Exception as e:
                print(f"  ⚠️ PS bulk error: {e}")

        # ═══ Fallback: تمكين الـ pnp_id المُعطى مباشرةً (إذا كان متاحاً) ═══
        if pnp_id:
            try:
                res2 = subprocess.run(
                    ['powershell', '-NonInteractive', '-Command',
                     f'Enable-PnpDevice -InstanceId "{pnp_id}" -Confirm:$false -ErrorAction SilentlyContinue'],
                    capture_output=True, text=True, timeout=20
                )
                if res2.returncode == 0:
                    ps_ok = True
                print(f"  Direct enable fallback: {'✅' if res2.returncode == 0 else '⚠️'}")
            except Exception as e:
                print(f"  ⚠️ Direct enable error: {e}")

        # ═══ Fallback: pnputil ═══
        pnp_ok = False
        if pnp_id:
            try:
                r2 = subprocess.run(
                    f'pnputil /enable-device "{pnp_id}"',
                    shell=True, capture_output=True, text=True, timeout=15
                )
                pnp_ok = r2.returncode == 0 or "Enabled" in r2.stdout
                print(f"  pnputil Enable: {'✅' if pnp_ok else '⚠️'}")
            except Exception as e:
                pnp_ok = False

        # ═══ الخطوة 5: مسح الأجهزة لإظهار الفلاش في This PC ═══
        time.sleep(1)
        try:
            subprocess.run('pnputil /scan-devices',
                           shell=True, capture_output=True, text=True, timeout=15)
            print("  ✅ Hardware scan triggered")
        except Exception:
            pass

        # ═══ الخطوة 6: إعادة السياسة الاسترجاعية ═══
        time.sleep(0.5)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions",
                                0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "DenyDeviceIDsRetroactive", 0, winreg.REG_DWORD, 1)
            print("  ✅ Retroactive policy restored")
        except Exception as e:
            print(f"  ⚠️ Restore policy: {e}")

        print("✅ Unblock complete")
        return ps_ok or pnp_ok