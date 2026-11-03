"""
formulas.py - document HOW calculated columns are calculated (formulas.json). Nothing is recalculated.

For every column that holds formulas:
    formula        the formula with this row written as {row}:   =IF(J{row}>'Inputs (Thresholds)'!$C$7,1,0)
    readable       same, with this sheet's columns replaced by their names:
                   =IF([Current Cycle | Roughness BI (in mm/km) | L4]>'Inputs (Thresholds)'!$C$7,1,0)
    rows_with_formula / rows_without_formula   (a typed-in value in a formula column shows up here)
    same_formula_in_all_rows                   False = someone changed the formula in some rows
    different_rows                             which rows, and their formula (first 20)
"""
import re
from collections import Counter

CELL_REF = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d+)")
MAX_EXAMPLES = 20


def _outside_quotes(formula, change):
    """Apply change() only to the parts of the formula that are not inside "text" literals."""
    parts = formula.split('"')
    return '"'.join(change(p) if i % 2 == 0 else p for i, p in enumerate(parts))


def formula_pattern(formula, row):
    """'=IF(J5>0,K4,0)' in row 5 -> '=IF(J{row}>0,K{row-1},0)'.  Absolute rows ($5) stay as they are."""
    def change(text):
        def replace(m):
            before = text[m.start() - 1] if m.start() > 0 else ""
            after = text[m.end()] if m.end() < len(text) else ""
            if (before and (before.isalnum() or before in "_.")) or (after and (after.isalnum() or after in "_(")):
                return m.group(0)                        # part of a name like LOG10( or Sheet1, not a cell
            col_abs, col, row_abs, number = m.groups()
            if row_abs:
                return m.group(0)
            offset = int(number) - row
            rel = "{row}" if offset == 0 else f"{{row{offset:+d}}}"
            return f"{col_abs}{col}{row_abs}{rel}"
        return CELL_REF.sub(replace, text)
    return _outside_quotes(formula, change)


def readable_formula(pattern, letter_to_name):
    """Replace same-sheet, same-row references (J{row}) by [column name]."""
    def change(text):
        def replace(m):
            before = text[m.start() - 1] if m.start() > 0 else ""
            name = letter_to_name.get(m.group(1))
            if name is None or (before and (before in "!$" or before.isalnum())):
                return m.group(0)                        # other sheet, absolute, or unknown column
            return f"[{name}]"
        return re.sub(r"([A-Z]{1,3})\{row\}", replace, text)
    return _outside_quotes(pattern, change)


def describe_formulas(formulas, table):
    """
    formulas: {column name: {excel_row: '=...'}}   (from ExcelWorkbook.read_formulas)
    Returns the formulas.json entry for one sheet: {column name: {...}}
    """
    letter_to_name = {c["letter"]: c["name"] for c in table.columns}
    letters = {c["name"]: c["letter"] for c in table.columns}
    result = {}
    for name, by_row in formulas.items():
        patterns = {row: formula_pattern(f, row) for row, f in by_row.items()}
        counts = Counter(patterns.values())
        main, _ = counts.most_common(1)[0]
        different = [{"row": r, "formula": p} for r, p in sorted(patterns.items()) if p != main]
        result[name] = {
            "excel_column": letters.get(name),
            "formula": main,
            "readable": readable_formula(main, letter_to_name),
            "rows_with_formula": len(by_row),
            "rows_without_formula": len(table.excel_rows) - len(by_row),
            "same_formula_in_all_rows": not different,
            "different_rows": different[:MAX_EXAMPLES],
        }
    return result
