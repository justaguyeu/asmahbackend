#!/usr/bin/env bash
# Render build step: install packages, collect the admin's static files, update the database.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
