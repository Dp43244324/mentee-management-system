# Deploy Mentee Management System on Render + external PostgreSQL

This package is prepared for a Render Web Service using an external PostgreSQL database instead of Render's temporary/free PostgreSQL database.

## 1. GitHub
1. Create/use the GitHub repository `mentee-management-system`.
2. Upload the CONTENTS of this project folder, not the ZIP itself.
3. Keep `app.py`, `run_server.py`, `requirements.txt`, `render.yaml`, `templates/`, and `static/` at the repository root.

Do not upload `.env`, local `*.db` files, `venv/`, or real student data.

## 2. Create the PostgreSQL database first

Recommended for this package: **Neon PostgreSQL**. Its current Free plan is a normal Neon project with usage limits; it is not the 30-day temporary Render Postgres database. Neon currently lists 1 GB storage per Free project and 100 compute-hours per project per month. Free-tier limits and terms can change, so check the provider before relying on it for long-term production use.

Create a Neon project, then copy its PostgreSQL connection string. Keep the connection string private.

## 3. Deploy the Render Web Service

In Render:
- New -> Web Service
- Connect the `mentee-management-system` GitHub repository
- Runtime: Python
- Build Command: `pip install -r requirements.txt`
- Start Command: `python run_server.py`
- Plan: Free (if available to your account)

The included `render.yaml` does **not** create a Render PostgreSQL database. It creates the web service and asks Render for the `DATABASE_URL` value separately.

## 4. Add the permanent/external PostgreSQL connection

After the Render service is created:
1. Open the service in Render.
2. Open **Environment**.
3. Add/edit `DATABASE_URL`.
4. Paste the PostgreSQL connection string from Neon.
5. Save and deploy.

Render supports external databases by supplying the external connection URL as an environment variable named `DATABASE_URL`.

## 5. Important data behaviour

When `DATABASE_URL` is present, this application uses PostgreSQL. When it is absent locally, it falls back to SQLite for local development.

For your deployed website, keep `DATABASE_URL` set to the external PostgreSQL database. This keeps student accounts, attendance, marks, suggestions, and other database records outside the Render web-service filesystem.

## 6. Do not put database credentials in GitHub

Never replace `DATABASE_URL` in `render.yaml` with the actual Neon password/connection string. Keep the real value only in Render's Environment settings.
