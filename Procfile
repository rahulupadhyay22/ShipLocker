web: python manage.py collectstatic --noinput && gunicorn indiabox.wsgi --workers 5 --threads 4 --worker-class gthread --timeout 120 --bind 0.0.0.0:$PORT --access-logfile - --error-logfile -
release: python manage.py migrate --no-input
