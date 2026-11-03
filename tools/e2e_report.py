"""
One text report per end-to-end run: bronze + silver + gold of one UPC, read back from S3.

    python tools/e2e_report.py --upc N/02001/19004/DL --zone A --phase 2 --cycle 2 --out logs/FINAL_x.txt
        [--bucket dhms-new-stgae --bronze-prefix NSV_Pipeline --silver-prefix NSV_Pipeline/silver
         --gold-prefix NSV_Pipeline/gold --status "bronze=ok;silver=exit 1;gold=ok"]

Reads what the layers wrote, so it also works after --from silver / --only gold. Every section is independent:
a missing file is reported in the file, never stops the report. run_e2e.sh calls it at the end of every run.
"""
import argparse
import io
import os
import re
import traceback
from datetime import datetime

import boto3
import pandas as pd

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 60)
pd.set_option("display.max_colwidth", 70)
pd.set_option("display.max_rows", 500)

LINE = "=" * 100


class S3:
    def __init__(self, bucket):
        self.bucket, self.s3 = bucket, boto3.client("s3")

    def keys(self, prefix):
        out = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            out += [(o["Key"], o["LastModified"], o["Size"]) for o in page.get("Contents", [])]
        return out

    def read(self, key):
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def csv(self, key):
        return pd.read_csv(io.BytesIO(self.read(key)), low_memory=False)

    def parquet(self, key):
        return pd.read_parquet(io.BytesIO(self.read(key)))


def section(out, title, fn):
    out.append(f"\n{LINE}\n{title}\n{LINE}")
    try:
        fn(out)
    except Exception as e:                                   # report the problem, keep going
        out.append(f"  (section not available: {type(e).__name__}: {e})")
        out.append("  " + traceback.format_exc().strip().splitlines()[-1])


# ── bronze ────────────────────────────────────────────────────────────────────
def bronze(s3, a, out):
    base = f"{a.bronze_prefix}/bronze/nsv/{a.part}/"
    keys = s3.keys(base)
    runs = {}
    for k, _, _ in keys:
        m = re.search(r"/(d\d)/run=(\d{8}_\d{6})/", k)
        if m:
            runs.setdefault(m.group(1), set()).add(m.group(2))
    out.append(f"  location: s3://{a.bucket}/{base}")
    for d in ["d1", "d2", "d3", "d4"]:
        latest = max(runs[d]) if d in runs else None
        n = sum(1 for k, _, _ in keys if latest and f"/{d}/run={latest}/" in k)
        out.append(f"  {d}: {'latest run=' + latest + f' ({n} files)' if latest else 'NOT IN S3'}")

    reports = sorted((k for k, _, _ in s3.keys(f"{a.bronze_prefix}/bronze/nsv/_batches/") if k.endswith("_report.csv")),
                     reverse=True)
    for k in reports:                                         # newest batch report that has this UPC
        r = s3.csv(k)
        upc_col = next((c for c in r.columns if c.lower() == "upc"), None)
        if upc_col is None:
            continue
        r = r[r[upc_col].astype(str).str.replace("/", "-").str.upper() == a.upc_dash]
        if r.empty:
            continue
        out.append(f"\n  batch report: s3://{a.bucket}/{k}  ({len(r)} sheet rows)")
        status_col = next((c for c in r.columns if c.lower() == "status"), None)
        d_col = next((c for c in r.columns if c.lower() == "deliverable"), None)
        if status_col and d_col:
            out.append(r.groupby([d_col, status_col]).size().rename("sheets").reset_index().to_string(index=False))
            bad = r[r[status_col].astype(str).str.upper().eq(r[status_col].astype(str))
                    & ~r[status_col].astype(str).str.lower().isin(["saved", "not_delivered"])]
            if len(bad):
                show = [c for c in r.columns if c.lower() in ("deliverable", "workbook", "sheet", "status", "reason")]
                out.append("\n  problems (upper-case status):")
                out.append(bad[show].to_string(index=False))
        break
    else:
        out.append("  no bronze batch report with this UPC")

    try:
        iv = s3.csv(f"{a.bronze_prefix}/master/reporting_intervals.csv")
        upc_col = next(c for c in iv.columns if "upc" in c.lower())
        row = iv[iv[upc_col].astype(str).str.replace("/", "-").str.upper() == a.upc_dash]
        out.append("\n  master reporting intervals (bronze):")
        out.append(row.T.to_string() if len(row) else "  UPC not in the master table")
    except Exception as e:
        out.append(f"\n  master reporting intervals: not available ({e})")


# ── silver ────────────────────────────────────────────────────────────────────
KEEP_LOG = re.compile(r"(thresholds|Survey date|WARNING|FAILED|✖|Traceback|Error|^\s*▶|^\s+master_\w+: |^\s+dlm_\w+: "
                      r"|^\s+(CA|IRC): overall|^\s+\w+: CA .* / IRC|checks PASS|Rule checks|Done |source:)")


def silver(s3, a, out):
    base = f"{a.silver_prefix}/{a.part}/"
    keys = {k: (t, sz) for k, t, sz in s3.keys(base)}
    out.append(f"  location: s3://{a.bucket}/{base}  ({len(keys)} files)")
    if not keys:
        out.append("  NOTHING IN S3 for this UPC")
        return
    log = s3.read(base + "run.log").decode("utf-8", "replace") if base + "run.log" in keys else ""
    out.append("\n  run.log (key lines):")
    out += ["    " + l.rstrip() for l in log.splitlines() if KEEP_LOG.search(l)]

    out.append("\n  tables (dlm_kpi parquet):")
    for k in sorted(k for k in keys if "/dlm_kpi/" in k and k.endswith(".parquet")):
        df = s3.parquet(k)
        extra = ""
        if "threshold_name" in df and "breach_flag" in df:
            b = df.groupby("threshold_name")["breach_flag"].sum()
            extra = " | breach " + ", ".join(f"{i.replace('As per ', '')} {int(v)}" for i, v in b.items())
        out.append(f"    {os.path.basename(k)[:-8]:32} {len(df):>8,} rows{extra}")
        if k.endswith("master_reporting_interval.parquet") or k.endswith("dlm_threshold_mapping.parquet"):
            cols = [c for c in df.columns if any(w in c for w in ("standard", "interval", "defect", "threshold",
                                                                  "source_sheet"))][:14]
            out.append("      " + df[cols].to_string(index=False).replace("\n", "\n      "))

    if base + "dq/dq_summary.csv" in keys:
        dq = s3.csv(base + "dq/dq_summary.csv")
        judged = dq[dq["status"].isin(["PASS", "FAIL"])]
        out.append(f"\n  DQ vs D2: {int((judged['status'] == 'PASS').sum())}/{len(judged)} PASS")
        cols = [c for c in ["check", "std", "defect", "lane", "level", "d2_rows", "matched", "match_%", "status", "note"]
                if c in dq.columns]
        out.append(dq[cols].to_string(index=False))
    if base + "dq/dq_mismatches.csv" in keys:
        mm = s3.csv(base + "dq/dq_mismatches.csv")
        if len(mm):
            mm["diff"] = pd.to_numeric(mm.get("diff"), errors="coerce")
            g = mm.groupby(["check", "std", "defect", "lane", "level"], dropna=False)
            stat = g.agg(rows=("bin_start", "size"), diff_mean=("diff", "mean"), diff_max=("diff", "max")).round(4)
            out.append("\n  DQ mismatches per check (rows, |D2 - silver|):")
            out.append(stat.reset_index().to_string(index=False))
            out.append("\n  first 3 mismatches per check:")
            show = [c for c in ["check", "std", "defect", "lane", "level", "bin_start", "d2_value", "silver_value",
                                "diff", "reason"] if c in mm.columns]
            out.append(g.head(3)[show].to_string(index=False))
    if base + "dq/rule_checks.csv" in keys:
        rc = s3.csv(base + "dq/rule_checks.csv")
        bad = rc[rc["status"] != "PASS"] if "status" in rc else rc
        out.append(f"\n  rule checks: {len(rc) - len(bad)}/{len(rc)} PASS")
        if len(bad):
            out.append(bad.to_string(index=False))


# ── gold ──────────────────────────────────────────────────────────────────────
def gold(s3, a, out):
    base = f"{a.gold_prefix}/{a.part}/"
    keys = [k for k, _, _ in s3.keys(base)]
    out.append(f"  location: s3://{a.bucket}/{base}  ({len(keys)} files)")
    if not keys:
        out.append("  NOTHING IN S3 for this UPC")
        return
    for k in sorted(k for k in keys if k.endswith(".csv")):
        df = s3.csv(k)
        name = os.path.basename(k)
        out.append(f"\n  {name}: {len(df):,} rows x {df.shape[1]} cols")
        if name == "final_intermediate.csv":
            flags = [c for c in df.columns if c.endswith("_flag")]
            lens = [c for c in df.columns if c.endswith("_length")]
            out.append("    flagged 10 m segments: " + ", ".join(f"{c} {int(df[c].sum())}" for c in flags))
            out.append("    affected length (m):   " + ", ".join(f"{c} {df[c].sum():,.0f}" for c in lens))
            iv = [c for c in df.columns if c.endswith(("_interval_ca", "_interval_irc"))]
            out.append("    intervals used:        " + ", ".join(f"{c} {sorted(df[c].dropna().unique().tolist())}"
                                                                 for c in iv))
        else:
            out.append("    " + df.to_string(index=False).replace("\n", "\n    "))
    log_key = base + "gold_run.log"
    if log_key in keys:
        log = s3.read(log_key).decode("utf-8", "replace").splitlines()
        tail = [l for l in log if re.search(r"WARNING|ERROR|Traceback|Error|FAILED|summary|\bOK\b|SKIPPED", l)]
        out.append("\n  gold_run.log (warnings / errors / summary):")
        out += ["    " + l for l in tail[-40:]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upc", required=True)
    ap.add_argument("--zone", required=True)
    ap.add_argument("--phase", required=True)
    ap.add_argument("--cycle", required=True)
    ap.add_argument("--bucket", default=os.environ.get("BUCKET", "dhms-new-stgae"))
    ap.add_argument("--bronze-prefix", default=os.environ.get("BRONZE_PREFIX", "NSV_Pipeline"))
    ap.add_argument("--silver-prefix", default=os.environ.get("SILVER_PREFIX", "NSV_Pipeline/silver"))
    ap.add_argument("--gold-prefix", default=os.environ.get("GOLD_PREFIX", "NSV_Pipeline/gold"))
    ap.add_argument("--status", default="", help='"bronze=ok;silver=exit 1;gold=ok" from run_e2e.sh')
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    a.upc_dash = re.sub(r"[/_ ]", "-", a.upc).upper()
    a.part = f"zone={a.zone}/phase={a.phase}/cycle={a.cycle}/{a.upc_dash}"

    s3 = S3(a.bucket)
    out = [LINE, f"NSV END-TO-END REPORT   {a.upc}   zone={a.zone} phase={a.phase} cycle={a.cycle}",
           f"written {datetime.now():%Y-%m-%d %H:%M:%S}", LINE]
    for part in filter(None, a.status.split(";")):
        out.append("  " + part.replace("=", ": ", 1))
    section(out, "1. BRONZE", lambda o: bronze(s3, a, o))
    section(out, "2. SILVER", lambda o: silver(s3, a, o))
    section(out, "3. GOLD", lambda o: gold(s3, a, o))

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"final report: {a.out}")


if __name__ == "__main__":
    main()
