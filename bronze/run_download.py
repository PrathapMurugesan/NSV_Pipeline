"""
run_download.py - run d1..d4 for a list of UPCs straight from a vendor download folder, without
copying files and without changing the pipeline.

The download is laid out like this:
    <RAW_ROOT>/Zone B/Phase 3/Cycle 1/N_02017_03001_RJ/<road name>/Deliverable 4 - NHAI One & .../...
The pipeline expects (settings.RAW_LAYOUT):
    nsv/zone=B/phase=3/cycle=1/N-02017-03001-RJ/d4/...

A small proxy storage presents the download in the expected layout ("Deliverable 1. RAMS DATA",
"Deliverable 4 NHAI One ..." -> d1 / d4, any spelling) and replaces settings.INPUT for this run only.
Results still go to settings.OUTPUT (data/bronze/...), exactly as with main.py.

    python run_download.py                     all UPCs below, d1..d4
    python run_download.py d4                  only d4 (any main.py option can be added)
    python run_download.py --root "D:\\other\\data"
    python run_download.py --root s3://bucket/prefix/    read the download straight from S3 (needs boto3)
    python run_download.py --all                every UPC folder in the download
    python run_download.py --upc N/01001/01001/HR N_02017_03001_RJ    these UPCs instead of the list
    python run_download.py --upc N_02008_04001_MP --zone C --phase 3 --cycle 1 --use-found
                                                when the UPC is only under another zone/phase/cycle: extract it there

Logs (everything that was printed): logs/run_download_<timestamp>.log
Folder mapping used:                logs/run_download_<timestamp>_mapping.csv
Plus, as usual: per-run run.log + manifest in each bronze folder, and the batch summary / report in
data/bronze/nsv/_batches/.
"""
import csv
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import settings
from nsv.log import FORMAT, get_logger

RAW_ROOT = r"C:\Users\Softsensor\Downloads\download\data"

UPCS = [
    "N/09011/01002/RJ",
    "N/09009/02001/RJ",
    "N/04003/12004/RJ",
    "N/04003/12005/RJ",
    "N/04003/12006/RJ",
    "N/08015/02001/RJ",
    "N/09013/01002/RJ",
    "N/04037/01001/RJ",
    "N/06015/01001/RJ",
    "N/02001/07001/RJ",
    "N/02017/03001/RJ",
]

LOG_DIR = settings.PROJECT_ROOT / "logs"

ZONE = re.compile(r"^zone[\s_\-]*([A-Za-z])$", re.IGNORECASE)      # "Zone B", "Zone_B", "Zone-B"
PHASE = re.compile(r"^phase[\s_\-]*(\d+)$", re.IGNORECASE)
CYCLE = re.compile(r"^cycle[\s_\-]*(\d+)$", re.IGNORECASE)
DELIVERABLE = re.compile(r"^deliverable\s*[-_.]?\s*(\d)\b", re.IGNORECASE)

logger = get_logger("nsv.download")


def _plain(path):
    """Path for logs / output tables, without the Windows long-path prefix."""
    return str(path).removeprefix("\\\\?\\")


# =============================================================================
# Find the folders:  (zone, phase, cycle, upc folder, d1..d4) -> real folder
# =============================================================================
def upc_key(text):
    """'N/09011/01002/RJ', 'N_09011_01002_RJ', 'N-09011-01002-RJ' -> 'N-09011-01002-RJ'"""
    return re.sub(r"[/_\-\s]+", "-", text.strip()).upper()


def find_deliverables(root, upcs):
    """Returns ({virtual folder: real folder}, mapping rows for the csv, UPCs not found).
    upcs = None: every UPC folder found."""
    wanted = None if upcs is None else {upc_key(u) for u in upcs}
    folders, rows, seen = {}, [], set()
    for zone_dir in sorted(p for p in root.iterdir() if p.is_dir() and ZONE.match(p.name)):
        for phase_dir in sorted(p for p in zone_dir.iterdir() if p.is_dir() and PHASE.match(p.name)):
            for cycle_dir in sorted(p for p in phase_dir.iterdir() if p.is_dir() and CYCLE.match(p.name)):
                for upc_dir in sorted(p for p in cycle_dir.iterdir() if p.is_dir()):
                    upc = upc_key(upc_dir.name)
                    if wanted is not None and upc not in wanted:
                        continue
                    zone = ZONE.match(zone_dir.name).group(1).upper()
                    phase = PHASE.match(phase_dir.name).group(1)
                    cycle = CYCLE.match(cycle_dir.name).group(1)
                    seen.add(upc)
                    # deliverable folders directly under the UPC or one level down (road-name folder)
                    candidates = [p for p in upc_dir.iterdir() if p.is_dir()]
                    candidates += [q for p in candidates if not DELIVERABLE.match(p.name)
                                   for q in p.iterdir() if q.is_dir()]
                    for d_dir in sorted(candidates):
                        m = DELIVERABLE.match(d_dir.name)
                        if not m:
                            continue
                        d = f"d{m.group(1)}"
                        virtual = settings.RAW_LAYOUT.format(zone=zone, phase=phase, cycle=cycle,
                                                             upc=upc, deliverable=d)
                        files = sum(1 for f in d_dir.rglob("*") if f.is_file())
                        if virtual in folders:
                            logger.error(f"{upc} {d}: two folders - using '{_plain(folders[virtual])}', "
                                         f"ignoring '{_plain(d_dir)}'")
                            rows.append([zone, phase, cycle, upc, d, _plain(d_dir), files, "ignored (duplicate)"])
                            continue
                        folders[virtual] = d_dir
                        rows.append([zone, phase, cycle, upc, d, _plain(d_dir), files, "used"])
    missing = [] if wanted is None else sorted(wanted - seen)
    return folders, rows, missing


# =============================================================================
# The same, straight from S3 (nothing is downloaded):  --root s3://bucket/prefix
# =============================================================================
def _s3_dirs(s3, bucket, prefix):
    """Names of the 'folders' directly under prefix (prefix ends with '/')."""
    names = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        names += [p["Prefix"][len(prefix):].rstrip("/") for p in page.get("CommonPrefixes", [])]
    return sorted(n for n in names if n)


def _s3_keys(s3, bucket, prefix):
    """Every object key under prefix, without 'folder' placeholder objects."""
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys += [o["Key"] for o in page.get("Contents", []) if not o["Key"].endswith("/")]
    return keys


def find_deliverables_s3(s3, bucket, prefix, upcs):
    """Like find_deliverables, on S3. Folders map to (real key prefix, [file paths below it])."""
    wanted = None if upcs is None else {upc_key(u) for u in upcs}
    folders, rows, seen = {}, [], set()
    for zone_dir in (n for n in _s3_dirs(s3, bucket, prefix) if ZONE.match(n)):
        zone_prefix = f"{prefix}{zone_dir}/"
        for phase_dir in (n for n in _s3_dirs(s3, bucket, zone_prefix) if PHASE.match(n)):
            phase_prefix = f"{zone_prefix}{phase_dir}/"
            for cycle_dir in (n for n in _s3_dirs(s3, bucket, phase_prefix) if CYCLE.match(n)):
                cycle_prefix = f"{phase_prefix}{cycle_dir}/"
                for upc_dir in _s3_dirs(s3, bucket, cycle_prefix):
                    upc = upc_key(upc_dir)
                    if wanted is not None and upc not in wanted:
                        continue
                    zone = ZONE.match(zone_dir).group(1).upper()
                    phase = PHASE.match(phase_dir).group(1)
                    cycle = CYCLE.match(cycle_dir).group(1)
                    seen.add(upc)
                    upc_prefix = f"{cycle_prefix}{upc_dir}/"
                    logger.info(f"listing s3://{bucket}/{upc_prefix}")
                    # deliverable folders directly under the UPC or one level down (road-name folder)
                    by_dir = {}
                    for key in _s3_keys(s3, bucket, upc_prefix):
                        parts = key[len(upc_prefix):].split("/")
                        if len(parts) >= 2 and DELIVERABLE.match(parts[0]):
                            d_dir, rest = parts[0], parts[1:]
                        elif len(parts) >= 3 and DELIVERABLE.match(parts[1]):
                            d_dir, rest = f"{parts[0]}/{parts[1]}", parts[2:]
                        else:
                            continue
                        by_dir.setdefault(d_dir, []).append("/".join(rest))
                    for d_dir in sorted(by_dir):
                        d = f"d{DELIVERABLE.match(d_dir.split('/')[-1]).group(1)}"
                        virtual = settings.RAW_LAYOUT.format(zone=zone, phase=phase, cycle=cycle,
                                                             upc=upc, deliverable=d)
                        real = f"{upc_prefix}{d_dir}/"
                        files = len(by_dir[d_dir])
                        if virtual in folders:
                            logger.error(f"{upc} {d}: two folders - using 's3://{bucket}/{folders[virtual][0]}', "
                                         f"ignoring 's3://{bucket}/{real}'")
                            rows.append([zone, phase, cycle, upc, d, f"s3://{bucket}/{real}", files,
                                         "ignored (duplicate)"])
                            continue
                        folders[virtual] = (real, sorted(by_dir[d_dir]))
                        rows.append([zone, phase, cycle, upc, d, f"s3://{bucket}/{real}", files, "used"])
    missing = [] if wanted is None else sorted(wanted - seen)
    return folders, rows, missing


# =============================================================================
# Proxy storage: the download, seen in the layout the pipeline expects (read only)
# =============================================================================
class DownloadStorage:
    def __init__(self, root, folders):
        self.root = root
        self.folders = folders                       # virtual folder -> real folder

    def describe(self):
        return f"local:{_plain(self.root)} (vendor layout via run_download.py)"

    def _real(self, path):
        path = path.strip("/")
        for virtual, real in self.folders.items():
            if path == virtual or path.startswith(virtual + "/"):
                return real / path[len(virtual):].lstrip("/")
        raise FileNotFoundError(f"not in the download mapping: {path}")

    def _files(self, real):
        """File paths below a real folder, relative to it."""
        return [f.relative_to(real).as_posix() for f in real.rglob("*") if f.is_file()]

    def list_files(self, folder=""):
        folder = folder.strip("/")
        out = []
        for virtual, real in self.folders.items():
            if folder and not (virtual == folder or virtual.startswith(folder + "/")
                               or folder.startswith(virtual + "/")):
                continue
            for rel in self._files(real):
                path = f"{virtual}/{rel}"
                if not folder or path.startswith(folder + "/"):
                    out.append(path)
        return sorted(out)

    def read_bytes(self, path):
        return self._real(path).read_bytes()

    def full_path(self, path):
        return _plain(self._real(path))

    def write_bytes(self, path, data):
        raise PermissionError("the download folder is read only - results go to settings.OUTPUT")


class S3DownloadStorage(DownloadStorage):
    """The same, on S3: folders map to (real key prefix, [file paths below it]); files are read on demand."""
    def __init__(self, s3, bucket, prefix, folders):
        super().__init__(f"s3://{bucket}/{prefix}", folders)
        self.s3, self.bucket = s3, bucket

    def describe(self):
        return f"{self.root} (vendor layout via run_download.py)"

    def _files(self, real):
        return real[1]

    def _real(self, path):
        path = path.strip("/")
        for virtual, (real, _) in self.folders.items():
            if path == virtual or path.startswith(virtual + "/"):
                return real + path[len(virtual):].lstrip("/")
        raise FileNotFoundError(f"not in the download mapping: {path}")

    def read_bytes(self, path):
        return self.s3.get_object(Bucket=self.bucket, Key=self._real(path))["Body"].read()

    def full_path(self, path):
        return f"s3://{self.bucket}/{self._real(path)}"


# =============================================================================
# Run
# =============================================================================
def _check_location(rows, args, use_found):
    """
    A UPC that is in the download only under another zone/phase/cycle than requested (--zone/--phase/--cycle,
    e.g. from the status CSV) would find nothing. Logged as an error; with --use-found the filters are
    dropped, so the UPC is extracted where it is. Returns the (possibly changed) arguments.
    """
    wanted = {name: _option(args, f"--{name}") for name in ("zone", "phase", "cycle")}
    elsewhere = []
    for upc in sorted({r[3] for r in rows if r[7] == "used"}):
        places = sorted({(r[0], r[1], r[2]) for r in rows if r[3] == upc and r[7] == "used"})
        if not any(all(not wanted[n] or str(v) in wanted[n] for n, v in zip(("zone", "phase", "cycle"), p))
                   for p in places):
            elsewhere.append(upc)
            logger.error(f"UPC {upc} is in the download only under {['/'.join(p) for p in places]} (zone/phase/cycle)"
                         f" - not under the requested {'/'.join(','.join(v) if v else '*' for v in wanted.values())}"
                         f" - check the zone / phase / cycle in the status list")
    if elsewhere and use_found:
        logger.warning(f"--use-found: extracting {elsewhere} where they are in the download "
                       f"(zone / phase / cycle filters dropped)")
        for name in ("--zone", "--phase", "--cycle"):
            if name in args:
                i = args.index(name)
                args = args[:i] + args[i + 1 + len(_option(args, name)):]
    return args


def _option(args, name):
    """Values after an option: ['--phase', '1', '2', 'd1'] -> ['1', '2'] (up to the next option / deliverable)."""
    if name not in args:
        return []
    values = []
    for a in args[args.index(name) + 1:]:
        if a.startswith("--") or re.fullmatch(r"d\d", a):
            break
        values.append(a)
    return values


def main(args):
    root = RAW_ROOT
    if "--root" in args:
        i = args.index("--root")
        root = args[i + 1]
        args = args[:i] + args[i + 2:]
    use_found = "--use-found" in args                # UPC under another zone/phase/cycle: extract it there
    args = [a for a in args if a != "--use-found"]
    upcs = UPCS
    if "--all" in args:                              # every UPC folder in the download
        upcs = None
        args = [a for a in args if a != "--all"]
    elif "--upc" in args:                            # these UPCs instead of the list above
        i = args.index("--upc")
        j = i + 1
        while j < len(args) and not args[j].startswith("--"):
            j += 1
        upcs = args[i + 1:j]
        args = args[:i] + args[j:]
    on_s3 = str(root).startswith("s3://")
    if on_s3:
        bucket, _, prefix = str(root)[len("s3://"):].partition("/")
        prefix = prefix if prefix.endswith("/") or not prefix else prefix + "/"   # keep any '//' inside
    else:
        root = Path(root).resolve()
    if not on_s3 and sys.platform == "win32" and not str(root).startswith("\\\\?\\"):
        # vendor paths reach 277 characters; without this prefix Windows (LongPathsEnabled = 0)
        # silently hides every file whose path is longer than 260
        root = Path("\\\\?\\" + str(root))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")     # %f: parallel runs get their own log
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"run_download_{stamp}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(FORMAT)
    logging.getLogger("nsv").addHandler(handler)    # every nsv.* logger also writes to this file

    logger.info(f"download folder: {root}")
    logger.info(f"UPCs requested: {'all found' if upcs is None else f'({len(upcs)}) {upcs}'}")
    if on_s3:
        import boto3                                 # only needed for an s3:// root
        s3 = boto3.client("s3")
        folders, rows, missing = find_deliverables_s3(s3, bucket, prefix, upcs)
    elif not root.is_dir():
        logger.error(f"folder not found: {root}")
        return 1
    else:
        folders, rows, missing = find_deliverables(root, upcs)
    map_path = LOG_DIR / f"run_download_{stamp}_mapping.csv"
    with open(map_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["zone", "phase", "cycle", "upc", "deliverable", "folder", "files", "status"])
        w.writerows(rows)
    for r in rows:
        logger.info(f"  {r[0]}/{r[1]}/{r[2]}/{r[3]:18} {r[4]}  {r[6]:4} file(s)  {r[7]:8} {r[5]}")
    for upc in missing:
        logger.error(f"UPC {upc} not found in {root}")
    args = _check_location(rows, args, use_found)
    if not folders:
        logger.error("nothing to run")
        return 1

    settings.INPUT = S3DownloadStorage(s3, bucket, prefix, folders) if on_s3 else DownloadStorage(root, folders)

    import main as pipeline_main                     # after settings.INPUT is replaced
    upcs = sorted({r[3] for r in rows if r[7] == "used"})
    code = 1
    try:
        code = pipeline_main.main([*args, "--upc", *upcs])
    except Exception:
        logger.exception("run_download crashed")
    logger.info(f"################ done, exit code {code} ################")
    if missing:
        logger.error(f"UPC(s) not found in the download: {missing}")
    logger.info(f"full log:       {log_path}")
    logger.info(f"folder mapping: {map_path}")
    handler.close()
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
