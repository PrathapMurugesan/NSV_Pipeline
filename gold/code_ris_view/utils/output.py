"""Final ris_view_dw table.

DB id columns (UPC_ID, Survey_ID, Survey_Source_ID, Defect_Type_ID) are not created here;
they are added when the output is pushed to Postgres. The keys those lookups need are kept:
    UPC_ID           <- UPC
    Survey_Source_ID <- Source_System (master_survey_system.system_code)
    Survey_ID        <- UPC + Phase + Cycle + Defect_Detected_On (survey_data_map)
    Defect_Type_ID   <- Defect_Asset_Type + Defect_Name (master_defect_type.asset_type / defect_name)
"""
from datetime import datetime

import numpy as np
import pandas as pd

POINT_DEFECTS = ["Pothole", "Cracking"]

DW_RENAME = {
    "source_system": "Source_System",
    "upc": "UPC",
    "phase": "Phase",
    "cycle": "Cycle",
    "detected_on": "Defect_Detected_On",
    "defect_type": "Defect_Type",
    "affected_length_ca_m": "Affected_length_CA(in_m)",
    "affected_length_irc_m": "Affected_Length_IRC(in_m)",
    "affected_length_ca_km": "Affected_length_CA(in_km)",
    "affected_length_irc_km": "Affected_Length_IRC(in_km)",
    "zone": "Zone",
    "pavement_type": "Pavement_Type",
}

DW_COLUMNS = [
    "Source_System", "UPC", "Phase", "Cycle", "Defect_Detected_On", "Defect_Type", "Zone", "Pavement_Type",
    "UPC Length(in km)", "UPC Lane Length(in km)", "State", "RO", "PIU", "NH", "AE/IE", "Contractor",
    "Total_Defects_IRC", "Total_Defects_CA",
    "Affected_length_CA(in_m)", "Affected_Length_IRC(in_m)",
    "Affected_length_CA(in_km)", "Affected_Length_IRC(in_km)",
    "populated_on",
    "Defect_Asset_Type", "Defect_Name",
]


def add_defect_type_keys(dw):
    """master_defect_type lookup keys (asset_type + defect_name) for Defect_Type_ID.

    - null Pavement_Type -> "Asphalt"
    - Point defects (Pothole, Cracking): Concrete -> "Rigid Pavements", otherwise "Flexible Pavement";
      Cracking on concrete -> "Crack on Rigid Pavement", otherwise "Cracking"
    - Line defects: Roughness / Ravelling -> "Pavement (NSV)", Rutting -> "PAVEMENT"
    """
    dw = dw.copy()
    dw["Pavement_Type"] = dw["Pavement_Type"].fillna("Asphalt")

    defect = dw["Defect_Type"]
    is_point = defect.isin(POINT_DEFECTS)
    is_concrete = dw["Pavement_Type"].astype(str).str.strip().str.lower().isin(["cement concrete", "cememt concrete"])

    dw["Defect_Asset_Type"] = np.select(
        [is_point & is_concrete, is_point & ~is_concrete,
         defect.isin(["Roughness", "Ravelling"]), defect == "Rutting"],
        ["Rigid Pavements", "Flexible Pavement", "Pavement (NSV)", "PAVEMENT"],
        default=None,
    )
    dw["Defect_Name"] = np.select(
        [(defect == "Cracking") & is_concrete, (defect == "Cracking") & ~is_concrete,
         defect == "Pothole", defect == "Roughness", defect == "Ravelling", defect == "Rutting"],
        ["Crack on Rigid Pavement", "Cracking", "Pothole", "Roughness", "Ravelling", "Rutting"],
        default=None,
    )
    return dw


def build_ris_view_dw(defect_rows, total_rows):
    dw = pd.concat([defect_rows, total_rows], ignore_index=True)
    dw["populated_on"] = datetime.now()
    dw = add_defect_type_keys(dw.rename(columns=DW_RENAME))
    # Drop rows where Zone is missing
    return dw.dropna(subset=["Zone"])[DW_COLUMNS].reset_index(drop=True)

