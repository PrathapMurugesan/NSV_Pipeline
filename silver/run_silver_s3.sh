#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Run the NSV silver layer on EC2 for ONE UPC stored in S3.
#
#   input : s3://dhms-new-stgae/nsv-extraction/bronze/nsv/zone=A/phase=2/cycle=1/N-02007-11001-HR/
#   output: s3://dhms-new-stgae/nsv-silver/zone=A/phase=2/cycle=1/N-02007-11001-HR/
#
# Usage (defaults = the UPC above):
#   ./run_silver_s3.sh
#   ./run_silver_s3.sh --zone A --phase 2 --cycle 1 --upc N-02007-11001-HR
#   ./run_silver_s3.sh --upc N-02007-11001-HR --tracker s3://dhms-new-stgae/<path>/NSV_Activity_Tracker.xlsx
#   default output: parquet + CSV (no Excel)
#   ./run_silver_s3.sh --excel          # also write .xlsx (slow, ~1.5 min per big table)
#   ./run_silver_s3.sh --no-csv         # parquet only
#
# main.py is NOT changed: this script copies the UPC to local disk, runs main.py on it,
# and uploads the output folder zone=/phase=/cycle=/<UPC>/ to nsv-silver/.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

# ── defaults ─────────────────────────────────────────────────────────────────
BUCKET="${BUCKET:-dhms-new-stgae}"                         # env overrides: set by ../run_e2e.sh from pipeline.env
BRONZE_PREFIX="${BRONZE_PREFIX:-nsv-extraction}/bronze/nsv"   # bronze writes <prefix>/bronze/nsv/zone=...
SILVER_PREFIX="${SILVER_PREFIX:-nsv-silver}"
REGION="${REGION:-ap-south-1}"
ZONE="A"
PHASE="2"
CYCLE="1"
UPC="N-02007-11001-HR"
TRACKER_S3=""            # optional: s3://.../NSV_Activity_Tracker_ZoneWise_Standardized.xlsx
EXCEL=0                  # 0 = --no-excel (parquet only)
CSV=1                    # 1 = also write .csv (default)
PULL_PREVIOUS=1          # 1 = download other UPCs' dlm_kpi parquet so display_defect_id counters continue
WORK_DIR="${WORK_DIR:-$HOME/nsv_work}"
CODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-$CODE_DIR/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$CODE_DIR/../.venv/bin/python"     # one venv at the repo root (nsv-e2e-pipeline/.venv)

# ── arguments ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --zone)    ZONE="$2"; shift 2 ;;
    --phase)   PHASE="$2"; shift 2 ;;
    --cycle)   CYCLE="$2"; shift 2 ;;
    --upc)     UPC="$2"; shift 2 ;;
    --bucket)  BUCKET="$2"; shift 2 ;;
    --tracker) TRACKER_S3="$2"; shift 2 ;;
    --excel)   EXCEL=1; shift ;;
    --csv)     CSV=1; shift ;;
    --no-csv)  CSV=0; shift ;;
    --no-previous) PULL_PREVIOUS=0; shift ;;
    -h|--help) sed -n 2,19p "$0"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done

[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
command -v aws >/dev/null || { echo "ERROR: aws CLI not installed"; exit 2; }

PART="zone=${ZONE}/phase=${PHASE}/cycle=${CYCLE}/${UPC}"
S3_IN="s3://${BUCKET}/${BRONZE_PREFIX}/${PART}/"
S3_OUT_ROOT="s3://${BUCKET}/${SILVER_PREFIX}"
S3_OUT="${S3_OUT_ROOT}/${PART}/"

LOCAL_IN="${WORK_DIR}/bronze/nsv/${PART}"      # keeps zone=/phase=/cycle= in the path → main.py reads them from it
LOCAL_OUT_ROOT="${WORK_DIR}/silver"
LOCAL_OUT="${LOCAL_OUT_ROOT}/${PART}"

echo "════════════════════════════════════════════════════════════════════"
echo " UPC     : ${UPC}  (zone=${ZONE} phase=${PHASE} cycle=${CYCLE})"
echo " input   : ${S3_IN}"
echo " output  : ${S3_OUT}"
echo " work dir: ${WORK_DIR}"
echo " python  : ${PYTHON}"
echo "════════════════════════════════════════════════════════════════════"

# ── 1. check the UPC exists in S3 ────────────────────────────────────────────
if ! aws s3 ls "${S3_IN}" --region "$REGION" >/dev/null 2>&1; then
  echo "ERROR: nothing found at ${S3_IN} (check zone / phase / cycle / UPC and the EC2 IAM role)"
  exit 3
fi

# ── 2. download the bronze UPC (fresh copy; images are not needed, only the image LIST parquet) ──
rm -rf "${LOCAL_IN}" "${LOCAL_OUT}"
mkdir -p "${LOCAL_IN}" "${LOCAL_OUT_ROOT}"
echo -e "\n[1/4] Downloading bronze …"
aws s3 sync "${S3_IN}" "${LOCAL_IN}" --region "$REGION" --only-show-errors \
    --exclude "*.jpg" --exclude "*.jpeg" --exclude "*.png" --exclude "*.JPG" --exclude "*.PNG" || {
  echo "ERROR: download failed"; exit 4; }
for d in d1 d2 d3 d4; do
  [[ -d "${LOCAL_IN}/${d}" ]] && echo "  ${d}: ok" || echo "  ${d}: MISSING"
done

# ── 3. optional tracker ──────────────────────────────────────────────────────
TRACKER_ARG=()
if [[ -n "$TRACKER_S3" ]]; then
  mkdir -p "${WORK_DIR}/tracker"
  TRACKER_LOCAL="${WORK_DIR}/tracker/$(basename "$TRACKER_S3")"
  aws s3 cp "$TRACKER_S3" "$TRACKER_LOCAL" --region "$REGION" --only-show-errors \
    && TRACKER_ARG=(--tracker "$TRACKER_LOCAL") \
    || echo "  WARNING: tracker not downloaded — CA standard / 100 m interval / survey date from data"
else
  TRACKER_ARG=(--tracker "${WORK_DIR}/tracker/none.xlsx")   # not there → main.py warns and continues
  echo "  no --tracker given — CA standard / 100 m interval / survey date taken from the data"
fi

# ── 4. other UPCs already in nsv-silver (only for the display_defect_id counter) ─
if [[ "$PULL_PREVIOUS" == 1 ]]; then
  echo -e "\n[2/4] Fetching existing silver dlm_kpi tables of other UPCs (display_defect_id counter) …"
  aws s3 sync "${S3_OUT_ROOT}/" "${LOCAL_OUT_ROOT}/" --region "$REGION" --only-show-errors \
      --exclude "*" --include "*/dlm_kpi/master_*.parquet" \
      --exclude "*/dlm_kpi/master_chainage.parquet" --exclude "*/dlm_kpi/master_reporting_interval.parquet" \
      --exclude "${PART}/*" || echo "  WARNING: could not read ${S3_OUT_ROOT} — counter starts at 1"
else
  echo -e "\n[2/4] Skipped fetching other UPCs (--no-previous)"
fi

# ── 5. run the silver code ───────────────────────────────────────────────────
echo -e "\n[3/4] Running silver …"
EXTRA=()
[[ "$EXCEL" == 0 ]] && EXTRA+=(--no-excel)
[[ "$CSV" == 1 ]] && EXTRA+=(--csv)
echo "  formats: parquet$([[ $EXCEL == 1 ]] && echo ' + excel')$([[ $CSV == 1 ]] && echo ' + csv')"
cd "$CODE_DIR"
"$PYTHON" main.py --project "${LOCAL_IN}" --out "${LOCAL_OUT_ROOT}" "${TRACKER_ARG[@]}" "${EXTRA[@]}"
RC=$?    # 1 = some table / DQ check failed — output is still uploaded so you can read run.log

# ── 6. upload: creates nsv-silver/zone=/phase=/cycle=/<UPC>/ ─────────────────
echo -e "\n[4/4] Uploading to ${S3_OUT} …"
if [[ ! -d "${LOCAL_OUT}" ]]; then
  echo "ERROR: no output folder ${LOCAL_OUT}"; exit 5
fi
aws s3 sync "${LOCAL_OUT}/" "${S3_OUT}" --region "$REGION" --only-show-errors || { echo "ERROR: upload failed"; exit 6; }
LATEST_BATCH="$(ls -t "${LOCAL_OUT_ROOT}/_batches/"batch=*.csv 2>/dev/null | head -1)"
[[ -n "$LATEST_BATCH" ]] && aws s3 cp "$LATEST_BATCH" "${S3_OUT_ROOT}/_batches/" --region "$REGION" --only-show-errors

echo -e "\nUploaded:"
aws s3 ls "${S3_OUT}" --recursive --region "$REGION" | awk '{print "  " $4}' | sed "s#${SILVER_PREFIX}/##"
echo -e "\nmain.py exit code: ${RC}  (0 = all ok, 1 = see run.log / batch csv)"
exit $RC
