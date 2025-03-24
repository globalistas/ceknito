#!/bin/bash
cd $(git rev-parse --show-toplevel)
git pull
poetry install
npm install
npm run build
npm audit fix
poetry run ./throat.py migration apply
poetry run ./throat.py translations compile
killall -HUP gunicorn
