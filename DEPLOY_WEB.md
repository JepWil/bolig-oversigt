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
c:/Users/Jeppe/Desktop/Bolig/.venv/Scripts/python.exe run_bolig_overview.py
```

Det sikrer at både modern/light og pendlingskort er opdateret i web-bundlen.

## Netlify (hurtigst)
1. Opret site på Netlify.
2. Connect repository eller drag/drop mappen `web`.
3. Hvis repository bruges, så læser Netlify `netlify.toml` i roden og publicerer `web` automatisk.
4. Del URL'en til andre.

## GitHub Pages (fra repository)
1. Push projektet til GitHub.
2. Workflow i `.github/workflows/deploy-pages.yml` kører automatisk ved push til `main` og bygger alle sider.
3. Når deployment er færdig, fås et fast GitHub Pages-link.
4. Del ét link til modern forsiden (`/`) - herfra kan brugere åbne både light-mode og interaktivt kort uden ekstra opsætning.

## PDF-version
Åbn `Bolig_oversigt_modern.html` i browser -> Print -> Save as PDF.
