web: python setup_db.py && gunicorn -w 1 --threads 4 -b 0.0.0.0:$PORT "backend.app:app"
