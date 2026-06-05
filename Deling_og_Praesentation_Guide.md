# Deling og praesentation af boligoversigt

Denne guide er lavet til den berigede fil `Jeppe_Bolig_Masterliste_Beriget.csv`.

## 1) Deling til familie/partner (hurtig beslutning)

Anbefalet: Google Sheets eller Excel Online.

- Importer CSV-filen.
- Frys header-rakke.
- Slug visning i to faner:
  - `Topliste`: sorter efter `Samlet vurdering` faldende.
  - `Budget`: filtrer paa `Mdl. leje <= 10000`.
- Tilfoej farveskala paa `Samlet vurdering` (groen hoej, roed lav).
- Beskyt formel- og scorekolonner mod utilsigtede aendringer.

Kolonner der boer vises foerst:
- `Rang`, `By`, `Type`, `Mdl. leje`, `Samlet vurdering`, `available_from_text`, `deposit_dkk`, `move_in_price_dkk`, `Kommentar`, `canonical_url`.

## 2) Deling til bred gruppe (privatliv)

Lav en light-version uden raa links:

- Fjern `URL` og `canonical_url`.
- Behold kun:
  - `Rang`, `By`, `m²`, `Vær.`, `Mdl. leje`, `Samlet vurdering`, `Kommentar`, `available_from_text`, `move_in_price_dkk`.
- Eksporter som PDF (1 side med top 10).

## 3) Effektiv smart praesentation

Lav 3 visualiseringer i samme delingsark:

1. Rangeret top-10 tabel
- Sorteret paa `Samlet vurdering`.

2. Pris vs score scatter
- X-akse: `Mdl. leje`.
- Y-akse: `Samlet vurdering`.
- Farve: `Billund-score`.

3. Flytteomkostningsoverblik
- Soejlediagram over `move_in_price_dkk` for top-10.

## 4) QA inden deling

Brug `Jeppe_Bolig_Masterliste_QA.csv` til sidste kontrol:

- Filtrer `rent_mismatch = yes` eller `area_mismatch = yes`.
- Gennemgaa disse rækker manuelt mod annoncen.
- Marker rækker som afklaret i en ekstra kolonne `reviewed`.

## 5) Opdateringsrutine

Koer scriptet igen ved nye annoncer:

```powershell
c:/Users/Jeppe/Desktop/Bolig/.venv/Scripts/python.exe enrich_bolig_csv.py
```

Filer der opdateres automatisk:
- `Jeppe_Bolig_Masterliste_Beriget.csv`
- `Jeppe_Bolig_Masterliste_QA.csv`
