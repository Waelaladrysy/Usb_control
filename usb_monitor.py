# -*- coding: utf-8 -*-
"""
USB Monitor - النسخة المصحّحة
الإصلاحات:
  1. استخراج بيانات الـ disk في نفس thread الـ COM قبل تمريرها
  2. إعادة تهيئة WMI تلقائياً عند الانهيار
  3. CoInitialize صريح في thread معالجة الأجهزة
  4. join() بـ timeout لتجنب تجميد الواجهة
  5. callback آمن عبر root.after() بدلاً من استدعاء مباشر
"""
import time
import threading
import wmi
import pythoncom
import re
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from usb_blocker import check_and_block_device
except ImportError:
    print("⚠️ Warning: usb_blocker not found.")
    check_and_block_device = None


def get_smart_vid_pid(pnp_id, serial):
    """
    استخراج ذكي لـ VID و PID.
    يجب استدعاؤها فقط من thread أُجري فيه CoInitialize مسبقاً.
    """
    vid, pid = "N/A", "N/A"
    if not pnp_id:
        return vid, pid

    # 1. استخراج مباشر من صيغة USB القياسية
    direct_match = re.search(r'USB\\VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})', pnp_id)
    if direct_match:
        return direct_match.group(1), direct_match.group(2)

    # 2. Fallback: البحث عن الجهاز الأب إذا كان المسار USBSTOR
    # ملاحظة: CoInitialize يجب أن يكون قد استُدعي في هذا الـ thread مسبقاً
    if pnp_id.startswith("USBSTOR") and serial and serial != "N/A":
        try:
            c = wmi.WMI()
            for pnp in c.Win32_PnPEntity():
                dev_pnp = getattr(pnp, 'PNPDeviceID', '') or ''
                if serial in dev_pnp and dev_pnp.startswith("USB\\VID_"):
                    vm = re.search(r'VID_([0-9A-Fa-f]{4})', dev_pnp)
                    pm = re.search(r'PID_([0-9A-Fa-f]{4})', dev_pnp)
                    if vm: vid = vm.group(1)
                    if pm: pid = pm.group(1)
                    break
        except Exception as e:
            print(f"⚠️ VID/PID fallback WMI error: {e}")

    return vid, pid


class USBMonitor:
    def __init__(self, on_device_change=None, tk_root=None):
        """
        on_device_change: callback(action, data) يُستدعى آمناً في main thread
        tk_root: نافذة Tkinter الجذر — إذا مُرِّرت، يُستخدم root.after() للـ callback
        """
        self.running = False
        self.monitor_thread = None
        self.callback = on_device_change
        self.tk_root = tk_root
        self.known_pnp_ids = set()

    def start(self):
        if self.running:
            return
        self.running = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="USBMonitorThread"
        )
        self.monitor_thread.start()
        print("✅ USB Monitor service started")

    def stop(self):
        self.running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)  # الإصلاح 4: timeout لتجنب التجميد
        print("⏹️ USB Monitor service stopped")

    def _safe_callback(self, action, data):
        """
        الإصلاح 5: استدعاء الـ callback آمناً في main thread.
        إذا كان tk_root متاحاً نستخدم after(0)، وإلا نستدعي مباشرة.
        """
        if not self.callback:
            return
        if self.tk_root:
            try:
                self.tk_root.after(0, self.callback, action, data)
            except Exception:
                pass  # النافذة ربما أُغلقت
        else:
            self.callback(action, data)

    def _monitor_loop(self):
        """حلقة المراقبة مع إعادة تهيئة WMI تلقائية عند الانهيار."""
        pythoncom.CoInitialize()
        try:
            while self.running:
                try:
                    self._monitor_cycle()
                except Exception as e:
                    print(f"⚠️ Monitor cycle error (will retry in 5s): {e}")
                    time.sleep(5)  # الإصلاح 2: إعادة المحاولة بدلاً من الخروج
        finally:
            pythoncom.CoUninitialize()

    def _monitor_cycle(self):
        """
        الإصلاح 2: إنشاء wmi.WMI() في كل دورة.
        يضمن التعافي التلقائي إذا انهارت جلسة WMI.
        """
        c = wmi.WMI()
        while self.running:
            try:
                disks = c.Win32_DiskDrive(InterfaceType="USB")
            except Exception as e:
                print(f"⚠️ WMI query failed: {e}")
                raise  # سيُعاد إنشاء WMI في _monitor_loop

            current_pnp_ids = set()

            for disk in disks:
                pnp_id = getattr(disk, 'PNPDeviceID', '') or ''
                if not pnp_id:
                    continue
                current_pnp_ids.add(pnp_id)

                if pnp_id not in self.known_pnp_ids:
                    self.known_pnp_ids.add(pnp_id)

                    # الإصلاح 1: استخراج جميع بيانات disk هنا في thread الـ COM
                    # قبل تمريرها لـ thread آخر لتجنب COMException
                    device_snapshot = self._extract_disk_data(disk, pnp_id)

                    threading.Thread(
                        target=self._process_new_device,
                        args=(device_snapshot,),
                        daemon=True,
                        name=f"USBProcess-{pnp_id[-10:]}"
                    ).start()

            removed = self.known_pnp_ids - current_pnp_ids
            if removed:
                self.known_pnp_ids -= removed
                for r_pnp in removed:
                    self._safe_callback("removed", r_pnp)

            time.sleep(2)

    def _extract_disk_data(self, disk, pnp_id):
        """
        الإصلاح 1: استخراج جميع قيم disk في thread الـ COM الصحيح.
        يُرجع dict من القيم الخام (strings/numbers) آمنة للتمرير بين الـ threads.
        """
        try:
            serial  = (disk.SerialNumber or "").strip() or "N/A"
            model   = (disk.Model or "Unknown").strip()
            size_gb = round(int(disk.Size) / (1024 ** 3), 2) if disk.Size else 0
        except Exception as e:
            print(f"⚠️ Error extracting disk data: {e}")
            serial  = "N/A"
            model   = "Unknown"
            size_gb = 0

        return {
            'pnp_id':  pnp_id,
            'serial':  serial,
            'model':   model,
            'size_gb': size_gb,
        }

    def _process_new_device(self, device_snapshot: dict):
        """
        الترتيب الصحيح:
          1. CoInitialize
          2. استخراج VID/PID
          3. إرسال callback للواجهة فوراً → يظهر الجهاز في القوائم
          4. الحظر في thread مستقل بعد 2 ثانية → يضمن أن الواجهة تعرضه أولاً
        """
        pythoncom.CoInitialize()
        try:
            pnp_id  = device_snapshot['pnp_id']
            serial  = device_snapshot['serial']
            model   = device_snapshot['model']
            size_gb = device_snapshot['size_gb']

            # الآن CoInitialize جرى، فـ get_smart_vid_pid تعمل بأمان
            vid, pid = get_smart_vid_pid(pnp_id, serial)

            device_data = {
                'pnp_id':      pnp_id,
                'hardware_id': f"USB\\VID_{vid}&PID_{pid}",
                'model':       model,
                'serial':      serial,
                'vid':         vid,
                'pid':         pid,
                'size_gb':     size_gb,
            }

            print(f"🔔 Detected: {model} | VID/PID: {vid}/{pid} | Serial: {serial}")

            # ── أولاً: أرسل callback للواجهة فوراً ──────────────────────────
            # يضمن ظهور الجهاز في القوائم قبل أي حظر
            self._safe_callback("new", device_data)

            # ── ثانياً: الحظر في thread مستقل بعد تأخير ─────────────────────
            # التأخير يتيح للواجهة معالجة الـ callback وعرض الجهاز أولاً
            threading.Thread(
                target=self._delayed_block,
                args=(device_data,),
                daemon=True,
                name=f"USBBlock-{pnp_id[-10:]}"
            ).start()

        except Exception as e:
            print(f"❌ Error processing device: {e}")
        finally:
            pythoncom.CoUninitialize()

    def _delayed_block(self, device_data: dict):
        """
        الحظر المؤجل — يعمل بعد 2 ثانية في thread مستقل.
        يتحقق من حالة الجهاز قبل الحظر — إذا أصبح في whitelist يتخطى الحظر.
        هذا يحل مشكلة: نقل من blacklist→whitelist ثم فصل وإعادة توصيل الجهاز.
        """
        pythoncom.CoInitialize()
        try:
            time.sleep(2)
            if check_and_block_device:
                # تحقق من الحالة الحالية قبل الحظر
                # إذا أصبح الجهاز في whitelist خلال الـ 2 ثانية لا تحظره
                try:
                    import hashlib
                    serial = device_data.get('serial', 'N/A')
                    vid    = device_data.get('vid', 'N/A')
                    pid    = device_data.get('pid', 'N/A')
                    size   = device_data.get('size_gb', 0)
                    raw    = f"{serial}{vid}{pid}_{size}"
                    fp     = hashlib.sha256(raw.encode()).hexdigest()

                    from security_logic.whitelist_manager import is_device_whitelisted
                    if is_device_whitelisted(fp):
                        print(f"✅ Skip block — device is now whitelisted: {device_data.get('model')}")
                        return
                except Exception:
                    pass  # إذا فشل التحقق، نكمل للحظر الطبيعي

                check_and_block_device(device_data)
        except Exception as e:
            print(f"❌ Delayed block error: {e}")
        finally:
            pythoncom.CoUninitialize()