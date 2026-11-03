#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Run the NSV bronze extraction on EC2 for ONE UPC: raw vendor download in S3 → bronze in S3.
#
#   input : <RAW_S3_ROOT>/Zone A/Phase 2/Cycle 2/N_02001_19004_DL/<road>/Deliverable 1..4 .../
#   output: s3://<BUCKET>/<BRONZE_PREFIX>/bronze/nsv/zone=A/phase=2/cycle=2/N-02001-19004-DL/d1..d4/run=<time>/
#           (exactly where silver/run_silver_s3.sh reads from)
#
# Usage:
#   ./run_bronze_s3.sh --zone A --phase 2 --cycle 2 --upc N/02001/19004/DL --raw-root s3://bucket/raw/prefix/
#   ./run_bronze_s3.sh ... d1 d2 d4        only these deliverables (silver does not use d3)
#
# run_download.py is NOT changed: it reads the raw files straight from S3 (boto3) and writes bronze to
# NSV_OUTPUT. Credentials come from the EC2 IAM role.
# Exit code: 0 = all extracted, 2 = extracted with problems (see the batch report), other = failed.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

BUCKET="${BUCKET:-dhms-new-stgae}"
BRONZE_PREFIX="${BRONZE_PREFIX:-nsv-extraction}"
REGION="${REGION:-ap-south-1}"
RAW_S3_ROOT="${RAW_S3_ROOT:-}"
ZONE="" PHASE="" CYCLE="" UPC=""
EXTRA=()
CODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-$CODE_DIR/../.venv/bin/python}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --zone)     ZONE="$2"; shift 2 ;;
    --phase)    PHASE="$2"; shift 2 ;;
    --cycle)    CYCLE="$2"; shift 2 ;;
    --upc)      UPC="$2"; shift 2 ;;
    --raw-root) RAW_S3_ROOT="$2"; shift 2 ;;
    -h|--help)  sed -n 2,17p "$0"; exit 0 ;;
    *)          EXTRA+=("$1"); shift ;;          # d1 d2 ... or any run_download.py / main.py option
  esac
done

[[ -n "$ZONE" && -n "$PHASE" && -n "$CYCLE" && -n "$UPC" ]] || { echo "ERROR: --zone --phase --cycle --upc are required"; exit 2; }
[[ "$RAW_S3_ROOT" == s3://* && "$RAW_S3_ROOT" != *CHANGE-ME* ]] || { echo "ERROR: RAW_S3_ROOT / --raw-root must be the s3:// root of the raw download (the folder that holds 'Zone A', 'Zone B', ...)"; exit 2; }
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"

UPC_DASH="$(echo "$UPC" | tr '/_ ' '---' | tr '[:lower:]' '[:upper:]')"
export NSV_OUTPUT="s3://${BUCKET}/${BRONZE_PREFIX}"
export AWS_DEFAULT_REGION="$REGION"
S3_OUT="${NSV_OUTPUT}/bronze/nsv/zone=${ZONE}/phase=${PHASE}/cycle=${CYCLE}/${UPC_DASH}/"

echo "════════════════════════════════════════════════════════════════════"
echo " BRONZE  : ${UPC}  (zone=${ZONE} phase=${PHASE} cycle=${CYCLE})"
echo " raw     : ${RAW_S3_ROOT}"
echo " output  : ${S3_OUT}"
echo " python  : ${PYTHON}"
echo "════════════════════════════════════════════════════════════════════"

cd "$CODE_DIR"
# no --use-found: a UPC found under another zone/phase/cycle must fail here, otherwise silver looks in the wrong place
"$PYTHON" run_download.py --root "$RAW_S3_ROOT" "${EXTRA[@]}" --zone "$ZONE" --phase "$PHASE" --cycle "$CYCLE" --upc "$UPC"
RC=$?

echo -e "\nBronze folders now in S3:"
for d in d1 d2 d3 d4; do
  if aws s3 ls "${S3_OUT}${d}/" --region "$REGION" >/dev/null 2>&1; then echo "  ${d}: ok"; else echo "  ${d}: MISSING"; fi
done
echo "run_download.py exit code: ${RC}  (0 = all ok, 2 = see data batch report / logs, other = failed)"
exit $RC
