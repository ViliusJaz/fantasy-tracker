# Fantasy trackeris

Vietinis BasketNews Fantasy draft lygų sekiklis: lygų sąrašas, turnyrinės lentelės
(head-to-head ir pagal taškus), mačai / turai, komandų sudėtys pagal turus, laisvieji
agentai ir žaidėjų traumos.

```bash
python3 server.py
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
- `data/archive/` – kiekvieno pasibaigusio turo galutinė kopija: visų žaidėjų statistika ir FP,
  rungtynės, pažangi statistika, lygų lentelės, mačai, perėjimai, draftas (~40 KB turui).

Vietinis `var/` (necommit'inamas): `health.json`, nuolatinis kešas `cache.sqlite`, SQLite indeksas
`tracker.sqlite` (`python3 -m backend.storage status`) ir paskutinių paleidimų kopijos
`snapshots/live/` (tik Mac). Kas, kur ir kiek laikoma – [docs/DATA.md](docs/DATA.md).



## Kodo struktūra

`server.py` (vietinis serveris) ir `export.py` (statinė svetainė) tik kviečia bendrus `backend/` modulius:

| Modulis | Ką daro |
|---|---|
| `backend/sources/` | Duomenų šaltiniai: BasketNews GraphQL (`basketnews.py`), pažangi statistika (`advanced.py`), traumų sąrašo puslapis (`injury_report.py`), Wikidata (`wikidata.py`). Tik čia žinoma, kaip atrodo jų atsakymai. |
| `backend/league.py`, `players.py`, `rounds.py` | Lyga, lentelės, tvarkaraštis, sudėtys, savininkai; žaidėjai; turų būsenos. |
| `backend/scoring.py` | Taškai pagal sudėtį, formacijos, optimali sudėtis. |
| `backend/history.py` | Tai, ką programa kaupia `data/`: sudėtys ir traumų epizodai. Duomenų gavimas čia nieko nerašo – tik perduoda, o įrašo `pipeline.store()`. |
| `backend/injuries.py`, `injury_lt.py` | Traumų sąrašas, būsenos ir komentarai LT/EN, žaidėjo traumų istorija. |
| `backend/analytics/` | Sezono analitika: turų suvestinės, efektyvumas, tvarkaraščio sunkumas, apdovanojimai, perėjimų ROI. |
| `backend/payloads/` | Kiekvieno API adreso JSON (tas pats ir serveriui, ir `export.py`). |
| `backend/pipeline.py`, `log.py`, `config.py` | Bendri žingsniai (gauti → įrašyti), žurnalas su etapais `[FETCH]`, `[STORAGE]`, `[EXPORT]`…, keliai ir nustatymai. |

`FT_DATA_DIR`, `FT_LEAGUES_FILE`, `FT_SITE_DIR` leidžia nukreipti `data/`, `leagues.json` ir `site/` kitur (taip daro testai).

## Patikimumas

- **Tinklas** (`backend/net.py`): kartojama tik tai, kas po akimirkos gali pavykti (HTTP 429, 5xx,
  laiko limitas, nutrūkęs ryšys, laikina DNS klaida) – po 1 s ir 2 s su atsitiktiniu nuokrypiu,
  gerbiant `Retry-After`. 403, 404 ar HTML puslapis vietoj JSON nekartojami. Vienu metu – ne daugiau
  kaip 6 užklausos į vieną šaltinį; po 6 nesėkmių iš eilės šaltinis 2 min. nebeklausiamas. Vienodos
  lygiagrečios užklausos sujungiamos į vieną.
- **Patikra prieš publikavimą** (`backend/validation.py`): WARNING – tik užrašoma; ERROR – to šaltinio
  nauji duomenys neįrašomi į istoriją, puslapiai naudoja paskutinius gerus (pvz. neperskaitytas traumų
  sąrašas pakeičiamas traumų žurnalo duomenimis), svetainė publikuojama kaip „degraded“; FATAL –
  niekas neįrašoma ir nepublikuojama, internete lieka ankstesnė svetainė. Lyginama su protingomis
  ribomis ir su paskutiniu sėkmingu to paties sezono paleidimu.
- **Svetainė statoma `site.new/`** ir tik sėkmės atveju pakeičia `site/`.
- **`site/api/health.json`** (ir `var/health.json`): būsena (`healthy` / `degraded` / `failed`),
  paskutinio bandymo ir paskutinio sėkmingo atnaujinimo laikas, trukmė, užklausų ir klaidų skaičiai,
  žaidėjų ir puslapių skaičiai, patikros pastabos, kiekvieno šaltinio būsena.

## Analitika

`backend/analytics/metrics.py` – kiekviena formulė vienoje vietoje, kaip gryna funkcija nuo sezono
duomenų (`dataset.py`). Iš jų skaičiuoja ir „Sezono rekordų“ puslapis (efektyvumas, tvarkaraščio
sunkumas, serijos), ir perėjimų ROI, ir naujas `api/league/<id>/analytics.<lt|en>.json` (svetainėje dar
nerodomas), kuris skaičiuojamas iš išsaugotos istorijos (SQLite indekso):

- „visi prieš visus“ rekordas, tikėtinos pergalės ir sėkmės indeksas (tikros pergalės − tikėtinos);
- efektyvumas ir dėl sudėties prarasti taškai, kapitono pasirinkimo nuostolis;
- perėjimų grynasis rezultatas, drafto vertė (pasirinkimo numeris − vieta pagal sezono taškus);
- tvarkaraščio sunkumas, varžovysčių istorija, serijos, rekordai.

Tik turai su išsaugotomis sudėtimis įeina į sudėčių metrikas; trūkstami išvardijami `basedOn`, niekas
neišgalvojama. Testas tikrina, kad istorija ir gyvi duomenys duoda tuos pačius skaičius.

## Mac ir telefonas kartu

`publish.sh` gali leisti ir Mac (kas 15 min.), ir telefonas. Kad jie vienas kitam nepakenktų:

- **Užraktas GitHub'e** – `publish-lock` šaka, užimama atomiškai (`git push --force-with-lease`:
  tik jei jos nėra arba joje vis dar tas pats pasibaigęs užraktas). Užrakto įraše – kas jį laiko ir
  iki kada (10 min.). Kitas įrenginys, radęs galiojantį užraktą, praleidžia paleidimą nieko
  nesiųsdamas; pasibaigusį užraktą perima. Baigus užraktas ištrinamas.
- Prieš kiekvieną `push` tikrinama, ar užraktas vis dar savas; `gh-pages` siunčiamas su „lease“ ant
  pradžioje matyto commit'o, todėl senesnis rezultatas niekada nepakeis naujesnio.
- `data/` failai sujungiami pagal turinį (`tools/merge_history.py`, `.gitattributes`), o nebaigtas
  rebase iš ankstesnio paleidimo sutvarkomas – likęs neišsiųstas commit'as nebeužstrigdo.
- Jei patikra nepraeina, į `gh-pages` įkeliamas tik `api/health.json`, o svetainė lieka ankstesnė.

## Testai

```bash
python3 -m pip install --user pytest   # tik kartą, tik kūrimui (telefonui nereikia)
python3 -m pytest
```

- `tests/fixtures/recording-r1.json.gz` – visi BasketNews / traumų sąrašo atsakymai iš vieno
  tikro paleidimo. `tools/replay_export.py replay` iš jų be interneto perstato visą svetainę
  (laikrodis užšaldomas įrašymo akimirkai), o `tests/test_golden.py` tikrina, kad kiekvienas
  failas liktų bitas į bitą toks pat (`tests/golden/recording-r1.sha256`).
- Naują įrašą padaro `python3 tools/replay_export.py record var/recordings/<vardas>.json.gz`
  (repozitorijos nekeičia), dvi versijas palygina `python3 tools/compare_site.py A B`.
- `DRY_RUN=1 ./publish.sh` – viskas kaip įprastai, tik nieko nekeičia git'e ir nieko neįkelia.
- `python3 tools/check_names.py` – neapibrėžti vardai ir nenaudojami importai (be papildomų bibliotekų).
