"""
WSGI entry point for Gunicorn / uWSGI.
Run with:  gunicorn wsgi:application -b 0.0.0.0:5000 -w 4
"""
from app import app as application

if __name__ == '__main__':
    application.run()
