#!/usr/bin/env bash
URL="http://127.0.0.1:8000/proxy"
n=100

exp=0
cheap=0

for i in $(seq 1 $n); do
  c=$(curl -s "$URL" | sed -n 's/.*"chosen":"\([^"]*\)".*/\1/p')
  if [ "$c" = "expensive" ]; then exp=$((exp+1)); else cheap=$((cheap+1)); fi
done

echo "expensive=$exp cheap=$cheap"

