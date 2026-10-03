import os
from app import app, init_db
from waitress import serve
init_db()
host=os.getenv("HOST","0.0.0.0"); port=int(os.getenv("PORT","5000"))
print(f"Starting Mentee Management System on http://{host}:{port}")
serve(app,host=host,port=port,threads=8)
