#!/bin/bash
# adsb.lol 일별 아카이브(GitHub Releases)에서 하루치 항적을 받아 필요한 항공기만 추출
# 사용: bash grab.sh 2026.11.05   → ./d1105/ 폴더에 trace_full_*.json 저장
D=$1; OUT="$(pwd)/d${D:5:2}${D:8:2}"; mkdir -p "$OUT"; cd "$OUT"
Y=${D:0:4}
B="https://github.com/adsblol/globe_history_${Y}/releases/download/v$D-planes-readsb-prod-0/v$D-planes-readsb-prod-0.tar"
probe(){ for i in 1 2 3 4 5 6; do c=$(curl -s -o /dev/null -w "%{http_code}" -r 0-0 -L -m 40 "$1"); [ "$c" = "206" ] && return 0; [ "$c" = "404" ] && return 1; sleep 8; done; return 1; }
for attempt in 1 2 3; do
  rm -rf traces; ok=1
  # 조각(.aa~)을 차례로 받아 저장 없이 바로 풀면서, 항공기 고유코드 앞자리로 거름
  # 71x=한국 등록기, 88x=태국, 75x=말레이시아·필리핀 등, 899=대만, 708=몽골 (필요 시 추가)
  ( for s in aa ab ac ad ae af ag; do probe "$B.$s" || break; curl -sS -L --retry 5 --retry-delay 5 "$B.$s" || exit 9; done ) \
   | tar -x --wildcards --no-anchored 'trace_full_71[89a-f]*' 'trace_full_88[0-9a-f]*' 'trace_full_75[0-9a-f]*' 'trace_full_899*' 'trace_full_708*' 2>tar.err || ok=0
  n=$(find . -name 'trace_full*' | wc -l); [ $n -gt 1400 ] && break
done
echo "$D done $n"
