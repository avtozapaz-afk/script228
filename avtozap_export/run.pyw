"""Двойной щелчок по этому файлу открывает программу (без чёрного окна).

Файл лежит внутри папки программы и сам находит остальные части.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from avtozap_export.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
