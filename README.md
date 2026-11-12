# RFID Classroom Attendance (ESP32 + RC522 + Flask)

Students tap an RFID card, the ESP32 sends the card UID to a Flask server, and staff see live attendance on a web dashboard.

```
backend/app.py                 Flask server + staff dashboard + CSV report
firmware/rfid_attendance.ino   ESP32 code (RC522 + OLED + buzzer + LEDs)
wokwi/libraries.txt            Libraries for the Wokwi online simulator
```

## Part 1: Put the server online (free)

The ESP32 (even in the simulator) needs a public URL to talk to.

**Option A: Render.com**
1. Push the `backend` folder to a GitHub repo.
2. Render -> New -> Web Service -> pick the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Environment variables:
   - `ADMIN_PASSWORD` = your dashboard password
   - `DEVICE_KEY` = a long random string (the ESP32 must send the same one)
6. Deploy. You get a URL like `https://your-app.onrender.com`.

Free Render apps sleep after some idle time and the first tap can take about 30 seconds. Open the dashboard once before a demo. Free disk is not permanent, so the SQLite data can reset on redeploy. For the real college deployment use a paid disk, a Raspberry Pi in the college, or PythonAnywhere.

**Option B: PythonAnywhere (free)**: upload `app.py`, create a Flask web app, set the same two environment variables in the WSGI file.

Open the URL, log in with user `staff` and your `ADMIN_PASSWORD`.

## Part 2: Test with the Wokwi simulator (no hardware)

1. Go to wokwi.com -> New Project -> **ESP32**.
2. Add parts with the blue "+" button: **MFRC522 RFID reader**, **SSD1306 OLED**, **Buzzer**, **2 LEDs** (green, red), **2 resistors (220 ohm)**.
3. Wire them like this:

| Part | Pin | ESP32 |
|---|---|---|
| MFRC522 | SDA | GPIO 5 |
| MFRC522 | SCK | GPIO 18 |
| MFRC522 | MOSI | GPIO 23 |
| MFRC522 | MISO | GPIO 19 |
| MFRC522 | RST | GPIO 27 |
| MFRC522 | 3.3V / GND | 3V3 / GND |
| OLED | SDA / SCL | GPIO 21 / GPIO 22 |
| OLED | VCC / GND | 3V3 / GND |
| Buzzer | + / - | GPIO 25 / GND |
| Green LED | anode via 220 ohm | GPIO 26 (cathode to GND) |
| Red LED | anode via 220 ohm | GPIO 32 (cathode to GND) |

4. Paste `firmware/rfid_attendance.ino` into the code tab. Paste `wokwi/libraries.txt` into the libraries tab.
5. Edit `SERVER_URL` (your Render URL + `/api/tap`) and `DEVICE_KEY`. Keep `WIFI_SSID "Wokwi-GUEST"` and an empty password.
6. Press Play. The OLED should show "Tap card / Ready".

### Test steps
1. Staff dashboard -> **Students** page. Click the MFRC522 part in Wokwi, pick a card (for example the Blue Card, UID `01020304`) and tap it. The OLED shows "Unknown, Card not registered".
2. Refresh the Students page. "Last unknown card" shows `01020304`. Press **Use**, fill roll and name, **Save**.
3. Register 2-3 more simulated cards the same way (Green `11223344`, Yellow `55667788`, Red `AABBCCDD`).
4. Dashboard -> type a subject -> **Start attendance**.
5. Tap a registered card. OLED shows "Present", green LED and beep, and the dashboard updates within about 4 seconds.
6. Tap the same card again -> "Already marked".
7. Wait more than 10 minutes after starting, tap another card -> "Late".
8. Tap a card while no session is open -> "Closed".
9. **End attendance**, then **Reports** -> download the monthly CSV and open it in Excel.

## Part 3: Real hardware
Same wiring as the table. Power the RC522 from **3.3V only**, 5V can damage it. Set your real `WIFI_SSID` and `WIFI_PASS`. Use MIFARE 1K (13.56 MHz) tags. Register each card once on the Students page, or bulk import a CSV (`roll,name,dept,uid`).

## Known limits (v1)
- No offline mode: if WiFi is down the OLED says "No network, Tap again".
- The ESP32 skips HTTPS certificate checking (`setInsecure`), fine for a project, pin the certificate before a wide rollout.
- A friend can tap with a borrowed card. The dashboard shows tap time so staff can spot it. Add fingerprint later if needed.
- Only UID, roll, name and department are stored.
