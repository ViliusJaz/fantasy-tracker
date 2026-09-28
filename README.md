# Fantasy trackeris

Vietinis BasketNews Fantasy draft lygų sekiklis: lygų sąrašas, turnyrinės lentelės
(head-to-head ir pagal taškus), mačai / turai, komandų sudėtys pagal turus, laisvieji
agentai ir žaidėjų traumos.

```bash
python3 fantasy-tracker/server.py
```

Atsidaryk http://127.0.0.1:8124. Papildomų bibliotekų nereikia (tik Python 3 standartinė biblioteka).

- Lygos saugomos `leagues.json`. Naują lygą pridėk pagrindiniame puslapyje įklijavęs jos
  nuorodą (`https://fantasy.basketnews.com/fantasy-leagues/<id>/...`).
- Duomenys imami iš viešo BasketNews GraphQL API (`/backend/graphql`) per serverį, nes
  naršyklė tiesiogiai jo kviesti negali (CORS). Atsakymai trumpam kešuojami atmintyje.
- „Liko“ – kiek pagrindinio penketo žaidėjų dar nesužaidė vykstančio turo rungtynių.
  Kai turas vyksta, puslapis atsinaujina kas minutę.
- Laisvieji agentai – visi varžybų žaidėjai (`playersSearchRecordsFromClient`), kurių nėra
  nė vienos lygos komandos sudėtyje.
- Traumos – iš BasketNews traumų sąrašo (Eurolygai
  https://basketnews.com/news-212393-euroleague-injury-report-updated.html), su tuo pačiu
  užrašu kaip sąraše („Out“, „Game-time“, „Uncertain“ ...).
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
