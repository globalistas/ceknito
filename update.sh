#!/bin/bash
cd $(git rev-parse --show-toplevel)
git pull
poetry lock --no-update
poetry install
npm install
npm run build
poetry run ./throat.py migration apply
poetry run ./throat.py translations compile
killall -HUP gunicorn
