"""
writer.py - turn tables and dictionaries into file bytes. No paths, no storage here (see run.py).

Raw layer rule: every value is saved as TEXT, exactly as the workbook shows it
(numbers, dates, booleans converted with str()). Nothing is parsed or "fixed" here;
the profile JSON says what each column really contains.
"""
import io
import json
from datetime import date, datetime

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def to_text(value):
    """One cell -> text. Empty stays empty (None)."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def as_text_frame(df):
    """Every column of the DataFrame -> text (None for empty)."""
    return pd.DataFrame({col: [to_text(v) for v in df[col].tolist()] for col in df.columns}, columns=df.columns)


def parquet_bytes(df):
    """DataFrame -> parquet bytes, with an explicit all-text schema (no type guessing by pyarrow)."""
    text = as_text_frame(df)
    schema = pa.schema([pa.field(str(c), pa.string()) for c in text.columns])
    table = pa.Table.from_pandas(text, schema=schema, preserve_index=False)
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def csv_bytes(df):
    """DataFrame -> csv bytes (utf-8 with BOM so Excel shows symbols like '–' correctly)."""
    return as_text_frame(df).to_csv(index=False).encode("utf-8-sig")


def json_bytes(obj):
    """dict -> pretty JSON bytes. Dates and other objects become text."""
    return json.dumps(obj, indent=2, ensure_ascii=False, default=to_text).encode("utf-8")
