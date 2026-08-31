#!/bin/bash
# Double-click or: ./run-scan.sh [--check] [--uarfcn 10813 ...]
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [[ -z "${UHD_IMAGES_DIR:-}" ]]; then
  for d in /usr/share/uhd/*/images /usr/share/uhd/images; do
    if [[ -f "${d}/usrp_b200_fw.hex" ]]; then
      export UHD_IMAGES_DIR="$d"
      break
    fi
  done
fi

exec python3 scan_3g.py "$@"
