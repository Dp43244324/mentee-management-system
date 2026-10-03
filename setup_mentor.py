"""Terminal helper for creating a mentor registration assignment for a specific academic year + semester + batch.
The mentor then uses the generated registration code at /mentor/register.
"""
from app import init_db, db, get_terms, make_code

init_db()
print("Available academic semesters:")
for t in get_terms():
    print(f"{t['term_id']}: {t['academic_year']} - Semester {t['semester']}" )
term_id=int(input("Term ID: "))
batch=input("Batch: ").strip()
department=input("Department: ").strip()
if not batch: raise SystemExit("Batch is required.")
con=db()
try:
    code=make_code("REG")
    con.execute("INSERT INTO MentorAssignment(term_id,department,batch,registration_code) VALUES(?,?,?,?)",(term_id,department,batch,code))
    con.commit(); print("Mentor registration assignment created."); print("Registration code:",code)
    print("Give this code only to the intended mentor. After registration, the system generates the mentor login code.")
except Exception as e:
    print("Could not create assignment:",e)
finally:
    con.close()
