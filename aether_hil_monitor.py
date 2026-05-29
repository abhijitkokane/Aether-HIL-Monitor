from gevent import monkey
monkey.patch_all()  # CRITICAL SENIOR FIX: Must be line 1 to unblock underlying POSIX network/process abstraction layers

import gevent  
import os
import cv2
import numpy as np
import time
import subprocess
import threading
import base64
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO

app = Flask(__name__)

# =====================================================================
# SYSTEM INFRASTRUCTURE & CONFIGURATION
# =====================================================================
socketio = SocketIO(
    app, 
    cors_allowed_origins="*", 
    async_mode='gevent', 
    max_http_buffer_size=50 * 1024 * 1024
)

# Thread-safe global memory configurations
DEVICE_TELEMETRY = {}
telemetry_lock = threading.Lock()

ACTIVE_WORKER_THREADS = set()
worker_lock = threading.Lock()

LAB_DEVICE_ROLES = {}  # Optional cluster mapping overrides: e.g., {"SERIAL_ID": "Primary_Display"}



# =====================================================================
# HARDWARE INTERFACE HELPER FUNCTIONS
# =====================================================================
def get_adb_devices():
    """Parses attached hardware interfaces natively via host subprocess pipelines."""
    try:
        output = subprocess.check_output(["adb", "devices"]).decode("utf-8")
        lines = output.strip().split("\n")[1:]
        return [line.split("\t")[0] for line in lines if "\tdevice" in line]
    except Exception:
        return []



def get_device_model(device_id):
    """Queries Android system properties to extract the true hardware marketing name."""
    try:
        cmd = ["adb", "-s", device_id, "shell", "getprop", "ro.product.model"]
        model_name = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8").strip()
        return model_name if model_name else f"Unknown Target ({device_id})"
    except Exception:
        return f"Generic Android ({device_id})"



def get_device_temperature(device_id):
    """Queries internal Android thermal sensors natively over the USB interface."""
    try:
        cmd = ["adb", "-s", device_id, "shell", "dumpsys", "battery"]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8")
        for line in output.splitlines():
            if "temperature:" in line:
                return float(int(line.split()[-1]) / 10.0)
        return 30.0 
    except Exception:
        try:
            cmd = ["adb", "-s", device_id, "shell", "cat", "/sys/class/thermal/thermal_zone0/temp"]
            raw_val = int(subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8").strip())
            return float(raw_val / 1000.0) if raw_val > 1000 else float(raw_val)
        except Exception:
            return 32.0 



# =====================================================================
# HIL ALGORITHMIC ENGINE DESIGN
# =====================================================================
class HILVisualValidator:
    def __init__(self, device_id):
        self.device_id = device_id
        self.prev_gray = None
        self.frame_count = 0
        self.start_time = time.time()
        self.frozen_frames = 0
        self.roi_coords = (0, 150, 540, 600)
        
    def analyze_frame(self, frame, current_temp=32.0):
        """Processes raw OS framebuffer matrix to evaluate rendering stream pipeline stability."""
        if frame is None:
            return None
        h, w, _ = frame.shape
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        mean_b, mean_g, mean_r, _ = cv2.mean(frame)
        decoder_fault = "NORMAL"
        if current_temp >= 40.0:
            decoder_fault = "THERMAL_OVERHEAT_ALERT"
        elif mean_g > 240 and mean_r < 15 and mean_b < 15:
            decoder_fault = "DECODER_CRASH_GREEN_SCREEN"
        elif mean_g < 5 and mean_r < 5 and mean_b < 5:
            decoder_fault = "GRAPHICS_DROPOUT_BLACK_SCREEN"
        change_pct = 0.0
        if self.prev_gray is not None:
            delta = cv2.absdiff(self.prev_gray, gray)
            _, thresh = cv2.threshold(delta, 20, 255, cv2.THRESH_BINARY)
            change_pct = (cv2.countNonZero(thresh) / gray.size) * 100.0
        rx, ry, rw, rh = self.roi_coords
        rx, ry = min(rx, w - 10), min(ry, h - 10)
        rw, rh = min(rw, w - rx), min(rh, h - ry)
        regional_freeze = False
        if self.prev_gray is not None and rw > 0 and rh > 0:
            roi_curr = gray[ry:ry+rh, rx:rx+rw]
            roi_prev = self.prev_gray[ry:ry+rh, rx:rx+rw]
            if cv2.countNonZero(cv2.threshold(cv2.absdiff(roi_curr, roi_prev), 10, 255, cv2.THRESH_BINARY)[1]) == 0:
                regional_freeze = True
        self.prev_gray = gray
        self.frame_count += 1
        elapsed = time.time() - self.start_time
        current_fps = self.frame_count / elapsed if elapsed > 0 else 0.0
        if change_pct < 1.0:
            self.frozen_frames += 1
            motion_status = "STUCK/HANGING" if self.frozen_frames > 90 else "STATIC"
        elif regional_freeze and change_pct < 4.0:
            motion_status = "STUCK/HANGING"
        else:
            self.frozen_frames = 0
            motion_status = "HEALTHY_ANIMATION"
        if decoder_fault != "NORMAL":
            status = "THERMAL_AND_FROZEN" if motion_status == "STUCK/HANGING" else "COLOR_CRITICAL_FAULT"
        else:
            status = motion_status
        return {
            "fps": round(current_fps, 1),
            "change_pct": round(change_pct, 2),
            "status": status,
            "decoder_fault": decoder_fault,
            "channels": {"R": round(mean_r, 1), "G": round(mean_g, 1), "B": round(mean_b, 1)}
        }



# =====================================================================
# CONCURRENCY PIPELINE WORKERS
# =====================================================================
def stream_device_worker(device_id, name_label):
    """Dedicated thread loop fetching and evaluating frames for a single hardware target."""
    print(f"[CORE] Spawning HIL pipeline thread for device: {device_id} ({name_label})")
    validator = HILVisualValidator(device_id)
    cmd = ["adb", "-s", device_id, "shell", "screencap", "-p"]
    with telemetry_lock:
        DEVICE_TELEMETRY[device_id] = {
            "name": name_label, "fps": 0, "status": "CONNECTING", "temperature": 0.0,
            "change_pct": 0.0, "color_logs": "R: 0 | G: 0 | B: 0", "encoded_frame": "",
            "decoder_fault": "NORMAL", "is_oled": True
        }
    thermal_check_counter = 0
    current_temp = 32.0  
    while True:
        try:
            if thermal_check_counter % 25 == 0:
                current_temp = get_device_temperature(device_id)
                thermal_check_counter = 0
            thermal_check_counter += 1
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            png_bytes, _ = proc.communicate()
            if not png_bytes:
                if device_id not in get_adb_devices(): raise RuntimeError("Node disconnected")
                gevent.sleep(0.02)
                continue
            frame = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                if device_id not in get_adb_devices(): raise RuntimeError("Decode extraction failed")
                gevent.sleep(0.01)
                continue
            h, w, _ = frame.shape
            small_frame = cv2.resize(frame, (270, int(270 * (h / w))), interpolation=cv2.INTER_AREA)
            metrics = validator.analyze_frame(small_frame, current_temp)
            if metrics:
                _, buffer = cv2.imencode('.jpg', small_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                with telemetry_lock:
                    if device_id in DEVICE_TELEMETRY:
                        DEVICE_TELEMETRY[device_id].update({
                            "fps": metrics["fps"], "status": metrics["status"], "temperature": current_temp, 
                            "change_pct": metrics["change_pct"], "decoder_fault": metrics["decoder_fault"],
                            "color_logs": f"R: {metrics['channels']['R']} | G: {metrics['channels']['G']} | B: {metrics['channels']['B']}",
                            "encoded_frame": base64.b64encode(buffer).decode('utf-8')
                        })
                socketio.emit('telemetry_update', DEVICE_TELEMETRY)
            gevent.sleep(0.033)
        except Exception as e:
            print(f"[AETHER-HIL Rig Alert] Thread {device_id} exception: {e}")
            if device_id not in get_adb_devices():
                print(f"[CLEANUP Worker]: Pruning disconnected node memory allocations for ID: {device_id}")
                with telemetry_lock:
                    if device_id in DEVICE_TELEMETRY: del DEVICE_TELEMETRY[device_id]
                socketio.emit('telemetry_update', DEVICE_TELEMETRY)
                break  
            gevent.sleep(1.0)



def orchestrate_hardware_lab():
    """Continuously loops in the background to handle hot-plug events cleanly."""
    global ACTIVE_WORKER_THREADS
    print("[SYSTEM] Dynamic Hardware Orchestrator initiated. Monitoring USB infrastructure...")
    while True:
        try:
            current_devices = get_adb_devices()
            with worker_lock:
                for dead_dev in [dev for dev in ACTIVE_WORKER_THREADS if dev not in DEVICE_TELEMETRY]:
                    ACTIVE_WORKER_THREADS.remove(dead_dev)
                for dev_id in current_devices:
                    if dev_id not in ACTIVE_WORKER_THREADS:
                        label = LAB_DEVICE_ROLES.get(dev_id, get_device_model(dev_id))
                        print(f"[HOT-PLUG] New hardware target verified: {dev_id} ({label}). Initializing stream...")
                        ACTIVE_WORKER_THREADS.add(dev_id)
                        threading.Thread(target=stream_device_worker, args=(dev_id, label), daemon=True).start()
        except Exception as scan_error:
            print(f"[ORCHESTRATOR ERROR] Issue scanning hardware state: {scan_error}")
        gevent.sleep(2.0)



def launch_app_on_device(device_id, package_name, activity_name):
    """Forces execution of an app package activity across specific connected hardware nodes."""
    try:
        print(f"[LAUNCHER] Sending sync-pulse to device: {device_id}")
        subprocess.run(["adb", "-s", device_id, "shell", "am", "force-stop", package_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["adb", "-s", device_id, "shell", "am", "start", "-n", f"{package_name}/{activity_name}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return (device_id, True)
    except Exception as e:
        print(f"[LAUNCHER ERROR] Failed to broadcast to {device_id}: {e}")
        return (device_id, False)



def broadcast_simultaneous_simulation(package_name, activity_name):
    """Spawns concurrent greenlets to hit all hardware paths instantly without locking the OS loop."""
    devices = get_adb_devices()
    if not devices:
        print("[BROADCAST] Aborted: No connected hardware targets detected.")
        return
    print(f"\n[SYSTEM] --- INITIALIZING PARALLEL SIMULATION BROADCAST (Devices: {len(devices)}) ---")
    greenlets = [gevent.spawn(launch_app_on_device, dev_id, package_name, activity_name) for dev_id in devices]
    gevent.joinall(greenlets, timeout=4.0)
    print("[SYSTEM] --- SIMULTANEOUS MATRIX PULSE COMPLETE ---\n")



# =====================================================================
# SYSTEM APPLICATION WEBMAP ROUTING INDICES
# =====================================================================
@app.route('/')
def index():
    return render_template('aether_hil_templates_dashboard.html')



@app.route('/api/telemetry')
def get_telemetry():
    with telemetry_lock:
        return jsonify(DEVICE_TELEMETRY)



@app.route('/api/launch_simulation')
def trigger_simulation():
    """Web hook execution endpoint to safely trigger your simulation."""
    TARGET_PACKAGE = "com.android.chrome"
    TARGET_ACTIVITY = "com.google.android.apps.chrome.Main"
    broadcast_simultaneous_simulation(TARGET_PACKAGE, TARGET_ACTIVITY)
    return jsonify({"status": "broadcast_complete", "devices_pulsed": len(get_adb_devices())})



# =====================================================================
# SYSTEM INITIALIZATION POINT
# =====================================================================
if __name__ == '__main__':
    gevent.spawn(orchestrate_hardware_lab)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
