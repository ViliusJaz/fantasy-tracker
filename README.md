# Fantasy trackeris

Vietinis BasketNews Fantasy draft lygų sekiklis: lygų sąrašas, turnyrinės lentelės
(head-to-head ir pagal taškus), mačai / turai, komandų sudėtys pagal turus, laisvieji
agentai ir žaidėjų traumos.

```bash
python3 fantasy-tracker/server.py
```

Atsidaryk http://127.0.0.1:8124. Papildomų bibliotekų nereikia (tik Python 3 standartinė biblioteka).
Kalbą (LT / EN) ir temą (tamsi / šviesi, smėlio spalvos) gali perjungti viršuje dešinėje –
pasirinkimai įsimenami naršyklėje, o serveris tekstus (apdovanojimus, traumų istoriją, klaidas)
grąžina pagal `?lang=`.

- Lygos saugomos `leagues.json`. Naują lygą pridėk pagrindiniame puslapyje įklijavęs jos
  nuorodą (`https://fantasy.basketnews.com/fantasy-leagues/<id>/...`).
- Duomenys imami iš viešo BasketNews GraphQL API (`/backend/graphql`) per serverį, nes
  naršyklė tiesiogiai jo kviesti negali (CORS). Atsakymai trumpam kešuojami atmintyje.
- „Liko“ – kiek pagrindinio penketo žaidėjų dar nesužaidė vykstančio turo rungtynių.
  Kai turas vyksta, puslapis atsinaujina kas minutę.
- Laisvieji agentai – visi varžybų žaidėjai (`playersSearchRecordsFromClient`), kurių nėra
  nė vienos lygos komandos sudėtyje. Rikiuojama paspaudus bet kurio stulpelio pavadinimą,
  statistikos filtrai – intervalo slankiklis su įvedamomis ribomis.
- „Rungtynių statistika“ – kiekvienų realių turo rungtynių lentelė (suskleista, atsidaro paspaudus)
  su kiekvieno žaidėjo statistika ir jo savininku lygoje. „Visi žaidėjai“ – kaip laisvieji
  agentai, bet su visais žaidėjais ir savininko stulpeliu.
- Pažangi statistika (USG%, TS%, reitingai, procentiliai) – iš BasketNews
  `advanced-stats/team-profile/players.json` (sezono ir kiekvieno turo, `sequence_from/to`).
  Turui, kurio BasketNews dar nepaskelbė, USG% apskaičiuojamas iš rungtynių statistikos.
- „Karjera Proballers“ – `/go/proballers/<lyga>/<žaidėjas>` nukreipia tiesiai į žaidėjo Proballers
  puslapį. Proballers ID imamas iš Wikidata (savybė P8548); bendrapavardžiai atskiriami pagal
  gimimo datą iš BasketNews profilio. Rasti adresai saugomi `data/proballers.json`. Jei žaidėjo
  Wikidata neturi, atidaroma paieška tik tarp Proballers žaidėjų puslapių.
- Atkovoti kamuoliai = `s_orb + s_drb` (API laukas `s_rbs` – gauti blokai, ne atkovoti kamuoliai).
- Traumos – iš BasketNews traumų sąrašo (Eurolygai
  https://basketnews.com/news-212393-euroleague-injury-report-updated.html). EN versijoje
  rodoma sąrašo formuluotė („Out“, „Game-time“ ...), LT versijoje – basketnews.lt žodžiai
  („Nežaidžia“, „Prieš rungtynes“ ...), o komentarus išverčia `injury_lt.py` (kūno dalis +
  pusė + traumos tipas su teisingais linksniais). Ko vertėjas nesupranta, serverio konsolėje
  pažymi „Traumos komentaras neišverstas“ ir rodo originalą.
- Taškai pagal sudėtį: penketas ×1, kapitonas ×2, 6-as žaidėjas (b-1) ×1, B2–B5 ×0.5,
  neaktyvūs („Out“) ×0. Leidžiamos formacijos (C-F-G): 1-2-2, 2-1-2, 2-2-1, 1-3-1, 1-1-3.
  „Optimali sudėtis“ – geriausias tų pačių aktyvių žaidėjų išdėstymas; skirtumas –
  „prarasta dėl sudėties“. Taisyklės patikrintos su oficialiais 1 turo rezultatais.
- „Sezono rekordai“ – turo apdovanojimai (tik pasibaigę turai), sezono „Oskarai“,
  rekordai ir forma. Kapitonų / MVP / prarastų taškų apdovanojimams reikia turo sudėčių.

## Ką programa kaupia pati (`data/`)

Viešas API rodo tik *dabartinę* sudėtį ir *dabartinį* traumų sąrašą, todėl kol veikia
serveris, kas 10 min. išsaugoma:

- `data/lineups/<lygos id>.json` – kiekvienos komandos sudėtis kiekvienam turui. HLA 1
  Divizionas 1 turo sudėtys įkeltos iš ekrano nuotraukų (`"source": "import"`). Iš jos
  komandos puslapyje rodomi ankstesnių turų penketai ir kapitonai. Turams, kurie praėjo
  prieš paleidžiant programą, rodomas dabartinis sąrašas su to turo taškais.
- `data/injuries.json` – traumų epizodai (kada žaidėjas atsirado sąraše, statuso ir
  komentarų pokyčiai, kada pasveiko). Kartu su praleistais turais iš statistikos iš to
  sudaroma žaidėjo traumų istorija.

## Viešas puslapis (GitHub Pages)

`export.py` sugeneruoja visą svetainę į `site/`: puslapį ir kiekvieną API atsakymą kaip
JSON failą (`site/api/<kelias>[.r<turas>].<kalba>.json`). Tokią svetainę gali rodyti bet
koks statinis hostingas, be veikiančio serverio.

`.github/workflows/pages.yml` tai daro GitHub'e kas ~15 min.: paleidžia `export.py`,
įrašo `data/` pokyčius (sudėtis, traumų žurnalą) atgal į repozitoriją ir paskelbia `site/`
per GitHub Pages. Viešoje versijoje:

- lygos pridedamos redaguojant `leagues.json` (`{"id": "<lygos id>", "title": "..."}`);
- „Mano komanda“ saugoma kiekvieno lankytojo naršyklėje;
- duomenys gali vėluoti iki ~15 min. (GitHub kartais paleidžia vėliau).

Lokaliai patikrinti: `python3 export.py`, tada `python3 -m http.server -d site` ir
atsidaryk http://127.0.0.1:8000.
