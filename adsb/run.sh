#!/bin/bash
# GitHub 작업용: 최근 N일(기본 14일) 항적 → app.json
set -e
N=${1:-14}; cd "$(dirname "$0")"; DIRS=()
for i in $(seq $N -1 2); do DIRS+=("d$(date -u -d "-$i day" +%m%d)"); done
for i in $(seq $N -1 2); do date -u -d "-$i day" +%Y.%m.%d; done | xargs -P 3 -I{} bash grab.sh {}
S=$(date -u -d "-$N day" +%Y.%m.%d); E=$(date -u -d "-2 day" +%m.%d)
python3 -I tod2.py "${DIRS[@]}" > tod2.log; python3 -I conv.py
python3 -I fl737.py "${DIRS[@]}" > fl737.json
python3 -I rwy.py "${DIRS[@]}"
python3 -I wind.py "${DIRS[@]}"
python3 -I route.py "${DIRS[@]}"
python3 -I pack.py "$S~$E"
python3 -I apops.py "${DIRS[@]}" || echo "apops 실패(건너뜀)"
rm -rf d????
