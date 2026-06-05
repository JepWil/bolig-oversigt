# Deling via ét link (GitHub Pages eller Netlify)

## Filer der allerede er klar
- `web/index.html` (moderne visning)
- `web/family.html` (light familievisning)
- `web/pendling.html` (interaktivt kort + pendlingsfilter)
- `web/Jeppe_Bolig_Masterliste_BillundPrioritet.csv`
- `web/Jeppe_Bolig_Masterliste_Pendling.csv`

## Opdater data før deploy
Kør appen eller scriptet:

```powershell
.\dist\BoligOversigtApp.exe
```

eller

```powershell
c:/Users/Jeppe/Desktop/Bolig/.venv/Scripts/python.exe build_bolig_showcase.py
```

## Netlify (hurtigst)
1. Opret site på Netlify.
2. Connect repository eller drag/drop mappen `web`.
3. Hvis repository bruges, så læser Netlify `netlify.toml` i roden og publicerer `web` automatisk.
4. Del URL'en til andre.

## GitHub Pages (fra repository)
1. Push projektet til GitHub.
2. Workflow i `.github/workflows/deploy-pages.yml` kører automatisk ved push til `main`.
3. Når deployment er færdig, fås et fast GitHub Pages-link.
4. Del linket:
   - Modern: `/`
   - Familie-light: `/family.html`
   - Pendling/kort: `/pendling.html`

## PDF-version
Åbn `Bolig_oversigt_modern.html` i browser -> Print -> Save as PDF.
