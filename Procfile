web: python manage.py collectstatic --noinput && gunicorn indiabox.wsgi --workers 3 --timeout 120 --bind 0.0.0.0:$PORT --access-logfile - --error-logfile -
