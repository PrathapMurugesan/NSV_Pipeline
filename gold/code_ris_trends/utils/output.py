"""Final ris_trends_dw table (Primary_ID is created when the output is pushed to the DB)."""
from datetime import datetime

FINAL_COLUMNS = [
    "UPC",
    "phase", "cycle", "zone", "pavement_type",
    "Survey_Source",
    "Defect_Detected_On",
    "PCI_Value",
    "Roughness_Average",
    "Average_Rutting_Depth",
    "row_rank",
    "roughness_ca_category",
    "roughness_ca_threshold",
    "lane_length_roughness_ca",
    "roughness_irc_category",
    "roughness_irc_threshold",
    "lane_length_roughness_irc",
    "rutting_ca_category",
    "rutting_ca_threshold",
    "lane_length_rutting_ca",
    "rutting_irc_category",
    "rutting_irc_threshold",
    "lane_length_rutting_irc",
    "populated_on",
]


def finalize_ris_trends(df):
    """Add populated_on and order columns to the target schema."""
    df = df.copy()
    df["populated_on"] = datetime.now()
    return df[FINAL_COLUMNS]
