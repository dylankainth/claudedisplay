#!/usr/bin/env python3
"""
Claude Usage Dashboard - Pi Zero W (Remote API + Web UI)
- Calls remote API server
- Displays on 16x2 LCD
- Web server with contrast slider (http://pi-ip:8080)
"""

import subprocess
import time
from datetime import datetime
import RPi.GPIO as GPIO
import sys
import urllib.request
import json
import threading
from flask import Flask, render_template_string, request, jsonify

# ============ Configuration ============
GPIO.setmode(GPIO.BCM)

# LCD Control pins
RS_PIN = 27
EN_PIN = 17
D4_PIN = 25
D5_PIN = 24
D6_PIN = 23
D7_PIN = 18

# PWM Contrast
CONTRAST_PIN = 12
CONTRAST_PWM_FREQ = 1000
CONTRAST_DUTY = 0  # 0-100

# PWM Brightness (Backlight)
BRIGHTNESS_PIN = 13
BRIGHTNESS_PWM_FREQ = 1000
BRIGHTNESS_DUTY = 100  # 0-100

# API Configuration - FIXED IP ADDRESS
API_URL = "http://192.168.0.67:5000/usage"
POLL_INTERVAL = 20  # seconds

# Web server
WEB_PORT = 8080

# Global state
current_usage = {
    "session": "--",
    "weekly": "--",
    "status": "?",
    "sessionResetIn": "--",
    "weeklyResetIn": "--"
}
pwm = None
pwm_brightness = None
lcd = None

# ============ HD44780 LCD Driver ============
class LCD16x2:
    """16x2 HD44780 LCD driver (parallel 4-bit mode)"""

    def __init__(self, rs, en, d4, d5, d6, d7):
        self.rs = rs
        self.en = en
        self.d4 = d4
        self.d5 = d5
        self.d6 = d6
        self.d7 = d7

        for pin in [self.rs, self.en, self.d4, self.d5, self.d6, self.d7]:
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)

        self.init_display()

    def _pulse_enable(self):
        GPIO.output(self.en, GPIO.HIGH)
        time.sleep(0.0005)
        GPIO.output(self.en, GPIO.LOW)
        time.sleep(0.0005)

    def _write_nibble(self, nibble):
        GPIO.output(self.d4, GPIO.HIGH if (nibble & 0x01) else GPIO.LOW)
        GPIO.output(self.d5, GPIO.HIGH if (nibble & 0x02) else GPIO.LOW)
        GPIO.output(self.d6, GPIO.HIGH if (nibble & 0x04) else GPIO.LOW)
        GPIO.output(self.d7, GPIO.HIGH if (nibble & 0x08) else GPIO.LOW)
        self._pulse_enable()

    def _write_byte(self, value, mode):
        GPIO.output(self.rs, GPIO.HIGH if mode else GPIO.LOW)
        self._write_nibble(value >> 4)
        time.sleep(0.0001)
        self._write_nibble(value & 0x0F)
        time.sleep(0.0001)

    def write_cmd(self, cmd):
        self._write_byte(cmd, 0)
        time.sleep(0.001)

    def write_data(self, data):
        self._write_byte(data, 1)
        time.sleep(0.001)

    def init_display(self):
        time.sleep(0.05)
        self._write_nibble(0x03)
        time.sleep(0.005)
        self._write_nibble(0x03)
        time.sleep(0.005)
        self._write_nibble(0x03)
        time.sleep(0.005)
        self._write_nibble(0x02)
        time.sleep(0.005)

        self.write_cmd(0x28)
        time.sleep(0.001)
        self.write_cmd(0x0C)
        time.sleep(0.001)
        self.write_cmd(0x01)
        time.sleep(0.002)
        self.write_cmd(0x06)
        time.sleep(0.001)

    def set_cursor(self, row, col):
        addr = 0x00 if row == 0 else 0x40
        addr += col
        self.write_cmd(0x80 | addr)
        time.sleep(0.001)

    def print(self, row, col, text):
        self.set_cursor(row, col)
        for char in text[:16]:
            self.write_data(ord(char))

    def clear(self):
        self.write_cmd(0x01)
        time.sleep(0.002)

    def create_char(self, location, pattern):
        """Upload a custom 5x8 character to CGRAM"""
        location &= 0x07
        self.write_cmd(0x40 | (location << 3))
        time.sleep(0.001)
        for row in pattern:
            self.write_data(row)
        # Reset DDRAM address to 0
        self.write_cmd(0x80)
        time.sleep(0.001)

    def cleanup(self):
        GPIO.cleanup()

# ============ API Calls ============
def fetch_usage():
    """Fetch usage from remote API server"""
    try:
        with urllib.request.urlopen(API_URL, timeout=8) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"[ERROR] Fetch failed: {e}")
        return None

# ============ Web Server ============
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Claude Dashboard</title>
    <style>
        body {
            font-family: monospace;
            max-width: 600px;
            margin: 50px auto;
            background: #1e1e1e;
            color: #00ff00;
            padding: 20px;
        }
        .container {
            background: #2d2d2d;
            padding: 20px;
            border-radius: 5px;
            border: 1px solid #00ff00;
        }
        h1 { color: #00ff00; text-align: center; }
        .usage {
            font-size: 24px;
            margin: 20px 0;
            text-align: center;
        }
        .reset {
            font-size: 14px;
            color: #888;
            margin: 10px 0;
        }
        .control {
            margin: 30px 0;
        }
        .slider-label {
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
        }
        input[type="range"] {
            width: 100%;
            height: 10px;
        }
        button {
            background: #00ff00;
            color: #1e1e1e;
            border: none;
            padding: 10px 20px;
            cursor: pointer;
            font-weight: bold;
            width: 100%;
            margin-top: 10px;
        }
        button:hover {
            background: #00cc00;
        }
        .status {
            text-align: center;
            font-size: 18px;
            margin: 20px 0;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Claude Usage Dashboard</h1>

        <div class="usage">
            <div>Session: <span id="session">--</span> <span id="sessionReset" class="reset"></span></div>
            <div>Weekly: <span id="weekly">--</span> <span id="weeklyReset" class="reset"></span></div>
        </div>

        <div class="status">
            Status: <span id="status">?</span>
        </div>

        <div class="control">
            <div class="slider-label">
                <label>Contrast</label>
                <span id="contrastValue">0%</span>
            </div>
            <input type="range" id="contrastSlider" min="0" max="100" value="0">
        </div>

        <div class="control">
            <div class="slider-label">
                <label>Brightness</label>
                <span id="brightnessValue">100%</span>
            </div>
            <input type="range" id="brightnessSlider" min="0" max="100" value="100">
        </div>

        <div style="text-align: center; color: #666; font-size: 12px; margin-top: 30px;">
            Updates every 20 seconds
        </div>
    </div>

    <script>
        // Update usage data
        function updateUsage() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('session').textContent = data.session;
                    document.getElementById('weekly').textContent = data.weekly;
                    document.getElementById('status').textContent = data.status;
                    document.getElementById('sessionReset').textContent = '(resets in ' + data.sessionResetIn + ')';
                    document.getElementById('weeklyReset').textContent = '(resets in ' + data.weeklyResetIn + ')';
                });
        }

        // Update contrast slider
        function updateContrast() {
            const value = document.getElementById('contrastSlider').value;
            document.getElementById('contrastValue').textContent = value + '%';
            fetch('/api/contrast', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({duty: parseInt(value)})
            });
        }

        // Update brightness slider
        function updateBrightness() {
            const value = document.getElementById('brightnessSlider').value;
            document.getElementById('brightnessValue').textContent = value + '%';
            fetch('/api/brightness', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({duty: parseInt(value)})
            });
        }

        // Initial update and set interval
        updateUsage();
        setInterval(updateUsage, 20000);

        document.getElementById('contrastSlider').addEventListener('input', updateContrast);
        document.getElementById('brightnessSlider').addEventListener('input', updateBrightness);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/status')
def get_status_api():
    return jsonify(current_usage)

@app.route('/api/contrast', methods=['POST'])
def set_contrast():
    global pwm
    data = request.json
    duty = data.get('duty', 0)

    if pwm and 0 <= duty <= 100:
        pwm.ChangeDutyCycle(duty)
        print(f"✓ Contrast set to {duty}%")
        return jsonify({"status": "ok", "duty": duty})

    return jsonify({"status": "error"}), 400

@app.route('/api/brightness', methods=['POST'])
def set_brightness():
    global pwm_brightness
    data = request.json
    duty = data.get('duty', 100)

    if pwm_brightness and 0 <= duty <= 100:
        pwm_brightness.ChangeDutyCycle(duty)
        print(f"✓ Brightness set to {duty}%")
        return jsonify({"status": "ok", "duty": duty})

    return jsonify({"status": "error"}), 400

# ============ LCD Helpers & Main Loop ============
def parse_percentage(val_str):
    if not val_str or val_str == '--':
        return 0
    try:
        return int(str(val_str).replace('%', '').strip())
    except ValueError:
        return 0

def generate_bar(percentage, width=5):
    steps = int(round((percentage / 100.0) * (width * 2)))
    steps = max(0, min(steps, width * 2))
    full_blocks = steps // 2
    half_blocks = steps % 2
    empty_blocks = width - full_blocks - half_blocks
    # chr(1) is our custom full block, chr(2) is custom half block
    return chr(1) * full_blocks + chr(2) * half_blocks + ' ' * empty_blocks

def format_right_side(val_str, reset_str):
    """Returns exactly a 7-character string for the right side."""
    pct = parse_percentage(val_str)
    if pct >= 100:
        # Show countdown, compress spaces if needed to fit (e.g. "24h 57m" -> "24h57m")
        text = str(reset_str).strip()
        if len(text) > 7:
            text = text.replace(" ", "")
        return text[:7].rjust(7)
    else:
        # Show usage level (e.g. "  51%")
        v = str(val_str).strip()
        if not v.endswith('%') and v != '--':
            v += '%'
        return v.rjust(7)

def lcd_update_loop():
    """Background thread: update LCD with API data"""
    global current_usage

    # Splash screen
    lcd.clear()
    lcd.print(0, 0, "Claude Usage")
    lcd.print(1, 0, "Connecting...")
    time.sleep(2)

    while True:
        data = fetch_usage()

        if data:
            current_usage = {
                "session": data.get('session', '--'),
                "weekly": data.get('weekly', '--'),
                "status": data.get('status', 'OK'),
                "sessionResetIn": data.get('sessionResetIn', '--'),
                "weeklyResetIn": data.get('weeklyResetIn', '--')
            }
        else:
            current_usage['status'] = '?'

        # Generate visual progress bars
        s_bar = generate_bar(parse_percentage(current_usage['session']))
        w_bar = generate_bar(parse_percentage(current_usage['weekly']))

        # Format the right side (usage % or countdown)
        s_right = format_right_side(current_usage['session'], current_usage['sessionResetIn'])
        w_right = format_right_side(current_usage['weekly'], current_usage['weeklyResetIn'])

        # Compile exact 16-character layout per line
        # Left side takes 9 chars ("S:[█████]"), Right side takes 7 chars
        line0 = f"S:[{s_bar}]{s_right}"
        line1 = f"W:[{w_bar}]{w_right}"

        line0 = line0[:16].ljust(16)
        line1 = line1[:16].ljust(16)

        # Update display
        try:
            lcd.clear()
            lcd.print(0, 0, line0)
            lcd.print(1, 0, line1)

            # Clean up the console log so custom characters don't look like gibberish
            log0 = line0.replace(chr(1), '#').replace(chr(2), '+')
            log1 = line1.replace(chr(1), '#').replace(chr(2), '+')
            print(f"✓ Display: {log0} | {log1}")
        except Exception as e:
            print(f"✗ Display error: {e}")

        time.sleep(POLL_INTERVAL)

def main():
    global pwm, pwm_brightness, lcd

    print("Claude Usage Dashboard - Pi Zero W (Remote API + Web UI)")
    print("=" * 50)
    print(f"API URL: {API_URL}")
    print(f"Web server: http://0.0.0.0:{WEB_PORT}")
    print("=" * 50)

    # Initialize LCD
    try:
        lcd = LCD16x2(RS_PIN, EN_PIN, D4_PIN, D5_PIN, D6_PIN, D7_PIN)

        # Upload our Custom Graphics to LCD CGRAM
        # Slot 1: Full block (5x8 pixels fully lit)
        lcd.create_char(1, [0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F])
        # Slot 2: Half block (Left 3 columns lit)
        lcd.create_char(2, [0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C])

        print("✓ LCD initialized with custom characters")
    except Exception as e:
        print(f"✗ LCD init failed: {e}")
        sys.exit(1)

    # Setup PWM contrast
    try:
        GPIO.setup(CONTRAST_PIN, GPIO.OUT)
        pwm = GPIO.PWM(CONTRAST_PIN, CONTRAST_PWM_FREQ)
        pwm.start(CONTRAST_DUTY)
        print(f"✓ PWM contrast enabled")
    except Exception as e:
        print(f"✗ PWM setup failed: {e}")
        lcd.cleanup()
        sys.exit(1)

    # Setup PWM brightness
    try:
        GPIO.setup(BRIGHTNESS_PIN, GPIO.OUT)
        pwm_brightness = GPIO.PWM(BRIGHTNESS_PIN, BRIGHTNESS_PWM_FREQ)
        pwm_brightness.start(BRIGHTNESS_DUTY)
        print(f"✓ PWM brightness enabled")
    except Exception as e:
        print(f"✗ PWM brightness setup failed: {e}")
        pwm_brightness = None

    # Start LCD update thread
    lcd_thread = threading.Thread(target=lcd_update_loop, daemon=True)
    lcd_thread.start()
    print("✓ LCD update thread started")

    # Start Flask web server
    print(f"✓ Starting web server on http://0.0.0.0:{WEB_PORT}")
    try:
        app.run(host='0.0.0.0', port=WEB_PORT, threaded=True, debug=False)
    except KeyboardInterrupt:
        print("\nShutdown.")
    finally:
        try:
            if pwm:
                pwm.stop()
            if pwm_brightness:
                pwm_brightness.stop()
            if lcd:
                lcd.cleanup()
        except:
            pass

if __name__ == "__main__":
    main()
