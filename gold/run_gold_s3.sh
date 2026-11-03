#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Run the NSV gold (RIS KPI) pipelines on EC2 for ONE UPC stored in S3.
#
#   input : s3://<BUCKET>/<SILVER_PREFIX>/zone=A/phase=2/cycle=2/N-02001-19004-DL/dlm_kpi/*.parquet
#           + project master (.xlsx / .csv with a UPC column)  PROJECT_MASTER_S3 / --project-master
#   output: s3://<BUCKET>/<GOLD_PREFIX>/zone=A/phase=2/cycle=2/N-02001-19004-DL/
#               ris_trends_dw.csv  final_intermediate.csv  ris_view_dw.csv  gold_run.log
#
# Usage:
#   ./run_gold_s3.sh --zone A --phase 2 --cycle 2 --upc N-02001-19004-DL --project-master s3://.../project_master.xlsx
#
# The gold code is NOT changed: each pipeline reads its paths from .env, and real environment variables win
# over .env, so this script downloads the silver tables to a work folder and points INPUT_DIR / OUTPUT_DIR /
# INTERMEDIATE_PATH / PROJECT_MASTER_PATH at it, runs run_all.py, and uploads the output folder.
# Without a project master: ris_trends + intermediate only (ris_view needs it), no project-master filter.
# Exit code: 0 = ok, 1 = a pipeline failed, 3 = silver not found, 4 = silver tables missing, 5/6 = download/upload.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

BUCKET="${BUCKET:-dhms-new-stgae}"
SILVER_PREFIX="${SILVER_PREFIX:-nsv-silver}"
GOLD_PREFIX="${GOLD_PREFIX:-nsv-gold}"
REGION="${REGION:-ap-south-1}"
PROJECT_MASTER_S3="${PROJECT_MASTER_S3:-}"
ZONE="" PHASE="" CYCLE="" UPC=""
WORK_DIR="${WORK_DIR:-$HOME/nsv_work}"
CODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-$CODE_DIR/../.venv/bin/python}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --zone)           ZONE="$2"; shift 2 ;;
    --phase)          PHASE="$2"; shift 2 ;;
    --cycle)          CYCLE="$2"; shift 2 ;;
    --upc)            UPC="$2"; shift 2 ;;
    --project-master) PROJECT_MASTER_S3="$2"; shift 2 ;;
    -h|--help)        sed -n 2,19p "$0"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done

[[ -n "$ZONE" && -n "$PHASE" && -n "$CYCLE" && -n "$UPC" ]] || { echo "ERROR: --zone --phase --cycle --upc are required"; exit 2; }
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
command -v aws >/dev/null || { echo "ERROR: aws CLI not installed"; exit 2; }

UPC_DASH="$(echo "$UPC" | tr '/_ ' '---' | tr '[:lower:]' '[:upper:]')"
PART="zone=${ZONE}/phase=${PHASE}/cycle=${CYCLE}/${UPC_DASH}"
S3_IN="s3://${BUCKET}/${SILVER_PREFIX}/${PART}/dlm_kpi/"
S3_OUT="s3://${BUCKET}/${GOLD_PREFIX}/${PART}/"
WORK="${WORK_DIR}/gold/${PART}"
LOCAL_IN="${WORK}/input"
LOCAL_OUT="${WORK}/output"

echo "════════════════════════════════════════════════════════════════════"
echo " GOLD    : ${UPC_DASH}  (zone=${ZONE} phase=${PHASE} cycle=${CYCLE})"
echo " input   : ${S3_IN}"
echo " output  : ${S3_OUT}"
echo " project : ${PROJECT_MASTER_S3:-<none>}"
echo " python  : ${PYTHON}"
echo "════════════════════════════════════════════════════════════════════"

# ── 1. silver tables of this UPC (dlm_kpi = all rows; gold uses breach_flag itself) ──
if ! aws s3 ls "${S3_IN}" --region "$REGION" >/dev/null 2>&1; then
  echo "ERROR: nothing found at ${S3_IN} - run silver for this UPC first"; exit 3
fi
rm -rf "${WORK}"
mkdir -p "${LOCAL_IN}" "${LOCAL_OUT}"
echo -e "\n[1/4] Downloading silver tables …"
aws s3 sync "${S3_IN}" "${LOCAL_IN}" --region "$REGION" --only-show-errors --exclude "*" --include "*.parquet" \
  || { echo "ERROR: download failed"; exit 5; }

# silver writes nothing for an empty table, and gold stops on a missing file → list them all up front
REQUIRED=(master_chainage master_reporting_interval dlm_threshold_mapping
          master_roughness_parent master_roughness_child_100m master_roughness_child_10m
          master_rutting_parent master_rutting_child_100m master_rutting_child_10m
          master_cracking_parent master_cracking_child_100m master_cracking)
[[ -n "$PROJECT_MASTER_S3" ]] && REQUIRED+=(master_pothole master_ravelling_parent)    # ris_view only
MISSING=()
for t in "${REQUIRED[@]}"; do
  [[ -f "${LOCAL_IN}/${t}.parquet" ]] && echo "  ${t}: ok" || { echo "  ${t}: MISSING"; MISSING+=("$t"); }
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "ERROR: silver tables missing for this UPC: ${MISSING[*]} (empty in silver - see the silver run.log)"; exit 4
fi

# ── 2. project master ────────────────────────────────────────────────────────
ONLY=()
if [[ -n "$PROJECT_MASTER_S3" ]]; then
  PM_LOCAL="${WORK}/$(basename "$PROJECT_MASTER_S3")"
  if [[ "$PROJECT_MASTER_S3" == s3://* ]]; then
    aws s3 cp "$PROJECT_MASTER_S3" "$PM_LOCAL" --region "$REGION" --only-show-errors \
      || { echo "ERROR: project master not downloaded: $PROJECT_MASTER_S3"; exit 5; }
  else
    cp "$PROJECT_MASTER_S3" "$PM_LOCAL" || exit 5
  fi
  export PROJECT_MASTER_PATH="$PM_LOCAL" CM_PROJECT_MASTER_PATH="$PM_LOCAL" FILTER_BY_PROJECT_MASTER=true
else
  echo "  WARNING: no project master - ris_view skipped, UPCs not filtered against the project master"
  export FILTER_BY_PROJECT_MASTER=false
  ONLY=(--only ris_trends intermediate)
fi

# ── 3. run the gold pipelines (paths through the environment, .env stays as it is) ──
export INPUT_DIR="$LOCAL_IN" OUTPUT_DIR="$LOCAL_OUT" INTERMEDIATE_PATH="${LOCAL_OUT}/final_intermediate.csv"
export KEEP_UPCS=""
echo -e "\n[3/4] Running gold …"
cd "$CODE_DIR"
"$PYTHON" run_all.py "${ONLY[@]}" 2>&1 | tee "${LOCAL_OUT}/gold_run.log"
RC=${PIPESTATUS[0]}

# ── 4. upload (also after a failure, so gold_run.log is in S3) ──────────────
echo -e "\n[4/4] Uploading to ${S3_OUT} …"
aws s3 sync "${LOCAL_OUT}/" "${S3_OUT}" --region "$REGION" --only-show-errors || { echo "ERROR: upload failed"; exit 6; }
aws s3 ls "${S3_OUT}" --region "$REGION" | awk '{print "  " $4}'
echo -e "\nrun_all.py exit code: ${RC}  (0 = all ok)"
exit $RC
