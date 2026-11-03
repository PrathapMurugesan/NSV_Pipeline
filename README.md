# NSV end-to-end pipeline (bronze → silver → gold)

One UPC at a time, S3 in → S3 out, the three layers run one after the other on EC2:

| # | Layer | Code | Reads | Writes |
|---|---|---|---|---|
| 1 | Bronze | [bronze/](bronze/README.md) `run_bronze_s3.sh` | raw vendor download `RAW_S3_ROOT/Zone A/Phase 2/Cycle 2/<UPC>/Deliverable 1..4` | `s3://dhms-new-stgae/nsv_extraction_new/bronze/nsv/<part>/d1..d4/run=<time>/` |
| 2 | Silver | [silver/](silver/README.md) `run_silver_s3.sh` | bronze, newest `run=` per deliverable | `s3://dhms-new-stgae/nsv-silver/<part>/dlm_kpi/ dlm_postgres/ dq/ run.log` |
| 3 | Gold | [gold/](gold/README.md) `run_gold_s3.sh` | silver `dlm_kpi/*.parquet` + project master | `s3://dhms-new-stgae/nsv-gold/<part>/ris_trends_dw.csv final_intermediate.csv ris_view_dw.csv gold_run.log` |

`<part>` = `zone=A/phase=2/cycle=2/N-02001-19004-DL`. All S3 locations are set in [pipeline.env](pipeline.env).

The layer code is unchanged apart from env overrides in `silver/run_silver_s3.sh`. Each wrapper script stages S3 data:
- **Bronze** already reads S3 (`run_download.py --root s3://…`) and writes S3 (`NSV_OUTPUT=s3://…`).
- **Silver** syncs one UPC's bronze folder to local disk, runs `main.py`, and uploads the result.
- **Gold** reads a local folder (`INPUT_DIR` in each `code_*/.env`). Real environment variables take precedence over `.env`,
  so `run_gold_s3.sh` downloads the UPC's silver tables to `~/nsv_work/gold/<part>/input` and points
  `INPUT_DIR`, `OUTPUT_DIR`, `INTERMEDIATE_PATH` and `PROJECT_MASTER_PATH` there. The `.env` files stay as they are for local runs.

## 1. EC2 IAM role (once)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::dhms-new-stgae" },
    { "Effect": "Allow", "Action": ["s3:GetObject"],  "Resource": "arn:aws:s3:::dhms-new-stgae/*" },
    { "Effect": "Allow", "Action": ["s3:PutObject"],  "Resource": [
        "arn:aws:s3:::dhms-new-stgae/nsv_extraction_new/*",
        "arn:aws:s3:::dhms-new-stgae/nsv-silver/*",
        "arn:aws:s3:::dhms-new-stgae/nsv-gold/*" ] }
  ]
}
```
The raw download is in another bucket (`nhai-dhms-uat/nsv-dowloads-raw/…`), so the role also needs
`s3:ListBucket` + `s3:GetObject` there. The EC2 instance used for the earlier bronze runs already has both.

## 2. Setup (once)

Ubuntu:
```bash
sudo apt update && sudo apt install -y git python3-venv tesseract-ocr awscli    # 24.04: sudo snap install aws-cli --classic
git clone <repo-url> nsv-e2e-pipeline && cd nsv-e2e-pipeline
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
chmod +x run_e2e.sh bronze/run_bronze_s3.sh silver/run_silver_s3.sh gold/run_gold_s3.sh
```
Amazon Linux 2023: `sudo dnf install -y git python3.11 tesseract` and `python3.11 -m venv .venv`.

`pipeline.env` already holds the raw root (`RAW_S3_ROOT`, the `$SRC` of the earlier bronze runs) and the bronze
output `nsv_extraction_new`. Set `PROJECT_MASTER_S3` and, optionally, `TRACKER_S3`.
Bronze is read from **`nsv_extraction_new`**, not the old `nsv-extraction`. In the old prefix, the CA comparison
table of N/02001/19004/DL was rejected, so silver had no CA reporting intervals.

## 3. Run

```bash
./run_e2e.sh                                                       # UPC from pipeline.env
./run_e2e.sh --zone A --phase 2 --cycle 2 --upc N/02001/19004/DL
./run_e2e.sh --from silver        # bronze already in S3
./run_e2e.sh --only gold          # one layer
./run_e2e.sh --strict             # stop on bronze 'problems' / silver DQ FAIL too
nohup ./run_e2e.sh --zone A --phase 2 --cycle 2 --upc N/02001/19004/DL > e2e.out 2>&1 &   tail -f e2e.out
```

| Layer exit | Meaning | run_e2e.sh |
|---|---|---|
| bronze 0 / 2 / other | ok / some sheet rejected or missing / failed | continue / warn + continue / **stop** |
| silver 0 / 1 / 3–6 | ok / a table or DQ check FAILED (output uploaded) / UPC not in S3, download, upload | continue / warn + continue / **stop** |
| gold 0 / other | ok / failed (`gold_run.log` in S3) | done / **stop** |

`--strict` turns the two "warn + continue" cases into stops. Log: `logs/e2e_<time>_<UPC>.log`.

## Known limits

- **Gold needs a non-empty silver table for every input.** Silver writes no file for an empty table. Gold then
  stops (exit 4) and lists the missing tables. For example, a UPC with no NHAI One pothole sheet has no `master_pothole`.
- **No project master** means `ris_view` is skipped and gold runs only `ris_trends` + `intermediate`, unfiltered.
- **Gold is per UPC.** Its CSVs cover only that UPC. Combining all UPCs means concatenating each `nsv-gold/**` CSV.
