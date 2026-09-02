#!/bin/bash
# Install scanner deps on Debian/Ubuntu. Do not run as root.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [[ "${EUID}" -eq 0 ]]; then
  echo "FAIL  run as a normal user (script uses sudo when needed)" >&2
  exit 1
fi

if ! command -v apt-get >/dev/null; then
  echo "FAIL  Debian/Ubuntu apt required" >&2
  echo "      install: python3.10+, numpy, scipy, python3-uhd, uhd-host, pip asn1tools" >&2
  exit 1
fi

pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "FAIL  python 3.10+ required (have ${pyver})" >&2
  exit 1
fi

echo "# apt  python3 numpy scipy uhd"
sudo apt-get update
sudo apt-get install -y \
  python3 \
  python3-pip \
  python3-numpy \
  python3-scipy \
  python3-uhd \
  uhd-host

echo "# pip  asn1tools"
pip_ok=0
if python3 -m pip install --user asn1tools; then
  pip_ok=1
elif python3 -m pip install --user --break-system-packages asn1tools; then
  pip_ok=1
fi
if [[ "${pip_ok}" -ne 1 ]]; then
  echo "FAIL  pip install asn1tools" >&2
  exit 1
fi

chmod +x run-scan.sh scan_3g.py uhd_capture.py wcdma_cellsearch.py wcdma_bch_decode.py wcdma_scan_report.py

if getent group usrp >/dev/null; then
  if ! id -nG "${USER}" | grep -qw usrp; then
    sudo usermod -aG usrp "${USER}"
    echo "# added ${USER} to group usrp — log out and back in before using the radio"
  fi
fi

echo "# check imports"
python3 - <<'PY'
import sys
missing = []
for name in ("numpy", "scipy", "asn1tools", "uhd"):
    try:
        __import__(name)
    except ImportError:
        missing.append(name)
if missing:
    print("FAIL  missing python modules:", ", ".join(missing), file=sys.stderr)
    sys.exit(1)
print("python", "%d.%d" % sys.version_info[:2], "numpy scipy asn1tools uhd ok")
PY

echo "# setup ok"
echo "  plug a B210 on USB3, then:  ./run-scan.sh --check"
echo "  scan:                       ./run-scan.sh --band 8 --uarfcn 2997"
