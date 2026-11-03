"""
survey.py - one survey = zone + phase + cycle + UPC. Every path is built from these four values.

    survey = Survey("A", "1", "1", "N-08004-03001-HR")
    survey.raw_path("d2")      -> nsv/zone=A/phase=1/cycle=1/N-08004-03001-HR/d2
    survey.bronze_path("d2")   -> bronze/nsv/zone=A/phase=1/cycle=1/N-08004-03001-HR/d2
    survey.upc                 -> N/08004/03001/HR       (the real code; folders use '-' instead of '/')

    discover_surveys(...)      -> every survey found in the raw folders, with the deliverables it has

The folder layout itself is defined once in settings.py (RAW_LAYOUT / BRONZE_LAYOUT).
"""
import re
from dataclasses import dataclass
from itertools import takewhile

import settings
from nsv.log import get_logger

logger = get_logger("nsv.survey")


# =============================================================================
# One survey
# =============================================================================
@dataclass(frozen=True, order=True)
class Survey:
    zone: str
    phase: str
    cycle: str
    upc_folder: str            # UPC as written in folder names: N-08004-03001-HR

    @property
    def upc(self):
        """The real UPC code: N-08004-03001-HR -> N/08004/03001/HR"""
        return self.upc_folder.replace("-", "/")

    @property
    def label(self):
        """Short name for logs: A/1/1/N-08004-03001-HR"""
        return f"{self.zone}/{self.phase}/{self.cycle}/{self.upc_folder}"

    def fields(self, deliverable):
        return {"zone": self.zone, "phase": self.phase, "cycle": self.cycle,
                "upc": self.upc_folder, "deliverable": deliverable}

    def raw_path(self, deliverable):
        return settings.RAW_LAYOUT.format(**self.fields(deliverable))

    def bronze_path(self, deliverable):
        return settings.BRONZE_LAYOUT.format(**self.fields(deliverable))

    def as_dict(self):
        """For manifests / batch summaries."""
        return {"zone": self.zone, "phase": self.phase, "cycle": self.cycle,
                "upc": self.upc, "upc_folder": self.upc_folder}


def upc_to_folder(upc):
    """Accept both forms from the command line: 'N/08004/03001/HR' or 'N-08004-03001-HR'."""
    return upc.strip().replace("/", "-")


# =============================================================================
# Find every survey in the raw folders
# =============================================================================
def _layout_regex():
    """RAW_LAYOUT 'nsv/zone={zone}/...' -> regex with one named group per {field}."""
    pattern = re.escape(settings.RAW_LAYOUT)
    for name in ("zone", "phase", "cycle", "upc", "deliverable"):
        pattern = pattern.replace(re.escape("{" + name + "}"), f"(?P<{name}>[^/]+)")
    return re.compile("^" + pattern + "/")


def discover_surveys(zones=None, phases=None, cycles=None, upcs=None):
    """
    Walk the raw folders and return {Survey: [deliverables it has]}, sorted.
    Optional filters (lists) narrow it down, e.g. zones=["A"], upcs=["N/08004/03001/HR"].
    Folders that don't fit the layout or the allowed values (settings.ZONES ...) are reported, not used.
    """
    root = "/".join(takewhile(lambda part: "{" not in part, settings.RAW_LAYOUT.split("/")))   # -> 'nsv'
    regex = _layout_regex()
    upcs = {upc_to_folder(u) for u in upcs} if upcs else None

    found, invalid = {}, set()
    for path in settings.INPUT.list_files(root):
        m = regex.match(path)
        if not m:
            continue
        f = m.groupdict()
        problems = [f"{key}={f[key]}" for key, allowed in
                    (("zone", settings.ZONES), ("phase", settings.PHASES), ("cycle", settings.CYCLES),
                     ("deliverable", settings.DELIVERABLES)) if f[key] not in allowed]
        if problems:
            invalid.add((m.group(0), ", ".join(problems)))
            continue
        survey = Survey(f["zone"], f["phase"], f["cycle"], f["upc"])
        if ((zones and survey.zone not in zones) or (phases and survey.phase not in phases)
                or (cycles and survey.cycle not in cycles) or (upcs and survey.upc_folder not in upcs)):
            continue
        found.setdefault(survey, set()).add(f["deliverable"])

    for folder, why in sorted(invalid):
        logger.warning(f"ignored folder '{folder}' - not an allowed value: {why}")
    logger.info(f"{len(found)} survey(s) found in '{settings.INPUT.describe()}/{root}'")
    return {s: sorted(found[s]) for s in sorted(found)}
