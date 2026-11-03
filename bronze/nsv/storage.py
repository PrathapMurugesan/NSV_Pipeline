"""
storage.py - every file read and write in the project goes through here.

Two classes with exactly the same four methods:

    LocalStorage(root)            files on this computer
    S3Storage(bucket, prefix)     files in an S3 bucket        (needs: pip install boto3)

    list_files(folder)   -> ["d2/Comparison.xlsm", ...]   every file under the folder, any depth
    read_bytes(path)     -> the file's content
    write_bytes(path, data)
    describe()           -> "local:C:/NSV/data"  or  "s3://bucket/prefix"   (for logs / manifest)

Paths are always relative to the root (or bucket prefix) and use forward slashes,
e.g. "d2/run=20261019_160500/raw/comparison/comparison_ca.parquet".

Which storage is used is chosen in settings.py - nothing else in the code knows
whether it is talking to a disk or to S3.
"""
from pathlib import Path


# =============================================================================
# Local disk
# =============================================================================
class LocalStorage:
    def __init__(self, root):
        self.root = Path(root)

    def describe(self):
        return f"local:{self.root}"

    def list_files(self, folder=""):
        """Every file under <root>/<folder>, any depth, as sorted relative paths."""
        base = self.root / folder
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())

    def read_bytes(self, path):
        return (self.root / path).read_bytes()

    def full_path(self, path):
        """Path to open the file directly, e.g. C:\\NSV\\data\\nsv\\...\\L1\\img.jpg"""
        return str(self.root / path)

    def write_bytes(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)     # create folders on the way
        target.write_bytes(data)


# =============================================================================
# Amazon S3
#   Not used today. To switch: in settings.py comment the LocalStorage line and
#   uncomment the S3Storage line. Credentials come from the usual AWS places
#   (environment variables, ~/.aws/credentials, or the machine's IAM role) -
#   never put keys in this code.
# =============================================================================
class S3Storage:
    def __init__(self, bucket, prefix="", **client_options):
        import boto3                       # imported here, so boto3 is only needed when S3 is switched on
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.s3 = boto3.client("s3", **client_options)   # e.g. region_name="ap-south-1"

    def describe(self):
        return f"s3://{self.bucket}/{self.prefix}".rstrip("/")

    def _key(self, path):
        """relative path -> full S3 key (prefix + path)"""
        path = path.strip("/")
        return f"{self.prefix}/{path}" if self.prefix else path

    def list_files(self, folder=""):
        """Every object under <prefix>/<folder>, as sorted paths relative to the prefix."""
        start = self._key(folder) + "/" if folder else (self.prefix + "/" if self.prefix else "")
        cut = len(self.prefix) + 1 if self.prefix else 0
        files = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=start):
            for obj in page.get("Contents", []):
                if not obj["Key"].endswith("/"):          # skip "folder" placeholder objects
                    files.append(obj["Key"][cut:])
        return sorted(files)

    def read_bytes(self, path):
        return self.s3.get_object(Bucket=self.bucket, Key=self._key(path))["Body"].read()

    def full_path(self, path):
        return f"s3://{self.bucket}/{self._key(path)}"

    def write_bytes(self, path, data):
        self.s3.put_object(Bucket=self.bucket, Key=self._key(path), Body=data)

    def check(self):
        """None if the bucket exists and can be reached, otherwise what is wrong (checked before a run)."""
        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except Exception as e:
            return (f"cannot use bucket '{self.bucket}': {e} - check the name with 'aws s3 ls' "
                    f"and the machine's IAM permissions")
        return None
