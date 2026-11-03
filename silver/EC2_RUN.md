# Running NSV Silver on EC2 (S3 in → S3 out)

```
in : s3://dhms-new-stgae/nsv_extraction_new/bronze/nsv/zone=A/phase=2/cycle=1/N-02007-11001-HR/
out: s3://dhms-new-stgae/nsv-silver/zone=A/phase=2/cycle=1/N-02007-11001-HR/
        dlm_kpi/*.parquet   dlm_postgres/*.parquet   dq/*.csv   run.log
     s3://dhms-new-stgae/nsv-silver/_batches/batch=<time>.csv
```

`run_silver_s3.sh` downloads the UPC, runs `main.py` unchanged, and uploads the result.
S3 has no real folders: `zone=A/phase=2/cycle=1/<UPC>/` appears as soon as the files are uploaded.

## 1. EC2 IAM role (once)

Attach a role to the instance with at least:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::dhms-new-stgae" },
    { "Effect": "Allow", "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::dhms-new-stgae/*" },
    { "Effect": "Allow", "Action": ["s3:PutObject"],
      "Resource": "arn:aws:s3:::dhms-new-stgae/nsv-silver/*" }
  ]
}
```

Check from the instance: `aws s3 ls s3://dhms-new-stgae/nsv_extraction_new/bronze/nsv/zone=A/phase=2/cycle=1/`

## 2. Setup (once) — Python 3.10 or newer

Ubuntu 22.04 / 24.04:
```bash
sudo apt update && sudo apt install -y python3-venv unzip awscli   # 24.04: sudo snap install aws-cli --classic
```
Amazon Linux 2023 (aws CLI already installed; default python3 is 3.9, so install 3.11):
```bash
sudo dnf install -y python3.11 unzip
```

Then:
```bash
unzip Silver_v8_ec2.zip && cd nsv_silver
python3 -m venv .venv            # Amazon Linux 2023: python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
chmod +x run_silver_s3.sh
```

## 3. Run

```bash
./run_silver_s3.sh                                   # N-02007-11001-HR, zone A, phase 2, cycle 1
./run_silver_s3.sh --zone A --phase 2 --cycle 1 --upc N-02007-11001-HR
./run_silver_s3.sh --tracker s3://dhms-new-stgae/<path>/NSV_Activity_Tracker_ZoneWise_Standardized.xlsx
./run_silver_s3.sh --excel                           # also .xlsx (slow); default is parquet + CSV
```

Long runs: `nohup ./run_silver_s3.sh > silver_run.out 2>&1 &` then `tail -f silver_run.out`.

| Option | Meaning |
|---|---|
| `--tracker s3://…xlsx` | NSV Activity Tracker. Without it: standard CA, 100 m interval, survey date from the data (same warning as on Windows) |
| `--no-previous` | Don't download other UPCs' silver tables. They are only used so `display_defect_id` continues the counter after UPCs already in nsv-silver |
| *(default)* | parquet + CSV, no Excel |
| `--excel` | Write .xlsx too (slow) |
| `--no-csv` | parquet only |
| `WORK_DIR=/data ./run_silver_s3.sh` | Local scratch folder (default `~/nsv_work`) |

Exit code 0 = all tables and checks passed; 1 = something FAILED (output is still uploaded — read `run.log`
and the batch csv); 3 = UPC not found in S3.
