# Mentee Management System – Render Web Service + External PostgreSQL

This version keeps the earlier system behaviour while using the Flask web/cloud-ready interface.

## Student registration
- One permanent student account per URN.
- The registration semester is chosen by the student from Semester 1 through Semester 8.
- Students can later receive new academic assignments (new semester/division/batch) without creating another account.

## Mentor behaviour
- A normal mentor is assigned to one academic semester + batch and sees only that batch.
- Mentor registration generates a unique mentor code.
- The same semester + batch cannot have two mentor assignments.

## Run locally
```text
pip install -r requirements.txt
python run_server.py
```
Then open `http://127.0.0.1:5000`.

## Database
The project supports SQLite locally and PostgreSQL when `DATABASE_URL` is supplied.
For the cloud deployment, use an external PostgreSQL provider such as Neon rather than Render's temporary/free PostgreSQL database.

## Mentor Reports & Analytics
The mentor reports page is restricted to the mentor's assigned semester + batch and provides:
- Individual report search by URN, name, or roll number.
- Student selection with Individual PDF and Individual Excel exports.
- Batch attendance graph.
- Add/View Mentor Suggestions.
- Batch PDF and Batch Excel exports.
- A web-based Reports Folder for generated report files.

There is no Admin dashboard or Admin navigation in this version.

## Updated Mentor Dashboard
The mentor web dashboard follows the desktop workflow shown in the reference screenshots, without adding an admin dashboard:
- Mentee Profile tab with Find Particular Student, Show All, Delete, Refresh All and Clear.
- Full student profile fields and student table.
- Attendance tab with Find Particular Student, Show All and <=75% attendance filtering.
- Semester Marks tab with Find Particular Student, Show All and >3 Backlogs filtering.
- Reports & Analytics tab with Individual Report and Batch Report/Analytics actions.
- Individual PDF/Excel, Batch PDF/Excel, attendance graph, mentor suggestions and generated report files remain available.
- Student dashboard continues to use Mentee Profile, Attendance and Semester Marks tabs.

## GitHub / Render Web Service
Upload the project contents to GitHub and connect the repository to Render.
Create the external PostgreSQL database separately, then add its connection string as `DATABASE_URL` in Render Environment settings.
Do not commit `.env`, local database files, virtual environments, or real student data.
