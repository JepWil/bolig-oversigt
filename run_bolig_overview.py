from __future__ import annotations

import os
import sys
from pathlib import Path
import webbrowser

import enrich_bolig_csv
import build_bolig_showcase
import build_pendling_map


def resolve_working_directory() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates = [
            exe_dir,
            exe_dir.parent,
            Path.cwd(),
        ]
        for base in candidates:
            if (base / "Jeppe_Bolig_Masterliste_Fuld(Masterliste).csv").exists() or (base / "Jeppe_Bolig_Masterliste_Beriget.csv").exists():
                return base
        return exe_dir
    return Path(__file__).resolve().parent


def main() -> None:
    workdir = resolve_working_directory()
    os.chdir(workdir)
    print(f"Arbejdsmappe: {workdir}")

    enrich_bolig_csv.main()
    build_bolig_showcase.main()
    build_pendling_map.main()
    webbrowser.open((workdir / "Bolig_oversigt_modern.html").resolve().as_uri())
    webbrowser.open((workdir / "Bolig_kort_pendling.html").resolve().as_uri())


if __name__ == "__main__":
    try:
        main()
        if getattr(sys, "frozen", False):
            input("\nFerdig. Tryk Enter for at lukke...")
    except Exception as exc:
        print(f"\nFEJL: {exc}")
        if getattr(sys, "frozen", False):
            input("Tryk Enter for at lukke...")
        raise
