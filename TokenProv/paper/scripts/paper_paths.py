"""Paths shared by portable paper generators; no model or proof execution."""
import csv
import os
from pathlib import Path

PAPER = Path(__file__).resolve().parents[1]
DATA = PAPER / 'data'
OUTPUT = Path(os.environ.get('TOKENPROV_PAPER_OUTPUT', PAPER / 'generated')).resolve()
for protected in (DATA, PAPER / 'published', PAPER / 'scripts'):
    if OUTPUT == PAPER or OUTPUT.is_relative_to(protected) or protected.is_relative_to(OUTPUT):
        raise ValueError('Output must be separate from the bundled inputs, scripts and published snapshots')
os.environ.setdefault('MPLCONFIGDIR', str(OUTPUT / '.matplotlib'))


def output_dir(section):
    path = OUTPUT / section
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_csv(path, numeric=False):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    if numeric:
        for row in rows:
            for key, value in row.items():
                if value in ('True', 'False'):
                    row[key] = value == 'True'
                else:
                    try:
                        row[key] = int(value)
                    except ValueError:
                        try:
                            row[key] = float(value)
                        except ValueError:
                            pass
    return rows


def register_fonts():
    directory = os.environ.get('TOKENPROV_PAPER_FONT_DIR')
    if directory:
        from matplotlib import font_manager
        for path in Path(directory).iterdir():
            if path.suffix.lower() in {'.ttf', '.otf', '.ttc'}:
                font_manager.fontManager.addfont(str(path))


# Font registration precedes the original generators' exact-font checks.
register_fonts()
