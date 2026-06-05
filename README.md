# Boligoversigt (Billund-fokus)

Projektet enrich'er boligdata fra CSV + annonce-URL'er og genererer:

- `Bolig_oversigt_modern.html` (Modern + Light toggle + indlejret korttab)
- `Bolig_oversigt_light.html` (let visning)
- `Bolig_kort_pendling.html` (interaktivt pendlingskort)
- `web/` bundle til hosting

## Kørsel

```powershell
python run_bolig_overview.py
```

## Byg exe

```powershell
python -m PyInstaller --onefile --name BoligOversigtApp run_bolig_overview.py
```

## Web deployment

Se `DEPLOY_WEB.md`.
