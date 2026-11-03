#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# NSV end to end for ONE UPC, one layer after the other, S3 in → S3 out:
#
#   1. bronze  bronze/run_bronze_s3.sh   raw download (S3)  → s3://$BUCKET/$BRONZE_PREFIX/bronze/nsv/<part>/
#   2. silver  silver/run_silver_s3.sh   bronze (S3)        → s3://$BUCKET/$SILVER_PREFIX/<part>/
#   3. gold    gold/run_gold_s3.sh       silver (S3)        → s3://$BUCKET/$GOLD_PREFIX/<part>/
#              <part> = zone=A/phase=2/cycle=2/N-02001-19004-DL
#
# Usage (settings from pipeline.env; options override them):
#   ./run_e2e.sh                                                  the UPC in pipeline.env
#   ./run_e2e.sh --zone A --phase 2 --cycle 2 --upc N/02001/19004/DL
#   ./run_e2e.sh --from silver                                    skip bronze (already in S3)
#   ./run_e2e.sh --only gold                                      one layer
#   ./run_e2e.sh --strict                                         also stop on bronze 'problems' / silver DQ FAIL
#
# A layer that fails stops the run. Without --strict, bronze exit 2 (some sheet rejected) and silver exit 1
# (a table / DQ check FAILED, output still uploaded) only warn, and the next layer runs.
# Log: logs/e2e_<time>_<UPC>.log
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/pipeline.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
fi

FROM=bronze TO=gold STRICT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --zone)     ZONE="$2"; shift 2 ;;
    --phase)    PHASE="$2"; shift 2 ;;
    --cycle)    CYCLE="$2"; shift 2 ;;
    --upc)      UPC="$2"; shift 2 ;;
    --raw-root) RAW_S3_ROOT="$2"; shift 2 ;;
    --tracker)  TRACKER_S3="$2"; shift 2 ;;
    --project-master) PROJECT_MASTER_S3="$2"; shift 2 ;;
    --from)     FROM="$2"; shift 2 ;;
    --to)       TO="$2"; shift 2 ;;
    --only)     FROM="$2"; TO="$2"; shift 2 ;;
    --strict)   STRICT=1; shift ;;
    -h|--help)  sed -n 2,20p "$0"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done

LAYERS=(bronze silver gold)
index() { for i in "${!LAYERS[@]}"; do [[ "${LAYERS[$i]}" == "$1" ]] && { echo "$i"; return; }; done; echo -1; }
FROM_I=$(index "$FROM"); TO_I=$(index "$TO")
(( FROM_I >= 0 && TO_I >= FROM_I )) || { echo "ERROR: --from/--to/--only must be bronze, silver or gold (in that order)"; exit 2; }
[[ -n "${ZONE:-}" && -n "${PHASE:-}" && -n "${CYCLE:-}" && -n "${UPC:-}" ]] || { echo "ERROR: zone / phase / cycle / upc not set"; exit 2; }

export BUCKET REGION BRONZE_PREFIX SILVER_PREFIX GOLD_PREFIX RAW_S3_ROOT PROJECT_MASTER_S3 WORK_DIR
export PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
export AWS_DEFAULT_REGION="$REGION"
UPC_DASH="$(echo "$UPC" | tr '/_ ' '---' | tr '[:lower:]' '[:upper:]')"

mkdir -p "$ROOT/logs"
LOG="$ROOT/logs/e2e_$(date +%Y%m%d_%H%M%S)_${UPC_DASH}.log"
exec > >(tee -a "$LOG") 2>&1

echo "################################################################################"
echo " NSV end to end   ${UPC}  zone=${ZONE} phase=${PHASE} cycle=${CYCLE}"
echo " layers           ${LAYERS[*]:$FROM_I:$((TO_I - FROM_I + 1))}$([[ $STRICT == 1 ]] && echo '  (strict)')"
echo " started          $(date '+%Y-%m-%d %H:%M:%S')"
echo "################################################################################"

declare -A STATUS SECONDS_TAKEN
run_layer() {   # run_layer <name> <command...>  → sets STATUS[name], returns the exit code
  local name="$1"; shift
  local t0=$SECONDS
  echo -e "\n\n==================== ${name^^} ====================\n"
  "$@"
  local rc=$?
  SECONDS_TAKEN[$name]=$((SECONDS - t0))
  STATUS[$name]="exit ${rc}"
  return $rc
}

summary() {
  echo -e "\n################################ summary ################################"
  for l in "${LAYERS[@]}"; do
    printf "  %-7s %-40s %6ss\n" "$l" "${STATUS[$l]:-not run}" "${SECONDS_TAKEN[$l]:-0}"
  done
  echo "  bronze: s3://${BUCKET}/${BRONZE_PREFIX}/bronze/nsv/zone=${ZONE}/phase=${PHASE}/cycle=${CYCLE}/${UPC_DASH}/"
  echo "  silver: s3://${BUCKET}/${SILVER_PREFIX}/zone=${ZONE}/phase=${PHASE}/cycle=${CYCLE}/${UPC_DASH}/"
  echo "  gold  : s3://${BUCKET}/${GOLD_PREFIX}/zone=${ZONE}/phase=${PHASE}/cycle=${CYCLE}/${UPC_DASH}/"
  echo "  log   : ${LOG}"
  if [[ -n "${PIPELINE_PREFIX:-}" ]]; then         # copy this run's log next to the layers
    aws s3 cp "$LOG" "s3://${BUCKET}/${PIPELINE_PREFIX}/logs/$(basename "$LOG")" --region "$REGION" --only-show-errors \
      && echo "          s3://${BUCKET}/${PIPELINE_PREFIX}/logs/$(basename "$LOG")"
  fi
}

stop() { STATUS[$1]="${STATUS[$1]} - $2"; summary; exit "$3"; }

# ── 1. bronze ────────────────────────────────────────────────────────────────
if (( FROM_I <= 0 && TO_I >= 0 )); then
  run_layer bronze bash "$ROOT/bronze/run_bronze_s3.sh" --zone "$ZONE" --phase "$PHASE" --cycle "$CYCLE" --upc "$UPC"
  rc=$?
  if (( rc == 2 )); then
    STATUS[bronze]="exit 2 - extracted with problems"
    (( STRICT )) && stop bronze "stopped (--strict)" 2
    echo "WARNING: bronze finished with problems (rejected / missing sheets) - continuing with silver"
  elif (( rc != 0 )); then
    stop bronze "FAILED" 10
  else
    STATUS[bronze]="ok"
  fi
fi

# ── 2. silver ────────────────────────────────────────────────────────────────
if (( FROM_I <= 1 && TO_I >= 1 )); then
  SILVER_ARGS=(--zone "$ZONE" --phase "$PHASE" --cycle "$CYCLE" --upc "$UPC_DASH" --bucket "$BUCKET")
  [[ -n "${TRACKER_S3:-}" ]] && SILVER_ARGS+=(--tracker "$TRACKER_S3")
  run_layer silver bash "$ROOT/silver/run_silver_s3.sh" "${SILVER_ARGS[@]}"
  rc=$?
  if (( rc == 1 )); then
    STATUS[silver]="exit 1 - table / DQ check FAILED (uploaded)"
    (( STRICT )) && stop silver "stopped (--strict)" 1
    echo "WARNING: silver reported FAILED tables / DQ checks (see run.log in S3) - continuing with gold"
  elif (( rc != 0 )); then
    stop silver "FAILED" 20
  else
    STATUS[silver]="ok"
  fi
fi

# ── 3. gold ──────────────────────────────────────────────────────────────────
if (( FROM_I <= 2 && TO_I >= 2 )); then
  run_layer gold bash "$ROOT/gold/run_gold_s3.sh" --zone "$ZONE" --phase "$PHASE" --cycle "$CYCLE" --upc "$UPC_DASH"
  rc=$?
  (( rc != 0 )) && stop gold "FAILED" 30
  STATUS[gold]="ok"
fi

summary
exit 0
