"""Entry point for web hosts (PythonAnywhere, gunicorn, etc.).

PythonAnywhere: point the web app's WSGI file at this module.
gunicorn:       gunicorn wsgi:application
"""
from app import app as application  # noqa: F401
