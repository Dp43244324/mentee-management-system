# Neon PostgreSQL setup for this project

1. Create a Neon account and a new PostgreSQL project.
2. Open the project's connection details.
3. Copy the PostgreSQL connection string.
4. In Render, open your Web Service -> Environment.
5. Add `DATABASE_URL` and paste the connection string.
6. Save and deploy.

Use the connection string supplied by Neon, including its SSL settings if Neon provides them. Do not commit the real connection string to GitHub.

The application already converts a standard `postgresql://...` URL to the SQLAlchemy PostgreSQL driver it uses.

## Free-plan note
Neon's current Free plan is usage-limited rather than a 30-day Render Postgres instance. As of October 2026, Neon lists 1 GB storage and 100 compute-hours per project per month on Free. Provider limits and pricing can change, so review Neon's current plan before production use.
