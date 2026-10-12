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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image
from reportlab.lib.styles import getSampleStyleSheet
from werkzeug.utils import secure_filename
from flask import send_from_directory
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

RESUME_UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "resumes")
ALLOWED_RESUME_EXTENSIONS = {"pdf", "docx"}
MAX_RESUME_SIZE = 5 * 1024 * 1024  # 5 MB


def allowed_resume_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_RESUME_EXTENSIONS
    )


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
os.makedirs(RESUME_UPLOAD_FOLDER, exist_ok=True)

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
                
"""CREATE TABLE IF NOT EXISTS SemesterMarks(
    mark_id SERIAL PRIMARY KEY,
    urn TEXT NOT NULL REFERENCES Student(urn) ON DELETE CASCADE,
    term_id INTEGER NOT NULL REFERENCES AcademicTerm(term_id) ON DELETE CASCADE,
    semester INTEGER NOT NULL,
    subject TEXT NOT NULL,
    total DOUBLE PRECISION NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'Fail',
    UNIQUE(urn, term_id, subject)
)""",
                """CREATE TABLE IF NOT EXISTS MentorSuggestions(suggestion_id SERIAL PRIMARY KEY, urn TEXT NOT NULL REFERENCES Student(urn) ON DELETE CASCADE, semester INTEGER NOT NULL, suggestion TEXT NOT NULL, suggestion_date DATE NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS AwardsCertificates(achievement_id SERIAL PRIMARY KEY, urn TEXT NOT NULL REFERENCES Student(urn) ON DELETE CASCADE, achievement_type TEXT, title TEXT, organization TEXT, year TEXT)""",
                """CREATE TABLE IF NOT EXISTS StudentParticipation(
    participation_id SERIAL PRIMARY KEY,
    urn TEXT NOT NULL REFERENCES Student(urn) ON DELETE CASCADE,
    event_name TEXT NOT NULL,
    event_date DATE NOT NULL,
    event_result TEXT NOT NULL CHECK(event_result IN ('Winner','Runner-up','Participated')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)""",
"""CREATE TABLE IF NOT EXISTS StudentCareerProfile(
    urn TEXT PRIMARY KEY REFERENCES Student(urn) ON DELETE CASCADE,
    hackerrank_url TEXT,
    github_url TEXT,
    vision TEXT,
    short_term_goal TEXT,
    short_term_action_plan TEXT,
    long_term_goal TEXT,
    long_term_action_plan TEXT,
    resume_filename TEXT,
    resume_path TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)""",
"""CREATE TABLE IF NOT EXISTS StudentMission(
    mission_id SERIAL PRIMARY KEY,
    urn TEXT NOT NULL REFERENCES Student(urn) ON DELETE CASCADE,
    mission TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)""",
"""CREATE TABLE IF NOT EXISTS StudentMonthlyAttendance(
    entry_id SERIAL PRIMARY KEY,
    urn TEXT NOT NULL REFERENCES Student(urn) ON DELETE CASCADE,
    term_id INTEGER NOT NULL REFERENCES AcademicTerm(term_id) ON DELETE CASCADE,
    start_month INTEGER NOT NULL CHECK(start_month BETWEEN 1 AND 12),
    end_month INTEGER NOT NULL CHECK(end_month BETWEEN 1 AND 12),
    monthly_percentage DOUBLE PRECISION NOT NULL CHECK(monthly_percentage BETWEEN 0 AND 100),
    cumulative_percentage DOUBLE PRECISION NOT NULL CHECK(cumulative_percentage BETWEEN 0 AND 100),
    UNIQUE(urn, term_id, start_month, end_month)
)""",
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
               """CREATE TABLE IF NOT EXISTS SemesterMarks(
    mark_id SERIAL PRIMARY KEY,
    urn TEXT NOT NULL REFERENCES Student(urn) ON DELETE CASCADE,
    term_id INTEGER NOT NULL REFERENCES AcademicTerm(term_id) ON DELETE CASCADE,
    semester INTEGER NOT NULL,
    subject TEXT NOT NULL,
    total DOUBLE PRECISION NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'Fail',
    UNIQUE(urn, term_id, subject)
)""",
                """CREATE TABLE IF NOT EXISTS MentorSuggestions(suggestion_id INTEGER PRIMARY KEY AUTOINCREMENT, urn TEXT NOT NULL, semester INTEGER NOT NULL, suggestion TEXT NOT NULL, suggestion_date TEXT NOT NULL, FOREIGN KEY(urn) REFERENCES Student(urn) ON DELETE CASCADE)""",
                """CREATE TABLE IF NOT EXISTS AwardsCertificates(achievement_id INTEGER PRIMARY KEY AUTOINCREMENT, urn TEXT NOT NULL, achievement_type TEXT, title TEXT, organization TEXT, year TEXT, FOREIGN KEY(urn) REFERENCES Student(urn) ON DELETE CASCADE)""",
                """CREATE TABLE IF NOT EXISTS StudentParticipation(
    participation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    urn TEXT NOT NULL,
    event_name TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_result TEXT NOT NULL CHECK(event_result IN ('Winner','Runner-up','Participated')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(urn) REFERENCES Student(urn) ON DELETE CASCADE
)""",
"""CREATE TABLE IF NOT EXISTS StudentCareerProfile(
    urn TEXT PRIMARY KEY,
    hackerrank_url TEXT,
    github_url TEXT,
    vision TEXT,
    short_term_goal TEXT,
    short_term_action_plan TEXT,
    long_term_goal TEXT,
    long_term_action_plan TEXT,
    resume_filename TEXT,
    resume_path TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(urn) REFERENCES Student(urn) ON DELETE CASCADE
)""",
"""CREATE TABLE IF NOT EXISTS StudentMission(
    mission_id INTEGER PRIMARY KEY AUTOINCREMENT,
    urn TEXT NOT NULL,
    mission TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(urn) REFERENCES Student(urn) ON DELETE CASCADE
)""",
"""CREATE TABLE IF NOT EXISTS StudentMonthlyAttendance(
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    urn TEXT NOT NULL,
    term_id INTEGER NOT NULL,
    start_month INTEGER NOT NULL CHECK(start_month BETWEEN 1 AND 12),
    end_month INTEGER NOT NULL CHECK(end_month BETWEEN 1 AND 12),
    monthly_percentage REAL NOT NULL CHECK(monthly_percentage BETWEEN 0 AND 100),
    cumulative_percentage REAL NOT NULL CHECK(cumulative_percentage BETWEEN 0 AND 100),
    UNIQUE(urn, term_id, start_month, end_month),
    FOREIGN KEY(urn) REFERENCES Student(urn) ON DELETE CASCADE,
    FOREIGN KEY(term_id) REFERENCES AcademicTerm(term_id) ON DELETE CASCADE
)""",
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
    con = db()

    q = """
        SELECT
            m.urn,
            s.name,
            sa.batch,
            sa.division,
            t.academic_year,
            t.semester,
            m.subject,
            m.total,
            m.status
        FROM SemesterMarks m
        JOIN Student s ON s.urn = m.urn
        JOIN AcademicTerm t ON t.term_id = m.term_id
        LEFT JOIN StudentAcademicAssignment sa
            ON sa.student_id = s.student_id
            AND sa.term_id = m.term_id
    """

    p = []
    w = []

    if urn:
        w.append("m.urn = ?")
        p.append(urn)

    if term_id:
        w.append("m.term_id = ?")
        p.append(term_id)

    if batch and batch != "ALL":
        w.append("sa.batch = ?")
        p.append(batch)

    if w:
        q += " WHERE " + " AND ".join(w)

    q += " ORDER BY s.name, t.semester, m.subject"

    try:
        df = query_df(q, p)
        return df
    finally:
        con.close()

def marks_with_status(urn=None, term_id=None, batch=None):
    df = marks_df(urn, term_id, batch)

    if df.empty:
        return df

    df["status"] = df["status"].fillna("Fail")
    df["reason"] = ""

    return df


def backlog_summary(mk):
    """
    Count failed subjects for each student.

    0 backlogs  -> No Backlogs
    1 backlog   -> 1 Backlog
    2 backlogs  -> 2 Backlogs
    3 backlogs  -> 3 Backlogs
    4+ backlogs -> More Than 3 Backlogs
    """

    if mk.empty:
        return pd.DataFrame(
            columns=[
                "urn",
                "name",
                "backlog_count",
                "backlog_status",
                "failed_subjects"
            ]
        )

    fail = mk[mk["status"] == "FAIL"].copy()

    if fail.empty:
        return pd.DataFrame(
            columns=[
                "urn",
                "name",
                "backlog_count",
                "backlog_status",
                "failed_subjects"
            ]
        )

    summary = (
        fail.groupby(["urn", "name"])
        .agg(
            backlog_count=("subject", "count"),
            failed_subjects=("subject", lambda x: ", ".join(x.astype(str)))
        )
        .reset_index()
    )

    def get_backlog_status(count):
        if count == 1:
            return "1 Backlog"
        elif count == 2:
            return "2 Backlogs"
        elif count == 3:
            return "3 Backlogs"
        else:
            return "More Than 3 Backlogs"

    summary["backlog_status"] = summary["backlog_count"].apply(
        get_backlog_status
    )

    summary = summary.sort_values(
        "backlog_count",
        ascending=False
    )

    return summary
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
    urn = get_student_urn()
    s = student_row(urn)
    ct = current_term()
    history = assignments_for_student(urn)
    a = assignment_for_student(urn, ct["term_id"] if ct else None)

    if a is None and history:
        a = history[0]

    ad = attendance_df(urn)
    
    con = db()
    try:
        career_profile = con.execute(
            "SELECT * FROM StudentCareerProfile WHERE urn = ?",
            (urn,)
        ).fetchone()

        participation_history = con.execute("""
            SELECT event_name, event_date, event_result
            FROM StudentParticipation
            WHERE urn = ?
            ORDER BY event_date DESC, participation_id DESC
        """, (urn,)).fetchall()

        missions = con.execute("""
            SELECT mission_id, mission
            FROM StudentMission
            WHERE urn = ?
            ORDER BY mission_id DESC
        """, (urn,)).fetchall()
    finally:
        con.close()

    mk = marks_with_status(urn)

    con = db()
    try:
        monthly_rows = con.execute("""
            SELECT
                sma.*,
                t.semester
            FROM StudentMonthlyAttendance sma
            JOIN AcademicTerm t ON t.term_id = sma.term_id
            WHERE sma.urn = ?
            ORDER BY t.semester, sma.start_month, sma.end_month
        """, (urn,)).fetchall()

        monthly_attendance = []

        month_names = [
            "", "January", "February", "March", "April",
            "May", "June", "July", "August", "September",
            "October", "November", "December"
        ]

        for r in monthly_rows:
            start = r["start_month"]
            end = r["end_month"]

            period = month_names[start]
            if end != start:
                period += "–" + month_names[end]

            monthly_attendance.append({
                "semester": r["semester"],
                "period": period,
                "monthly_percentage": r["monthly_percentage"],
                "cumulative_percentage": r["cumulative_percentage"]
            })
    finally:
        con.close()

    return render_template(
        "student_dashboard.html",
        s=s,
        career_profile=career_profile,
        participation_history=participation_history,
        missions=missions,
        assignment=a,
        history=history,
        terms=get_terms(),
        attendance=ad.to_dict("records"),
        monthly_attendance=monthly_attendance,
        marks=mk.to_dict("records"),
        low_att=ad[ad.percentage < LOW_ATTENDANCE].to_dict("records")
        if not ad.empty else [],
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


@app.route("/student/participation", methods=["POST"])
@login_required("student")
def add_student_participation():
    urn = get_student_urn()
    event_name = request.form.get("event_name", "").strip()
    event_date = request.form.get("event_date", "").strip()
    event_result = request.form.get("event_result", "").strip()

    if not event_name or not event_date or event_result not in (
        "Winner", "Runner-up", "Participated"
    ):
        flash("Please enter valid participation details.", "danger")
        return redirect(url_for("student_dashboard"))

    con = db()
    try:
        con.execute("""
            INSERT INTO StudentParticipation
                (urn, event_name, event_date, event_result)
            VALUES (?, ?, ?, ?)
        """, (urn, event_name, event_date, event_result))
        con.commit()
        flash("Participation details added.", "success")
    except Exception:
        con.rollback()
        flash("Could not save participation details.", "danger")
    finally:
        con.close()

    return redirect(url_for("student_dashboard"))



@app.route("/student/career-profile", methods=["POST"])
@login_required("student")
def save_student_career_profile():
    urn = get_student_urn()

    fields = (
        "hackerrank_url",
        "github_url",
        "vision",
        "short_term_goal",
        "short_term_action_plan",
        "long_term_goal",
        "long_term_action_plan",
    )

    con = db()
    try:
        existing = con.execute(
            "SELECT * FROM StudentCareerProfile WHERE urn = ?",
            (urn,)
        ).fetchone()

        values = {}
        for field in fields:
            if field in request.form:
                values[field] = request.form.get(field, "").strip()
            else:
                values[field] = (
                    (existing[field] or "")
                    if existing else ""
                )

        for field in ("hackerrank_url", "github_url"):
            url = values[field]
            if url and not url.startswith(("https://", "http://")):
                flash(
                    "Profile links must begin with http:// or https://.",
                    "danger"
                )
                return redirect(url_for("student_dashboard"))

        con.execute("""
            INSERT INTO StudentCareerProfile (
                urn, hackerrank_url, github_url, vision,
                short_term_goal, short_term_action_plan,
                long_term_goal, long_term_action_plan
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(urn) DO UPDATE SET
                hackerrank_url = excluded.hackerrank_url,
                github_url = excluded.github_url,
                vision = excluded.vision,
                short_term_goal = excluded.short_term_goal,
                short_term_action_plan = excluded.short_term_action_plan,
                long_term_goal = excluded.long_term_goal,
                long_term_action_plan = excluded.long_term_action_plan,
                updated_at = CURRENT_TIMESTAMP
        """, (
            urn,
            values["hackerrank_url"],
            values["github_url"],
            values["vision"],
            values["short_term_goal"],
            values["short_term_action_plan"],
            values["long_term_goal"],
            values["long_term_action_plan"],
        ))

        con.commit()
        flash("Career profile saved successfully.", "success")

    except Exception:
        con.rollback()
        flash("Could not save your career profile.", "danger")

    finally:
        con.close()

    return redirect(url_for("student_dashboard"))



@app.route("/student/mission", methods=["POST"])
@login_required("student")
def add_student_mission():
    urn = get_student_urn()
    mission = request.form.get("mission", "").strip()

    if not mission:
        flash("Please enter a mission.", "danger")
        return redirect(url_for("student_dashboard"))

    con = db()
    try:
        con.execute("""
            INSERT INTO StudentMission (urn, mission)
            VALUES (?, ?)
        """, (urn, mission))
        con.commit()
        flash("Mission added successfully.", "success")
    except Exception:
        con.rollback()
        flash("Could not save the mission.", "danger")
    finally:
        con.close()

    return redirect(url_for("student_dashboard"))


@app.route("/student/resume", methods=["POST"])
@login_required("student")
def upload_student_resume():
    urn = get_student_urn()
    uploaded_file = request.files.get("resume")

    if not uploaded_file or not uploaded_file.filename:
        flash("Please select a resume file.", "danger")
        return redirect(url_for("student_dashboard"))

    if not allowed_resume_file(uploaded_file.filename):
        flash("Only PDF and DOCX resumes are allowed.", "danger")
        return redirect(url_for("student_dashboard"))

    uploaded_file.stream.seek(0, os.SEEK_END)
    file_size = uploaded_file.stream.tell()
    uploaded_file.stream.seek(0)

    if file_size > MAX_RESUME_SIZE:
        flash("Resume must be 5 MB or smaller.", "danger")
        return redirect(url_for("student_dashboard"))

    original_name = secure_filename(uploaded_file.filename)
    if not original_name:
        flash("Invalid filename.", "danger")
        return redirect(url_for("student_dashboard"))

    extension = original_name.rsplit(".", 1)[1].lower()
    stored_name = f"{uuid.uuid4().hex}.{extension}"
    file_path = os.path.join(RESUME_UPLOAD_FOLDER, stored_name)

    con = db()
    try:
        uploaded_file.save(file_path)

        con.execute("""
            INSERT INTO StudentCareerProfile (urn, resume_filename, resume_path)
            VALUES (?, ?, ?)
            ON CONFLICT(urn) DO UPDATE SET
                resume_filename=excluded.resume_filename,
                resume_path=excluded.resume_path,
                updated_at=CURRENT_TIMESTAMP
        """, (urn, original_name, stored_name))

        con.commit()
        flash("Resume uploaded successfully.", "success")

    except Exception:
        con.rollback()
        if os.path.exists(file_path):
            os.remove(file_path)
        flash("Could not save your resume. Please try again.", "danger")

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



@app.route("/student/attendance", methods=["POST"])
@login_required("student")
def student_attendance():
    urn = get_student_urn()
    con = db()

    try:
        semester = int(request.form.get("semester", ""))
        start_month = int(request.form.get("start_month", ""))
        end_month = int(request.form.get("end_month", ""))
        monthly_percentage = float(
            request.form.get("monthly_percentage", "")
        )

        if semester not in range(1, 9):
            raise ValueError("Select a valid semester.")

        if start_month not in range(1, 13):
            raise ValueError("Select a valid starting month.")

        if end_month not in range(start_month, 13):
            raise ValueError(
                "Ending month must be the same as or later than starting month."
            )

        if not (0 <= monthly_percentage <= 100):
            raise ValueError(
                "Attendance percentage must be between 0 and 100."
            )

        term = con.execute("""
            SELECT t.term_id
            FROM StudentAcademicAssignment sa
            JOIN Student s ON s.student_id = sa.student_id
            JOIN AcademicTerm t ON t.term_id = sa.term_id
            WHERE s.urn = ? AND t.semester = ?
            ORDER BY t.term_id DESC
            LIMIT 1
        """, (urn, semester)).fetchone()

        if not term:
            raise ValueError("You are not assigned to that semester.")

        # Retain the database column, but no longer use cumulative attendance.
        con.execute("""
            INSERT INTO StudentMonthlyAttendance (
                urn,
                term_id,
                start_month,
                end_month,
                monthly_percentage,
                cumulative_percentage
            )
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(urn, term_id, start_month, end_month)
            DO UPDATE SET
                monthly_percentage = excluded.monthly_percentage
        """, (
            urn,
            term["term_id"],
            start_month,
            end_month,
            monthly_percentage
        ))

        con.commit()
        flash("Attendance period saved successfully.", "success")

    except (ValueError, TypeError) as e:
        con.rollback()
        flash(str(e) or "Enter valid attendance details.", "danger")

    except Exception:
        con.rollback()
        flash("Could not save attendance. Please try again.", "danger")

    finally:
        con.close()

    return redirect(url_for("student_dashboard"))

@app.route("/student/marks", methods=["POST"])
@login_required("student")
def student_marks():
    con = None

    try:
        urn = get_student_urn()
        sem = int(request.form.get("semester", ""))
        subject = request.form.get("subject", "").strip()
        total = float(request.form.get("total_marks", ""))
        status = request.form.get("status", "").strip()

        if sem not in range(1, 9):
            raise ValueError("Enter a valid semester from 1 to 8.")

        if not subject:
            raise ValueError("Enter the subject name.")

        if not 0 <= total <= 100:
            raise ValueError("Total marks must be between 0 and 100.")

        if status not in ("Pass", "Fail"):
            raise ValueError("Please select Pass or Fail.")

        if not student_has_semester(urn, sem):
            raise ValueError("You are not assigned to that semester.")

        con = db()

        term = con.execute("""
            SELECT t.term_id
            FROM StudentAcademicAssignment sa
            JOIN Student s ON s.student_id = sa.student_id
            JOIN AcademicTerm t ON t.term_id = sa.term_id
            WHERE s.urn = ? AND t.semester = ?
            ORDER BY t.term_id DESC
            LIMIT 1
        """, (urn, sem)).fetchone()

        if not term:
            raise ValueError("You are not assigned to that semester.")

        con.execute("""
            INSERT INTO SemesterMarks
                (urn, term_id, semester, subject, total, status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(urn, term_id, subject)
            DO UPDATE SET
                total = excluded.total,
                status = excluded.status
        """, (urn, term["term_id"], sem, subject, total, status))

        con.commit()
        flash("Marks saved successfully.", "success")

    except (ValueError, TypeError) as e:
        if con:
            con.rollback()
        flash(str(e) or "Enter valid marks.", "danger")

    except Exception:
        if con:
            con.rollback()
        flash(
            "Could not save marks. Check the SemesterMarks table "
            "and marks-reading queries.",
            "danger"
        )

    finally:
        if con:
            con.close()

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
    try:
        students = con.execute(
            """
            SELECT s.*, sa.class_name, sa.division, sa.roll_no, sa.batch
            FROM Student s
            JOIN StudentAcademicAssignment sa
              ON sa.student_id = s.student_id
            WHERE sa.term_id = ? AND sa.batch = ?
            ORDER BY s.name
            """,
            (term_id, batch),
        ).fetchall()

        student_list = [dict(x) for x in students]
        student_urns = [str(s["urn"]) for s in student_list]

        career_profiles = {}
        participation_by_urn = {}
        missions_by_urn = {}
        monthly_attendance = []

        if student_urns:
            placeholders = ",".join("?" for _ in student_urns)

            profiles = con.execute(
                f"""
                SELECT urn, github_url, hackerrank_url, vision,
                       short_term_goal, short_term_action_plan,
                       long_term_goal, long_term_action_plan,
                       resume_filename
                FROM StudentCareerProfile
                WHERE urn IN ({placeholders})
                """,
                student_urns,
            ).fetchall()

            career_profiles = {
                str(row["urn"]): dict(row) for row in profiles
            }

            participation = con.execute(
                f"""
                SELECT urn, event_name, event_date, event_result
                FROM StudentParticipation
                WHERE urn IN ({placeholders})
                ORDER BY event_date DESC
                """,
                student_urns,
            ).fetchall()

            for row in participation:
                urn = str(row["urn"])
                participation_by_urn.setdefault(urn, []).append(dict(row))
            
            mission_rows = con.execute(
                f"""
                SELECT urn, mission
                FROM StudentMission
                WHERE urn IN ({placeholders})
                ORDER BY mission_id DESC
                """,
                student_urns,
            ).fetchall()

            for row in mission_rows:
                urn = str(row["urn"])
                missions_by_urn.setdefault(urn, []).append(
                    row["mission"]
                )


            monthly_rows = con.execute(
                f"""
                SELECT sma.urn, s.name, sa.batch, t.semester,
                       sma.start_month, sma.end_month,
                       sma.monthly_percentage
                FROM StudentMonthlyAttendance sma
                JOIN Student s ON s.urn = sma.urn
                JOIN AcademicTerm t ON t.term_id = sma.term_id
                JOIN StudentAcademicAssignment sa
                  ON sa.student_id = s.student_id
                 AND sa.term_id = sma.term_id
                WHERE sma.urn IN ({placeholders})
                  AND sa.batch = ?
                ORDER BY s.name, t.semester,
                         sma.start_month, sma.end_month
                """,
                student_urns + [batch],
            ).fetchall()

            monthly_attendance = [dict(row) for row in monthly_rows]

    finally:
        con.close()

    ad = attendance_df(term_id=term_id, batch=batch)
    mk = marks_with_status(term_id=term_id, batch=batch)
    low = ad[ad.percentage < LOW_ATTENDANCE] if not ad.empty else ad
    fail = mk[mk.status == "FAIL"] if not mk.empty else mk

    return render_template(
        "mentor_dashboard.html",
        students=student_list,
        career_profiles=career_profiles,
        participation_by_urn=participation_by_urn,
        missions_by_urn=missions_by_urn,
        monthly_attendance=monthly_attendance,
        attendance=ad.to_dict("records"),
        marks=mk.to_dict("records"),
        low_count=len(low["urn"].unique()) if not low.empty else 0,
        backlog_count=len(fail["urn"].unique()) if not fail.empty else 0,
        student_count=len(student_list),
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
    if not mentor_batch_ok(urn):
        abort(403)

    ma = mentor_scope()
    s = student_row(urn)
    assignment = assignment_for_student(urn, ma["term_id"])

    ad = attendance_df(urn, term_id=ma["term_id"])
    mk = marks_with_status(urn)

    con = db()
    try:
        career_profile = con.execute("""
            SELECT *
            FROM StudentCareerProfile
            WHERE urn = ?
        """, (urn,)).fetchone()

        participation_history = con.execute("""
            SELECT event_name, event_date, event_result
            FROM StudentParticipation
            WHERE urn = ?
            ORDER BY event_date DESC
        """, (urn,)).fetchall()

        monthly_attendance = con.execute("""
            SELECT
                t.semester,
                a.start_month,
                a.end_month,
                a.monthly_percentage
            FROM StudentMonthlyAttendance a
            JOIN AcademicTerm t ON t.term_id = a.term_id
            WHERE a.urn = ?
            ORDER BY t.semester, a.start_month
        """, (urn,)).fetchall()

    finally:
        con.close()

    return render_template(
        "mentor_student.html",
        s=s,
        assignment=assignment,
        attendance=ad.to_dict("records"),
        marks=mk.to_dict("records"),
        achievements=get_achievements(urn),
        suggestions=get_suggestions(urn),
        career_profile=career_profile,
        participation_history=participation_history,
        monthly_attendance=monthly_attendance
    )

@app.route("/mentor/student/<urn>/resume")
@login_required("mentor")
def download_student_resume(urn):
    if not mentor_batch_ok(urn):
        abort(403)

    con = db()
    try:
        profile = con.execute("""
            SELECT resume_filename, resume_path
            FROM StudentCareerProfile
            WHERE urn = ?
        """, (urn,)).fetchone()
    finally:
        con.close()

    if not profile or not profile["resume_path"]:
        flash("No resume has been uploaded for this student.", "warning")
        return redirect(url_for("mentor_student", urn=urn))

    stored_name = os.path.basename(profile["resume_path"])
    file_path = os.path.join(RESUME_UPLOAD_FOLDER, stored_name)

    if not os.path.isfile(file_path):
        flash("Resume file is not currently available.", "danger")
        return redirect(url_for("mentor_student", urn=urn))

    return send_from_directory(
        RESUME_UPLOAD_FOLDER,
        stored_name,
        as_attachment=True,
        download_name=secure_filename(profile["resume_filename"] or stored_name)
    )

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

def dataframe_for_report(term_id, batch):
    """
    Return students belonging to the mentor's assigned
    semester + batch, with their academic and profile details.
    """
    sql = """
        SELECT
            s.urn,
            s.name,
            s.address,
            s.mobile,
            s.parent_name,
            s.parent_mobile,
            s.hobbies,
            s.ssc_medium,
            s.hsc_medium,
            s.ssc_marks,
            s.hsc_marks,
            s.awards,
            s.certifications,
            sa.class_name,
            sa.division,
            sa.batch,
            sa.roll_no,
            t.academic_year,
            t.semester
        FROM Student s
        JOIN StudentAcademicAssignment sa
            ON sa.student_id = s.student_id
        JOIN AcademicTerm t
            ON t.term_id = sa.term_id
        WHERE sa.term_id = ?
          AND sa.batch = ?
        ORDER BY s.name
    """

    return query_df(sql, (term_id, batch))

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
    """Build an individual Excel report using the latest submitted attendance."""

    out = io.BytesIO()

    # -----------------------------
    # Student Profile
    # -----------------------------
    df = dataframe_for_report(ma["term_id"], ma["batch"])
    student = df[df["urn"].astype(str) == str(urn)]

    if student.empty:
        raise ValueError("Student not found for individual report.")

    # -----------------------------
    # Latest Submitted Attendance
    # -----------------------------
    con = db()
    try:
        monthly_rows = con.execute("""
            SELECT
                sma.urn,
                sma.start_month,
                sma.end_month,
                sma.monthly_percentage,
                sma.cumulative_percentage,
                t.semester
            FROM StudentMonthlyAttendance sma
            JOIN AcademicTerm t
              ON t.term_id = sma.term_id
            WHERE sma.urn = ?
              AND sma.term_id = ?
            ORDER BY
                sma.end_month DESC,
                sma.start_month DESC
        """, (urn, ma["term_id"])).fetchall()
    finally:
        con.close()

    month_names = [
        "", "January", "February", "March", "April",
        "May", "June", "July", "August", "September",
        "October", "November", "December"
    ]

    attendance_records = []

    if monthly_rows:
        latest = monthly_rows[0]

        start_month = latest["start_month"]
        end_month = latest["end_month"]

        period = month_names[start_month]
        if start_month != end_month:
            period += " – " + month_names[end_month]

        attendance_records.append({
            "URN": str(urn),
            "Semester": latest["semester"],
            "Latest Month / Period": period,
            "Monthly Attendance (%)": latest["monthly_percentage"],
            "Cumulative Attendance (%)": latest["cumulative_percentage"]
        })

    attendance_df_latest = pd.DataFrame(attendance_records)

    # -----------------------------
    # Semester Marks
    # -----------------------------
    mk = marks_with_status(
        urn=urn,
        term_id=ma["term_id"]
    )

    if not mk.empty:
        marks_columns = [
            col for col in
            ["urn", "name", "batch", "semester", "subject", "total", "status"]
            if col in mk.columns
        ]

        marks_export = mk[marks_columns].copy()

        marks_export = marks_export.rename(columns={
            "urn": "URN",
            "name": "Name",
            "batch": "Batch",
            "semester": "Semester",
            "subject": "Subject",
            "total": "Total",
            "status": "Status"
        })
    else:
        marks_export = pd.DataFrame(
            columns=[
                "URN", "Name", "Batch", "Semester",
                "Subject", "Total", "Status"
            ]
        )

    # -----------------------------
    # Export to Excel
    # -----------------------------
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        student.to_excel(
            writer,
            index=False,
            sheet_name="Student Profile"
        )

        attendance_df_latest.to_excel(
            writer,
            index=False,
            sheet_name="Attendance"
        )

        marks_export.to_excel(
            writer,
            index=False,
            sheet_name="Semester Marks"
        )

        # Make the sheets easier to read.
        for worksheet in writer.sheets.values():
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for column_cells in worksheet.columns:
                max_length = max(
                    len(str(cell.value or ""))
                    for cell in column_cells
                )
                column_letter = column_cells[0].column_letter
                worksheet.column_dimensions[column_letter].width = min(
                    max_length + 3, 35
                )

    out.seek(0)
    return out

def build_individual_pdf(urn, ma):
    df = dataframe_for_report(ma["term_id"], ma["batch"])
    student = df[df["urn"].astype(str) == str(urn)]
    con = db()
    try:
        monthly_rows = con.execute("""
            SELECT
                sma.start_month,
                sma.end_month,
                sma.monthly_percentage,
                sma.cumulative_percentage,
                t.semester
            FROM StudentMonthlyAttendance sma
            JOIN AcademicTerm t
              ON t.term_id = sma.term_id
            WHERE sma.urn = ?
              AND sma.term_id = ?
            ORDER BY
                sma.end_month DESC,
                sma.start_month DESC
        """, (urn, ma["term_id"])).fetchall()
    finally:
        con.close()

    # Keep only the latest submitted period for this semester.
    latest_attendance = monthly_rows[0] if monthly_rows else None

    month_names = [
        "", "January", "February", "March", "April",
        "May", "June", "July", "August", "September",
        "October", "November", "December"
    ]

    if latest_attendance:
        start_month = latest_attendance["start_month"]
        end_month = latest_attendance["end_month"]

        attendance_period = month_names[start_month]
        if start_month != end_month:
            attendance_period += " – " + month_names[end_month]

        attendance_percentage = latest_attendance["monthly_percentage"]
    mk = marks_with_status(urn=urn, term_id=ma["term_id"])

    out = io.BytesIO()

    doc = SimpleDocTemplate(
        out,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    if student.empty:
        raise ValueError("Student not found for individual report.")

    s = student.iloc[0]

    story = [
        Paragraph("Mentee Management Report", styles["Title"]),
        Spacer(1, 18),
    ]

    # -----------------------------
    # Student Profile
    # -----------------------------
    profile_data = [
        ["Field", "Value"],
        ["URN", s.get("urn", "")],
        ["Name", s.get("name", "")],
        ["Batch", s.get("batch", "") or ""],
        ["Class Name", s.get("class_name", "") or ""],
        ["Division", s.get("division", "") or ""],
        ["Roll No", s.get("roll_no", "") or ""],
        ["Address", s.get("address", "") or ""],
        ["Mobile", s.get("mobile", "") or ""],
        ["Parent Name", s.get("parent_name", "") or ""],
        ["Parent Mobile", s.get("parent_mobile", "") or ""],
        ["Hobbies", s.get("hobbies", "") or ""],
        ["SSC Medium", s.get("ssc_medium", "") or ""],
        ["HSC Medium", s.get("hsc_medium", "") or ""],
        ["SSC Marks", s.get("ssc_marks", "") or ""],
        ["HSC Marks", s.get("hsc_marks", "") or ""],
        ["Awards", s.get("awards", "") or ""],
        ["Certifications", s.get("certifications", "") or ""],
    ]

    t = Table(profile_data, colWidths=[130, 380], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    story.append(t)
    story.append(Spacer(1, 22))

    # -----------------------------
    # Latest Submitted Attendance
    # -----------------------------
    story.append(
        Paragraph("Latest Submitted Attendance", styles["Heading2"])
    )
    story.append(Spacer(1, 8))

    if not latest_attendance:
        story.append(
            Paragraph(
                "No monthly attendance submitted by the student.",
                styles["Normal"]
            )
        )
    else:
        attendance_data = [
            ["Semester", "Latest Month / Period", "Attendance"],
            [
                latest_attendance["semester"],
                attendance_period,
                f"{attendance_percentage:.2f}%"
            ]
        ]

        attendance_table = Table(
            attendance_data,
            colWidths=[100, 230, 120],
            repeatRows=1
        )

        attendance_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))

        story.append(attendance_table)

        # Graph for the latest submitted period
        fig, ax = plt.subplots(figsize=(7.5, 3.8))

        ax.bar(
            [attendance_period],
            [attendance_percentage]
        )

        ax.set_title("Latest Submitted Attendance")
        ax.set_ylabel("Attendance (%)")
        ax.set_ylim(0, 100)
        ax.grid(axis="y", alpha=0.3)

        fig.tight_layout()

        chart_buf = io.BytesIO()
        fig.savefig(
            chart_buf,
            format="png",
            dpi=140,
            bbox_inches="tight"
        )
        plt.close(fig)

        chart_buf.seek(0)

        story.append(Spacer(1, 12))
        story.append(
            Image(chart_buf, width=450, height=230)
        )

    story.append(Spacer(1, 22))

    # -----------------------------
    # Semester Marks
    # -----------------------------
    story.append(Paragraph("Semester Marks", styles["Heading2"]))
    story.append(Spacer(1, 8))

    if mk.empty:
        story.append(Paragraph(
            "No semester marks available.",
            styles["Normal"]
        ))
    else:
        marks_data = [["Semester", "Subject", "Total", "Status"]]

        for r in mk.itertuples():
            marks_data.append([
                r.semester,
                r.subject,
                r.total,
                r.status
            ])

        t = Table(
            marks_data,
            colWidths=[70, 190, 100, 100],
            repeatRows=1
        )
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        story.append(t)

    story.append(Spacer(1, 22))

    # -----------------------------
    # Mentor Suggestions
    # -----------------------------
    story.append(Paragraph("Mentor Suggestions", styles["Heading2"]))
    story.append(Spacer(1, 8))

    suggestions = get_suggestions(urn)

    if not suggestions:
        story.append(Paragraph(
            "No mentor suggestions available.",
            styles["Normal"]
        ))
    else:
        suggestion_data = [["Semester", "Date", "Suggestion"]]

        for sug in suggestions:
            suggestion_data.append([
                sug.get("semester", ""),
                sug.get("suggestion_date", ""),
                sug.get("suggestion", "")
            ])

        t = Table(
            suggestion_data,
            colWidths=[70, 100, 330],
            repeatRows=1
        )
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(t)

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
    """Batch Excel report with latest submitted attendance and simplified marks."""
    ma = mentor_scope()
    term_id = ma["term_id"]
    batch = ma["batch"]

    out = io.BytesIO()

    # -----------------------------
    # Student Profile
    # -----------------------------
    df = dataframe_for_report(term_id, batch)

    # -----------------------------
    # Latest Submitted Attendance
    # -----------------------------
    con = db()
    try:
        monthly_rows = con.execute("""
            SELECT
                sma.urn,
                s.name,
                sma.start_month,
                sma.end_month,
                sma.monthly_percentage,
                sma.cumulative_percentage,
                t.semester
            FROM StudentMonthlyAttendance sma
            JOIN Student s ON s.urn = sma.urn
            JOIN AcademicTerm t ON t.term_id = sma.term_id
            JOIN StudentAcademicAssignment sa
              ON sa.student_id = s.student_id
             AND sa.term_id = sma.term_id
            WHERE sma.term_id = ?
              AND sa.batch = ?
            ORDER BY
                sma.urn,
                sma.end_month DESC,
                sma.start_month DESC
        """, (term_id, batch)).fetchall()
    finally:
        con.close()

    month_names = [
        "", "January", "February", "March", "April",
        "May", "June", "July", "August", "September",
        "October", "November", "December"
    ]

    # Keep one latest submitted period per student.
    latest_by_urn = {}

    for row in monthly_rows:
        urn = str(row["urn"])

        if urn not in latest_by_urn:
            latest_by_urn[urn] = row

    attendance_records = []

    for urn, row in latest_by_urn.items():
        start_month = row["start_month"]
        end_month = row["end_month"]

        period = month_names[start_month]
        if start_month != end_month:
            period += " – " + month_names[end_month]

        attendance_records.append({
            "URN": urn,
            "Name": row["name"],
            "Semester": row["semester"],
            "Latest Month / Period": period,
            "Monthly Attendance (%)": row["monthly_percentage"],
            "Cumulative Attendance (%)": row["cumulative_percentage"]
        })

    attendance_export = pd.DataFrame(
        attendance_records,
        columns=[
            "URN",
            "Name",
            "Semester",
            "Latest Month / Period",
            "Monthly Attendance (%)",
            "Cumulative Attendance (%)"
        ]
    )

    # -----------------------------
    # Semester Marks
    # -----------------------------
    mk = marks_with_status(term_id=term_id, batch=batch)

    marks_columns = [
        "urn", "name", "batch", "semester",
        "subject", "total", "status"
    ]

    if mk.empty:
        marks_export = pd.DataFrame(columns=[
            "URN", "Name", "Batch", "Semester",
            "Subject", "Total", "Status"
        ])
    else:
        available_columns = [
            col for col in marks_columns if col in mk.columns
        ]

        marks_export = mk[available_columns].copy()

        marks_export = marks_export.rename(columns={
            "urn": "URN",
            "name": "Name",
            "batch": "Batch",
            "semester": "Semester",
            "subject": "Subject",
            "total": "Total",
            "status": "Status"
        })

    # -----------------------------
    # Write Excel Workbook
    # -----------------------------
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(
            writer,
            index=False,
            sheet_name="Students"
        )

        attendance_export.to_excel(
            writer,
            index=False,
            sheet_name="Attendance"
        )

        marks_export.to_excel(
            writer,
            index=False,
            sheet_name="Semester Marks"
        )

        # Format every sheet for readability.
        for worksheet in writer.sheets.values():
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for column_cells in worksheet.columns:
                max_length = max(
                    len(str(cell.value or ""))
                    for cell in column_cells
                )

                column_letter = column_cells[0].column_letter
                worksheet.column_dimensions[column_letter].width = min(
                    max_length + 3, 35
                )

    filename = (
        f"Batch_Report_"
        f"{re.sub(r'[^A-Za-z0-9_.-]', '_', str(batch))}.xlsx"
    )

    save_report_file(filename, out.getvalue())

    out.seek(0)

    return send_file(
        out,
        as_attachment=True,
        download_name=filename,
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )
    )



@app.route("/mentor/export/pdf")
@login_required("mentor")
def export_pdf():
    """Batch PDF report using each student's latest submitted attendance period."""
    ma = mentor_scope()
    term_id = ma["term_id"]
    batch = ma["batch"]

    df = dataframe_for_report(term_id, batch)
    mk = marks_with_status(term_id=term_id, batch=batch)

    con = db()
    try:
        monthly_rows = con.execute("""
            SELECT
                sma.urn,
                s.name,
                sma.start_month,
                sma.end_month,
                sma.monthly_percentage,
                sma.cumulative_percentage,
                t.semester
            FROM StudentMonthlyAttendance sma
            JOIN Student s ON s.urn = sma.urn
            JOIN AcademicTerm t ON t.term_id = sma.term_id
            JOIN StudentAcademicAssignment sa
              ON sa.student_id = s.student_id
             AND sa.term_id = sma.term_id
            WHERE sma.term_id = ?
              AND sa.batch = ?
            ORDER BY
                sma.urn,
                sma.end_month DESC,
                sma.start_month DESC
        """, (term_id, batch)).fetchall()
    finally:
        con.close()

    # Keep the latest submitted period for each student.
    latest_by_urn = {}
    for row in monthly_rows:
        urn = str(row["urn"])
        if urn not in latest_by_urn:
            latest_by_urn[urn] = dict(row)

    month_names = [
        "", "January", "February", "March", "April",
        "May", "June", "July", "August", "September",
        "October", "November", "December"
    ]

    attendance_records = []
    for urn, row in latest_by_urn.items():
        start = row["start_month"]
        end = row["end_month"]

        period = month_names[start]
        if start != end:
            period += " – " + month_names[end]

        attendance_records.append({
            "urn": urn,
            "name": row["name"],
            "semester": row["semester"],
            "period": period,
            "percentage": float(row["monthly_percentage"])
        })

    attendance_df_latest = pd.DataFrame(attendance_records)

    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out,
        pagesize=landscape(A4),
        rightMargin=24,
        leftMargin=24,
        topMargin=24,
        bottomMargin=24
    )

    styles = getSampleStyleSheet()
    story = [
        Paragraph("Mentee Management Report", styles["Title"]),
        Spacer(1, 12),
        Paragraph(f"Batch: {batch}", styles["Heading2"]),
        Paragraph(f"Total Mentees: {len(df)}", styles["Heading2"]),
        Spacer(1, 12),
    ]

    # Students
    student_data = [
        ["URN", "Name", "Batch", "Class", "Division", "Roll No", "Mobile"]
    ]

    for r in df.itertuples():
        student_data.append([
            r.urn,
            r.name,
            r.batch,
            getattr(r, "class_name", "") or "",
            getattr(r, "division", "") or "",
            getattr(r, "roll_no", "") or "",
            getattr(r, "mobile", "") or ""
        ])

    student_table = Table(student_data, repeatRows=1)
    student_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(student_table)

    # Latest submitted attendance per student
    story.append(PageBreak())
    story.append(Paragraph(
        "Batch Attendance - Latest Submitted Period",
        styles["Heading2"]
    ))
    story.append(Spacer(1, 8))

    if attendance_df_latest.empty:
        story.append(Paragraph(
            "No monthly attendance submitted by students.",
            styles["Normal"]
        ))
    else:
        attendance_data = [
            ["URN", "Student", "Semester", "Latest Month / Period", "Attendance %"]
        ]

        for r in attendance_df_latest.itertuples():
            attendance_data.append([
                r.urn,
                r.name,
                r.semester,
                r.period,
                f"{r.percentage:.2f}%"
            ])

        attendance_table = Table(
            attendance_data,
            colWidths=[85, 155, 65, 180, 100],
            repeatRows=1
        )
        attendance_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(attendance_table)

        # Graph using the latest submitted attendance for each student
        graph_df = attendance_df_latest.sort_values("name")
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.bar(graph_df["name"], graph_df["percentage"])
        ax.axhline(
            LOW_ATTENDANCE,
            linestyle="--",
            label=f"Low Attendance Threshold ({LOW_ATTENDANCE}%)"
        )
        ax.set_title(f"Latest Submitted Attendance - {batch}")
        ax.set_xlabel("Students")
        ax.set_ylabel("Attendance (%)")
        ax.set_ylim(0, 100)
        ax.tick_params(axis="x", rotation=45)
        ax.legend()
        fig.tight_layout()

        graph_buffer = io.BytesIO()
        fig.savefig(
            graph_buffer,
            format="png",
            dpi=140,
            bbox_inches="tight"
        )
        plt.close(fig)
        graph_buffer.seek(0)
        story.append(Spacer(1, 16))
        story.append(Image(graph_buffer, width=700, height=280))

    # Semester marks: only total and status
    story.append(PageBreak())
    story.append(Paragraph("Semester Marks", styles["Heading2"]))
    story.append(Spacer(1, 8))

    if mk.empty:
        story.append(Paragraph(
            "No semester marks available.",
            styles["Normal"]
        ))
    else:
        marks_data = [
            ["URN", "Name", "Batch", "Semester", "Subject", "Total", "Status"]
        ]

        for r in mk.itertuples():
            marks_data.append([
                r.urn,
                r.name,
                r.batch,
                r.semester,
                r.subject,
                r.total,
                r.status
            ])

        marks_table = Table(
            marks_data,
            repeatRows=1
        )
        marks_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(marks_table)

    # Students with backlogs
    story.append(PageBreak())
    story.append(Paragraph("Students with Backlogs", styles["Heading2"]))
    story.append(Spacer(1, 8))

    backlogs = backlog_summary(mk)

    if backlogs.empty:
        story.append(Paragraph(
            "No students have backlogs.",
            styles["Normal"]
        ))
    else:
        backlog_data = [["Name", "Backlogs", "Failed Subjects", "Status"]]

        for r in backlogs.itertuples():
            backlog_data.append([
                r.name,
                r.backlog_count,
                Paragraph(str(r.failed_subjects), styles["Normal"]),
                r.backlog_status
            ])

        backlog_table = Table(
            backlog_data,
            colWidths=[170, 80, 330, 140],
            repeatRows=1
        )
        backlog_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(backlog_table)

    doc.build(story)

    filename = (
        f"Batch_Report_"
        f"{re.sub(r'[^A-Za-z0-9_.-]', '_', str(batch))}.pdf"
    )
    save_report_file(filename, out.getvalue())
    out.seek(0)

    return send_file(
        out,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf"
    )



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
