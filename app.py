"""
RFID Attendance - backend + staff dashboard (Flask + SQLite)

Run locally:   pip install -r requirements.txt && python app.py
Environment (set these when you deploy):
  ADMIN_PASSWORD  password for the staff dashboard (user name: staff)
  DEVICE_KEY      secret the ESP32 sends in the X-Device-Key header
  LATE_MINUTES    minutes after session start that count as "late" (default 10)
  DB_PATH         SQLite file path (default attendance.db)
"""
import csv
import io
import os
import sqlite3
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, jsonify, redirect, render_template_string, request, url_for

IST = ZoneInfo("Asia/Kolkata")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
DEVICE_KEY = os.environ.get("DEVICE_KEY", "change-this-device-key")
LATE_MINUTES = int(os.environ.get("LATE_MINUTES", "10"))
DB_PATH = os.environ.get("DB_PATH", "attendance.db")

app = Flask(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  roll TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  dept TEXT DEFAULT '',
  uid TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT
);
CREATE TABLE IF NOT EXISTS attendance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  student_id INTEGER NOT NULL REFERENCES students(id),
  tapped_at TEXT NOT NULL,
  status TEXT NOT NULL,
  UNIQUE(session_id, student_id)
);
CREATE TABLE IF NOT EXISTS last_unknown (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  uid TEXT,
  seen_at TEXT
);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO last_unknown (id, uid, seen_at) VALUES (1, NULL, NULL)")
    conn.commit()
    conn.close()


def now():
    return datetime.now(IST)


def normalize_uid(uid):
    return "".join(c for c in (uid or "").upper() if c in "0123456789ABCDEF")


def staff_only(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != "staff" or auth.password != ADMIN_PASSWORD:
            return Response("Staff login required", 401, {"WWW-Authenticate": 'Basic realm="Attendance"'})
        return fn(*args, **kwargs)
    return wrapper


def active_session():
    return db().execute("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()


# ---------------------------------------------------------------- device API
@app.post("/api/tap")
def api_tap():
    if request.headers.get("X-Device-Key") != DEVICE_KEY:
        return jsonify(status="error", message="Bad device key"), 401

    data = request.get_json(silent=True) or {}
    uid = normalize_uid(data.get("uid"))
    if not uid:
        return jsonify(status="error", message="No UID"), 400

    conn = db()
    session = active_session()
    student = conn.execute("SELECT * FROM students WHERE uid = ?", (uid,)).fetchone()

    if student is None:
        conn.execute("UPDATE last_unknown SET uid = ?, seen_at = ? WHERE id = 1", (uid, now().isoformat()))
        conn.commit()
        return jsonify(status="unknown", name="", message="Card not registered")

    if session is None:
        return jsonify(status="closed", name=student["name"], message="Attendance closed")

    existing = conn.execute(
        "SELECT status FROM attendance WHERE session_id = ? AND student_id = ?",
        (session["id"], student["id"]),
    ).fetchone()
    if existing:
        return jsonify(status="already", name=student["name"], message="Already marked")

    started = datetime.fromisoformat(session["started_at"])
    minutes_in = (now() - started).total_seconds() / 60
    status = "present" if minutes_in <= LATE_MINUTES else "late"
    conn.execute(
        "INSERT INTO attendance (session_id, student_id, tapped_at, status) VALUES (?, ?, ?, ?)",
        (session["id"], student["id"], now().isoformat(), status),
    )
    conn.commit()
    return jsonify(status=status, name=student["name"], message=status.capitalize())


# --------------------------------------------------------------- staff pages
PAGE = """
<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RFID Attendance</title>
<style>
  :root { --bg:#f5f7fb; --card:#fff; --text:#1c2333; --muted:#6b7488; --ok:#16a34a; --late:#d97706; --bad:#dc2626; --line:#e3e8f2; --brand:#2563eb; }
  @media (prefers-color-scheme: dark) { :root { --bg:#0f1420; --card:#1a2133; --text:#e8ecf5; --muted:#8b95ad; --line:#2a3550; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:system-ui,sans-serif; }
  header { padding:14px 16px; display:flex; justify-content:space-between; align-items:center; }
  header h1 { font-size:1.15rem; margin:0; }
  nav a { color:var(--brand); margin-left:14px; text-decoration:none; font-size:.9rem; }
  main { padding:0 16px 32px; max-width:900px; margin:auto; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:14px; }
  .stats { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
  .stat b { display:block; font-size:1.6rem; }
  .stat span { color:var(--muted); font-size:.8rem; }
  input, button { font:inherit; padding:9px 11px; border-radius:8px; border:1px solid var(--line); background:var(--card); color:var(--text); }
  button { background:var(--brand); color:#fff; border:0; cursor:pointer; }
  button.red { background:var(--bad); }
  button.grey { background:var(--muted); }
  form.inline { display:flex; gap:8px; flex-wrap:wrap; }
  table { width:100%; border-collapse:collapse; font-size:.9rem; }
  th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-weight:600; font-size:.8rem; }
  .tag { padding:2px 8px; border-radius:99px; font-size:.75rem; color:#fff; }
  .present { background:var(--ok); } .late { background:var(--late); } .absent { background:var(--bad); }
  .muted { color:var(--muted); font-size:.85rem; }
  .scroll { overflow-x:auto; }
</style></head><body>
<header><h1>RFID Attendance</h1>
<nav><a href="{{ url_for('dashboard') }}">Dashboard</a><a href="{{ url_for('students') }}">Students</a><a href="{{ url_for('history') }}">Reports</a></nav></header>
<main>{{ body|safe }}</main>
</body></html>
"""


def page(body):
    return render_template_string(PAGE, body=body)


DASH = """
<div class="card" id="sessionBox">
{% if session %}
  <div><b>{{ session.subject }}</b> <span class="muted">started {{ session.started_at[11:16] }}</span></div>
  <form method="post" action="{{ url_for('end_session') }}" style="margin-top:10px"><button class="red">End attendance</button></form>
{% else %}
  <div class="muted" style="margin-bottom:8px">No active session. Cards are rejected until you start one.</div>
  <form class="inline" method="post" action="{{ url_for('start_session') }}">
    <input name="subject" placeholder="Subject / period" required>
    <button>Start attendance</button>
  </form>
{% endif %}
</div>
<div class="card stats">
  <div class="stat"><b id="cPresent">0</b><span>Present</span></div>
  <div class="stat"><b id="cLate">0</b><span>Late</span></div>
  <div class="stat"><b id="cAbsent">0</b><span>Absent</span></div>
</div>
<div class="card scroll"><b>Tapped</b>
  <table><thead><tr><th>Roll</th><th>Name</th><th>Time</th><th>Status</th><th></th></tr></thead><tbody id="tapped"></tbody></table>
</div>
<div class="card scroll"><b>Not yet tapped</b>
  <table><thead><tr><th>Roll</th><th>Name</th><th></th></tr></thead><tbody id="absent"></tbody></table>
</div>
<script>
async function load() {
  try {
    const r = await fetch("{{ url_for('api_live') }}");
    const d = await r.json();
    document.getElementById("cPresent").textContent = d.present;
    document.getElementById("cLate").textContent = d.late;
    document.getElementById("cAbsent").textContent = d.absent.length;
    const t = document.getElementById("tapped"); t.innerHTML = "";
    d.tapped.forEach(x => {
      const tr = document.createElement("tr");
      [x.roll, x.name, x.time].forEach(v => { const td = document.createElement("td"); td.textContent = v; tr.appendChild(td); });
      const s = document.createElement("td"); const tag = document.createElement("span");
      tag.className = "tag " + x.status; tag.textContent = x.status; s.appendChild(tag); tr.appendChild(s);
      tr.appendChild(document.createElement("td")); t.appendChild(tr);
    });
    const a = document.getElementById("absent"); a.innerHTML = "";
    d.absent.forEach(x => {
      const tr = document.createElement("tr");
      [x.roll, x.name].forEach(v => { const td = document.createElement("td"); td.textContent = v; tr.appendChild(td); });
      const td = document.createElement("td");
      if (d.session_id) {
        const f = document.createElement("form"); f.method = "post"; f.action = "{{ url_for('manual_mark') }}";
        f.innerHTML = '<input type="hidden" name="student_id" value="' + x.id + '"><button class="grey">Mark present</button>';
        td.appendChild(f);
      }
      tr.appendChild(td); a.appendChild(tr);
    });
  } catch (e) {}
}
load(); setInterval(load, 4000);
</script>
"""


@app.get("/")
@staff_only
def dashboard():
    body = render_template_string(DASH, session=active_session())
    return page(body)


@app.get("/api/live")
@staff_only
def api_live():
    conn = db()
    session = active_session()
    if not session:
        return jsonify(session_id=None, present=0, late=0, tapped=[], absent=[])
    tapped = conn.execute(
        """SELECT s.id, s.roll, s.name, a.tapped_at, a.status FROM attendance a
           JOIN students s ON s.id = a.student_id WHERE a.session_id = ? ORDER BY a.tapped_at DESC""",
        (session["id"],),
    ).fetchall()
    absent = conn.execute(
        """SELECT id, roll, name FROM students WHERE id NOT IN
           (SELECT student_id FROM attendance WHERE session_id = ?) ORDER BY roll""",
        (session["id"],),
    ).fetchall()
    return jsonify(
        session_id=session["id"],
        present=sum(1 for t in tapped if t["status"] == "present"),
        late=sum(1 for t in tapped if t["status"] == "late"),
        tapped=[dict(roll=t["roll"], name=t["name"], time=t["tapped_at"][11:16], status=t["status"]) for t in tapped],
        absent=[dict(id=a["id"], roll=a["roll"], name=a["name"]) for a in absent],
    )


@app.post("/session/start")
@staff_only
def start_session():
    subject = (request.form.get("subject") or "").strip()
    if subject and not active_session():
        db().execute("INSERT INTO sessions (subject, started_at) VALUES (?, ?)", (subject, now().isoformat()))
        db().commit()
    return redirect(url_for("dashboard"))


@app.post("/session/end")
@staff_only
def end_session():
    s = active_session()
    if s:
        db().execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (now().isoformat(), s["id"]))
        db().commit()
    return redirect(url_for("dashboard"))


@app.post("/session/manual")
@staff_only
def manual_mark():
    s = active_session()
    sid = request.form.get("student_id", type=int)
    if s and sid:
        db().execute(
            "INSERT OR IGNORE INTO attendance (session_id, student_id, tapped_at, status) VALUES (?, ?, ?, 'present')",
            (s["id"], sid, now().isoformat()),
        )
        db().commit()
    return redirect(url_for("dashboard"))


STUDENTS = """
<div class="card">
  <b>Add student</b>
  <p class="muted">Tap the new card on the reader once. It shows up below as "Last unknown card". Press Use, fill name and roll, save.</p>
  <p>Last unknown card: <b id="lastUid">-</b> <button type="button" class="grey" onclick="useUid()">Use</button></p>
  <form class="inline" method="post" action="{{ url_for('add_student') }}">
    <input name="roll" placeholder="Roll no" required>
    <input name="name" placeholder="Name" required>
    <input name="dept" placeholder="Department">
    <input name="uid" id="uid" placeholder="Card UID" required>
    <button>Save</button>
  </form>
  {% if error %}<p style="color:var(--bad)">{{ error }}</p>{% endif %}
</div>
<div class="card scroll"><b>Students ({{ rows|length }})</b>
  <table><thead><tr><th>Roll</th><th>Name</th><th>Dept</th><th>Card UID</th><th></th></tr></thead><tbody>
  {% for r in rows %}
  <tr><td>{{ r.roll }}</td><td>{{ r.name }}</td><td>{{ r.dept }}</td><td>{{ r.uid }}</td>
  <td><form method="post" action="{{ url_for('delete_student', sid=r.id) }}" onsubmit="return confirm('Remove {{ r.name }}?')"><button class="red">Remove</button></form></td></tr>
  {% endfor %}
  </tbody></table>
</div>
<div class="card">
  <b>Bulk add (CSV)</b>
  <p class="muted">Format per line: roll,name,dept,uid</p>
  <form method="post" action="{{ url_for('bulk_students') }}">
    <textarea name="csv" rows="4" style="width:100%" placeholder="21CS001,Ravi,CSE,A34F9C12"></textarea>
    <button style="margin-top:8px">Import</button>
  </form>
</div>
<script>
let last = "";
async function poll() {
  try {
    const r = await fetch("{{ url_for('api_last_unknown') }}"); const d = await r.json();
    last = d.uid || ""; document.getElementById("lastUid").textContent = last || "-";
  } catch (e) {}
}
function useUid() { if (last) document.getElementById("uid").value = last; }
poll(); setInterval(poll, 3000);
</script>
"""


@app.get("/students")
@staff_only
def students(error=None):
    rows = db().execute("SELECT * FROM students ORDER BY roll").fetchall()
    return page(render_template_string(STUDENTS, rows=rows, error=error))


@app.get("/api/last-unknown")
@staff_only
def api_last_unknown():
    row = db().execute("SELECT uid, seen_at FROM last_unknown WHERE id = 1").fetchone()
    return jsonify(uid=row["uid"], seen_at=row["seen_at"])


def insert_student(roll, name, dept, uid):
    uid = normalize_uid(uid)
    if not (roll and name and uid):
        raise ValueError("Roll, name and UID are required")
    try:
        db().execute("INSERT INTO students (roll, name, dept, uid) VALUES (?, ?, ?, ?)", (roll, name, dept, uid))
    except sqlite3.IntegrityError:
        raise ValueError(f"Roll {roll} or card {uid} is already registered")


@app.post("/students/add")
@staff_only
def add_student():
    try:
        insert_student(
            request.form.get("roll", "").strip(),
            request.form.get("name", "").strip(),
            request.form.get("dept", "").strip(),
            request.form.get("uid", ""),
        )
        db().commit()
    except ValueError as e:
        return students(error=str(e))
    return redirect(url_for("students"))


@app.post("/students/bulk")
@staff_only
def bulk_students():
    errors = []
    for i, row in enumerate(csv.reader(io.StringIO(request.form.get("csv", ""))), 1):
        if len(row) < 4:
            if any(row):
                errors.append(f"Line {i}: needs roll,name,dept,uid")
            continue
        try:
            insert_student(row[0].strip(), row[1].strip(), row[2].strip(), row[3])
        except ValueError as e:
            errors.append(f"Line {i}: {e}")
    db().commit()
    if errors:
        return students(error=" | ".join(errors))
    return redirect(url_for("students"))


@app.post("/students/<int:sid>/delete")
@staff_only
def delete_student(sid):
    db().execute("DELETE FROM attendance WHERE student_id = ?", (sid,))
    db().execute("DELETE FROM students WHERE id = ?", (sid,))
    db().commit()
    return redirect(url_for("students"))


HISTORY = """
<div class="card">
  <b>Monthly report</b>
  <form class="inline" method="get" action="{{ url_for('export_csv') }}" style="margin-top:8px">
    <input type="month" name="month" value="{{ month }}">
    <button>Download CSV (opens in Excel)</button>
  </form>
</div>
<div class="card scroll"><b>Recent sessions</b>
  <table><thead><tr><th>Date</th><th>Subject</th><th>Present</th><th>Late</th><th>Total</th></tr></thead><tbody>
  {% for s in sessions %}
  <tr><td>{{ s.started_at[:10] }} {{ s.started_at[11:16] }}</td><td>{{ s.subject }}</td><td>{{ s.present }}</td><td>{{ s.late }}</td><td>{{ total }}</td></tr>
  {% endfor %}
  </tbody></table>
</div>
"""


@app.get("/reports")
@staff_only
def history():
    conn = db()
    sessions = conn.execute(
        """SELECT s.*,
             (SELECT COUNT(*) FROM attendance a WHERE a.session_id = s.id AND a.status = 'present') AS present,
             (SELECT COUNT(*) FROM attendance a WHERE a.session_id = s.id AND a.status = 'late') AS late
           FROM sessions s ORDER BY s.id DESC LIMIT 30"""
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    return page(render_template_string(HISTORY, sessions=sessions, total=total, month=now().strftime("%Y-%m")))


@app.get("/export.csv")
@staff_only
def export_csv():
    month = request.args.get("month") or now().strftime("%Y-%m")
    conn = db()
    sessions = conn.execute(
        "SELECT * FROM sessions WHERE substr(started_at, 1, 7) = ? ORDER BY started_at", (month,)
    ).fetchall()
    students_ = conn.execute("SELECT * FROM students ORDER BY roll").fetchall()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Roll", "Name", "Dept"] + [f"{s['started_at'][:10]} {s['subject']}" for s in sessions] + ["Attended", "Percent"])
    for st in students_:
        cells, attended = [], 0
        for s in sessions:
            r = conn.execute(
                "SELECT status FROM attendance WHERE session_id = ? AND student_id = ?", (s["id"], st["id"])
            ).fetchone()
            if r:
                attended += 1
                cells.append("P" if r["status"] == "present" else "L")
            else:
                cells.append("A")
        pct = f"{round(100 * attended / len(sessions))}%" if sessions else ""
        w.writerow([st["roll"], st["name"], st["dept"]] + cells + [attended, pct])

    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=attendance-{month}.csv"},
    )


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
