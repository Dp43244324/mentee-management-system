import os, io, secrets, base64, re
from datetime import date, datetime
from functools import wraps

from sqlalchemy import create_engine, text
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
LOW_ATTENDANCE = 75

if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
    CLOUD_MODE = True
else:
    DB_FILE = os.path.join(BASE_DIR, "mentee_management.db")
    engine = create_engine(f"sqlite:///{DB_FILE}", connect_args={"check_same_thread": False})
    CLOUD_MODE = False

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "1" if CLOUD_MODE else "0") == "1",
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
)

class DBConnection:
    def __init__(self):
        self.conn = engine.connect()
        self.trans = self.conn.begin()

    def _convert(self, sql, params):
        if not params:
            return text(sql), {}
        if isinstance(params, dict):
            return text(sql), params
        parts = sql.split("?")
        if len(parts) == 1:
            return text(sql), {}
        bind = {}
        rebuilt = parts[0]
        for i, part in enumerate(parts[1:]):
            key = f"p{i}"
            rebuilt += f":{key}" + part
            bind[key] = params[i]
        return text(rebuilt), bind

    def execute(self, sql, params=()):
        stmt, bind = self._convert(sql, params)
        return self.conn.execute(stmt, bind).mappings()

    def commit(self):
        self.trans.commit()
        self.trans = self.conn.begin()

    def rollback(self):
        if self.trans.is_active:
            self.trans.rollback()
        self.trans = self.conn.begin()

    def close(self):
        try:
            if self.trans.is_active:
                self.trans.commit()
        finally:
            self.conn.close()

def db():
    return DBConnection()

def query_df(sql, params=()):
    helper = DBConnection.__new__(DBConnection)
    statement, values = helper._convert(sql, params)
    with engine.connect() as conn:
        return pd.read_sql_query(statement, conn, params=values)

def init_db():
    con = db()
    try:
        if CLOUD_MODE:
            statements = [
                """CREATE TABLE IF NOT EXISTS AcademicTerm(term_id SERIAL PRIMARY KEY, academic_year TEXT NOT NULL, semester INTEGER NOT NULL CHECK(semester BETWEEN 1 AND 8), is_current BOOLEAN NOT NULL DEFAULT FALSE, UNIQUE(academic_year,semester))""",
                """CREATE TABLE IF NOT EXISTS Mentor(mentor_id SERIAL PRIMARY KEY, name TEXT NOT NULL, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, department TEXT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
                """CREATE TABLE IF NOT EXISTS Student(student_id SERIAL PRIMARY KEY, urn TEXT UNIQUE NOT NULL, name TEXT NOT NULL, address TEXT, mobile TEXT, parent_name TEXT, parent_mobile TEXT, hobbies TEXT, ssc_medium TEXT, hsc_medium TEXT, ssc_marks DOUBLE PRECISION, hsc_marks DOUBLE PRECISION, awards TEXT, certifications TEXT, photo TEXT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
                """CREATE TABLE IF NOT EXISTS StudentAccount(account_id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, urn TEXT UNIQUE NOT NULL REFERENCES Student(urn) ON DELETE CASCADE)""",
                """CREATE TABLE IF NOT EXISTS StudentAcademicAssignment(assignment_id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES Student(student_id) ON DELETE CASCADE, term_id INTEGER NOT NULL REFERENCES AcademicTerm(term_id) ON DELETE CASCADE, class_name TEXT, division TEXT, batch TEXT NOT NULL, roll_no TEXT, UNIQUE(student_id,term_id))""",
                """CREATE TABLE IF NOT EXISTS MentorAssignment(mentor_assignment_id SERIAL PRIMARY KEY, mentor_id INTEGER REFERENCES Mentor(mentor_id) ON DELETE SET NULL, term_id INTEGER NOT NULL REFERENCES AcademicTerm(term_id) ON DELETE CASCADE, department TEXT, batch TEXT NOT NULL, registration_code TEXT UNIQUE NOT NULL, mentor_code TEXT UNIQUE, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(term_id,batch))""",
                """CREATE TABLE IF NOT EXISTS Attendance(attendance_id SERIAL PRIMARY KEY, urn TEXT NOT NULL REFERENCES Student(urn) ON DELETE CASCADE, term_id INTEGER NOT NULL REFERENCES AcademicTerm(term_id) ON DELETE CASCADE, semester INTEGER NOT NULL, total_lectures INTEGER NOT NULL, present INTEGER NOT NULL, absent INTEGER NOT NULL, UNIQUE(urn,term_id))""",
                """CREATE TABLE IF NOT EXISTS SemesterMarks(mark_id SERIAL PRIMARY KEY, urn TEXT NOT NULL REFERENCES Student(urn) ON DELETE CASCADE, term_id INTEGER NOT NULL REFERENCES AcademicTerm(term_id) ON DELETE CASCADE, semester INTEGER NOT NULL, subject TEXT NOT NULL, ise DOUBLE PRECISION NOT NULL, mse DOUBLE PRECISION NOT NULL, ese DOUBLE PRECISION NOT NULL, UNIQUE(urn,term_id,subject))""",
                """CREATE TABLE IF NOT EXISTS MentorSuggestions(suggestion_id SERIAL PRIMARY KEY, urn TEXT NOT NULL REFERENCES Student(urn) ON DELETE CASCADE, semester INTEGER NOT NULL, suggestion TEXT NOT NULL, suggestion_date DATE NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS AwardsCertificates(achievement_id SERIAL PRIMARY KEY, urn TEXT NOT NULL REFERENCES Student(urn) ON DELETE CASCADE, achievement_type TEXT, title TEXT, organization TEXT, year TEXT)""",
                "CREATE INDEX IF NOT EXISTS idx_student_assignment_term_batch ON StudentAcademicAssignment(term_id,batch)",
                "CREATE INDEX IF NOT EXISTS idx_mentor_assignment_term_batch ON MentorAssignment(term_id,batch)",
            ]
        else:
            statements = [
                """CREATE TABLE IF NOT EXISTS AcademicTerm(term_id INTEGER PRIMARY KEY AUTOINCREMENT, academic_year TEXT NOT NULL, semester INTEGER NOT NULL CHECK(semester BETWEEN 1 AND 8), is_current INTEGER NOT NULL DEFAULT 0, UNIQUE(academic_year,semester))""",
                """CREATE TABLE IF NOT EXISTS Mentor(mentor_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, department TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
                """CREATE TABLE IF NOT EXISTS Student(student_id INTEGER PRIMARY KEY AUTOINCREMENT, urn TEXT UNIQUE NOT NULL, name TEXT NOT NULL, address TEXT, mobile TEXT, parent_name TEXT, parent_mobile TEXT, hobbies TEXT, ssc_medium TEXT, hsc_medium TEXT, ssc_marks REAL, hsc_marks REAL, awards TEXT, certifications TEXT, photo TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
                """CREATE TABLE IF NOT EXISTS StudentAccount(account_id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, urn TEXT UNIQUE NOT NULL, FOREIGN KEY(urn) REFERENCES Student(urn) ON DELETE CASCADE)""",
                """CREATE TABLE IF NOT EXISTS StudentAcademicAssignment(assignment_id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL, term_id INTEGER NOT NULL, class_name TEXT, division TEXT, batch TEXT NOT NULL, roll_no TEXT, UNIQUE(student_id,term_id), FOREIGN KEY(student_id) REFERENCES Student(student_id) ON DELETE CASCADE, FOREIGN KEY(term_id) REFERENCES AcademicTerm(term_id) ON DELETE CASCADE)""",
                """CREATE TABLE IF NOT EXISTS MentorAssignment(mentor_assignment_id INTEGER PRIMARY KEY AUTOINCREMENT, mentor_id INTEGER, term_id INTEGER NOT NULL, department TEXT, batch TEXT NOT NULL, registration_code TEXT UNIQUE NOT NULL, mentor_code TEXT UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(term_id,batch), FOREIGN KEY(mentor_id) REFERENCES Mentor(mentor_id) ON DELETE SET NULL, FOREIGN KEY(term_id) REFERENCES AcademicTerm(term_id) ON DELETE CASCADE)""",
                """CREATE TABLE IF NOT EXISTS Attendance(attendance_id INTEGER PRIMARY KEY AUTOINCREMENT, urn TEXT NOT NULL, term_id INTEGER NOT NULL, semester INTEGER NOT NULL, total_lectures INTEGER NOT NULL, present INTEGER NOT NULL, absent INTEGER NOT NULL, UNIQUE(urn,term_id), FOREIGN KEY(urn) REFERENCES Student(urn) ON DELETE CASCADE, FOREIGN KEY(term_id) REFERENCES AcademicTerm(term_id) ON DELETE CASCADE)""",
                """CREATE TABLE IF NOT EXISTS SemesterMarks(mark_id INTEGER PRIMARY KEY AUTOINCREMENT, urn TEXT NOT NULL, term_id INTEGER NOT NULL, semester INTEGER NOT NULL, subject TEXT NOT NULL, ise REAL NOT NULL, mse REAL NOT NULL, ese REAL NOT NULL, UNIQUE(urn,term_id,subject), FOREIGN KEY(urn) REFERENCES Student(urn) ON DELETE CASCADE, FOREIGN KEY(term_id) REFERENCES AcademicTerm(term_id) ON DELETE CASCADE)""",
                """CREATE TABLE IF NOT EXISTS MentorSuggestions(suggestion_id INTEGER PRIMARY KEY AUTOINCREMENT, urn TEXT NOT NULL, semester INTEGER NOT NULL, suggestion TEXT NOT NULL, suggestion_date TEXT NOT NULL, FOREIGN KEY(urn) REFERENCES Student(urn) ON DELETE CASCADE)""",
                """CREATE TABLE IF NOT EXISTS AwardsCertificates(achievement_id INTEGER PRIMARY KEY AUTOINCREMENT, urn TEXT NOT NULL, achievement_type TEXT, title TEXT, organization TEXT, year TEXT, FOREIGN KEY(urn) REFERENCES Student(urn) ON DELETE CASCADE)""",
                "CREATE INDEX IF NOT EXISTS idx_student_assignment_term_batch ON StudentAcademicAssignment(term_id,batch)",
                "CREATE INDEX IF NOT EXISTS idx_mentor_assignment_term_batch ON MentorAssignment(term_id,batch)",
            ]
        for statement in statements:
            con.execute(statement)

        # Keep all 8 semester choices available for student registration.
        # CURRENT_SEMESTER/CURRENT_ACADEMIC_YEAR only identify which term is
        # marked as the current term elsewhere in the application; they do not
        # restrict which semester a new student may select.
        configured_year = os.getenv("CURRENT_ACADEMIC_YEAR") or os.getenv("DEFAULT_ACADEMIC_YEAR") or "2026-27"
        configured_sem_raw = os.getenv("CURRENT_SEMESTER") or os.getenv("DEFAULT_SEMESTER") or "1"
        try:
            configured_sem = int(configured_sem_raw)
        except ValueError:
            configured_sem = 1
        if configured_sem not in range(1, 9):
            configured_sem = 1

        # Ensure Semester 1 through Semester 8 exist for the configured
        # academic-year label so a fresh installation can offer all choices.
        for sem in range(1, 9):
            term = con.execute(
                "SELECT term_id FROM AcademicTerm WHERE academic_year=? AND semester=?",
                (configured_year, sem),
            ).fetchone()
            if term is None:
                con.execute(
                    "INSERT INTO AcademicTerm(academic_year,semester,is_current) VALUES(?,?,?)",
                    (configured_year, sem, True if (not CLOUD_MODE and sem == configured_sem) else False),
                )

        # Mark the configured term as current, but do not use this flag to
        # restrict student registration.
        term = con.execute(
            "SELECT term_id FROM AcademicTerm WHERE academic_year=? AND semester=?",
            (configured_year, configured_sem),
        ).fetchone()
        if term:
            con.execute("UPDATE AcademicTerm SET is_current=FALSE")
            con.execute("UPDATE AcademicTerm SET is_current=TRUE WHERE term_id=?", (term["term_id"],))
        con.commit()
    finally:
        con.close()

def current_term():
    con=db(); r=con.execute("SELECT * FROM AcademicTerm WHERE is_current=TRUE ORDER BY term_id DESC LIMIT 1").fetchone();
    if not r: r=con.execute("SELECT * FROM AcademicTerm ORDER BY term_id DESC LIMIT 1").fetchone()
    con.close(); return r

def term_by_id(term_id):
    con=db(); r=con.execute("SELECT * FROM AcademicTerm WHERE term_id=?", (term_id,)).fetchone(); con.close(); return r

def mentor_assignment(assignment_id):
    con=db(); r=con.execute("""SELECT ma.*,m.name mentor_name,m.username, t.academic_year,t.semester
        FROM MentorAssignment ma JOIN AcademicTerm t ON t.term_id=ma.term_id
        LEFT JOIN Mentor m ON m.mentor_id=ma.mentor_id WHERE ma.mentor_assignment_id=?""", (assignment_id,)).fetchone(); con.close(); return r

def assignment_for_student(urn, term_id=None):
    term_id = term_id or (current_term()["term_id"] if current_term() else None)
    con=db(); r=con.execute("""SELECT sa.*,t.academic_year,t.semester FROM StudentAcademicAssignment sa
        JOIN Student s ON s.student_id=sa.student_id JOIN AcademicTerm t ON t.term_id=sa.term_id
        WHERE s.urn=? AND sa.term_id=?""", (urn,term_id)).fetchone(); con.close(); return r

def assignments_for_student(urn):
    con=db(); rows=con.execute("""SELECT sa.*,t.academic_year,t.semester FROM StudentAcademicAssignment sa
        JOIN Student s ON s.student_id=sa.student_id JOIN AcademicTerm t ON t.term_id=sa.term_id
        WHERE s.urn=? ORDER BY t.term_id DESC""", (urn,)).fetchall(); con.close(); return [dict(x) for x in rows]

def student_row(urn):
    con=db(); r=con.execute("SELECT * FROM Student WHERE urn=?", (urn,)).fetchone(); con.close(); return r

def mentor_scope():
    if session.get("role") != "mentor":
        return None
    return mentor_assignment(session.get("mentor_assignment_id"))

def mentor_batch_ok(urn):
    ma=mentor_scope()
    if not ma: return False
    a=assignment_for_student(urn, ma["term_id"])
    return bool(a and a["batch"] == ma["batch"])

def get_student_urn(): return session.get("urn")

def validate_marks(ise,mse,ese):
    ise,mse,ese=float(ise),float(mse),float(ese)
    if not (0<=ise<=40 and 0<=mse<=60 and 0<=ese<=60): raise ValueError("ISE must be 0–40 and MSE/ESE must be 0–60.")
    reasons=[]; total=ise+mse+ese
    if ise<16: reasons.append("ISE below 16")
    if mse+ese<24: reasons.append("MSE + ESE below 24")
    if total<40: reasons.append("Total below 40")
    return ise,mse,ese,total,"PASS" if not reasons else "FAIL","; ".join(reasons)

def attendance_df(urn=None, term_id=None, batch=None):
    con=db(); q="""SELECT a.urn,s.name,sa.batch,sa.division,t.academic_year,t.semester,a.total_lectures,a.present,a.absent,
         ROUND((a.present*100.0/a.total_lectures),2) percentage FROM Attendance a
         JOIN Student s ON s.urn=a.urn JOIN AcademicTerm t ON t.term_id=a.term_id
         LEFT JOIN StudentAcademicAssignment sa ON sa.student_id=s.student_id AND sa.term_id=a.term_id"""
    p=[];w=[]
    if urn:w.append("a.urn=?");p.append(urn)
    if term_id:w.append("a.term_id=?");p.append(term_id)
    if batch and batch!="ALL":w.append("sa.batch=?");p.append(batch)
    if w:q+=" WHERE "+" AND ".join(w)
    q+=" ORDER BY s.name,t.semester"; df=query_df(q,p); con.close(); return df

def marks_df(urn=None, term_id=None, batch=None):
    con=db(); q="""SELECT m.urn,s.name,sa.batch,sa.division,t.academic_year,t.semester,m.subject,m.ise,m.mse,m.ese,
         ROUND(CAST((m.ise+m.mse+m.ese) AS NUMERIC),2) total FROM SemesterMarks m JOIN Student s ON s.urn=m.urn
         JOIN AcademicTerm t ON t.term_id=m.term_id LEFT JOIN StudentAcademicAssignment sa ON sa.student_id=s.student_id AND sa.term_id=m.term_id"""
    p=[];w=[]
    if urn:w.append("m.urn=?");p.append(urn)
    if term_id:w.append("m.term_id=?");p.append(term_id)
    if batch and batch!="ALL":w.append("sa.batch=?");p.append(batch)
    if w:q+=" WHERE "+" AND ".join(w)
    q+=" ORDER BY s.name,t.semester,m.subject"; df=query_df(q,p); con.close(); return df

def marks_with_status(urn=None, term_id=None, batch=None):
    df=marks_df(urn,term_id,batch)
    if df.empty:
        df["status"]=pd.Series(dtype=str);df["reason"]=pd.Series(dtype=str);return df
    vals=[validate_marks(r.ise,r.mse,r.ese) for r in df.itertuples()]
    df["status"]=[x[4] for x in vals];df["reason"]=[x[5] for x in vals];return df

def get_achievements(urn):
    con=db(); rows=con.execute("SELECT * FROM AwardsCertificates WHERE urn=? ORDER BY year DESC,achievement_id DESC",(urn,)).fetchall();con.close();return [dict(x) for x in rows]

def get_suggestions(urn):
    con=db(); rows=con.execute("SELECT * FROM MentorSuggestions WHERE urn=? ORDER BY suggestion_id DESC",(urn,)).fetchall();con.close();return [dict(x) for x in rows]

def allowed_student_or_mentor(urn): return (session.get("role")=="student" and session.get("urn")==urn) or mentor_batch_ok(urn)

def login_required(role=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a,**kw):
            if not session.get("user_id"): return redirect(url_for("login"))
            if role and session.get("role")!=role: abort(403)
            return fn(*a,**kw)
        return wrapper
    return deco

@app.context_processor
def inject():
    ma=mentor_scope(); ct=current_term()
    return {"role":session.get("role"),"display_name":session.get("name"),"mentor_assignment":ma,"current_term":ct}

@app.route("/")
def index():
    if session.get("role")=="student":return redirect(url_for("student_dashboard"))
    if session.get("role")=="mentor":return redirect(url_for("mentor_dashboard"))
    return redirect(url_for("login"))

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        username=request.form.get("username","").strip();password=request.form.get("password","");mentor_code=request.form.get("mentor_code","").strip()
        con=db(); student=con.execute("SELECT * FROM StudentAccount WHERE username=?",(username,)).fetchone()
        if student and check_password_hash(student["password"],password):
            con.close(); session.clear(); session["user_id"]=student["account_id"];session["role"]="student";session["urn"]=student["urn"]
            session["name"]=student_row(student["urn"])["name"];return redirect(url_for("index"))
        mentor=con.execute("SELECT * FROM Mentor WHERE username=?",(username,)).fetchone()
        assignment=None
        if mentor and mentor_code:
            assignment=con.execute("""SELECT ma.*,t.academic_year,t.semester FROM MentorAssignment ma JOIN AcademicTerm t ON t.term_id=ma.term_id
                WHERE ma.mentor_id=? AND ma.mentor_code=? AND ma.mentor_code IS NOT NULL""",(mentor["mentor_id"],mentor_code)).fetchone()
        con.close()
        if mentor and assignment and check_password_hash(mentor["password"],password):
            session.clear();session["user_id"]=mentor["mentor_id"];session["role"]="mentor";session["name"]=mentor["name"];session["mentor_assignment_id"]=assignment["mentor_assignment_id"]
            return redirect(url_for("index"))
        flash("Invalid username/password, or invalid mentor code.","danger")
    return render_template("login.html")

@app.route("/logout")
def logout(): session.clear();return redirect(url_for("login"))

@app.route("/student/register",methods=["GET","POST"])
def student_register():
    if request.method=="POST":
        f=request.form; urn=f.get("urn","").strip();name=f.get("name","").strip();username=f.get("username","").strip();pw=f.get("password",""); term_id=f.get("term_id");batch=f.get("batch","").strip();division=f.get("division","").strip()
        if not all([urn,name,username,pw,term_id,batch,division]): flash("All required fields must be completed.","danger")
        elif len(pw)<8: flash("Password must be at least 8 characters.","danger")
        elif not term_by_id(term_id): flash("Invalid academic semester.","danger")
        else:
            con=db()
            try:
                con.execute("INSERT INTO Student(urn,name) VALUES(?,?)",(urn,name))
                sid=con.execute("SELECT student_id FROM Student WHERE urn=?",(urn,)).fetchone()["student_id"]
                con.execute("INSERT INTO StudentAccount(username,password,urn) VALUES(?,?,?)",(username,generate_password_hash(pw),urn))
                con.execute("INSERT INTO StudentAcademicAssignment(student_id,term_id,class_name,division,batch,roll_no) VALUES(?,?,?,?,?,?)",(sid,int(term_id),f.get("class_name"),division,batch,f.get("roll_no")))
                con.commit();flash("Student account created. Your account is permanent; future semesters can be added without creating another account.","success");return redirect(url_for("login"))
            except Exception:
                con.rollback(); flash("URN, username, or semester assignment already exists.","danger")
            finally: con.close()
    terms=get_terms(); return render_template("student_register.html",terms=terms,current=current_term())

def get_terms():
    con=db();rows=con.execute("SELECT * FROM AcademicTerm ORDER BY term_id DESC").fetchall();con.close();return [dict(x) for x in rows]

@app.route("/student")
@login_required("student")
def student_dashboard():
    urn=get_student_urn()
    s=student_row(urn)
    ct=current_term()
    history=assignments_for_student(urn)
    a=assignment_for_student(urn,ct["term_id"] if ct else None)

    if a is None and history:
        a=history[0]

    ad=attendance_df(urn)
    mk=marks_with_status(urn)

    return render_template(
        "student_dashboard.html",
        s=s,
        assignment=a,
        history=history,
        terms=get_terms(),
        attendance=ad.to_dict("records"),
        marks=mk.to_dict("records"),
        low_att=ad[ad.percentage<LOW_ATTENDANCE].to_dict("records") if not ad.empty else [],
        achievements=get_achievements(urn),
        suggestions=get_suggestions(urn)
    )
@app.route("/student/profile",methods=["POST"])
@login_required("student")
def student_profile():
    f = request.form
    urn = get_student_urn()

    term_id = f.get("term_id")
    batch = f.get("batch", "").strip()
    division = f.get("division", "").strip()

    if not term_id or not batch or not division:
        flash("Semester, batch and division are required.", "danger")
        return redirect(url_for("student_dashboard"))

    term = term_by_id(term_id)

    if not term or term["semester"] not in [4, 5]:
        flash("Please select semester 4 or 5.", "danger")
        return redirect(url_for("student_dashboard"))

    con = db()

    try:
        # Save personal profile
        con.execute("""
            UPDATE Student
            SET name=?,
                address=?,
                mobile=?,
                parent_name=?,
                parent_mobile=?,
                hobbies=?,
                ssc_medium=?,
                hsc_medium=?,
                ssc_marks=?,
                hsc_marks=?,
                awards=?,
                certifications=?
            WHERE urn=?
        """, (
            f.get("name", "").strip(),
            f.get("address"),
            f.get("mobile"),
            f.get("parent_name"),
            f.get("parent_mobile"),
            f.get("hobbies"),
            f.get("ssc_medium"),
            f.get("hsc_medium"),
            f.get("ssc_marks") or None,
            f.get("hsc_marks") or None,
            f.get("awards"),
            f.get("certifications"),
            urn
        ))

        # Get permanent student ID
        sid = con.execute(
            "SELECT student_id FROM Student WHERE urn=?",
            (urn,)
        ).fetchone()["student_id"]

        # Check whether this semester assignment already exists
        existing = con.execute("""
            SELECT assignment_id
            FROM StudentAcademicAssignment
            WHERE student_id=? AND term_id=?
        """, (sid, int(term_id))).fetchone()

        if existing:
            # Update existing semester details
            con.execute("""
                UPDATE StudentAcademicAssignment
                SET class_name=?,
                    division=?,
                    batch=?,
                    roll_no=?
                WHERE assignment_id=?
            """, (
                f.get("class_name"),
                division,
                batch,
                f.get("roll_no"),
                existing["assignment_id"]
            ))
        else:
            # Create assignment for this semester
            con.execute("""
                INSERT INTO StudentAcademicAssignment
                (student_id, term_id, class_name, division, batch, roll_no)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                sid,
                int(term_id),
                f.get("class_name"),
                division,
                batch,
                f.get("roll_no")
            ))

        con.commit()

        flash("Mentee profile updated successfully.", "success")

    except Exception:
        con.rollback()
        flash("Could not save your profile details.", "danger")

    finally:
        con.close()

    return redirect(url_for("student_dashboard"))
@app.route("/student/academic-assignment",methods=["POST"])
@login_required("student")
def student_academic_assignment():
    f=request.form; urn=get_student_urn(); term_id=f.get("term_id"); batch=f.get("batch","").strip(); division=f.get("division","").strip()
    if not term_id or not batch or not division:
        flash("Semester, division and batch are required.","danger"); return redirect(url_for("student_dashboard"))
    term=term_by_id(term_id)
    if not term:
        flash("Invalid academic semester.","danger"); return redirect(url_for("student_dashboard"))
    con=db()
    try:
        sid=con.execute("SELECT student_id FROM Student WHERE urn=?",(urn,)).fetchone()["student_id"]
        existing=con.execute("SELECT assignment_id FROM StudentAcademicAssignment WHERE student_id=? AND term_id=?",(sid,int(term_id))).fetchone()
        if existing:
            con.execute("UPDATE StudentAcademicAssignment SET class_name=?,division=?,batch=?,roll_no=? WHERE assignment_id=?",(f.get("class_name"),division,batch,f.get("roll_no"),existing["assignment_id"]))
        else:
            con.execute("INSERT INTO StudentAcademicAssignment(student_id,term_id,class_name,division,batch,roll_no) VALUES(?,?,?,?,?,?)",(sid,int(term_id),f.get("class_name"),division,batch,f.get("roll_no")))
        con.commit(); flash("Academic semester/batch assignment updated. Your permanent account stays the same.","success")
    except Exception:
        con.rollback(); flash("Could not save this academic assignment.","danger")
    finally: con.close()
    return redirect(url_for("student_dashboard"))

def student_has_semester(urn, semester):
    con=db(); r=con.execute("""SELECT sa.assignment_id FROM StudentAcademicAssignment sa JOIN Student s ON s.student_id=sa.student_id JOIN AcademicTerm t ON t.term_id=sa.term_id WHERE s.urn=? AND t.semester=?""",(urn,semester)).fetchone(); con.close(); return bool(r)

@app.route("/student/attendance",methods=["POST"])
@login_required("student")
def student_attendance():
    try:
        sem=int(request.form["semester"]);total=int(request.form["total"]);present=int(request.form["present"])
        if sem not in range(1,9) or total<=0 or present<0 or present>total or not student_has_semester(get_student_urn(), sem):raise ValueError
        con=db(); term=con.execute("""SELECT t.term_id FROM StudentAcademicAssignment sa JOIN Student s ON s.student_id=sa.student_id JOIN AcademicTerm t ON t.term_id=sa.term_id WHERE s.urn=? AND t.semester=?""",(get_student_urn(),sem)).fetchone()
        if not term: raise ValueError("You are not assigned to that semester.")
        con.execute("""INSERT INTO Attendance(urn,term_id,semester,total_lectures,present,absent) VALUES(?,?,?,?,?,?) ON CONFLICT(urn,term_id) DO UPDATE SET total_lectures=excluded.total_lectures,present=excluded.present,absent=excluded.absent""",(get_student_urn(),term["term_id"],sem,total,present,total-present));con.commit();con.close();flash("Attendance saved.","success")
    except Exception:flash("Enter valid attendance values.","danger")
    return redirect(url_for("student_dashboard"))

@app.route("/student/marks",methods=["POST"])
@login_required("student")
def student_marks():
    try:
        sem=int(request.form["semester"]);subject=request.form["subject"].strip();v=validate_marks(request.form["ise"],request.form["mse"],request.form["ese"])
        if sem not in range(1,9) or not subject or not student_has_semester(get_student_urn(), sem):raise ValueError("You are not assigned to that semester.")
        con=db(); term=con.execute("""SELECT t.term_id FROM StudentAcademicAssignment sa JOIN Student s ON s.student_id=sa.student_id JOIN AcademicTerm t ON t.term_id=sa.term_id WHERE s.urn=? AND t.semester=?""",(get_student_urn(),sem)).fetchone()
        if not term: raise ValueError("You are not assigned to that semester.")
        con.execute("""INSERT INTO SemesterMarks(urn,term_id,semester,subject,ise,mse,ese) VALUES(?,?,?,?,?,?,?) ON CONFLICT(urn,term_id,subject) DO UPDATE SET ise=excluded.ise,mse=excluded.mse,ese=excluded.ese""",(get_student_urn(),term["term_id"],sem,subject,v[0],v[1],v[2]));con.commit();con.close();flash("Marks saved.","success")
    except Exception as e:flash(str(e),"danger")
    return redirect(url_for("student_dashboard"))

@app.route("/student/achievement",methods=["POST"])
@login_required("student")
def student_achievement():
    f=request.form;con=db();con.execute("INSERT INTO AwardsCertificates(urn,achievement_type,title,organization,year) VALUES(?,?,?,?,?)",(get_student_urn(),f.get("achievement_type"),f.get("title"),f.get("organization"),f.get("year")));con.commit();con.close();flash("Achievement added.","success");return redirect(url_for("student_dashboard"))


@app.route("/mentor/register",methods=["GET","POST"])
def mentor_register():
    if request.method=="POST":
        f=request.form
        name=f.get("name","").strip()
        username=f.get("username","").strip()
        pw=f.get("password","")
        academic_year=f.get("academic_year","").strip()
        semester=f.get("semester","").strip()
        batch=f.get("batch","").strip()
        department=f.get("department","").strip()
        if not all([name,username,pw,academic_year,semester,batch]):
            flash("Please complete all required fields.","danger")
        elif len(pw)<8:
            flash("Password must be at least 8 characters.","danger")
        elif not semester.isdigit() or not 1 <= int(semester) <= 8:
            flash("Semester must be between 1 and 8.","danger")
        else:
            semester=int(semester)
            con=db()
            try:
                term=con.execute("SELECT * FROM AcademicTerm WHERE academic_year=? AND semester=?",(academic_year,semester)).fetchone()
                if term is None:
                    con.execute("INSERT INTO AcademicTerm(academic_year,semester,is_current) VALUES(?,?,?)",(academic_year,semester,True if CLOUD_MODE else 1))
                    term=con.execute("SELECT * FROM AcademicTerm WHERE academic_year=? AND semester=?",(academic_year,semester)).fetchone()
                existing=con.execute("SELECT * FROM MentorAssignment WHERE term_id=? AND batch=?",(term["term_id"],batch)).fetchone()
                if existing:
                    con.close()
                    flash("This batch already has a mentor for this semester. A second mentor code cannot be created for the same semester + batch.","danger")
                else:
                    mentor_code=make_code("MC")
                    con.execute("INSERT INTO Mentor(name,username,password,department) VALUES(?,?,?,?)",(name,username,generate_password_hash(pw),department))
                    mid=con.execute("SELECT mentor_id FROM Mentor WHERE username=?",(username,)).fetchone()["mentor_id"]
                    con.execute("INSERT INTO MentorAssignment(mentor_id,term_id,department,batch,registration_code,mentor_code) VALUES(?,?,?,?,?,?)",(mid,term["term_id"],department,batch,mentor_code,mentor_code))
                    # Registering a mentor for a semester does NOT change the
                    # college's current semester. The current semester is controlled
                    # by CURRENT_ACADEMIC_YEAR/CURRENT_SEMESTER.
                    con.commit(); con.close()
                    assignment={"academic_year":academic_year,"semester":semester,"batch":batch}
                    return render_template("mentor_registered.html",name=name,username=username,mentor_code=mentor_code,assignment=assignment)
            except Exception:
                con.rollback(); con.close(); flash("Username may already exist or mentor registration failed.","danger")
    return render_template("mentor_register.html")

def make_code(prefix):
    return prefix + "-" + secrets.token_hex(4).upper()

@app.route("/mentor")
@login_required("mentor")
def mentor_dashboard():
    ma = mentor_scope()
    term_id, batch = ma["term_id"], ma["batch"]
    con = db()
    students = con.execute(
    """SELECT s.*,sa.class_name,sa.division,sa.roll_no,sa.batch
       FROM Student s
       JOIN StudentAcademicAssignment sa ON sa.student_id=s.student_id
       WHERE sa.term_id=? AND sa.batch=? ORDER BY s.name""",
    (term_id, batch),
).fetchall()
    con.close()
    ad = attendance_df(term_id=term_id, batch=batch)
    mk = marks_with_status(term_id=term_id, batch=batch)
    low = ad[ad.percentage < LOW_ATTENDANCE] if not ad.empty else ad
    fail = mk[mk.status == "FAIL"] if not mk.empty else mk
    return render_template(
        "mentor_dashboard.html",
        students=[dict(x) for x in students],
        attendance=ad.to_dict("records"),
        marks=mk.to_dict("records"),
        low_count=len(low["urn"].unique()) if not low.empty else 0,
        backlog_count=len(fail["urn"].unique()) if not fail.empty else 0,
        student_count=len(students),
        assignment=ma,
    )


@app.route("/mentor/student/<urn>/delete", methods=["POST"])
@login_required("mentor")
def mentor_delete_student(urn):
    # Keep deletion inside the mentor's current semester + batch scope.
    if not mentor_batch_ok(urn):
        abort(403)
    con = db()
    try:
        row = con.execute("SELECT student_id FROM Student WHERE urn=?", (urn,)).fetchone()
        if not row:
            flash("Student not found.", "danger")
            return redirect(url_for("mentor_dashboard"))
        # Delete dependent records explicitly so this works even when SQLite
        # foreign-key cascading is disabled by an existing local database.
        for table in ("Attendance", "SemesterMarks", "MentorSuggestions", "AwardsCertificates", "StudentAccount"):
            con.execute(f"DELETE FROM {table} WHERE urn=?", (urn,))
        con.execute("DELETE FROM StudentAcademicAssignment WHERE student_id=?", (row["student_id"],))
        con.execute("DELETE FROM Student WHERE student_id=?", (row["student_id"],))
        con.commit()
        flash("Student deleted from the system.", "success")
    except Exception:
        con.rollback()
        flash("The student could not be deleted.", "danger")
    finally:
        con.close()
    return redirect(url_for("mentor_dashboard"))

@app.route("/mentor/student/<urn>")
@login_required("mentor")
def mentor_student(urn):
    if not mentor_batch_ok(urn):abort(403)
    ma=mentor_scope();s=student_row(urn)
    assignment=assignment_for_student(urn,ma["term_id"])
    ad=attendance_df(urn,term_id=ma["term_id"]);mk=marks_with_status(urn,term_id=ma["term_id"])
    return render_template("mentor_student.html",s=s,assignment=assignment,attendance=ad.to_dict("records"),marks=mk.to_dict("records"),achievements=get_achievements(urn),suggestions=get_suggestions(urn))

@app.route("/mentor/suggestion",methods=["POST"])
@login_required("mentor")
def mentor_suggestion():
    urn=request.form["urn"]
    if not mentor_batch_ok(urn):abort(403)
    ma=mentor_scope()
    semester = ma["semester"]
    con=db();con.execute("INSERT INTO MentorSuggestions(urn,semester,suggestion,suggestion_date) VALUES(?,?,?,?)",(urn,semester,request.form["suggestion"].strip(),date.today().isoformat()));con.commit();con.close();flash("Suggestion added.","success");return redirect(url_for("mentor_student",urn=urn))

@app.route("/mentor/student/<urn>/attendance",methods=["POST"])
@login_required("mentor")
def mentor_attendance(urn):
    if not mentor_batch_ok(urn):abort(403)
    ma=mentor_scope()
    try:
        total=int(request.form["total"]);present=int(request.form["present"])
        if total<=0 or not(0<=present<=total):raise ValueError
        term_id, semester = ma["term_id"], ma["semester"]
        con=db();con.execute("""INSERT INTO Attendance(urn,term_id,semester,total_lectures,present,absent) VALUES(?,?,?,?,?,?) ON CONFLICT(urn,term_id) DO UPDATE SET total_lectures=excluded.total_lectures,present=excluded.present,absent=excluded.absent""",(urn,term_id,semester,total,present,total-present));con.commit();con.close();flash("Attendance updated.","success")
    except:flash("Invalid attendance values.","danger")
    return redirect(url_for("mentor_student",urn=urn))

@app.route("/mentor/student/<urn>/marks",methods=["POST"])
@login_required("mentor")
def mentor_marks(urn):
    if not mentor_batch_ok(urn):abort(403)
    ma=mentor_scope()
    try:
        subject=request.form["subject"].strip();v=validate_marks(request.form["ise"],request.form["mse"],request.form["ese"])
        if not subject:raise ValueError
        term_id, semester = ma["term_id"], ma["semester"]
        con=db();con.execute("""INSERT INTO SemesterMarks(urn,term_id,semester,subject,ise,mse,ese) VALUES(?,?,?,?,?,?,?) ON CONFLICT(urn,term_id,subject) DO UPDATE SET ise=excluded.ise,mse=excluded.mse,ese=excluded.ese""",(urn,term_id,semester,subject,v[0],v[1],v[2]));con.commit();con.close();flash("Marks updated.","success")
    except Exception as e:flash(str(e),"danger")
    return redirect(url_for("mentor_student",urn=urn))

@app.route("/mentor/reports")
@login_required("mentor")
def mentor_reports():
    """Reports hub for the mentor's assigned semester + batch only.

    The page supports the previous desktop-style workflow:
    - Individual report: search by URN/name/roll number, select a student,
      then export that student's PDF/Excel.
    - Batch report: attendance graph, mentor suggestions, PDF and Excel.
    """
    ma = mentor_scope()
    term_id, batch = ma["term_id"], ma["batch"]

    q = request.args.get("q", "").strip()
    selected_urn = request.args.get("urn", "").strip()

    students_df = dataframe_for_report(term_id, batch)
    if q:
        needle = q.lower()
        mask = (
            students_df["urn"].fillna("").astype(str).str.lower().str.contains(needle, regex=False)
            | students_df["name"].fillna("").astype(str).str.lower().str.contains(needle, regex=False)
            | students_df["roll_no"].fillna("").astype(str).str.lower().str.contains(needle, regex=False)
        )
        search_results = students_df[mask]
    else:
        search_results = students_df.iloc[0:0]

    selected = None
    if selected_urn:
        row = students_df[students_df["urn"].astype(str) == selected_urn]
        if not row.empty:
            selected = row.iloc[0].to_dict()

    ad = attendance_df(term_id=term_id, batch=batch)
    mk = marks_with_status(term_id=term_id, batch=batch)
    low = ad[ad.percentage < LOW_ATTENDANCE] if not ad.empty else ad
    fail = mk[mk.status == "FAIL"] if not mk.empty else mk

    # Batch suggestion list is restricted to this mentor's current batch.
    suggestions = []
    con = db()
    try:
        rows = con.execute(
            """SELECT ms.*,s.name,sa.batch,t.academic_year
               FROM MentorSuggestions ms
               JOIN Student s ON s.urn=ms.urn
               JOIN StudentAcademicAssignment sa ON sa.student_id=s.student_id
                    AND sa.term_id=?
               JOIN AcademicTerm t ON t.term_id=?
               WHERE sa.batch=? AND ms.semester=?
               ORDER BY ms.suggestion_id DESC""",
            (term_id, term_id, batch, ma["semester"]),
        ).fetchall()
        suggestions = [dict(r) for r in rows]
    finally:
        con.close()

    return render_template(
        "mentor_reports.html",
        assignment=ma,
        students=students_df.to_dict("records"),
        search_results=search_results.to_dict("records"),
        query=q,
        selected=selected,
        low=low.to_dict("records"),
        fail=fail.to_dict("records"),
        suggestions=suggestions,
    )


def report_student(urn):
    """Return a student row only when the student belongs to this mentor's scope."""
    if not mentor_batch_ok(urn):
        abort(403)
    ma = mentor_scope()
    df = dataframe_for_report(ma["term_id"], ma["batch"])
    row = df[df["urn"].astype(str) == str(urn)]
    if row.empty:
        abort(404)
    return ma, row.iloc[0].to_dict()


def build_individual_excel(urn, ma):
    out = io.BytesIO()
    df = dataframe_for_report(ma["term_id"], ma["batch"])
    student = df[df["urn"].astype(str) == str(urn)]
    ad = attendance_df(urn=urn, term_id=ma["term_id"])
    mk = marks_with_status(urn=urn, term_id=ma["term_id"])
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        student.to_excel(w, index=False, sheet_name="Student Profile")
        ad.to_excel(w, index=False, sheet_name="Attendance")
        mk.to_excel(w, index=False, sheet_name="Semester Marks")
    out.seek(0)
    return out


def build_individual_pdf(urn, ma):
    df = dataframe_for_report(ma["term_id"], ma["batch"])
    student = df[df["urn"].astype(str) == str(urn)]
    ad = attendance_df(urn=urn, term_id=ma["term_id"])
    mk = marks_with_status(urn=urn, term_id=ma["term_id"])

    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    styles = getSampleStyleSheet()
    s = student.iloc[0]
    story = [
        Paragraph("Mentee Management System - Individual Report", styles["Title"]),
        Paragraph(
            f"Academic Year: {ma['academic_year']} | Semester: {ma['semester']} | "
            f"Batch: {ma['batch']} | Mentor: {ma['mentor_name']}",
            styles["Normal"],
        ),
        Spacer(1, 12),
        Paragraph("Student Details", styles["Heading2"]),
    ]
    profile_data = [
        ["URN", s.get("urn", "")],
        ["Name", s.get("name", "")],
        ["Batch", s.get("batch", "")],
        ["Class", s.get("class_name", "") or ""],
        ["Division", s.get("division", "") or ""],
        ["Roll No", s.get("roll_no", "") or ""],
        ["Mobile", s.get("mobile", "") or ""],
        ["Parent Name", s.get("parent_name", "") or ""],
    ]
    t = Table(profile_data, colWidths=[100, 400])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), .4, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eaf2f8")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Attendance", styles["Heading2"]))
    if ad.empty:
        story.append(Paragraph("No attendance data available.", styles["Normal"]))
    else:
        da = [["Semester", "Total", "Present", "Absent", "Attendance %"]]
        da += [[r.semester, r.total_lectures, r.present, r.absent, r.percentage] for r in ad.itertuples()]
        t = Table(da, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), .3, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        story.append(t)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Semester Marks / Backlogs", styles["Heading2"]))
    if mk.empty:
        story.append(Paragraph("No semester marks available.", styles["Normal"]))
    else:
        dm = [["Semester", "Subject", "ISE", "MSE", "ESE", "Total", "Status"]]
        dm += [[r.semester, r.subject, r.ise, r.mse, r.ese, r.total, r.status] for r in mk.itertuples()]
        t = Table(dm, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), .3, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        story.append(t)

    story.append(Spacer(1, 14))
    story.append(Paragraph("Mentor Suggestions", styles["Heading2"]))
    for sug in get_suggestions(urn):
        story.append(Paragraph(
            f"{sug.get('suggestion_date', '')}: {sug.get('suggestion', '')}",
            styles["Normal"],
        ))

    doc.build(story)
    out.seek(0)
    return out


def save_report_file(filename, data):
    reports_dir = os.path.join(BASE_DIR, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, filename)
    with open(path, "wb") as f:
        f.write(data)
    return path


@app.route("/mentor/report/individual/excel/<urn>")
@login_required("mentor")
def individual_excel(urn):
    ma, student = report_student(urn)
    out = build_individual_excel(urn, ma)
    filename = f"Individual_Report_{re.sub(r'[^A-Za-z0-9_.-]', '_', str(urn))}.xlsx"
    save_report_file(filename, out.getvalue())
    out.seek(0)
    return send_file(
        out, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/mentor/report/individual/pdf/<urn>")
@login_required("mentor")
def individual_pdf(urn):
    ma, student = report_student(urn)
    out = build_individual_pdf(urn, ma)
    filename = f"Individual_Report_{re.sub(r'[^A-Za-z0-9_.-]', '_', str(urn))}.pdf"
    save_report_file(filename, out.getvalue())
    out.seek(0)
    return send_file(out, as_attachment=True, download_name=filename, mimetype="application/pdf")


@app.route("/mentor/export/excel")
@login_required("mentor")
def export_excel():
    """Batch Excel report."""
    ma = mentor_scope()
    out = io.BytesIO()
    df = dataframe_for_report(ma["term_id"], ma["batch"])
    ad = attendance_df(term_id=ma["term_id"], batch=ma["batch"])
    mk = marks_with_status(term_id=ma["term_id"], batch=ma["batch"])
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Students")
        ad.to_excel(w, index=False, sheet_name="Attendance")
        mk.to_excel(w, index=False, sheet_name="Semester Marks")
    filename = f"Batch_Report_{re.sub(r'[^A-Za-z0-9_.-]', '_', str(ma['batch']))}.xlsx"
    save_report_file(filename, out.getvalue())
    out.seek(0)
    return send_file(
        out, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/mentor/export/pdf")
@login_required("mentor")
def export_pdf():
    """Batch PDF report."""
    ma = mentor_scope()
    df = dataframe_for_report(ma["term_id"], ma["batch"])
    ad = attendance_df(term_id=ma["term_id"], batch=ma["batch"])
    mk = marks_with_status(term_id=ma["term_id"], batch=ma["batch"])

    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=landscape(A4), rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Mentee Management System - Batch Report", styles["Title"]),
        Paragraph(
            f"Academic Year: {ma['academic_year']} | Semester: {ma['semester']} | "
            f"Batch: {ma['batch']} | Mentor: {ma['mentor_name']}",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]

    data = [["URN", "Name", "Batch", "Class", "Div", "Roll", "Mobile"]]
    data += [
        [r.urn, r.name, r.batch, r.class_name or "", r.division or "", r.roll_no or "", r.mobile or ""]
        for r in df.itertuples()
    ]
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), .3, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
    ]))
    story.append(t)
    story.append(PageBreak())

    story.append(Paragraph("Batch Attendance - All Students", styles["Heading2"]))
    if ad.empty:
        story.append(Paragraph("No attendance data available.", styles["Normal"]))
    else:
        da = [["URN", "Name", "Sem", "Total", "Present", "Absent", "%"]]
        da += [[r.urn, r.name, r.semester, r.total_lectures, r.present, r.absent, r.percentage] for r in ad.itertuples()]
        t = Table(da, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), .3, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
        ]))
        story.append(t)
    story.append(PageBreak())

    story.append(Paragraph("Semester Marks / Backlogs", styles["Heading2"]))
    if mk.empty:
        story.append(Paragraph("No semester marks available.", styles["Normal"]))
    else:
        dm = [["URN", "Name", "Sem", "Subject", "ISE", "MSE", "ESE", "Total", "Status"]]
        dm += [[r.urn, r.name, r.semester, r.subject, r.ise, r.mse, r.ese, r.total, r.status] for r in mk.itertuples()]
        t = Table(dm, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), .3, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
        ]))
        story.append(t)

    doc.build(story)
    filename = f"Batch_Report_{re.sub(r'[^A-Za-z0-9_.-]', '_', str(ma['batch']))}.pdf"
    save_report_file(filename, out.getvalue())
    out.seek(0)
    return send_file(out, as_attachment=True, download_name=filename, mimetype="application/pdf")


@app.route("/mentor/suggestion/add", methods=["GET", "POST"])
@login_required("mentor")
def add_mentor_suggestion():
    ma = mentor_scope()
    students = dataframe_for_report(ma["term_id"], ma["batch"]).to_dict("records")
    if request.method == "POST":
        urn = request.form.get("urn", "").strip()
        suggestion = request.form.get("suggestion", "").strip()
        if not urn or not suggestion:
            flash("Select a student and enter a suggestion.", "danger")
        elif not mentor_batch_ok(urn):
            abort(403)
        else:
            con = db()
            try:
                con.execute(
                    "INSERT INTO MentorSuggestions(urn,semester,suggestion,suggestion_date) VALUES(?,?,?,?)",
                    (urn, ma["semester"], suggestion, date.today().isoformat()),
                )
                con.commit()
                flash("Suggestion added.", "success")
                return redirect(url_for("mentor_suggestions"))
            finally:
                con.close()
    return render_template("mentor_suggestion_add.html", assignment=ma, students=students)

@app.route("/mentor/suggestions")
@login_required("mentor")
def mentor_suggestions():
    ma = mentor_scope()
    con = db()
    try:
        rows = con.execute(
            """SELECT ms.*,s.name,sa.batch,sa.division,sa.roll_no
               FROM MentorSuggestions ms
               JOIN Student s ON s.urn=ms.urn
               JOIN StudentAcademicAssignment sa
                 ON sa.student_id=s.student_id AND sa.term_id=?
               WHERE sa.batch=? AND ms.semester=?
               ORDER BY ms.suggestion_id DESC""",
            (ma["term_id"], ma["batch"], ma["semester"]),
        ).fetchall()
        suggestions = [dict(r) for r in rows]
    finally:
        con.close()
    return render_template("mentor_suggestions.html", assignment=ma, suggestions=suggestions)


@app.route("/mentor/reports/files")
@login_required("mentor")
def report_files():
    """Web equivalent of the old 'Open Reports Folder' button."""
    reports_dir = os.path.join(BASE_DIR, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    files = sorted(
        [f for f in os.listdir(reports_dir) if os.path.isfile(os.path.join(reports_dir, f))],
        reverse=True,
    )
    return render_template("report_files.html", assignment=mentor_scope(), files=files)


@app.route("/mentor/reports/files/<path:filename>")
@login_required("mentor")
def download_report_file(filename):
    # Report files are generated by this application only. Do not allow path traversal.
    safe_name = os.path.basename(filename)
    path = os.path.join(BASE_DIR, "reports", safe_name)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=safe_name)


@app.route("/mentor/analytics")
@login_required("mentor")
def analytics():
    ma = mentor_scope()
    ad = attendance_df(term_id=ma["term_id"], batch=ma["batch"])
    chart = None
    if not ad.empty:
        avg = ad.groupby(["urn", "name"])["percentage"].mean().reset_index().sort_values("name")
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.bar(avg["name"], avg["percentage"])
        ax.axhline(LOW_ATTENDANCE, linestyle="--")
        ax.set_ylabel("Average attendance %")
        ax.set_ylim(0, 105)
        ax.tick_params(axis="x", rotation=45)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=140)
        plt.close(fig)
        chart = base64.b64encode(buf.getvalue()).decode()
    return render_template("analytics.html", chart=chart, assignment=ma)

@app.route("/health")
def health():
    try:con=db();con.execute("SELECT 1");con.close();return {"status":"ok","database":"postgresql" if CLOUD_MODE else "sqlite"}
    except Exception:return {"status":"error"},500

@app.errorhandler(403)
def forbidden(e):return render_template("error.html",code=403,message="You are not authorized to access this page."),403
@app.errorhandler(404)
def not_found(e):return render_template("error.html",code=404,message="Page not found."),404

if __name__=="__main__":
    init_db();app.run(host=os.getenv("HOST","0.0.0.0"),port=int(os.getenv("PORT","5000")),debug=False)
