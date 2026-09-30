"use strict";

const app = document.getElementById("app");
const switcher = document.getElementById("league-switch");
const langSwitch = document.getElementById("lang-switch");
const modal = document.getElementById("player-modal");
const modalBody = document.getElementById("player-modal-body");
// The GitHub Pages copy (export.py) has no server: pages come from pre-built JSON files
// that a scheduled job refreshes every ~15 minutes.
const STATIC = !!document.querySelector('meta[name="ft-static"]');
const REFRESH_LIVE_MS = STATIC ? 5 * 60_000 : 60_000;

let leaguesCache = null;
let refreshTimer = null;
let renderToken = 0;
let modalToken = 0;
let animateView = false;
let dataTime = null;  // when the static files were generated
let LEAGUE_AVG = null;  // per-game league averages for the stat tooltips (latest payload that had them)

// ------------------------------------------------------------------ i18n

const I18N = {
  lt: {
    brand: "Fantasy tracker",
    skip: "Pereiti prie turinio",
    themeLight: "Šviesi tema",
    themeDark: "Tamsi tema",
    allLeagues: "Visos lygos",
    myLeagues: "Mano lygos",
    pickLeague: "Pasirink lygą, kad matytum turnyrinę lentelę.",
    leader: "Lyderis",
    teamsN: "{n} komandos",
    seasonNotStartedShort: "sezonas dar neprasidėjo",
    playedN: "sužaista {n} tur.",
    addLeague: "Pridėti lygą",
    add: "Pridėti",
    checking: "Tikrinama…",
    remove: "Pašalinti",
    removeLeague: "Pašalinti lygą",
    confirmRemove: "Pašalinti šią lygą iš sąrašo?",
    ptsShort: "tšk.",
    round: "{n} turas",
    roundShort: "{n} t.",
    tabStandings: "Lentelė",
    tabMatchups: "Mačai",
    tabRounds: "Turai",
    tabRecords: "Sezono rekordai",
    tabFA: "Laisvieji agentai",
    tabGames: "Rungtynių statistika",
    tabPlayers: "Visi žaidėjai",
    free: "Laisvas",
    ownerCol: "Savininkas",
    ownerAll: "Visi savininkai",
    ownerOwned: "Tik užimti",
    ownerFree: "Tik laisvi",
    statusSortTitle: "Rikiuoti pagal traumos sunkumą",
    allNote: "Statistika: sezono vidurkiai (metimai: taiklumo %). Paspausk ant stulpelio pavadinimo, kad surikiuotum. Traumos iš ",
    viewBasic: "Statistika",
    viewAdv: "Pažangi statistika",
    advListNote: "Pažangi sezono statistika iš {link}. Spalva: palyginimas su žaidėjais, vidutiniškai žaidžiančiais bent {m} min. – žalia: geriausias ketvirtis, raudona: prasčiausias. ↓ – mažiau yra geriau. Užvedus pelę ant stulpelio pavadinimo – paaiškinimas.",
    advListNone: "BasketNews pažangios statistikos šiam žaidėjui neturi",
    advRegular: "Tik žaidžiantys {m}+ min.",
    combinedNote: "komanda šį turą žaidė dukart, statistika sudėta",
    noBoxYet: "Statistikos dar nėra.",
    previewReady: "Apžvalga",
    pvRecord: "Balansas",
    pvAvg: "Vid. taškai",
    pvAvgTip: "Vidutiniškai įmesta : praleista per rungtynes šį sezoną",
    pvLast: "Paskutinės",
    pvKey: "Pagrindiniai žaidėjai",
    pvLine: "{p} tšk. · {r} atk. · {a} rp.",
    pvInjuries: "Traumos",
    pvNoInjuries: "Traumų sąraše nėra.",
    pvNoStats: "Šį sezoną dar nežaidė.",
    pvNotes: "Į ką atkreipti dėmesį",
    pvTeamStats: "Komandų statistika (vieta tarp {n})",
    pvStrengths: "Stiprybės",
    pvStrWeak: "Stiprybės ir silpnybės",
    pvWeaknesses: "Silpnybės",
    teamStat: {
      ortg: ["Puolimo reitingas", "Įmesti taškai per 100 atakų"],
      drtg: ["Gynybos reitingas", "Praleisti taškai per 100 atakų; mažiau yra geriau"],
      pace: ["Tempas", "Atakų skaičius per rungtynes"],
      pts: ["Taškai", "Įmesti taškai per rungtynes"],
      ptsAgainst: ["Praleista taškų", "Praleisti taškai per rungtynes"],
      ts: ["TS%", "Tikrasis metimų taiklumas: dvitaškiai, tritaškiai ir baudos kartu"],
      p3: ["Tritaškiai", "Tritaškių taiklumas"],
      p3Against: ["Varžovų tritaškiai", "Kiek taikliai prieš juos meta varžovai"],
      oreb: ["Atk. kam. puolime", "Kokią dalį galimų kamuolių atkovoja puolime"],
      dreb: ["Atk. kam. gynyboje", "Kokią dalį galimų kamuolių atkovoja gynyboje"],
      ast: ["Rez. perdavimai", "Kokia dalis pataikymų po rezultatyvaus perdavimo"],
      tov: ["Klaidos", "Kokia atakų dalis baigiasi klaida; mažiau yra geriau"],
    },
    tgTitle: "Rungtynės, kuriose komanda turi daugiausia žaidėjų",
    tgPlayers: "žaidėjai",
    tgInactive: "+{n} neregistr.",
    gameCanceled: "Atšauktos",
    gameFinal: "Baigtos",
    ownedInGame: "{n} lygos žaid.",
    topFp: "Geriausias",
    expandAll: "Išskleisti visas",
    collapseAll: "Suskleisti",
    noGames: "Šį turą rungtynių nėra.",
    gamesNote: "Paspausk ant rungtynių, kad pamatytum kiekvieno žaidėjo statistiką. Pilkai pažymėti laisvieji agentai.",
    advTitle: "Pažangi statistika",
    advNote: "Šaltinis: {link}. #N yra vieta tarp {n} žaidėjų, o juosta rodo procentilį.",
    advLink: "BasketNews advanced stats",
    advNone: "Pažangios statistikos šiam žaidėjui dar nėra.",
    advInfo: "Kas tai?",
    lvl: { high: "Aukštas", avg: "Vidutinis", low: "Žemas" },
    ctxAvg: "Lygos vidurkis",
    ctxHigh: "Aukštas nuo",
    ctxLow: "Žemas iki",
    ctxHighLower: "Aukštas iki",
    ctxLowLower: "Žemas nuo",
    ctxNote: "Lyginama su žaidėjais, kurie vidutiniškai žaidžia bent {m} min.",
    lowerBetter: "Šios metrikos mažesnė reikšmė yra geresnė.",
    tabDraft: "Draftas",
    projTip: "Prognozė: dabartiniai taškai + likusių žaidėjų laukiami taškai. Procentai: pergalės tikimybė.",
    effTitle: "Vadybininkų efektyvumas",
    effPoints: "Taškai",
    effMax: "Maksimalūs",
    effPct: "Efektyvumas",
    effLost: "Prarasta",
    effTip: "Surinkti taškai / taškai, kuriuos būtų davusi geriausia įmanoma tų pačių žaidėjų sudėtis",
    effNote: "Skaičiuojama turams, kurių sudėtys išsaugotos. 100% reiškia, kad kiekvieną turą buvo pasirinkta geriausia įmanoma sudėtis ir kapitonas.",
    sosTitle: "Tvarkaraščio sunkumas",
    sosFaced: "Iki šiol",
    sosFacedTip: "Buvusių varžovų vidutiniai taškai per turą šį sezoną; #1 yra sunkiausias",
    sosAgainst: "Prieš tave",
    sosAgainstTip: "Kiek vidutiniškai surinko varžovai būtent prieš šią komandą",
    sosNext: "Kiti 5 turai",
    sosNextTip: "Artimiausių 5 varžovų vidutiniai taškai per turą; #1 yra sunkiausias",
    sosRest: "Likęs sezonas",
    sosRestTip: "Visų likusių varžovų vidutiniai taškai per turą; #1 yra sunkiausias",
    sosNote: "Varžovo stiprumas yra jo vidutiniai taškai per turą šį sezoną. #1 reiškia sunkiausią tvarkaraštį.",
    roiHeadTip: "Kiek taškų davė perėjimas: atėjusių žaidėjų taškai nuo perėjimo minus kiek būtų surinkę išleisti",
    roiTip: "Atėję surinko {a} FP, išleisti {b} FP ({n} tur.)",
    roiPer100: "{v} FP už 100 kreditų",
    splitLabel: "Rodyti statistiką",
    splitAll: "Viso",
    splitHome: "Namuose",
    splitAway: "Išvykoje",
    splitNoHome: "Namuose šį sezoną dar nežaidė.",
    splitNoAway: "Išvykoje šį sezoną dar nežaidė.",
    diffVsAway: "Mažesni skaičiai: skirtumas nuo rungtynių išvykoje.",
    diffVsHome: "Mažesni skaičiai: skirtumas nuo rungtynių namuose.",
    lastTip: "Vidutiniai FP per paskutinius {n} sužaistus turus",
    ddTip: "Rungtynės, kuriose bent du iš šių rodiklių siekė 10: taškai, atkovoti kamuoliai, rezultatyvūs perdavimai, perimti kamuoliai, blokai",
    defTitle: "FP prieš gynybas",
    defSub: "pagal BasketNews gynybos reitingą, {n} komandų",
    defTop: "Prieš {n} geriausių gynybų",
    defBottom: "Prieš {n} prasčiausių gynybų",
    defGames: "{n} rungt.",
    defTip: "Vidutiniai fantasy taškai rungtynėse prieš komandas, kurių gynyba (praleisti taškai per 100 atakų) šiuo metu yra {a}–{b} vietoje. Skaičiuojami turai su vienomis rungtynėmis.",
    defNone: "Dar nežaidė prieš šias komandas",
    defDiffNote: "Mažesni skaičiai: skirtumas nuo kitos grupės.",
    defShow: "Rodyti gynybas",
    defHide: "Slėpti gynybas",
    defListTop: "{n} geriausių gynybų",
    defListBottom: "{n} prasčiausių gynybų",
    defRating: "Gynybos reitingas: {v} praleistų taškų per 100 atakų",
    defFaced: "Šio žaidėjo vid. FP prieš juos ({n} rungt.)",
    draftAwardsTitle: "Draftas ir perėjimai",
    rebSplitHead: "G/P",
    rebSplitTip: "Mažesni skaičiai: gynyboje / puolime",
    dayShort: "{n} diena",
    dayTip: "Žaidžia {n}-ąją turo dieną",
    tabInjuries: "Traumos",
    newsBad: "Bloga žinia komandai {t}:",
    newsGood: "Gera žinia komandai {t}:",
    newsOwnedOnly: "Visų lygos komandų žaidėjai",
    newsAll: "Visi žaidėjai",
    newsEmptyTeam: "Šios komandos žaidėjų traumų sąraše nebuvo.",
    newsCount: "{n} įrašai",
    newsEmpty: "Traumų sąraše pokyčių dar nebuvo.",
    newsNote: "Pagal BasketNews traumų sąrašą ({link}). Pokyčiai tikrinami kas 15 min., laikas rodo, kada pokytis pastebėtas.",
    injuryReportLink: "traumų sąrašas",
    avgTipGame: "Lygos vidurkis: {v} per rungtynes",
    avgTipPct: "Lygos vidurkis: {v}",
    avgTipPlain: "Lygos vidurkis: {v}",
    avgTipWho: "(žaidėjai, žaidžiantys bent {m} min.)",
    tabTransfers: "Perėjimai",
    avgRound: "vid. {v} tšk. per turą",
    avgRoundTip: "Vidutiniškai surinkta taškų per {n} žaistus turus",
    shootingTitle: "Metimai per sezoną",
    shootingSub: "{n} rungt.",
    shotFg: "Iš žaidimo",
    shot2: "Dvitaškiai",
    shot3: "Tritaškiai",
    shotFt: "Baudos",
    draftEmpty: "Šios lygos drafto duomenų nėra.",
    draftDate: "Draftas {d}",
    draftOrderType: { reverse_snake: "Atvirkštinė gyvatėlė", snake: "Gyvatėlė", default: "Gyvatėlė", straight: "Ta pati tvarka kiekviename rate" },
    draftPicksN: "{n} pasirinkimai, {r} ratų",
    draftRound: "{n} ratas",
    allTeams: "Visos komandos",
    player: "Žaidėjas",
    avgFpShort: "Vid. FP",
    draftNow: "Dabar",
    draftKept: "Vis dar komandoje",
    draftReleased: "Laisvasis agentas",
    draftNote: "Pirmas skaičius yra bendras pasirinkimo numeris, antras rodo ratą ir eilę jame. „Dabar“ rodo, kur žaidėjas yra šiandien.",
    creditsShort: "kr.",
    creditsShortHead: "Kreditai",
    moveTrade: "Mainai",
    moveFA: "Laisvasis agentas",
    tradeWith: "mainai su {t}",
    windowClosed: "Perėjimų langas uždarytas iki",
    nextProcessing: "Perėjimai prieš {n} turą bus įvykdyti",
    creditsTitle: "Kreditai",
    creditsStart: "pradžioje po {n}",
    creditsLeft: "Liko",
    creditsSpent: "Išleista",
    signings: "Laisvieji agentai",
    trades: "Mainai",
    bidsTitle: "Statymai prieš {n} turą",
    bidsCount: "Statymų",
    bidsTop: "Didžiausias",
    noMoves: "Šį sezoną perėjimų dar nebuvo.",
    movesTitle: "Įvykę perėjimai",
    movesBefore: "Prieš {n} turą",
    noMovesRound: "Prieš šį turą perėjimų nebuvo.",
    moveType: "Tipas",
    moveIn: "Atėjo",
    moveOut: "Išėjo",
    moveWhen: "Laikas",
    relNow: "dabar",
    relIn: "po {s}",
    relAgo: "prieš {s}",
    relH: "{h} val.",
    relM: "{m} min.",
    relHM: "{h} val. {m} min.",
    awardInfo: "Kaip skaičiuojama?",
    rankTip: "Vieta tarp {n} žaidėjų, 1 yra geriausias",
    proballers: "Karjera Proballers",
    proballersTitle: "Atidaro žaidėjo karjeros statistiką Proballers svetainėje",
    partialLineups: "{r} turo sudėčių neturime komandoms: {teams}. Jų kapitonų, MVP ir „prarasta dėl sudėties“ šiame ture neskaičiuojame.",
    formatH2H: "Head-to-head",
    formatClassic: "Pagal taškus",
    liveRound: "Vyksta {r}",
    nextRound: "Kitas: {r} iš {total}",
    updated: "Atnaujinta {t}",
    team: "Komanda",
    colW: "P",
    colL: "Pr",
    colT: "L",
    points: "Taškai",
    thisRound: "Šio turo",
    seasonNotStarted: "Sezonas dar neprasidėjo",
    afterRound: "Po {n} turo",
    clickTeam: "Paspausk ant komandos pavadinimo, kad pamatytum jos sudėtį.",
    leagueAvg: "Lygos vidurkis",
    noMatchups: "Šiam turui mačų nėra.",
    live: "Vyksta",
    notPlayedYet: "Dar nežaista",
    roundPoints: "Turo taškai",
    total: "Iš viso",
    position: "Vieta",
    roundAwards: "{n} turo apdovanojimai",
    oscars: "Sezono „Oskarai“",
    afterDone: "(po {n} baigto turo)",
    seasonRecords: "Sezono rekordai",
    upTo: "(iki {n} turo)",
    formTitle: "Forma ir serijos",
    last5: "Paskutiniai 5",
    streak: "Serija",
    longestW: "Ilg. P",
    longestWTitle: "Ilgiausia pergalių serija",
    longestL: "Ilg. Pr",
    longestLTitle: "Ilgiausia pralaimėjimų serija",
    avg: "Vid.",
    best: "Geriausias",
    worst: "Blogiausias",
    noRoundsYet: "Dar nesužaistas nė vienas turas. Apdovanojimai atsiras po pirmojo turo.",
    missingLineups: "Neturime {r} turo sudėčių, todėl kapitonų, MVP ir „prarasta dėl sudėties“ skaičiavimuose tie turai neįtraukti.",
    recordsNote: "Rodomi tik jau pasibaigę turai. „Prarasta dėl sudėties“ rodo, kiek taškų komanda būtų surinkusi daugiau, jei tų pačių aktyvių žaidėjų penketą, kapitoną ir 6-ą žaidėją būtų išdėsčiusi optimaliai.",
    search: "Ieškoti žaidėjo…",
    allPositions: "Visos pozicijos",
    guards: "Gynėjai",
    forwards: "Puolėjai",
    centers: "Centrai",
    allClubs: "Visi klubai",
    healthyOnly: "Tik sveiki",
    nPlayers: "{n} žaidėjai",
    player: "Žaidėjas",
    status: "Būklė",
    avgFp: "Vid. FP",
    avgFpTitle: "Vidutiniai fantasy taškai",
    lastFpTitle: "Fantasy taškai paskutiniame ture",
    gp: "RUNG",
    gpTitle: "Sužaistos rungtynės",
    faNote: "Statistika: sezono vidurkiai (metimai: taiklumo %). Paspausk ant stulpelio pavadinimo, kad surikiuotum. Laisvieji agentai yra visi {total} {comp} žaidėjai, išskyrus {owned} esančius lygos komandų sudėtyse. Traumos iš ",
    injuryReport: "BasketNews traumų sąrašo",
    faNoteEnd: ". Paspausk ant žaidėjo, kad matytum daugiau.",
    noPlayers: "Nėra žaidėjų pagal filtrus",
    addFilter: "+ Statistikos filtras",
    clearFilters: "Išvalyti filtrus",
    from: "nuo",
    to: "iki",
    placeOf: "{p} vieta iš {n}",
    current: "(dabartinis)",
    toCurrent: "Į dabartinį turą",
    prevRound: "Ankstesnis turas",
    nextRoundAria: "Kitas turas",
    stateFinished: "Baigtas",
    stateLive: "Vyksta",
    stateUpcoming: "Dar neprasidėjo",
    firstGame: "Pirmos rungtynės {t}",
    won: "Laimėjo",
    lost: "Pralaimėjo",
    tie: "Lygiosios",
    roundRank: "Turo vieta",
    posAfter: "Vieta po turo",
    recordAfter: "Rekordas po turo",
    optimal: "Optimali sudėtis",
    lostToLineup: "Prarasta dėl sudėties",
    leftToPlay: "Liko žaisti",
    lineupTitle: "Sudėtis · {r}",
    formation: "formacija {f}",
    lineupNA: "Sudėtis nepasiekiama.",
    statsAvgNote: "Statistika: sezono vidurkiai (metimai: taiklumo %).",
    statsRoundNote: "Statistika: {n} turo (metimai: pataikyta/mesta).",
    multNote: "Geltonai pažymėti taškai komandai: penketas ×1, kapitonas ×2, 6-as žaidėjas ×1, B2-B5 ×0.5, neregistruoti ×0.",
    notRegistered: "Neregistruoti",
    teamPts: "Tšk",
    teamPtsTitle: "Taškai komandai",
    fp: "FP",
    games: "Rungtynės",
    avgTitle: "Sezono vidurkis (FP)",
    noGame: "Nežaidžia",
    canceled: "atšauktos",
    dnpTitle: "Nežaidė",
    captain: "Kapitonas",
    chartPos: "Vieta lentelėje pagal turus",
    chartPts: "Taškai per turą",
    posValue: "{v} vieta",
    posSeries: "Vieta",
    seasonLog: "Sezono eiga",
    opponent: "Varžovas",
    score: "Rezultatas",
    next: "Kitas",
    teamBadge: "Komanda: {t}",
    freeAgent: "Laisvasis agentas",
    expectedReturn: "Numatomas grįžimas: {r}",
    healthy: "Sveikas",
    notOnReport: "Traumų sąraše nėra",
    injuryHistory: "Traumų istorija",
    kindInjury: "Trauma",
    kindOther: "Kita",
    noReason: "Priežastis nenurodyta",
    now: "dabar",
    daysShort: "d.",
    ongoing: "(tęsiasi)",
    missed: "praleido: {r}",
    historyNote: "Istorija kaupiama automatiškai iš {link} kol veikia programa, o praleisti turai nustatomi iš rungtynių statistikos.",
    rounds: "Turai",
    didNotPlay: "Nežaidė",
    fpChartOpen: "FP grafikas per turus",
    predTitle: "Prognozė",
    predWins: "{t} turėtų laimėti",
    predShort: "Prognozė: {t} {p}%",
    predWhy: "Labiausiai lemia",
    predNote: "Modelis: komandų reitingas (atnaujinamas po kiekvienų rungtynių pagal rezultatą), namų aikštė ir komandos puolimas prieš varžovo gynybą (taškai per 100 atakų). Patikrintas su praėjusiais sezonais naudojant tik tai, kas buvo žinoma prieš rungtynes: 2025–26 sezone atspėjo {a}% rungtynių (namų komanda laimėjo 63,7%), 2023–24 ir 2024–25 – apie 67%.",
    ddShort: "DD",
    fpChartTitle: "Fantasy taškai per turus",
    fpSeasonAvg: "Sezono vidurkis",
    fpBest: "Geriausias turas",
    fpWorst: "Prasčiausias turas",
    fpPlayed: "Sužaista turų",
    fpChartNote: "Tarpai linijoje – turai, kurių žaidėjas nežaidė arba jo komanda neturėjo rungtynių. Užvesk pelę ant grafiko: turas, varžovas ir taškai.",
    fpChartNone: "Šį sezoną žaidėjas dar nežaidė.",
    teamNoGame: "Komanda nežaidė",
    noRoundsPlayed: "Dar nėra sužaistų turų",
    loading: "Kraunama…",
    close: "Uždaryti",
    error: "Klaida {s}",
    staticMissing: "Šių duomenų dar nėra. Svetainė atsinaujina kas 15 minučių.",
    pos: { guard: "Gynėjas", forward: "Puolėjas", center: "Centras" },
    tiles: { avgFp: "Vid. FP", gp: "Rungt.", min: "Min.", pts: "Tšk.", reb: "Atk. kam.", ast: "Rez. perd.", stl: "Perimti", blk: "Blokai", eff: "NB", last3: "Pask. 3 FP", last5: "Pask. 5 FP", dd: "Dvigubi dubliai" },
    resShort: { W: "P", L: "Pr", T: "L" },
  },
  en: {
    brand: "Fantasy tracker",
    skip: "Skip to content",
    themeLight: "Light theme",
    themeDark: "Dark theme",
    allLeagues: "All leagues",
    myLeagues: "My leagues",
    pickLeague: "Pick a league to see its standings.",
    leader: "Leader",
    teamsN: "{n} teams",
    seasonNotStartedShort: "season not started",
    playedN: "{n} rounds played",
    playedOne: "1 round played",
    addLeague: "Add league",
    add: "Add",
    checking: "Checking…",
    remove: "Remove",
    removeLeague: "Remove league",
    confirmRemove: "Remove this league from the list?",
    ptsShort: "pts",
    round: "Round {n}",
    roundShort: "R{n}",
    tabStandings: "Standings",
    tabMatchups: "Matchups",
    tabRounds: "Rounds",
    tabRecords: "Season records",
    tabFA: "Free agents",
    tabGames: "Box scores",
    tabPlayers: "All players",
    free: "Free",
    ownerCol: "Owner",
    ownerAll: "All owners",
    ownerOwned: "Owned only",
    ownerFree: "Free only",
    statusSortTitle: "Sort by injury severity",
    allNote: "Stats are season averages (shooting as %). Click a column name to sort. Injuries come from the ",
    viewBasic: "Stats",
    viewAdv: "Advanced stats",
    advListNote: "Season advanced stats from {link}. Colour compares with players averaging at least {m} min: green is the best quarter, red the worst. ↓ means lower is better. Hover a column name for what it measures.",
    advListNone: "BasketNews has no advanced stats for this player",
    advRegular: "Only players averaging {m}+ min",
    combinedNote: "played twice this round, stats combined",
    noBoxYet: "No stats yet.",
    previewReady: "Preview",
    pvRecord: "Record",
    pvAvg: "Avg score",
    pvAvgTip: "Average points scored : allowed per game this season",
    pvLast: "Last games",
    pvKey: "Key players",
    pvLine: "{p} pts · {r} reb · {a} ast",
    pvInjuries: "Injuries",
    pvNoInjuries: "Nobody on the injury report.",
    pvNoStats: "Has not played yet this season.",
    pvNotes: "Things to watch",
    pvTeamStats: "Team stats (rank among {n})",
    pvStrengths: "Strengths",
    pvStrWeak: "Strengths and weaknesses",
    pvWeaknesses: "Weaknesses",
    teamStat: {
      ortg: ["Offensive rating", "Points scored per 100 possessions"],
      drtg: ["Defensive rating", "Points allowed per 100 possessions; lower is better"],
      pace: ["Pace", "Possessions per game"],
      pts: ["Points", "Points scored per game"],
      ptsAgainst: ["Points allowed", "Points allowed per game"],
      ts: ["TS%", "True shooting: twos, threes and free throws together"],
      p3: ["3-point %", "Three-point accuracy"],
      p3Against: ["Opponent 3-point %", "How well opponents shoot threes against them"],
      oreb: ["Offensive rebounds", "Share of available offensive rebounds they get"],
      dreb: ["Defensive rebounds", "Share of available defensive rebounds they get"],
      ast: ["Assists", "Share of made baskets that were assisted"],
      tov: ["Turnovers", "Share of possessions ending in a turnover; lower is better"],
    },
    tgTitle: "Games with the most of this team's players",
    tgPlayers: "players",
    tgInactive: "+{n} not registered",
    gameCanceled: "Canceled",
    gameFinal: "Final",
    ownedInGame: "{n} league players",
    topFp: "Top",
    expandAll: "Expand all",
    collapseAll: "Collapse",
    noGames: "No games this round.",
    gamesNote: "Click a game to see every player's stats. Grey players are free agents.",
    advTitle: "Advanced stats",
    advNote: "Source: {link}. #N is the rank among {n} players, the bar is the percentile.",
    advLink: "BasketNews advanced stats",
    advNone: "No advanced stats for this player yet.",
    advInfo: "What is this?",
    lvl: { high: "High", avg: "Average", low: "Low" },
    ctxAvg: "League average",
    ctxHigh: "High from",
    ctxLow: "Low up to",
    ctxHighLower: "High up to",
    ctxLowLower: "Low from",
    ctxNote: "Compared with players averaging at least {m} minutes.",
    lowerBetter: "For this metric a lower value is better.",
    tabDraft: "Draft",
    projTip: "Projection: points so far + expected points of the players still to play. Percentages: chance to win.",
    effTitle: "Manager efficiency",
    effPoints: "Points",
    effMax: "Maximum",
    effPct: "Efficiency",
    effLost: "Lost",
    effTip: "Points scored / points the best possible lineup of the same players would have scored",
    effNote: "Counts rounds with saved lineups. 100% means the best possible lineup and captain every round.",
    sosTitle: "Strength of schedule",
    sosFaced: "So far",
    sosFacedTip: "Past opponents' average points per round this season; #1 is the hardest",
    sosAgainst: "Against you",
    sosAgainstTip: "What opponents scored against this team on average",
    sosNext: "Next 5 rounds",
    sosNextTip: "Next 5 opponents' average points per round; #1 is the hardest",
    sosRest: "Rest of season",
    sosRestTip: "All remaining opponents' average points per round; #1 is the hardest",
    sosNote: "An opponent's strength is their average points per round this season. #1 means the toughest schedule.",
    roiHeadTip: "Points the move has brought: incoming players' points since, minus what the players let go scored",
    roiTip: "Incoming scored {a} FP, outgoing {b} FP ({n} rounds)",
    roiPer100: "{v} FP per 100 credits",
    splitLabel: "Show stats for",
    splitAll: "Total",
    splitHome: "Home",
    splitAway: "Away",
    splitNoHome: "Has not played at home yet this season.",
    splitNoAway: "Has not played away yet this season.",
    diffVsAway: "Small numbers: difference from away games.",
    diffVsHome: "Small numbers: difference from home games.",
    lastTip: "Average FP over the last {n} rounds played",
    ddTip: "Games with 10 or more in at least two of: points, rebounds, assists, steals, blocks",
    defTitle: "FP against defenses",
    defSub: "by BasketNews defensive rating, {n} teams",
    defTop: "Vs the {n} best defenses",
    defBottom: "Vs the {n} worst defenses",
    defGames: "{n} gm",
    defTip: "Average fantasy points in games against teams whose defense (points allowed per 100 possessions) currently ranks {a}-{b}. Only rounds with one game count.",
    defNone: "No games against these teams yet",
    defDiffNote: "Small numbers: difference from the other group.",
    defShow: "Show defenses",
    defHide: "Hide defenses",
    defListTop: "The {n} best defenses",
    defListBottom: "The {n} worst defenses",
    defRating: "Defensive rating: {v} points allowed per 100 possessions",
    defFaced: "This player's average FP against them ({n} gm)",
    draftAwardsTitle: "Draft and moves",
    rebSplitHead: "D/O",
    rebSplitTip: "Small numbers: defensive / offensive",
    dayShort: "Day {n}",
    dayTip: "Plays on day {n} of the round",
    tabInjuries: "Injuries",
    newsBad: "Bad news for {t}:",
    newsGood: "Good news for {t}:",
    newsOwnedOnly: "Players on any league team",
    newsAll: "All players",
    newsEmptyTeam: "No players of this team have been on the injury report.",
    newsCount: "{n} updates",
    newsEmpty: "No injury report changes yet.",
    newsNote: "From the BasketNews injury report ({link}). Checked every 15 minutes; the time shows when a change was spotted.",
    injuryReportLink: "injury report",
    avgTipGame: "League average: {v} per game",
    avgTipPct: "League average: {v}",
    avgTipPlain: "League average: {v}",
    avgTipWho: "(players averaging {m}+ minutes)",
    tabTransfers: "Transfers",
    avgRound: "avg {v} pts per round",
    avgRoundTip: "Average points over {n} rounds played",
    shootingTitle: "Season shooting",
    shootingSub: "{n} games",
    shotFg: "Field goals",
    shot2: "2-point field goals",
    shot3: "3-point field goals",
    shotFt: "Free throws",
    draftEmpty: "No draft data for this league.",
    draftDate: "Draft {d}",
    draftOrderType: { reverse_snake: "Reverse snake", snake: "Snake", default: "Snake", straight: "Same order every round" },
    draftPicksN: "{n} picks, {r} rounds",
    draftRound: "Draft round {n}",
    allTeams: "All teams",
    player: "Player",
    avgFpShort: "Avg FP",
    draftNow: "Now",
    draftKept: "Still on the team",
    draftReleased: "Free agent",
    draftNote: "The first number is the overall pick, the second the draft round and pick within it. \"Now\" shows where the player is today.",
    creditsShort: "cr.",
    creditsShortHead: "Credits",
    moveTrade: "Trade",
    moveFA: "Free agent",
    tradeWith: "trade with {t}",
    windowClosed: "Transfer window closed until",
    nextProcessing: "Transfers before round {n} are processed",
    creditsTitle: "Credits",
    creditsStart: "{n} each at the start",
    creditsLeft: "Left",
    creditsSpent: "Spent",
    signings: "Free agents",
    trades: "Trades",
    bidsTitle: "Bids before round {n}",
    bidsCount: "Bids",
    bidsTop: "Highest",
    noMoves: "No transfers this season yet.",
    movesTitle: "Completed transfers",
    movesBefore: "Before round {n}",
    noMovesRound: "No transfers before this round.",
    moveType: "Type",
    moveIn: "In",
    moveOut: "Out",
    moveWhen: "When",
    relNow: "now",
    relIn: "in {s}",
    relAgo: "{s} ago",
    relH: "{h} h",
    relM: "{m} min",
    relHM: "{h} h {m} min",
    awardInfo: "How is it worked out?",
    rankTip: "Rank among {n} players, 1 is the best",
    proballers: "Career on Proballers",
    proballersTitle: "Opens the player's career stats on Proballers",
    partialLineups: "Round {r} lineups are missing for: {teams}. Their captain, MVP and points-lost numbers are left out for that round.",
    formatH2H: "Head-to-head",
    formatClassic: "Total points",
    liveRound: "{r} live",
    nextRound: "Next: {r} of {total}",
    updated: "Updated {t}",
    team: "Team",
    colW: "W",
    colL: "L",
    colT: "T",
    points: "Points",
    thisRound: "This round",
    seasonNotStarted: "Season has not started",
    afterRound: "After round {n}",
    clickTeam: "Click a team name to see its roster.",
    leagueAvg: "League average",
    noMatchups: "No matchups this round.",
    live: "Live",
    notPlayedYet: "Not played yet",
    roundPoints: "Round points",
    total: "Total",
    position: "Position",
    roundAwards: "Round {n} awards",
    oscars: "Season “Oscars”",
    afterDone: "(after {n} completed rounds)",
    seasonRecords: "Season records",
    upTo: "(through round {n})",
    formTitle: "Form & streaks",
    last5: "Last 5",
    streak: "Streak",
    longestW: "Best W",
    longestWTitle: "Longest winning streak",
    longestL: "Worst L",
    longestLTitle: "Longest losing streak",
    avg: "Avg",
    best: "Best",
    worst: "Worst",
    noRoundsYet: "No round finished yet. Awards appear after the first round.",
    missingLineups: "Lineups for round {r} are missing, so captain, MVP and points-lost stats skip those rounds.",
    recordsNote: "Only finished rounds are shown. “Lost to lineup” is how many more points the team would have scored with the best arrangement of the same active players (starting five, captain and 6th man).",
    search: "Search player…",
    allPositions: "All positions",
    guards: "Guards",
    forwards: "Forwards",
    centers: "Centers",
    allClubs: "All clubs",
    healthyOnly: "Healthy only",
    nPlayers: "{n} players",
    player: "Player",
    status: "Status",
    avgFp: "Avg FP",
    avgFpTitle: "Average fantasy points",
    lastFpTitle: "Fantasy points in the last round",
    gp: "GP",
    gpTitle: "Games played",
    faNote: "Stats are season averages (shooting as %). Click a column name to sort. Free agents are all {total} {comp} players except the {owned} on league rosters. Injuries come from the ",
    injuryReport: "BasketNews injury report",
    faNoteEnd: ". Click a player for details.",
    noPlayers: "No players match the filters",
    addFilter: "+ Stat filter",
    clearFilters: "Clear filters",
    from: "from",
    to: "to",
    placeOf: "{p} of {n}",
    current: "(current)",
    toCurrent: "Go to current round",
    prevRound: "Previous round",
    nextRoundAria: "Next round",
    stateFinished: "Finished",
    stateLive: "Live",
    stateUpcoming: "Not started",
    firstGame: "First game {t}",
    won: "Won",
    lost: "Lost",
    tie: "Tie",
    roundRank: "Round rank",
    posAfter: "Position after round",
    recordAfter: "Record after round",
    optimal: "Optimal lineup",
    lostToLineup: "Lost to lineup",
    leftToPlay: "Left to play",
    lineupTitle: "Lineup · {r}",
    formation: "formation {f}",
    lineupNA: "Lineup not available.",
    statsAvgNote: "Stats are season averages (shooting as %).",
    statsRoundNote: "Stats are for round {n} (shots made/attempted).",
    multNote: "Yellow = points for the team: starting five ×1, captain ×2, 6th man ×1, B2-B5 ×0.5, not registered ×0.",
    notRegistered: "Not registered",
    teamPts: "Pts",
    teamPtsTitle: "Points for the team",
    fp: "FP",
    games: "Games",
    avgTitle: "Season average (FP)",
    noGame: "No game",
    canceled: "canceled",
    dnpTitle: "Did not play",
    captain: "Captain",
    chartPos: "League position by round",
    chartPts: "Points per round",
    posValue: "Position {v}",
    posSeries: "Position",
    seasonLog: "Season log",
    opponent: "Opponent",
    score: "Score",
    next: "Next",
    teamBadge: "Team: {t}",
    freeAgent: "Free agent",
    expectedReturn: "Expected return: {r}",
    healthy: "Healthy",
    notOnReport: "Not on the injury report",
    injuryHistory: "Injury history",
    kindInjury: "Injury",
    kindOther: "Other",
    noReason: "No reason given",
    now: "now",
    daysShort: "days",
    ongoing: "(ongoing)",
    missed: "missed: {r}",
    historyNote: "The history is built automatically from the {link} while the app runs; missed rounds come from game stats.",
    rounds: "Rounds",
    didNotPlay: "Did not play",
    fpChartOpen: "FP chart by round",
    predTitle: "Prediction",
    predWins: "{t} should win",
    predShort: "Pick: {t} {p}%",
    predWhy: "Mostly because of",
    predNote: "Model: team ratings (updated after every game from the result), home court, and each side's offense against the other's defense (points per 100 possessions). Backtested on past seasons with only what was known before each game: it picked {a}% of 2025-26 games right (home teams won 63.7%), about 67% in 2023-24 and 2024-25.",
    ddShort: "DD",
    fpChartTitle: "Fantasy points by round",
    fpSeasonAvg: "Season average",
    fpBest: "Best round",
    fpWorst: "Worst round",
    fpPlayed: "Rounds played",
    fpChartNote: "Gaps in the line are rounds the player did not play or his team had no game. Hover the chart for the round, opponent and points.",
    fpChartNone: "The player has not played this season yet.",
    teamNoGame: "Team did not play",
    noRoundsPlayed: "No rounds played yet",
    loading: "Loading…",
    close: "Close",
    error: "Error {s}",
    staticMissing: "This data is not available yet. The site refreshes every 15 minutes.",
    pos: { guard: "Guard", forward: "Forward", center: "Center" },
    tiles: { avgFp: "Avg FP", gp: "GP", min: "MIN", pts: "PTS", reb: "REB", ast: "AST", stl: "STL", blk: "BLK", eff: "PIR", last3: "Last 3 FP", last5: "Last 5 FP", dd: "Double-doubles" },
    resShort: { W: "W", L: "L", T: "T" },
  },
};

// Box-score columns: [key, LT abbr, LT title, EN abbr, EN title]
const STAT_DEFS = [
  ["min", "MIN", "Minutės", "MIN", "Minutes"],
  ["pts", "TŠK", "Taškai", "PTS", "Points"],
  ["reb", "AK", "Atkovoti kamuoliai", "REB", "Rebounds"],
  ["ast", "RP", "Rezultatyvūs perdavimai", "AST", "Assists"],
  ["stl", "PR", "Perimti kamuoliai", "STL", "Steals"],
  ["blk", "BL", "Blokuoti metimai", "BLK", "Blocks"],
  ["tov", "KL", "Klaidos", "TO", "Turnovers"],
  ["p2", "2T", "Dvitaškiai", "2P", "Two-pointers"],
  ["p3", "3T", "Tritaškiai", "3P", "Three-pointers"],
  ["ft", "BM", "Baudų metimai", "FT", "Free throws"],
  ["eff", "NB", "Naudingumo balas", "PIR", "Performance index rating"],
  ["usg", "USG%", "Naudojimo dažnis (usage rate)", "USG%", "Usage rate"],
];

function readLang() {
  try { return localStorage.getItem("ft-lang") === "en" ? "en" : "lt"; } catch { return "lt"; }
}
let LANG = readLang();

function t(key, vars = {}) {
  const val = I18N[LANG][key] ?? I18N.lt[key] ?? key;
  return typeof val === "string" ? val.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "") : val;
}

function statCols(skip) {
  return STAT_DEFS.filter(([key]) => !skip?.includes(key))
    .map(([key, lta, ltt, ena, ent]) => ({ key, abbr: LANG === "en" ? ena : lta, title: LANG === "en" ? ent : ltt }));
}

const skipBtn = document.getElementById("skip");
skipBtn.addEventListener("click", () => app.focus());

function applyLangChrome() {
  skipBtn.textContent = t("skip");
  document.documentElement.lang = LANG;
  document.title = t("brand");
  document.getElementById("brand-text").textContent = t("brand");
  applyTheme(currentTheme());
  langSwitch.innerHTML = ["lt", "en"].map((l) =>
    `<button type="button" class="lang${l === LANG ? " active" : ""}" data-lang="${l}" aria-pressed="${l === LANG}">${l.toUpperCase()}</button>`).join("");
}

langSwitch.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-lang]");
  if (!btn || btn.dataset.lang === LANG) return;
  LANG = btn.dataset.lang;
  try { localStorage.setItem("ft-lang", LANG); } catch { /* per-browser preference only */ }
  applyLangChrome();
  if (modal.open) modal.close();
  if (chartModal.open) chartModal.close();
  route(true);
});

// ------------------------------------------------------------------ theme (dark / beige light)

const themeBtn = document.getElementById("theme-toggle");
const SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>';

function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function applyTheme(theme) {
  if (theme === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  document.querySelector('meta[name="theme-color"]').content = theme === "light" ? "#ece4d3" : "#0c0f14";
  const label = theme === "light" ? t("themeDark") : t("themeLight");
  themeBtn.innerHTML = theme === "light" ? MOON : SUN;
  themeBtn.setAttribute("aria-label", label);
  themeBtn.dataset.tip = label;
}

themeBtn.addEventListener("click", () => {
  const next = currentTheme() === "light" ? "dark" : "light";
  try { localStorage.setItem("ft-theme", next); } catch { /* per-browser preference only */ }
  applyTheme(next);
});

// ------------------------------------------------------------------ dropdowns

// A native <select> opens the system's grey menu, which cannot be styled. Each
// select.select gets a themed button + listbox; the select stays (hidden) and keeps
// its value and change listeners, so the page code does not change.
let closeOpenMenu = null;  // the open dropdown's close(), so a page change can close it

function enhanceSelect(sel) {
  sel.dataset.dd = "1";
  const wrap = document.createElement("div");
  wrap.className = "dd";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = `${sel.className} dd-btn`;
  btn.setAttribute("aria-haspopup", "listbox");
  btn.setAttribute("aria-expanded", "false");
  if (sel.getAttribute("aria-label")) btn.setAttribute("aria-label", sel.getAttribute("aria-label"));
  const menu = document.createElement("ul");
  menu.className = "dd-menu";
  menu.setAttribute("role", "listbox");
  menu.tabIndex = -1;
  menu.hidden = true;
  sel.replaceWith(wrap);
  wrap.append(sel, btn);

  let active = -1;
  const items = () => [...menu.children];
  const label = () => { btn.textContent = sel.options[sel.selectedIndex]?.text ?? ""; };
  const mark = (i) => {
    active = i;
    items().forEach((li, j) => li.classList.toggle("active", j === i));
    const li = items()[i];
    if (li) { li.scrollIntoView({ block: "nearest" }); menu.setAttribute("aria-activedescendant", li.id); }
  };
  const outside = (e) => { if (!wrap.contains(e.target) && !menu.contains(e.target)) close(false); };
  const onScroll = (e) => { if (!menu.contains(e.target)) close(false); };
  const close = (focusBtn) => {
    if (menu.hidden) return;
    menu.hidden = true;
    menu.remove();
    closeOpenMenu = null;
    btn.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("scroll", onScroll, true);
    window.removeEventListener("resize", onScroll);
    if (focusBtn) btn.focus();
  };
  const open = () => {
    const uid = `dd${Math.random().toString(36).slice(2, 8)}`;
    menu.innerHTML = [...sel.options].map((o, i) =>
      `<li role="option" id="${uid}-${i}" aria-selected="${o.selected}">${esc(o.text)}</li>`).join("");
    // Floats above everything (cards and table scrollers would clip or cover it), placed
    // under the button, or above it when there is no room below.
    (btn.closest("dialog") || document.body).append(menu);
    menu.hidden = false;
    const r = btn.getBoundingClientRect();
    menu.style.minWidth = `${r.width}px`;
    const h = menu.offsetHeight;
    const below = window.innerHeight - r.bottom;
    const top = below < h + 12 && r.top > below ? r.top - h - 6 : r.bottom + 6;
    menu.style.top = `${Math.max(8, top)}px`;
    menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8))}px`;
    btn.setAttribute("aria-expanded", "true");
    mark(Math.max(0, sel.selectedIndex));
    menu.focus({ preventScroll: true });
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    closeOpenMenu?.();
    closeOpenMenu = () => close(false);
  };
  const choose = (i) => {
    close(true);
    if (i < 0 || i === sel.selectedIndex) return;
    sel.selectedIndex = i;
    label();
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  };
  btn.addEventListener("click", () => (menu.hidden ? open() : close(true)));
  btn.addEventListener("keydown", (e) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); open(); }
  });
  menu.addEventListener("keydown", (e) => {
    const n = sel.options.length;
    if (e.key === "ArrowDown") { e.preventDefault(); mark(Math.min(n - 1, active + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); mark(Math.max(0, active - 1)); }
    else if (e.key === "Home") { e.preventDefault(); mark(0); }
    else if (e.key === "End") { e.preventDefault(); mark(n - 1); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(active); }
    else if (e.key === "Escape") { e.preventDefault(); close(true); }
    else if (e.key === "Tab") close(false);
  });
  menu.addEventListener("pointermove", (e) => {
    const li = e.target.closest("li");
    if (li) mark(items().indexOf(li));
  });
  menu.addEventListener("click", (e) => {
    const li = e.target.closest("li");
    if (li) choose(items().indexOf(li));
  });
  sel.addEventListener("change", label);
  label();
}

function enhanceSelects(root) {
  root.querySelectorAll("select.select:not([data-dd])").forEach(enhanceSelect);
}
new MutationObserver(() => enhanceSelects(app)).observe(app, { childList: true, subtree: true });

// ------------------------------------------------------------------ tooltips

const tip = document.createElement("div");
tip.className = "hover-tip";
tip.hidden = true;
document.body.append(tip);

function showTip(el) {
  if (el.hasAttribute("title")) {  // native titles show late; move them to the instant tooltip
    el.dataset.tip = el.getAttribute("title");
    el.removeAttribute("title");
  }
  const host = el.closest("dialog") || document.body;  // the player dialog sits in the top layer
  if (tip.parentNode !== host) host.append(tip);
  tip.textContent = el.dataset.tip;
  tip.hidden = false;
  const r = el.getBoundingClientRect();
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = r.left + r.width / 2 - w / 2;
  x = Math.max(8, Math.min(x, window.innerWidth - w - 8));
  const above = r.top - h - 8;
  const y = above > 4 ? above : r.bottom + 8;
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
  // Inside an animating dialog "fixed" is measured from the dialog, not the window:
  // shift by whatever offset that adds so the tip still sits next to the element.
  const got = tip.getBoundingClientRect();
  if (Math.abs(got.left - x) > 1 || Math.abs(got.top - y) > 1) {
    tip.style.left = `${2 * x - got.left}px`;
    tip.style.top = `${2 * y - got.top}px`;
  }
}

document.addEventListener("pointerover", (e) => {
  const el = e.target.closest("[data-tip], th[title], .tip-able[title]");
  if (el) showTip(el); else tip.hidden = true;
});
document.addEventListener("scroll", () => { tip.hidden = true; }, true);

// ------------------------------------------------------------------ helpers

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmt = (n) => (n === null || n === undefined ? "-" : Number(n).toFixed(2).replace(/\.?0+$/, ""));
const fmt1 = (n) => (n === null || n === undefined ? "-" : Number(n).toFixed(1).replace(/\.0$/, ""));

const roundLabel = (r) => t("round", { n: r + 1 });

const pad = (n) => String(n).padStart(2, "0");
function when(iso) {
  const d = new Date(iso);
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const shortDay = (isoDay) => (isoDay ? isoDay.slice(5) : "");

const POS = { guard: "G", forward: "F", center: "C" };
const SEVERITY = { out: "bad", doubtful: "warn", uncertain: "warn", questionable: "warn", "game-time": "mild", expected: "mild" };
const pct = (m, a) => (a ? (m / a) * 100 : null);

// League averages are only shown inside the player card (withAvg), not in the lists.
function statHeads(sortable = false, withAvg = false, skip) {
  return statCols(skip).map((c) => {
    const reb = c.key === "reb";  // total, then defensive / offensive in small print
    const title = c.title + (reb ? `\n${t("rebSplitTip")}` : "") + (withAvg ? avgTip(c.key) : "");
    return `<th class="num stat${sortable ? " sortable" : ""}"${sortable ? ` data-sort="${c.key}"` : ""} title="${esc(title)}">${esc(c.abbr)}${reb ? `<span class="split-head">${t("rebSplitHead")}</span>` : ""}</th>`;
  }).join("");
}

// mode "round": totals of one round (made/attempted); mode "avg": season averages (shooting as %).
function statCells(line, mode, skip) {
  const cols = statCols(skip);
  if (!line) return cols.map(() => '<td class="num stat dim">-</td>').join("");
  const shot = (m, a) => {
    if (mode === "round") return `${m}/${a}`;
    return a ? `${Math.round((m / a) * 100)}%` : "-";
  };
  const val = (key) => {
    if (key === "p2") return shot(line.p2m, line.p2a);
    if (key === "p3") return shot(line.p3m, line.p3a);
    if (key === "ft") return shot(line.ftm, line.fta);
    if (key === "min") return mode === "round" ? Math.round(line.min) : fmt1(line.min);
    if (key === "usg") return fmt1(line.usg);
    const f = mode === "round" ? fmt : fmt1;
    if (key === "reb") return `${f(line.reb)}<span class="split">${f(line.dreb)}/${f(line.oreb)}</span>`;
    return f(line[key]);
  };
  return cols.map((c) => `<td class="num stat">${val(c.key)}</td>`).join("");
}

// When the static files were built (api/meta.json); checked at most every 30 seconds.
let dataTimeChecked = 0;
async function loadDataTime() {
  if (Date.now() - dataTimeChecked < 30_000) return;
  dataTimeChecked = Date.now();
  try {
    const meta = await (await fetch("api/meta.json", { cache: "no-cache" })).json();
    dataTime = meta.generatedAt || dataTime;
  } catch { /* the stamp falls back to the time of loading */ }
}

// "/api/league/X/team/Y?round=3" -> "api/league/X/team/Y.r3.lt.json" (the names export.py writes).
function staticUrl(path) {
  const [p, qs] = path.split("?");
  const round = new URLSearchParams(qs || "").get("round");
  return `${p.replace(/^\/api\//, "api/")}${round !== null ? `.r${round}` : ""}.${LANG}.json`;
}

async function api(path, opts = {}) {
  if (STATIC) {
    const [res] = await Promise.all([fetch(staticUrl(path), { cache: "no-cache" }), loadDataTime()]);
    if (!res.ok) throw new Error(res.status === 404 ? t("staticMissing") : t("error", { s: res.status }));
    const data = await res.json();
    if (data.leagueAvg) LEAGUE_AVG = data.leagueAvg;
    return data;
  }
  const url = `${path}${path.includes("?") ? "&" : "?"}lang=${LANG}`;
  const res = await fetch(url, {
    ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || t("error", { s: res.status }));
  if (data.leagueAvg) LEAGUE_AVG = data.leagueAvg;
  return data;
}

// Second and third tooltip lines: the league average for a stat, and who it covers.
const PCT_AVG = new Set(["p2", "p3", "ft", "fg"]);
function avgTip(key) {
  const v = LEAGUE_AVG?.[key];
  if (v === null || v === undefined) return "";
  const value = PCT_AVG.has(key) ? `${fmt1(v)}%` : key === "usg" ? `${fmt1(v)}%` : fmt1(v);
  return `\n${t(PCT_AVG.has(key) || key === "usg" ? "avgTipPct" : "avgTipGame", { v: value })}\n${t("avgTipWho", { m: LEAGUE_AVG.minutes })}`;
}

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  return { parts: path.split("/").filter(Boolean), params: new URLSearchParams(qs || "") };
}

function setView(html) {
  app.innerHTML = html;
  if (animateView && !html.includes('class="skeleton"')) {
    app.classList.remove("enter");
    void app.offsetWidth;  // restart the entrance animation
    app.classList.add("enter");
  }
}

function stateBox(msg, isError = false) {
  return `<div class="state${isError ? " error" : ""}">${esc(msg)}</div>`;
}

function skeletonTable(rows = 8) {
  return `<div class="card">${'<div class="skeleton"></div>'.repeat(rows)}</div>`;
}

function stamp() {
  const d = STATIC && dataTime ? new Date(dataTime) : new Date();
  return t("updated", { t: `${pad(d.getHours())}:${pad(d.getMinutes())}` });
}

// Injury status in BasketNews' own words: "Out" on .com, "Nežaidžia" on .lt.
function injuryBadge(injury) {
  if (!injury) return "";
  const cls = SEVERITY[injury.status] || "mild";
  const title = [injury.return && t("expectedReturn", { r: injury.return }), injury.comment].filter(Boolean).join(". ");
  return `<span class="inj ${cls}" title="${esc(title)}">${esc(injury.label)}</span>`;
}

const initials = (name) => (name || "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

// Head shots are tight, differently-sized crops: show the whole picture in a rounded tile.
function avatar(p, size = "") {
  const ph = `<span class="avatar ph ${size}">${esc(initials(p.name))}</span>`;
  if (!p.photo) return `<span class="avatar-wrap noimg">${ph}</span>`;
  return `<span class="avatar-wrap"><img class="avatar ${size}" src="${esc(p.photo)}" alt="" loading="lazy" onerror="this.parentNode.classList.add('noimg')">${ph}</span>`;
}

// A fantasy-points value that opens the player's FP-by-round chart.
function fpLink(pid, html) {
  return pid ? `<button type="button" class="fp-link" data-fp-chart="${esc(pid)}" title="${esc(t("fpChartOpen"))}">${html}</button>` : html;
}

function clubMini(club, size = "") {
  return club?.logo ? `<img class="club-mini ${size}" src="${esc(club.logo)}" alt="" loading="lazy" onerror="this.remove()">` : "";
}

function clubTag(club) {
  return club ? `<span class="club-tag">${clubMini(club)}${esc(club.abbr || "")}</span>` : "";
}

function clubCell(club) {
  if (!club) return "-";
  const logo = club.logo ? `<img src="${esc(club.logo)}" alt="" loading="lazy" onerror="this.remove()">` : "";
  return `<div class="club">${logo}${esc(club.abbr)}</div>`;
}

function gameCell(games) {
  if (!games || !games.length) return `<span class="dim">${t("noGame")}</span>`;
  return games.map((g) => {
    const day = g.day ? `<span class="day-tag d${g.day}" data-tip="${esc(t("dayTip", { n: g.day }))}">${t("dayShort", { n: g.day })}</span>` : "";
    const vs = `${day}${g.home ? "vs" : "@"} ${esc(g.opponent)}`;
    if (g.canceled) return `<div class="game done">${vs} <span class="when">${t("canceled")}</span></div>`;
    if (g.live) return `<div class="game">${vs} <span class="live-dot">${g.score[0]}:${g.score[1]} LIVE</span></div>`;
    if (g.completed) {
      const res = g.score[0] > g.score[1] ? "W" : "L";
      return `<div class="game done">${vs} <span class="when">${res} ${g.score[0]}:${g.score[1]}</span></div>`;
    }
    return `<div class="game">${vs}<span class="when">${when(g.at)}</span></div>`;
  }).join("");
}

// ------------------------------------------------------------------ line chart (inline SVG)

// Theme-aware: the values live in style.css (--chart-team / --chart-avg per theme).
const CHART_TEAM = "var(--chart-team)";
const CHART_AVG = "var(--chart-avg)";

function niceStep(range, target) {
  const raw = range / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
}

/* series: [{name, color, values: [{y}]}]; xs: category labels in order. */
function lineChart(id, { xs, series, invert = false, yMin, yMax, integer = false, format = fmt, heads = xs }) {
  const W = 560, H = 220, L = 44, R = 16, T = 14, B = 30;
  const plotW = W - L - R, plotH = H - T - B;
  let lo = yMin, hi = yMax;
  const all = series.flatMap((s) => s.values.map((v) => v.y)).filter((v) => v !== null && v !== undefined);
  if (lo === undefined) lo = Math.min(0, ...all);
  if (hi === undefined) hi = Math.max(1, ...all);
  const step = integer ? Math.max(1, Math.ceil((hi - lo) / 7)) : niceStep(hi - lo || 1, 4);
  if (!integer) hi = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + 1e-9; v += step) ticks.push(v);
  const x = (i) => (xs.length === 1 ? L + plotW / 2 : L + (plotW * i) / (xs.length - 1));
  const y = (v) => {
    const k = (v - lo) / (hi - lo || 1);
    return invert ? T + plotH * k : T + plotH * (1 - k);
  };
  const grid = ticks.map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid-line"/>
    <text x="${L - 8}" y="${y(v) + 4}" class="tick" text-anchor="end">${integer ? v : fmt(v)}</text>`).join("");
  const every = Math.ceil(xs.length / 12);  // keep the labels readable over a long season
  const xlabels = xs.map((lbl, i) => (i % every && i !== xs.length - 1 ? ""
    : `<text x="${x(i)}" y="${H - 8}" class="tick" text-anchor="middle">${esc(lbl)}</text>`)).join("");
  const lines = series.map((s) => {
    const pts = s.values.map((v, i) => (v.y === null || v.y === undefined ? null : [x(i), y(v.y)]));
    let d = "", pen = false;
    pts.forEach((p) => { if (!p) { pen = false; return; } d += `${pen ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`; pen = true; });
    const dots = pts.map((p) => (p ? `<circle cx="${p[0]}" cy="${p[1]}" r="4.5" style="fill:${s.color}" class="dot"/>` : "")).join("");
    return `<path d="${d}" fill="none" style="stroke:${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>${dots}`;
  }).join("");
  const bands = xs.map((_, i) => {
    const x0 = xs.length === 1 ? L : Math.max(L, x(i) - plotW / (2 * (xs.length - 1)));
    const x1 = xs.length === 1 ? W - R : Math.min(W - R, x(i) + plotW / (2 * (xs.length - 1)));
    return `<rect x="${x0}" y="${T}" width="${x1 - x0}" height="${plotH}" fill="transparent" data-i="${i}" tabindex="0"/>`;
  }).join("");
  const legend = series.length > 1
    ? `<div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : "";
  const html = `${legend}<div class="chart-wrap" id="${id}">
    <svg viewBox="0 0 ${W} ${H}" role="img">${grid}${xlabels}
      <line class="crosshair" x1="0" x2="0" y1="${T}" y2="${T + plotH}" style="display:none"/>${lines}${bands}</svg>
    <div class="chart-tip" hidden></div></div>`;

  const bind = () => {
    const wrap = document.getElementById(id);
    if (!wrap) return;
    const svg = wrap.querySelector("svg");
    const tip = wrap.querySelector(".chart-tip");
    const cross = wrap.querySelector(".crosshair");
    const show = (i) => {
      const px = x(i);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.style.display = "";
      tip.replaceChildren();
      const head = document.createElement("div");
      head.className = "tip-head"; head.textContent = heads[i];
      tip.append(head);
      series.forEach((s) => {
        const v = s.values[i]?.y;
        const row = document.createElement("div");
        row.className = "tip-row";
        const key = document.createElement("i"); key.style.background = s.color;
        const val = document.createElement("strong"); val.textContent = v === null || v === undefined ? "-" : format(v);
        const name = document.createElement("span"); name.textContent = s.name;
        row.append(key, val, name);
        tip.append(row);
      });
      tip.hidden = false;
      const rect = svg.getBoundingClientRect();
      const left = (px / W) * rect.width;
      tip.style.left = `${Math.min(Math.max(left, 70), rect.width - 70)}px`;
    };
    const hide = () => { tip.hidden = true; cross.style.display = "none"; };
    wrap.querySelectorAll("rect[data-i]").forEach((r) => {
      r.addEventListener("pointerenter", () => show(+r.dataset.i));
      r.addEventListener("focus", () => show(+r.dataset.i));
      r.addEventListener("blur", hide);
    });
    svg.addEventListener("pointerleave", hide);
  };
  return { html, bind };
}

// ------------------------------------------------------------------ nav

async function loadLeagues(force = false) {
  if (!leaguesCache || force) leaguesCache = (await api("/api/leagues")).leagues;
  return leaguesCache;
}

function renderSwitcher(activeId) {
  const items = (leaguesCache || []).map(
    (l) => `<a class="chip${l.league.id === activeId ? " active" : ""}" href="#/l/${l.league.id}">${esc(l.league.title)}</a>`
  );
  switcher.innerHTML = `<a class="chip${activeId ? "" : " active"}" href="#/">${t("allLeagues")}</a>${items.join("")}`;
}

const formatLabel = (f) => (f === "head_to_head" ? t("formatH2H") : f === "classic" ? t("formatClassic") : f);

function leagueHeader(league, tab) {
  const tabs = [
    ["standings", t("tabStandings"), `#/l/${league.id}`],
    ["rounds", league.format === "head_to_head" ? t("tabMatchups") : t("tabRounds"), `#/l/${league.id}/rounds`],
    ["games", t("tabGames"), `#/l/${league.id}/games`],
    ["transfers", t("tabTransfers"), `#/l/${league.id}/transfers`],
    ["injuries", t("tabInjuries"), `#/l/${league.id}/injuries`],
    ["records", t("tabRecords"), `#/l/${league.id}/records`],
    ["players", t("tabPlayers"), `#/l/${league.id}/players`],
    ["free-agents", t("tabFA"), `#/l/${league.id}/free-agents`],
    ["draft", t("tabDraft"), `#/l/${league.id}/draft`],
  ];
  const status = league.roundStarted
    ? `<span class="badge live">${t("liveRound", { r: roundLabel(league.currentRound) })}</span>`
    : `<span>${t("nextRound", { r: roundLabel(league.currentRound), total: league.totalRounds })}</span>`;
  return `
    <div class="league-head">
      <div>
        <h1 class="page-title">${esc(league.title)}</h1>
        <div class="meta-line divided">
          <span class="badge format">${esc(formatLabel(league.format))}</span>
          <span>${esc(league.competition)}</span>
          <span>${t("teamsN", { n: league.teamsCount ?? "" })}</span>
          ${status}
        </div>
      </div>
      <a class="ext-link" href="${esc(league.url)}" target="_blank" rel="noopener">BasketNews ↗</a>
    </div>
    <nav class="tabs">
      ${tabs.map(([id, label, href]) => `<a class="tab${id === tab ? " active" : ""}" href="${href}">${label}</a>`).join("")}
    </nav>`;
}

function roundSelect(from, to, selected, labelFn = roundLabel) {
  if (to < from) return "";
  const opts = [];
  for (let r = to; r >= from; r--) {
    opts.push(`<option value="${r}"${r === selected ? " selected" : ""}>${labelFn(r)}</option>`);
  }
  return `<select class="select" id="round-select" aria-label="${esc(t("rounds"))}">${opts.join("")}</select>`;
}

function bindRoundSelect(baseHash) {
  const sel = document.getElementById("round-select");
  if (sel) sel.addEventListener("change", () => (location.hash = `${baseHash}?r=${sel.value}`));
}

function roundArrows(base, r, first, last, labelFn = roundLabel) {
  const prev = r > first ? `<a class="btn nav" href="${base}?r=${r - 1}" aria-label="${t("prevRound")}">‹</a>` : '<span class="btn nav disabled">‹</span>';
  const next = r < last ? `<a class="btn nav" href="${base}?r=${r + 1}" aria-label="${t("nextRoundAria")}">›</a>` : '<span class="btn nav disabled">›</span>';
  return `${prev}${roundSelect(first, last, r, labelFn)}${next}`;
}

function scheduleRefresh(live) {
  clearTimeout(refreshTimer);
  if (live) refreshTimer = setTimeout(() => route(true), REFRESH_LIVE_MS);
}

// ------------------------------------------------------------------ home

async function renderHome(token) {
  renderSwitcher(null);
  const head = `<h1 class="page-title">${t("myLeagues")}</h1><p class="page-sub">${t("pickLeague")}</p>`;
  setView(`${head}<div class="league-grid">${'<div class="league-card"><div class="skeleton" style="border:0"></div></div>'.repeat(2)}</div>`);
  let leagues;
  try {
    leagues = await loadLeagues(true);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  renderSwitcher(null);

  const cards = leagues.map((l) => {
    const lg = l.league;
    if (l.error) {
      return `<div class="league-card"><h3>${esc(lg.title)}</h3><div class="form-msg err">${esc(l.error)}</div>
        <button class="remove" data-remove="${lg.id}" title="${t("remove")}">×</button></div>`;
    }
    const leader = l.leader
      ? `${esc(l.leader.team.title)} · ${lg.format === "head_to_head" ? `${l.leader.wins}-${l.leader.losses}` : `${fmt(l.leader.pointsTotal)} ${t("ptsShort")}`}`
      : "-";
    return `
      <a class="league-card" href="#/l/${lg.id}">
        <div>
          <h3>${esc(lg.title)}</h3>
          <div class="meta-line" style="margin-top:6px">
            <span class="badge format">${esc(formatLabel(lg.format))}</span>
            <span>${esc(lg.competition)}</span>
          </div>
        </div>
        <div class="stats">
          <div><div class="stat-label">${t("leader")}</div><div class="stat-value">${leader}</div></div>
        </div>
        <div class="meta-line">${t("teamsN", { n: l.teams })} · ${l.round === null ? t("seasonNotStartedShort") : (l.round === 0 && I18N[LANG].playedOne ? t("playedOne") : t("playedN", { n: l.round + 1 }))}</div>
        ${STATIC ? "" : `<button class="remove" data-remove="${lg.id}" title="${t("removeLeague")}">×</button>`}
      </a>`;
  });

  if (!STATIC) cards.push(`
    <div class="league-card add-card">
      <form class="add-form" id="add-form">
        <label for="add-url" style="font-weight:600">${t("addLeague")}</label>
        <input class="input" id="add-url" placeholder="https://fantasy.basketnews.com/fantasy-leagues/…" autocomplete="off">
        <button class="btn primary" type="submit">${t("add")}</button>
        <div class="form-msg" id="add-msg"></div>
      </form>
    </div>`);

  setView(`${head}<div class="league-grid">${cards.join("")}</div>`);
  if (STATIC) return;

  document.getElementById("add-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = document.getElementById("add-url");
    const msg = document.getElementById("add-msg");
    msg.className = "form-msg";
    msg.textContent = t("checking");
    try {
      await api("/api/leagues", { method: "POST", body: JSON.stringify({ url: input.value }) });
      route();
    } catch (e) {
      msg.className = "form-msg err";
      msg.textContent = e.message;
    }
  });

  app.querySelectorAll("[data-remove]").forEach((btn) =>
    btn.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!confirm(t("confirmRemove"))) return;
      await api(`/api/leagues/${btn.dataset.remove}`, { method: "DELETE" });
      route();
    })
  );
}

// ------------------------------------------------------------------ standings

function moveMark(gained) {
  if (!gained) return "";
  return gained > 0 ? `<span class="move up">▲${gained}</span>` : `<span class="move down">▼${-gained}</span>`;
}

function standingsTable(data) {
  const { league, rows, hasTies } = data;
  const h2h = league.format === "head_to_head";
  const head = `
    <tr>
      <th class="rank">#</th><th>${t("team")}</th>
      ${h2h ? `<th class="ctr">${t("colW")}</th><th class="ctr">${t("colL")}</th>${hasTies ? `<th class="ctr">${t("colT")}</th>` : ""}` : ""}
      <th class="num">${t("points")}</th><th class="num">${t("thisRound")}</th>
    </tr>`;
  const body = rows
    .map((r) => `
      <tr>
        <td class="rank">${r.position}${moveMark(r.positionGained)}</td>
        <td><a class="team-name" href="#/l/${league.id}/t/${r.team.id}">${esc(r.team.title)}</a><span class="owner">${esc(r.team.owner)}</span></td>
        ${h2h ? `<td class="ctr wins">${r.wins}</td><td class="ctr">${r.losses}</td>${hasTies ? `<td class="ctr">${r.ties}</td>` : ""}` : ""}
        <td class="num">${fmt(r.pointsTotal)}</td>
        <td class="num">${fmt(r.pointsRound)}</td>
      </tr>`)
    .join("");
  return `<div class="card table-scroll"><table class="grid"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

async function renderStandings(fid, params, token, silent) {
  const round = params.get("r");
  if (!silent) setView(skeletonTable());
  let data;
  try {
    data = await api(`/api/league/${fid}/standings${round !== null ? `?round=${round}` : ""}`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league } = data;
  const caption = data.round === null
    ? t("seasonNotStarted")
    : data.live ? t("liveRound", { r: roundLabel(data.round) }) : t("afterRound", { n: data.round + 1 });
  setView(`
    ${leagueHeader(league, "standings")}
    <div class="toolbar">
      <div class="toolbar-left">
        ${data.round !== null ? roundSelect(league.firstRound, league.latestRound, data.round) : ""}
        <span>${caption}</span>
      </div>
      <span class="updated">${stamp()}</span>
    </div>
    ${standingsTable(data)}
    <p class="note">${t("clickTeam")}</p>`);
  bindRoundSelect(`#/l/${fid}`);
  scheduleRefresh(data.live);
}

// ------------------------------------------------------------------ rounds / matchups

async function renderRounds(fid, params, token, silent) {
  const round = params.get("r");
  if (!silent) setView(skeletonTable(4));
  let data;
  try {
    data = await api(`/api/league/${fid}/rounds${round !== null ? `?round=${round}` : ""}`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league } = data;
  const played = data.round < league.currentRound || data.live;
  let content;

  if (league.format === "head_to_head") {
    const cards = data.matchups.map((m) => {
      const side = (tm, cls) => tm
        ? `<div class="side ${cls}"><a href="#/l/${fid}/t/${tm.id}?r=${data.round}">${esc(tm.title)}</a><span class="owner">${esc(tm.owner)}</span></div>`
        : `<div class="side ${cls}"><span class="dim">${t("leagueAvg")}</span></div>`;
      const s1 = played ? fmt(m.score1) : "-";
      const s2 = played ? fmt(m.score2) : "-";
      const w1 = played && !data.live && m.score1 > m.score2;
      const w2 = played && !data.live && m.score2 > m.score1;
      return `<div class="matchup-wrap"><div class="matchup">
        ${side(m.team1, "")}
        <div class="score"><span class="${w1 ? "win" : ""}">${s1}</span><span class="sep">:</span><span class="${w2 ? "win" : ""}">${s2}</span></div>
        ${side(m.team2, "right")}
      </div>${matchupProjection(m)}</div>`;
    });
    content = cards.length ? `<div class="matchups">${cards.join("")}</div>` : stateBox(t("noMatchups"));
  } else {
    const rows = data.rows.map((r, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td><a class="team-name" href="#/l/${fid}/t/${r.team.id}?r=${data.round}">${esc(r.team.title)}</a><span class="owner">${esc(r.team.owner)}</span></td>
        <td class="num">${fmt(r.pointsRound)}</td>
        <td class="num">${fmt(r.pointsTotal)}</td>
        <td class="num">${r.position}</td>
      </tr>`).join("");
    content = `<div class="card table-scroll"><table class="grid">
      <thead><tr><th class="rank">#</th><th>${t("team")}</th><th class="num">${t("roundPoints")}</th><th class="num">${t("total")}</th><th class="num">${t("position")}</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }

  const lastSelectable = league.format === "head_to_head" ? league.totalRounds - 1 : league.latestRound;
  const caption = data.live ? `<span class="badge live">${t("live")}</span>` : played ? "" : `<span>${t("notPlayedYet")}</span>`;
  setView(`
    ${leagueHeader(league, "rounds")}
    <div class="toolbar">
      <div class="toolbar-left">${roundSelect(league.firstRound, lastSelectable, data.round)} ${caption}</div>
      <span class="updated">${stamp()}</span>
    </div>
    ${content}`);
  bindRoundSelect(`#/l/${fid}/rounds`);
  scheduleRefresh(data.live);
}

// ------------------------------------------------------------------ projections

// Current round, H2H: projected finals and the chance to win, as a split bar.
function matchupProjection(m) {
  const p = m.projection;
  if (!p) return "";
  const w1 = Math.round(p.win1 * 100);
  return `<div class="proj-line" data-tip="${esc(t("projTip"))}">
    <span class="proj-num">${fmt1(p.team1.mean)}<small>${w1}%</small></span>
    <span class="proj-bar"><i style="width:${w1}%"></i></span>
    <span class="proj-num right"><small>${100 - w1}%</small>${fmt1(p.team2.mean)}</span>
  </div>`;
}

// ------------------------------------------------------------------ season records

function efficiencyTable(fid, rows) {
  if (!rows?.length) return "";
  return `<h2 class="section-title">${t("effTitle")}</h2>
    <div class="card table-scroll"><table class="grid">
      <thead><tr><th>${t("team")}</th><th class="num">${t("effPoints")}</th><th class="num">${t("effMax")}</th>
        <th class="num" title="${esc(t("effTip"))}">${t("effPct")}</th><th class="num">${t("effLost")}</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td><a class="team-name" href="#/l/${fid}/t/${r.team.id}">${esc(r.team.title)}</a></td>
        <td class="num">${fmt(r.points)}</td><td class="num">${fmt(r.optimal)}</td>
        <td class="num"><div class="credit-cell"><b>${r.efficiency == null ? "-" : `${fmt1(r.efficiency)}%`}</b><span class="adv-bar"><i style="width:${r.efficiency || 0}%"></i></span></div></td>
        <td class="num">${r.lost ? `−${fmt(r.lost)}` : "0"}</td>
      </tr>`).join("")}</tbody></table></div>
    <p class="note">${t("effNote")}</p>`;
}

function scheduleTable(fid, rows) {
  if (!rows?.length) return "";
  const cell = (v, rank) => `${v == null ? "-" : fmt1(v)}${rank ? `<span class="rk-mini">#${rank}</span>` : ""}`;
  return `<h2 class="section-title">${t("sosTitle")}</h2>
    <div class="card table-scroll"><table class="grid">
      <thead><tr><th>${t("team")}</th>
        <th class="num" title="${esc(t("sosFacedTip"))}">${t("sosFaced")}</th>
        <th class="num" title="${esc(t("sosAgainstTip"))}">${t("sosAgainst")}</th>
        <th class="num" title="${esc(t("sosNextTip"))}">${t("sosNext")}</th>
        <th class="num" title="${esc(t("sosRestTip"))}">${t("sosRest")}</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td><a class="team-name" href="#/l/${fid}/t/${r.team.id}">${esc(r.team.title)}</a></td>
        <td class="num">${cell(r.faced, r.facedRank)}</td><td class="num">${cell(r.against)}</td>
        <td class="num">${cell(r.next5, r.next5Rank)}</td><td class="num">${cell(r.rest, r.restRank)}</td>
      </tr>`).join("")}</tbody></table></div>
    <p class="note">${t("sosNote")}</p>`;
}

function awardCards(cards) {
  return `<div class="awards">${cards.map((c) => {
    const target = c.playerId ? `data-player="${c.playerId}"` : c.teamId ? `data-team="${c.teamId}"` : "";
    return `<div class="award${target ? " clickable" : ""}" ${target}>
      <div class="award-title"><span>${c.icon ? `<span aria-hidden="true">${c.icon}</span> ` : ""}${esc(c.title)}</span>${c.info
        ? `<button type="button" class="info-btn" data-award-info aria-expanded="false" aria-label="${esc(t("awardInfo"))}" data-tip="${esc(t("awardInfo"))}">i</button>` : ""}</div>
      <div class="award-main"><span class="award-name">${esc(c.name)}</span>${c.value !== "" ? `<span class="award-value">${esc(c.value)}</span>` : ""}</div>
      ${c.sub ? `<div class="award-sub">${esc(c.sub)}</div>` : ""}
      ${c.info ? `<p class="award-info" hidden>${esc(c.info)}</p>` : ""}
    </div>`;
  }).join("")}</div>`;
}

function formTable(data) {
  const h2h = data.league.format === "head_to_head";
  const res = t("resShort");
  const rows = data.form.map((f) => {
    const chips = h2h
      ? f.last.map((x) => `<span class="res ${x.result}" title="${esc(`${roundLabel(x.round)}: ${fmt(x.points)} : ${fmt(x.against)} · ${x.opponent}`)}">${res[x.result]}</span>`).join("")
      : f.last.map((x) => `<span class="pts-chip" title="${roundLabel(x.round)}">${fmt(x.points)}</span>`).join("");
    const streak = h2h && f.streak.kind ? `${res[f.streak.kind]}${f.streak.length}` : "-";
    return `<tr>
      <td class="rank">${f.position}</td>
      <td><a class="team-name" href="#/l/${data.league.id}/t/${f.team.id}">${esc(f.team.title)}</a></td>
      <td><div class="chips">${chips || '<span class="dim">-</span>'}</div></td>
      ${h2h ? `<td class="num">${streak}</td><td class="num">${f.longestWin}</td><td class="num">${f.longestLoss}</td>` : ""}
      <td class="num">${fmt(f.avg)}</td><td class="num">${fmt(f.best)}</td><td class="num">${fmt(f.worst)}</td>
    </tr>`;
  }).join("");
  return `<div class="card table-scroll"><table class="grid form-table">
    <thead><tr><th class="rank">#</th><th>${t("team")}</th><th>${t("last5")}</th>
      ${h2h ? `<th class="num">${t("streak")}</th><th class="num" title="${esc(t("longestWTitle"))}">${t("longestW")}</th><th class="num" title="${esc(t("longestLTitle"))}">${t("longestL")}</th>` : ""}
      <th class="num">${t("avg")}</th><th class="num">${t("best")}</th><th class="num">${t("worst")}</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

async function renderRecords(fid, params, token, silent) {
  const round = params.get("r");
  if (!silent) setView(skeletonTable(4));
  let data;
  try {
    data = await api(`/api/league/${fid}/records${round !== null ? `?round=${round}` : ""}`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league } = data;
  if (!data.finished.length) {
    setView(`${leagueHeader(league, "records")}${stateBox(t("noRoundsYet"))}`);
    return;
  }
  const first = data.finished[0], last = data.finished[data.finished.length - 1];
  const done = last + 1;
  const missing = (data.missingLineups.length
    ? `<p class="note warn-note">${esc(t("missingLineups", { r: data.missingLineups.map((r) => r + 1).join(", ") }))}</p>` : "")
    + (data.partialLineups || []).map((x) => `<p class="note warn-note">${esc(t("partialLineups", { r: x.round + 1, teams: x.teams.join(", ") }))}</p>`).join("");
  setView(`
    ${leagueHeader(league, "records")}
    ${missing}
    <div class="section-head">
      <h2 class="section-title">${t("roundAwards", { n: data.round + 1 })}</h2>
      <div class="round-nav inline">${roundArrows(`#/l/${fid}/records`, data.round, first, last)}</div>
    </div>
    ${awardCards(data.roundAwards)}
    <h2 class="section-title">${t("oscars")} <span class="dim small">${t("afterDone", { n: done })}</span></h2>
    ${awardCards(data.oscars)}
    ${data.draftAwards?.length ? `<h2 class="section-title">${t("draftAwardsTitle")}</h2>${awardCards(data.draftAwards)}` : ""}
    <h2 class="section-title">${t("seasonRecords")} <span class="dim small">${t("upTo", { n: done })}</span></h2>
    ${awardCards(data.records)}
    ${efficiencyTable(fid, data.efficiency)}
    ${scheduleTable(fid, data.schedule)}
    <h2 class="section-title">${t("formTitle")}</h2>
    ${formTable(data)}
    <p class="note">${esc(t("recordsNote"))}</p>`);
  bindRoundSelect(`#/l/${fid}/records`);
  app.querySelectorAll("[data-team]").forEach((el) =>
    el.addEventListener("click", (e) => {
      if (!e.target.closest("[data-award-info], .award-info")) location.hash = `#/l/${fid}/t/${el.dataset.team}`;
    }));
}

// ------------------------------------------------------------------ draft

function playerMini(p) {
  return `<span class="pmini" data-player="${esc(p.id || "")}">${avatar(p)}<span>
    <span class="player-name">${esc(p.name)}</span>
    <span class="sub">${POS[p.position] || ""}${p.club ? ` · ${clubTag(p.club)}` : ""}</span></span></span>`;
}

function dateTime(iso) {
  return iso ? new Date(iso).toLocaleString(LANG === "en" ? "en-GB" : "lt-LT",
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
}

async function renderDraft(fid, params, token, silent) {
  if (!silent) setView(skeletonTable());
  let data;
  try {
    data = await api(`/api/league/${fid}/draft`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league, picks, teams } = data;
  if (!picks.length) {
    setView(`${leagueHeader(league, "draft")}${stateBox(t("draftEmpty"))}`);
    return;
  }
  const only = params.get("team");
  const d = league.draft || {};
  const info = [
    d.date ? t("draftDate", { d: dateTime(d.date) }) : null,
    t("draftOrderType")[d.order] || null,
    t("draftPicksN", { n: picks.length, r: picks[picks.length - 1].round }),
  ].filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join("");
  const select = `<select class="select" id="draft-team" aria-label="${esc(t("team"))}">
      <option value="">${t("allTeams")}</option>
      ${teams.map((tm) => `<option value="${tm.id}"${tm.id === only ? " selected" : ""}>${esc(tm.title)}</option>`).join("")}
    </select>`;
  let lastRound = null;
  const rows = picks.filter((pk) => !only || pk.team.id === only).map((pk) => {
    const band = !only && pk.round !== lastRound
      ? `<tr class="band-row"><td colspan="5">${t("draftRound", { n: pk.round })}</td></tr>` : "";
    lastRound = pk.round;
    const kept = pk.owner && pk.owner.id === pk.team.id;
    const now = kept ? `<span class="dim">${t("draftKept")}</span>`
      : pk.owner ? `<a class="team-name" href="#/l/${fid}/t/${pk.owner.id}">→ ${esc(pk.owner.title)}</a>`
      : `<span class="warn-text">${t("draftReleased")}</span>`;
    return `${band}<tr class="clickable" data-player="${esc(pk.player.id || "")}">
      <td class="num pick-no"><b>${pk.overall}</b><span class="dim">${pk.round}.${pk.pick}</span></td>
      <td><a class="team-name" href="#/l/${fid}/t/${pk.team.id}">${esc(pk.team.title)}</a></td>
      <td>${playerMini(pk.player)}</td>
      <td class="num pts-strong">${fpLink(pk.player.id, fmt1(pk.player.avgPts))}</td>
      <td>${now}</td>
    </tr>`;
  }).join("");
  setView(`
    ${leagueHeader(league, "draft")}
    <div class="toolbar"><div class="toolbar-left">${select}</div><div class="meta-line divided">${info}</div></div>
    <div class="card table-scroll"><table class="grid draft">
      <thead><tr><th class="num">#</th><th>${t("team")}</th><th>${t("player")}</th>
        <th class="num" title="${esc(t("avgFpTitle"))}">${t("avgFpShort")}</th><th>${t("draftNow")}</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="note">${t("draftNote")}</p>`);
  document.getElementById("draft-team").addEventListener("change", (e) => {
    location.hash = `#/l/${fid}/draft${e.target.value ? `?team=${e.target.value}` : ""}`;
  });
}

// ------------------------------------------------------------------ transfers

function creditText(n) {
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n)} ${t("creditsShort")}`;
}

// Points the move has brought so far: what came in minus what the players let go scored.
function roiCell(r, credits) {
  if (!r) return '<span class="dim">-</span>';
  const tip = t("roiTip", { a: fmt(r.inFp), b: fmt(r.outFp), n: r.rounds })
    + (credits < 0 && r.inFp ? `\n${t("roiPer100", { v: fmt1((100 * r.inFp) / -credits) })}` : "");
  return `<span class="roi ${r.net > 0 ? "up" : r.net < 0 ? "down" : ""}" data-tip="${esc(tip)}">${r.net > 0 ? "+" : r.net < 0 ? "−" : ""}${fmt(Math.abs(r.net))} FP</span>`;
}

// One row per team involved: what came in, what went out, credits.
function moveRows(fid, m, usesCredits) {
  const sides = m.type === "trade"
    ? [[m.offer, m.request, m.creditChange], [m.request, m.offer, -m.creditChange]]
    : [[m.offer, m.request, m.creditChange]];
  return sides.filter(([own]) => own.team).map(([own, other, credits]) => `
    <tr>
      <td><span class="pill ${m.type}">${m.type === "trade" ? t("moveTrade") : t("moveFA")}</span></td>
      <td><a class="team-name" href="#/l/${fid}/t/${own.team.id}">${esc(own.team.title)}</a>
        ${m.type === "trade" && other.team ? `<div class="sub">${t("tradeWith", { t: esc(other.team.title) })}</div>` : ""}</td>
      <td><div class="move-in">${other.players.map(playerMini).join("") || '<span class="dim">-</span>'}</div></td>
      <td><div class="move-out">${own.players.map(playerMini).join("") || '<span class="dim">-</span>'}</div></td>
      ${usesCredits ? `<td class="num credit ${credits < 0 ? "neg" : credits > 0 ? "pos" : ""}">${credits ? creditText(credits) : "-"}</td>` : ""}
      <td class="num">${roiCell(m.roi?.[own.team.id], credits)}</td>
      <td class="dim nowrap">${esc(dateTime(m.at))}</td>
    </tr>`).join("");
}

async function renderTransfers(fid, params, token, silent) {
  if (!silent) setView(skeletonTable(6));
  let data;
  try {
    data = await api(`/api/league/${fid}/transfers`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league, moves, teams, upcoming, lock, usesCredits } = data;

  const deadline = lock?.nextChange
    ? `<div class="deadline${lock.locked ? " locked" : ""}">
        <span class="deadline-label">${lock.locked ? t("windowClosed") : t("nextProcessing", { n: league.currentRound + 1 })}</span>
        <strong>${esc(dateTime(lock.nextChange))}</strong>
        <span class="dim">${esc(relTime(lock.nextChange))}</span>
      </div>` : "";

  const creditsTable = usesCredits ? `
    <h2 class="section-title">${t("creditsTitle")} <span class="dim small">${t("creditsStart", { n: data.startingCredits })}</span></h2>
    <div class="card table-scroll"><table class="grid">
      <thead><tr><th>${t("team")}</th><th class="num">${t("creditsLeft")}</th><th class="num">${t("creditsSpent")}</th>
        <th class="num">${t("signings")}</th><th class="num">${t("trades")}</th><th class="num" title="${esc(t("roiHeadTip"))}">ROI</th></tr></thead>
      <tbody>${teams.map((r) => `<tr>
        <td><a class="team-name" href="#/l/${fid}/t/${r.team.id}">${esc(r.team.title)}</a><span class="owner">${esc(r.team.owner || "")}</span></td>
        <td class="num"><div class="credit-cell"><b>${r.credits}</b><span class="adv-bar"><i style="width:${Math.max(0, Math.min(100, 100 * r.credits / (data.startingCredits || 1)))}%"></i></span></div></td>
        <td class="num">${r.spent || "-"}</td><td class="num">${r.signings || "-"}</td><td class="num">${r.trades || "-"}</td>
        <td class="num">${r.roi == null ? '<span class="dim">-</span>' : `<span class="roi ${r.roi > 0 ? "up" : r.roi < 0 ? "down" : ""}">${r.roi > 0 ? "+" : r.roi < 0 ? "−" : ""}${fmt(Math.abs(r.roi))}</span>`}</td>
      </tr>`).join("")}</tbody></table></div>` : "";

  const bids = upcoming.length ? `
    <h2 class="section-title">${t("bidsTitle", { n: league.currentRound + 1 })}</h2>
    <div class="card table-scroll"><table class="grid">
      <thead><tr><th>${t("player")}</th><th class="num">${t("bidsCount")}</th><th class="num">${t("bidsTop")}</th></tr></thead>
      <tbody>${upcoming.map((b) => `<tr class="clickable" data-player="${esc(b.player.id)}">
        <td class="sticky">${playerMini(b.player)}</td><td class="num">${b.totalBids}</td><td class="num">${b.highestBid ?? "-"}</td></tr>`).join("")}</tbody>
    </table></div>` : "";

  // One "before round N" group at a time, picked with the arrows / list (?r=N; newest with moves by default).
  const base = `#/l/${fid}/transfers`;
  const count = (r) => moves.filter((m) => m.round === r).length;
  const withMoves = moves.map((m) => m.round);
  const first = league.firstRound;
  const last = Math.max(league.currentRound, ...withMoves);
  const asked = params.has("r") ? Number(params.get("r")) : NaN;
  const r = Number.isInteger(asked) ? Math.max(first, Math.min(asked, last))
    : withMoves.length ? Math.max(...withMoves) : league.currentRound;
  const label = (x) => `${t("movesBefore", { n: x + 1 })}${count(x) ? ` (${count(x)})` : ""}`;
  const nav = `<div class="round-nav inline moves-nav">${roundArrows(base, r, first, last, label)}</div>`;
  let list;
  if (!moves.length) {
    list = `<div class="card">${stateBox(t("noMoves"))}</div>`;
  } else if (!count(r)) {
    list = `${nav}<div class="card">${stateBox(t("noMovesRound"))}</div>`;
  } else {
    list = `${nav}
      <div class="card table-scroll"><table class="grid moves">
        <thead><tr><th>${t("moveType")}</th><th>${t("team")}</th><th>${t("moveIn")}</th><th>${t("moveOut")}</th>
          ${usesCredits ? `<th class="num">${t("creditsShortHead")}</th>` : ""}<th class="num" title="${esc(t("roiHeadTip"))}">ROI</th><th>${t("moveWhen")}</th></tr></thead>
        <tbody>${moves.filter((m) => m.round === r).map((m) => moveRows(fid, m, usesCredits)).join("")}</tbody>
      </table></div>`;
  }

  setView(`
    ${leagueHeader(league, "transfers")}
    ${deadline}
    <h2 class="section-title">${t("movesTitle")}</h2>
    ${list}
    ${bids}
    ${creditsTable}`);
  bindRoundSelect(base);
}

// ------------------------------------------------------------------ injury news

function newsTime(e) {
  if (!e.hasTime) return e.at.slice(5, 10);
  const d = new Date(e.at);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function renderInjuries(fid, params, token, silent) {
  if (!silent) setView(skeletonTable(8));
  let data;
  try {
    data = await api(`/api/league/${fid}/injuries`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league } = data;
  // "" = everyone, "owned" = players on any league team, otherwise one team's players
  const filter = params.get("team") || (params.get("owned") === "1" ? "owned" : "");
  const events = data.events.filter((e) => !filter || (filter === "owned" ? e.owner : e.owner?.id === filter));
  const rows = events.map((e) => {
    const where = [e.player.club?.abbr, e.owner?.title].filter(Boolean).map(esc).join(", ");
    const prefix = e.owner && e.tone !== "neutral"
      ? `<span class="news-prefix ${e.tone}">${t(e.tone === "bad" ? "newsBad" : "newsGood", { t: esc(e.owner.title) })}</span> ` : "";
    const name = e.player.id
      ? `<span class="news-name" data-player="${esc(e.player.id)}">${esc(e.player.name)}</span>`
      : `<span class="news-name">${esc(e.player.name)}</span>`;
    const extra = [e.comment.replace(/\.$/, ""), e.return ? t("expectedReturn", { r: e.return }) : ""].filter(Boolean).map(esc).join(". ");
    return `<li class="news-row">
      <time class="news-time" datetime="${esc(e.at)}">${newsTime(e)}</time>
      <div class="news-body">
        <div class="news-line">${prefix}${name}${where ? ` <span class="dim">(${where})</span>` : ""} <span class="news-phrase ${e.status}">${esc(e.phrase)}</span></div>
        ${extra ? `<div class="news-comment">${extra}</div>` : ""}
      </div>
    </li>`;
  }).join("");
  const report = data.reportUrl ? `<a class="link" href="${esc(data.reportUrl)}" target="_blank" rel="noopener">${t("injuryReportLink")}</a>` : "";
  setView(`
    ${leagueHeader(league, "injuries")}
    <div class="toolbar">
      <select class="select" id="news-team" aria-label="${esc(t("team"))}">
        <option value="">${t("newsAll")}</option>
        <option value="owned"${filter === "owned" ? " selected" : ""}>${t("newsOwnedOnly")}</option>
        ${(data.teams || []).map((tm) => `<option value="${esc(tm.id)}"${tm.id === filter ? " selected" : ""}>${esc(tm.title)}</option>`).join("")}
      </select>
      <span class="dim small">${t("newsCount", { n: events.length })}</span>
    </div>
    ${events.length ? `<ul class="card news">${rows}</ul>`
      : `<div class="card">${stateBox(filter && filter !== "owned" ? t("newsEmptyTeam") : t("newsEmpty"))}</div>`}
    <p class="note">${t("newsNote", { link: report })}</p>`);
  document.getElementById("news-team").addEventListener("change", (ev) => {
    location.hash = `#/l/${fid}/injuries${ev.target.value ? `?team=${ev.target.value}` : ""}`;
  });
}

function relTime(iso) {
  const mins = Math.round((new Date(iso) - Date.now()) / 60000);
  if (Math.abs(mins) < 1) return t("relNow");
  const h = Math.floor(Math.abs(mins) / 60), m = Math.abs(mins) % 60;
  const span = h ? (m ? t("relHM", { h, m }) : t("relH", { h })) : t("relM", { m });
  return mins > 0 ? t("relIn", { s: span }) : t("relAgo", { s: span });
}

// ------------------------------------------------------------------ player lists (free agents / all players)

const STATUS_RANK = { out: 4, doubtful: 3, uncertain: 3, questionable: 3, "game-time": 2, expected: 2 };

// Sortable / filterable values of a player-list row.
const FA_VALUE = {
  name: (p) => p.name,
  status: (p) => (p.injury ? STATUS_RANK[p.injury.status] || 1 : 0),
  owner: (p) => p.owner?.team?.title || "",
  avgPts: (p) => p.avgPts,
  roundPts: (p) => p.roundPts,
  gamesPlayed: (p) => p.gamesPlayed,
  dd: (p) => p.dd,
  min: (p) => p.season?.min,
  pts: (p) => p.season?.pts,
  reb: (p) => p.season?.reb,
  ast: (p) => p.season?.ast,
  stl: (p) => p.season?.stl,
  blk: (p) => p.season?.blk,
  tov: (p) => p.season?.tov,
  eff: (p) => p.season?.eff,
  usg: (p) => p.season?.usg,
  p2: (p) => (p.season ? pct(p.season.p2m, p.season.p2a) : null),
  p3: (p) => (p.season ? pct(p.season.p3m, p.season.p3a) : null),
  ft: (p) => (p.season ? pct(p.season.ftm, p.season.fta) : null),
};
const PCT_KEYS = new Set(["p2", "p3", "ft"]);
// "adv:<key>": a season advanced stat from BasketNews (see advanced_table in the backend)
const valueOf = (key) => FA_VALUE[key] || (key.startsWith("adv:") ? (p) => p.adv?.[key.slice(4)] : () => null);
let advLower = new Set();  // advanced stats where a smaller number is the better one
const TEXT_KEYS = new Set(["name", "owner"]);

const listState = {
  free: { search: "", pos: "", club: "", owner: "", healthyOnly: false, sort: "avgPts", dir: -1, ranges: [], view: "basic", regularOnly: true },
  all: { search: "", pos: "", club: "", owner: "", healthyOnly: false, sort: "avgPts", dir: -1, ranges: [], view: "basic", regularOnly: true },
};

function listFilterOptions(lastLabel, advanced) {
  return [
    ["avgPts", t("avgFp")], ["roundPts", lastLabel], ["gamesPlayed", t("gp")], ["dd", t("ddShort")],
    ...statCols().map((c) => [c.key, PCT_KEYS.has(c.key) ? `${c.abbr} %` : c.abbr]),
    ...(advanced?.columns || []).map((c) => [`adv:${c.key}`, c.short]),
  ];
}

// Green / red against the league's upper / lower quartile (reversed where lower is better).
function advLevel(v, ctx) {
  if (v == null || !ctx) return "";
  const good = ctx.better === "lower" ? v <= ctx.low : v >= ctx.high;
  const bad = ctx.better === "lower" ? v >= ctx.high : v <= ctx.low;
  return good ? " adv-good" : bad ? " adv-bad" : "";
}

function rangeBounds(players, key) {
  if (PCT_KEYS.has(key)) return { lo: 0, hi: 100, step: 1 };
  const vals = players.map(valueOf(key)).filter((v) => v !== null && v !== undefined);
  const lo = Math.floor(Math.min(0, ...vals));
  const hi = Math.ceil(Math.max(1, ...vals));
  return { lo, hi, step: key === "gamesPlayed" || key === "dd" ? 1 : 0.5 };
}

function ownerCell(owner, withSlot = true) {
  if (!owner) return `<span class="dim">${t("free")}</span>`;
  const pill = withSlot && owner.slotLabel ? `<span class="slot ${owner.slot || ""} mini">${esc(owner.slotLabel)}</span>` : "";
  return `<span class="owner-cell"><span>${esc(owner.team.title)}</span>${pill}</span>`;
}

async function renderPlayerList(fid, scope, token, silent) {
  if (!silent) setView(skeletonTable());
  let data;
  try {
    data = await api(`/api/league/${fid}/${scope === "all" ? "players" : "free-agents"}`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const st = listState[scope];
  const { league } = data;
  const all = scope === "all";
  const clubs = [...new Set(data.players.map((p) => p.club?.abbr).filter(Boolean))].sort();
  const lastLabel = `${t("roundShort", { n: data.statsRound + 1 })} FP`;
  const adv = data.advanced;
  advLower = new Set((adv?.columns || []).filter((c) => c.better === "lower").map((c) => `adv:${c.key}`));
  const filterOptions = listFilterOptions(lastLabel, adv);
  const extraCols = all ? 1 : 0;
  if (!adv) st.view = "basic";

  // Table head of the current view; the advanced one has a row of group names above.
  const head = () => {
    const lead = `<th class="sticky sortable" data-sort="name"${st.view === "adv" ? ' rowspan="2"' : ""}>${t("player")}</th>
      ${all ? `<th class="sortable" data-sort="owner"${st.view === "adv" ? ' rowspan="2"' : ""}>${t("ownerCol")}</th>` : ""}`;
    if (st.view !== "adv") {
      return `<tr>${lead}
        <th class="sortable" data-sort="status" title="${esc(t("statusSortTitle"))}">${t("status")}</th>
        <th>${roundLabel(league.currentRound)}</th>
        <th class="num sortable" data-sort="avgPts" title="${esc(t("avgFpTitle"))}">${t("avgFp")}</th>
        <th class="num sortable" data-sort="roundPts" title="${esc(t("lastFpTitle"))}">${esc(lastLabel)}</th>
        <th class="num sortable" data-sort="gamesPlayed" title="${esc(t("gpTitle"))}">${t("gp")}</th>
        <th class="num sortable" data-sort="dd" title="${esc(`${t("tiles").dd}\n${t("ddTip")}`)}">${t("ddShort")}</th>
        ${statHeads(true)}</tr>`;
    }
    const groups = adv.groups.map((g) => {
      const n = adv.columns.filter((c) => c.group === g.id).length;
      return n ? `<th class="adv-group-head" colspan="${n}">${esc(g.title)}</th>` : "";
    }).join("");
    const cols = adv.columns.map((c) => {
      const ctx = adv.context[c.key];
      const tip = `${c.title}\n${c.desc}${ctx ? `\n${t("avgTipPlain", { v: fmt1(ctx.avg) })}` : ""}`;
      return `<th class="num sortable" data-sort="adv:${c.key}" title="${esc(tip)}">${esc(c.short)}${c.better === "lower" ? '<span class="adv-dir">↓</span>' : ""}</th>`;
    }).join("");
    return `<tr>${lead}
        <th class="num sortable" data-sort="avgPts" rowspan="2" title="${esc(t("avgFpTitle"))}">${t("avgFp")}</th>
        <th class="num sortable" data-sort="gamesPlayed" rowspan="2" title="${esc(t("gpTitle"))}">${t("gp")}</th>
        ${groups}</tr><tr>${cols}</tr>`;
  };
  const advCells = (p) => (p.adv
    ? adv.columns.map((c) => `<td class="num stat${advLevel(p.adv[c.key], adv.context[c.key])}">${fmt1(p.adv[c.key])}</td>`).join("")
    : `<td class="dim" colspan="${adv.columns.length}">${t("advListNone")}</td>`);
  const views = adv ? `<div class="list-views-row"><div class="seg list-views" role="group">${[["basic", t("viewBasic")], ["adv", t("viewAdv")]].map(([v, label]) =>
    `<button type="button" class="seg-btn${st.view === v ? " on" : ""}" data-view="${v}" aria-pressed="${st.view === v}">${label}</button>`).join("")}</div>
    <label class="check" id="fa-regular-box"${st.view === "adv" ? "" : " hidden"}><input type="checkbox" id="fa-regular"${st.regularOnly ? " checked" : ""}> ${t("advRegular", { m: adv.contextMinutes })}</label></div>` : "";
  const note = () => (st.view === "adv"
    ? t("advListNote", { link: `<a class="link" href="${esc(adv.url)}" target="_blank" rel="noopener">BasketNews</a>`, m: adv.contextMinutes })
    : `${esc(all ? t("allNote") : t("faNote", { total: data.totalPlayers, comp: league.competition, owned: data.rosteredPlayers }))}<a class="link" href="${esc(data.injuryReportUrl || "#")}" target="_blank" rel="noopener">${t("injuryReport")}</a>${t("faNoteEnd")}`);

  setView(`
    ${leagueHeader(league, all ? "players" : "free-agents")}
    <div class="filters">
      <input class="input" id="fa-search" type="search" placeholder="${esc(t("search"))}" value="${esc(st.search)}" autocomplete="off">
      <select class="select" id="fa-pos" aria-label="${esc(t("allPositions"))}">
        <option value="">${t("allPositions")}</option>
        <option value="guard">${t("guards")}</option><option value="forward">${t("forwards")}</option><option value="center">${t("centers")}</option>
      </select>
      <select class="select" id="fa-club" aria-label="${esc(t("allClubs"))}">
        <option value="">${t("allClubs")}</option>${clubs.map((c) => `<option>${esc(c)}</option>`).join("")}
      </select>
      ${all ? `<select class="select" id="fa-owner" aria-label="${esc(t("ownerCol"))}">
        <option value="">${t("ownerAll")}</option><option value="owned">${t("ownerOwned")}</option><option value="free">${t("ownerFree")}</option>
      </select>` : ""}
      <label class="check"><input type="checkbox" id="fa-healthy"${st.healthyOnly ? " checked" : ""}> ${t("healthyOnly")}</label>
      <button class="btn" type="button" id="fa-add-range">${t("addFilter")}</button>
      <span class="updated" id="fa-count"></span>
    </div>
    <div class="range-filters" id="fa-ranges"></div>
    ${views}
    <div class="card table-scroll">
      <table class="grid players stats-table">
        <thead id="fa-head">${head()}</thead>
        <tbody id="fa-body"></tbody>
      </table>
    </div>
    <p class="note" id="fa-note">${note()}</p>`);

  document.getElementById("fa-pos").value = st.pos;
  document.getElementById("fa-club").value = clubs.includes(st.club) ? st.club : "";
  if (all) document.getElementById("fa-owner").value = st.owner;

  const draw = () => {
    const q = st.search.trim().toLowerCase();
    const rows = data.players.filter((p) =>
      (!q || p.name.toLowerCase().includes(q)) &&
      (!st.pos || p.position === st.pos) &&
      (!st.club || p.club?.abbr === st.club) &&
      (!all || !st.owner || (st.owner === "owned" ? !!p.owner : !p.owner)) &&
      (!st.healthyOnly || !p.injury) &&
      (st.view !== "adv" || !st.regularOnly || (p.season?.min ?? 0) >= adv.contextMinutes) &&
      st.ranges.every((r) => {
        const v = valueOf(r.key)(p);
        return v !== null && v !== undefined && v >= r.min - 1e-9 && v <= r.max + 1e-9;
      }));
    const key = st.sort;
    const get = valueOf(key);
    rows.sort((a, b) => {
      const av = get(a), bv = get(b);
      if (TEXT_KEYS.has(key)) {
        if (!av !== !bv) return !av - !bv;  // blanks last
        return av.localeCompare(bv) * st.dir || a.name.localeCompare(b.name);
      }
      const aMissing = av === null || av === undefined, bMissing = bv === null || bv === undefined;
      if (aMissing || bMissing) return aMissing - bMissing || a.name.localeCompare(b.name);  // blanks last
      return (av > bv ? 1 : av < bv ? -1 : 0) * st.dir || a.name.localeCompare(b.name);
    });
    document.getElementById("fa-count").textContent = t("nPlayers", { n: rows.length });
    app.querySelectorAll("th.sortable").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.sort === key);
      th.dataset.dir = st.dir > 0 ? "↑" : "↓";
    });
    const advView = st.view === "adv";
    const width = advView ? 3 + extraCols + adv.columns.length : 7 + extraCols + STAT_DEFS.length;
    document.getElementById("fa-body").innerHTML = rows.map((p) => `
      <tr class="clickable${all && !p.owner ? " free-row" : ""}" data-player="${p.id}">
        <td class="sticky"><div class="player">${avatar(p)}<div>
          <span class="player-name">${esc(p.name)}</span>
          <div class="sub">${POS[p.position] || ""} · ${clubTag(p.club)}</div></div></div></td>
        ${all ? `<td>${ownerCell(p.owner)}</td>` : ""}
        ${advView ? `<td class="num pts-strong">${fpLink(p.id, fmt1(p.avgPts))}</td><td class="num">${p.gamesPlayed}</td>${advCells(p)}` : `
        <td>${p.injury ? injuryBadge(p.injury) : '<span class="dim">-</span>'}</td>
        <td>${gameCell(p.games)}</td>
        <td class="num pts-strong">${fpLink(p.id, fmt1(p.avgPts))}</td>
        <td class="num">${fpLink(p.id, fmt(p.roundPts))}</td>
        <td class="num">${p.gamesPlayed}</td>
        <td class="num">${p.dd ? `<b>${p.dd}</b>` : '<span class="dim">0</span>'}</td>
        ${statCells(p.season, "avg")}`}
      </tr>`).join("") || `<tr><td colspan="${width}" class="dim" style="text-align:center">${t("noPlayers")}</td></tr>`;
  };

  // Stat range filters: a two-thumb slider plus number boxes that stay in sync.
  const drawRanges = () => {
    const box = document.getElementById("fa-ranges");
    box.innerHTML = st.ranges.map((r, i) => {
      const b = rangeBounds(data.players, r.key);
      return `<div class="range-filter" data-i="${i}">
        <select class="select rf-key">${filterOptions.map(([k, label]) => `<option value="${k}"${k === r.key ? " selected" : ""}>${esc(label)}</option>`).join("")}</select>
        <span class="dim small">${t("from")}</span>
        <input class="input rf-num rf-min" type="number" step="${b.step}" min="${b.lo}" max="${b.hi}" value="${r.min}">
        <div class="dual">
          <div class="dual-track"><div class="dual-fill"></div></div>
          <input type="range" class="rf-lo" min="${b.lo}" max="${b.hi}" step="${b.step}" value="${r.min}" aria-label="${t("from")}">
          <input type="range" class="rf-hi" min="${b.lo}" max="${b.hi}" step="${b.step}" value="${r.max}" aria-label="${t("to")}">
        </div>
        <span class="dim small">${t("to")}</span>
        <input class="input rf-num rf-max" type="number" step="${b.step}" min="${b.lo}" max="${b.hi}" value="${r.max}">
        <button class="close small-close" type="button" data-remove-range aria-label="${t("remove")}">×</button>
      </div>`;
    }).join("") + (st.ranges.length > 1 ? `<button class="link-btn" type="button" id="fa-clear-ranges">${t("clearFilters")}</button>` : "");

    box.querySelectorAll(".range-filter").forEach((row) => {
      const i = +row.dataset.i;
      const r = st.ranges[i];
      const b = rangeBounds(data.players, r.key);
      const lo = row.querySelector(".rf-lo"), hi = row.querySelector(".rf-hi");
      const minBox = row.querySelector(".rf-min"), maxBox = row.querySelector(".rf-max");
      const fill = row.querySelector(".dual-fill");
      const paint = () => {
        const span = b.hi - b.lo || 1;
        fill.style.left = `${((r.min - b.lo) / span) * 100}%`;
        fill.style.right = `${100 - ((r.max - b.lo) / span) * 100}%`;
      };
      const set = (min, max) => {
        r.min = Math.max(b.lo, Math.min(min, max));
        r.max = Math.min(b.hi, Math.max(max, r.min));
        lo.value = r.min; hi.value = r.max; minBox.value = r.min; maxBox.value = r.max;
        paint(); draw();
      };
      lo.addEventListener("input", () => set(Math.min(+lo.value, r.max), r.max));
      hi.addEventListener("input", () => set(r.min, Math.max(+hi.value, r.min)));
      minBox.addEventListener("change", () => set(minBox.value === "" ? b.lo : +minBox.value, r.max));
      maxBox.addEventListener("change", () => set(r.min, maxBox.value === "" ? b.hi : +maxBox.value));
      row.querySelector(".rf-key").addEventListener("change", (e) => {
        const nb = rangeBounds(data.players, e.target.value);
        st.ranges[i] = { key: e.target.value, min: nb.lo, max: nb.hi };
        drawRanges(); draw();
      });
      row.querySelector("[data-remove-range]").addEventListener("click", () => {
        st.ranges.splice(i, 1);
        drawRanges(); draw();
      });
      paint();
    });
    document.getElementById("fa-clear-ranges")?.addEventListener("click", () => {
      st.ranges = [];
      drawRanges(); draw();
    });
  };

  document.getElementById("fa-search").addEventListener("input", (e) => { st.search = e.target.value; draw(); });
  document.getElementById("fa-pos").addEventListener("change", (e) => { st.pos = e.target.value; draw(); });
  document.getElementById("fa-club").addEventListener("change", (e) => { st.club = e.target.value; draw(); });
  if (all) document.getElementById("fa-owner").addEventListener("change", (e) => { st.owner = e.target.value; draw(); });
  document.getElementById("fa-healthy").addEventListener("change", (e) => { st.healthyOnly = e.target.checked; draw(); });
  document.getElementById("fa-regular")?.addEventListener("change", (e) => { st.regularOnly = e.target.checked; draw(); });
  document.getElementById("fa-add-range").addEventListener("click", () => {
    const used = new Set(st.ranges.map((r) => r.key));
    const key = filterOptions.map(([k]) => k).find((k) => !used.has(k)) || "avgPts";
    const b = rangeBounds(data.players, key);
    st.ranges.push({ key, min: b.lo, max: b.hi });
    drawRanges(); draw();
  });
  app.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
    st.view = b.dataset.view;
    app.querySelectorAll("[data-view]").forEach((x) => {
      x.classList.toggle("on", x === b);
      x.setAttribute("aria-pressed", String(x === b));
    });
    document.getElementById("fa-head").innerHTML = head();
    document.getElementById("fa-regular-box").hidden = st.view !== "adv";
    if (!document.querySelector(`#fa-head th[data-sort="${CSS.escape(st.sort)}"]`)) {
      st.sort = "avgPts";  // the sorted column is not in this view
      st.dir = -1;
    }
    document.getElementById("fa-note").innerHTML = note();
    draw();
  }));
  document.getElementById("fa-head").addEventListener("click", (e) => {
    const th = e.target.closest("th.sortable");
    if (!th) return;
    const key = th.dataset.sort;
    st.dir = st.sort === key ? -st.dir : TEXT_KEYS.has(key) || advLower.has(key) ? 1 : -1;
    st.sort = key;
    draw();
  });
  drawRanges();
  draw();
}

// ------------------------------------------------------------------ box scores (real games of a round)

// [key, LT abbr, LT title, EN abbr, EN title]
const BOX_COLS = [
  ["min", "MIN", "Minutės", "MIN", "Minutes"],
  ["pts", "TŠK", "Taškai", "PTS", "Points"],
  ["p2", "2T", "Dvitaškiai", "2P", "Two-pointers"],
  ["p3", "3T", "Tritaškiai", "3P", "Three-pointers"],
  ["ft", "BM", "Baudų metimai", "FT", "Free throws"],
  ["oreb", "PAK", "Atkovoti kamuoliai puolime", "OR", "Offensive rebounds"],
  ["dreb", "GAK", "Atkovoti kamuoliai gynyboje", "DR", "Defensive rebounds"],
  ["reb", "AK", "Atkovoti kamuoliai", "REB", "Rebounds"],
  ["ast", "RP", "Rezultatyvūs perdavimai", "AST", "Assists"],
  ["stl", "PR", "Perimti kamuoliai", "STL", "Steals"],
  ["blk", "BL", "Blokuoti metimai", "BLK", "Blocks"],
  ["ba", "GBL", "Gauti blokai", "BA", "Blocks against"],
  ["tov", "KL", "Klaidos", "TO", "Turnovers"],
  ["pf", "PRŽ", "Pražangos", "PF", "Fouls"],
  ["fd", "IPRŽ", "Išprovokuotos pražangos", "FD", "Fouls drawn"],
  ["eff", "NB", "Naudingumo balas", "PIR", "Performance index rating"],
  ["usg", "USG%", "Naudojimo dažnis", "USG%", "Usage rate"],
];

const mmss = (sec) => `${Math.floor(sec / 60)}:${pad(sec % 60)}`;
const openGames = new Set();

function boxTable(side) {
  const cols = BOX_COLS.map(([key, lta, ltt, ena, ent]) => ({ key, abbr: LANG === "en" ? ena : lta, title: LANG === "en" ? ent : ltt }));
  const cell = (line, key) => {
    if (key === "min") return mmss(line.sec);
    if (key === "p2") return `${line.p2m}-${line.p2a}`;
    if (key === "p3") return `${line.p3m}-${line.p3a}`;
    if (key === "ft") return `${line.ftm}-${line.fta}`;
    if (key === "usg") return fmt1(line.usg);
    return fmt(line[key]);
  };
  const rows = side.players.map((p) => `
    <tr class="clickable${p.owner ? "" : " free-row"}" data-player="${p.id}">
      <td class="sticky"><div class="player">${clubMini(side)}<span class="player-name">${esc(p.name)}</span></div></td>
      <td class="num fp-cell">${fpLink(p.id, fmt(p.fp))}</td>
      ${cols.map((c) => `<td class="num stat">${cell(p.line, c.key)}</td>`).join("")}
      <td class="owner-col">${ownerCell(p.owner, false)}</td>
    </tr>`).join("");
  return `<div class="box-team">
    <div class="box-team-head">${clubMini(side, "md")}<strong>${esc(side.name || side.abbr)}</strong>
      ${side.combined ? `<span class="dim small">${t("combinedNote")}</span>` : ""}</div>
    ${side.players.length ? `<div class="table-scroll"><table class="grid box stats-table">
      <thead><tr><th class="sticky">${t("player")}</th><th class="num">FP</th>
        ${cols.map((c) => `<th class="num stat" title="${esc(c.title)}">${esc(c.abbr)}</th>`).join("")}
        <th class="owner-col">${t("ownerCol")}</th></tr></thead>
      <tbody>${rows}</tbody></table></div>` : `<p class="dim small box-empty">${t("noBoxYet")}</p>`}
  </div>`;
}

function gameCardHead(g) {
  let status;
  if (g.canceled) status = `<span class="badge">${t("gameCanceled")}</span>`;
  else if (g.live) status = `<span class="badge live">${t("live")}</span>`;
  else if (g.completed) status = `<span class="badge">${t("gameFinal")}</span>`;
  else status = `<span class="badge">${when(g.at)}</span>`;
  const score = g.homeScore !== null && g.homeScore !== undefined
    ? `<span class="${g.completed && g.homeScore > g.awayScore ? "win" : ""}">${g.homeScore}</span><span class="sep">:</span><span class="${g.completed && g.awayScore > g.homeScore ? "win" : ""}">${g.awayScore}</span>`
    : '<span class="dim">-</span>';
  const pick = g.preview?.prediction;
  const meta = [
    pick ? t("predShort", { t: esc(pick.winner), p: Math.round(pick.prob * 100) }) : g.preview ? t("previewReady") : null,
    g.owned ? t("ownedInGame", { n: g.owned }) : null,
    g.top && g.top.fp !== null ? `${t("topFp")}: ${esc(g.top.name)} ${fmt(g.top.fp)}` : null,
  ].filter(Boolean).join(" · ");
  return `<button class="game-head" type="button" data-game="${esc(g.id)}" aria-expanded="${openGames.has(g.id)}">
    <span class="gh-teams">
      <span class="gh-team">${clubMini(g.home, "md")}<strong>${esc(g.home.abbr)}</strong></span>
      <span class="gh-score">${score}</span>
      <span class="gh-team right"><strong>${esc(g.away.abbr)}</strong>${clubMini(g.away, "md")}</span>
    </span>
    <span class="gh-meta dim small">${meta}</span>
    ${status}
    <span class="chev" aria-hidden="true">▾</span>
  </button>`;
}

// Before tip-off: form, key players, injuries and generated notes for each side.
function previewSide(fid, side, pv) {
  const res = t("resShort");
  const rec = pv.record ? `${pv.record.w}-${pv.record.l}` : "-";
  const avg = pv.avgFor !== null ? `${fmt1(pv.avgFor)} : ${fmt1(pv.avgAgainst)}` : "-";
  const last = pv.last.map((x) => `<span class="res ${x.won ? "W" : "L"}" data-tip="${esc(`${x.home ? "vs" : "@"} ${x.opp} ${x.score[0]}:${x.score[1]}`)}">${x.won ? res.W : res.L}</span>`).join("");
  const player = (v, extra) => `<li class="pv-player" data-player="${esc(v.id)}">
      ${avatar(v)}<span class="pv-name"><span class="player-name">${esc(v.name)}</span>
      <span class="sub">${POS[v.position] || ""}${v.owner ? ` · ${esc(v.owner.title)}` : ` · <span class="free-tag">${t("draftReleased")}</span>`}</span></span>
      ${extra}</li>`;
  const key = pv.key.slice(0, 3).map((v) => player(v, `<span class="pv-num"><b>${fmt1(v.avgPts)}</b><span class="dim">${t("pvLine", { p: fmt1(v.line.pts), r: fmt1(v.line.reb), a: fmt1(v.line.ast) })}</span></span>`)).join("");
  const inj = pv.injuries.map((v) => player(v, `<span class="pv-num">${injuryBadge(v.injury)}</span>`)).join("");
  return `<div class="pv-side">
    <div class="pv-team">${clubMini(side, "md")}<strong>${esc(side.name || side.abbr)}</strong></div>
    <div class="pv-stats">
      <div><span class="stat-label">${t("pvRecord")}</span><span class="stat-value">${rec}</span></div>
      <div><span class="stat-label" data-tip="${esc(t("pvAvgTip"))}">${t("pvAvg")}</span><span class="stat-value">${avg}</span></div>
      <div><span class="stat-label">${t("pvLast")}</span><span class="chips">${last || '<span class="dim">-</span>'}</span></div>
    </div>
    <h5 class="pv-h">${t("pvKey")}</h5>
    <ul class="pv-list">${key || `<li class="dim small">${t("pvNoStats")}</li>`}</ul>
    <h5 class="pv-h">${t("pvInjuries")}</h5>
    <ul class="pv-list">${inj || `<li class="dim small">${t("pvNoInjuries")}</li>`}</ul>
    ${pv.team?.strengths.length || pv.team?.weaknesses.length ? `<details class="pv-more-box">
      <summary>${t("pvStrWeak")}</summary>
      ${pv.team.strengths.length ? `<h5 class="pv-h">${t("pvStrengths")}</h5><ul class="pv-bullets good">${pv.team.strengths.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${pv.team.weaknesses.length ? `<h5 class="pv-h">${t("pvWeaknesses")}</h5><ul class="pv-bullets bad">${pv.team.weaknesses.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    </details>` : ""}
  </div>`;
}

// Side-by-side team ratings; the better rank of each row is highlighted.
const TEAM_ROWS = ["ortg", "drtg", "pace", "pts", "ptsAgainst", "ts", "p3", "p3Against", "oreb", "dreb", "ast", "tov"];
function teamCompare(g) {
  const h = g.preview.home.team, a = g.preview.away.team;
  if (!h || !a) return "";
  const labels = t("teamStat");
  const cell = (x, better, side) => `<td class="cmp-${side}${better ? " better" : ""}">
      <b>${x.value == null ? "-" : fmt1(x.value)}</b>${x.rank ? `<span class="rk">#${x.rank}</span>` : ""}</td>`;
  const row = (k) => {
    const x = h.stats[k], y = a.stats[k];
    const hb = x.rank && y.rank && x.rank < y.rank, ab = x.rank && y.rank && y.rank < x.rank;
    return `<tr>${cell(x, hb, "l")}<td class="cmp-label"><span data-tip="${esc(labels[k][1])}">${labels[k][0]}</span></td>${cell(y, ab, "r")}</tr>`;
  };
  const head = `<thead><tr><th class="cmp-l">${clubMini(g.home)} ${esc(g.home.abbr)}</th><th></th><th class="cmp-r">${esc(g.away.abbr)} ${clubMini(g.away)}</th></tr></thead>`;
  const half = Math.ceil(TEAM_ROWS.length / 2);
  // two half tables side by side: half the height of one long table
  return `<div class="pv-compare">
    <h5 class="pv-h">${t("pvTeamStats", { n: g.preview.teamCount || 20 })}</h5>
    <div class="cmp-grid">
      <table class="cmp">${head}<tbody>${TEAM_ROWS.slice(0, half).map(row).join("")}</tbody></table>
      <table class="cmp">${head}<tbody>${TEAM_ROWS.slice(half).map(row).join("")}</tbody></table>
    </div>
  </div>`;
}

// Who should win, how sure the model is (a bar split home / away) and what decided it.
function predictionBox(g, pick) {
  if (!pick) return "";
  const home = Math.round(pick.homeWin * 100);
  const why = pick.factors.map((f) => `<li><span class="pred-dot ${f.toward === g.home.abbr ? "home" : "away"}"></span>${esc(f.text)}
    <span class="dim">→ ${esc(f.toward)}</span></li>`).join("");
  return `<div class="prediction" data-tip="${esc(t("predNote", { a: pick.accuracy ?? "-" }))}">
    <div class="pred-head"><span class="pv-h">${t("predTitle")}</span>
      <strong>${t("predWins", { t: esc(pick.winner) })}</strong><span class="pred-prob">${Math.round(pick.prob * 100)}%</span></div>
    <div class="pred-bar"><span class="pred-side">${esc(g.home.abbr)} ${home}%</span>
      <span class="proj-bar"><i style="width:${home}%"></i></span><span class="pred-side">${100 - home}% ${esc(g.away.abbr)}</span></div>
    ${why ? `<div class="pred-why"><span class="dim small">${t("predWhy")}:</span><ul>${why}</ul></div>` : ""}
  </div>`;
}

function gamePreview(fid, g) {
  const pv = g.preview;
  const notes = pv.notes;
  return `<div class="preview">
    ${predictionBox(g, pv.prediction)}
    ${teamCompare(g)}
    <div class="pv-sides">${previewSide(fid, g.home, pv.home)}${previewSide(fid, g.away, pv.away)}</div>
    ${notes.length ? `<div class="pv-notes"><h5 class="pv-h">${t("pvNotes")}</h5><ul>${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>` : ""}
  </div>`;
}

async function renderGames(fid, params, token, silent) {
  const round = params.get("r");
  if (!silent) setView(skeletonTable(6));
  let data;
  try {
    data = await api(`/api/league/${fid}/games${round !== null ? `?round=${round}` : ""}`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league } = data;
  const cards = data.games.map((g) => `
    <section class="game-card${openGames.has(g.id) ? " open" : ""}" data-card="${esc(g.id)}">
      ${gameCardHead(g)}
      <div class="game-body">${g.preview ? gamePreview(fid, g) : `${boxTable(g.home)}${boxTable(g.away)}`}</div>
    </section>`).join("");
  setView(`
    ${leagueHeader(league, "games")}
    <div class="toolbar">
      <div class="toolbar-left round-nav inline">${roundArrows(`#/l/${fid}/games`, data.round, league.firstRound, (league.totalRounds || league.currentRound + 1) - 1,
        (x) => `${roundLabel(x)}${x === league.currentRound ? ` ${t("current")}` : ""}`)}
        ${data.state === "live" ? `<span class="badge live">${t("live")}</span>` : ""}</div>
      <div class="toolbar-left">
        <button class="btn" type="button" id="games-open">${t("expandAll")}</button>
        <button class="btn" type="button" id="games-close">${t("collapseAll")}</button>
        <span class="updated">${stamp()}</span>
      </div>
    </div>
    ${cards ? `<div class="games">${cards}</div>` : stateBox(t("noGames"))}
    <p class="note">${t("gamesNote")}</p>`);
  bindRoundSelect(`#/l/${fid}/games`);
  const toggle = (card, open) => {
    card.classList.toggle("open", open);
    card.querySelector(".game-head").setAttribute("aria-expanded", open);
    if (open) openGames.add(card.dataset.card); else openGames.delete(card.dataset.card);
  };
  app.querySelectorAll(".game-head").forEach((btn) =>
    btn.addEventListener("click", () => { const card = btn.closest(".game-card"); toggle(card, !card.classList.contains("open")); }));
  document.getElementById("games-open")?.addEventListener("click", () => app.querySelectorAll(".game-card").forEach((c) => toggle(c, true)));
  document.getElementById("games-close")?.addEventListener("click", () => app.querySelectorAll(".game-card").forEach((c) => toggle(c, false)));
  scheduleRefresh(data.state === "live");
}

// ------------------------------------------------------------------ team

function lineupTable(lineup, state) {
  const upcoming = state === "upcoming";
  const scored = lineup.source !== "roster";
  const colspan = 3 + STAT_DEFS.length + 1;
  let bandShown = false;
  const rows = lineup.players.map((p) => {
    const half = scored && /^B[2-5]$/.test(p.slotLabel || "");
    const finishedGames = p.games.length && p.games.every((g) => g.completed || g.canceled);
    const dnp = !upcoming && finishedGames && !p.roundPlayed;
    let pts;
    if (upcoming) pts = '<span class="dim">-</span>';
    else if (!scored) pts = `<span title="${t("fp")}">${fmt(p.roundPts)}</span>`;
    else if (p.slot === "inactive") pts = '<span class="dim">0</span>';
    else if (dnp) pts = `<span class="dim" title="${t("dnpTitle")}">DNP</span>`;
    else pts = `<span class="team-pts${half ? " half" : ""}">${fmt(p.contrib)}</span>`;
    const band = p.slot === "inactive" && !bandShown
      ? `<tr class="band-row"><td class="sticky" colspan="1">${t("notRegistered")}</td><td colspan="${colspan - 1}"></td></tr>` : "";
    if (p.slot === "inactive") bandShown = true;

    const pill = scored ? `<span class="slot ${p.slot}">${esc(p.slotLabel)}</span>` : `<span class="slot bench">${POS[p.position] || "-"}</span>`;
    return `${band}<tr class="${p.slot}${half ? " half" : ""} clickable" data-player="${p.id}">
      <td class="sticky"><div class="player">${pill}${avatar(p)}<div>
        <span class="player-name">${esc(p.name)}</span>${p.captain ? `<span class="cap" title="${t("captain")}">C</span>` : ""}
        <div class="sub">${POS[p.position] || ""} · ${clubTag(p.club)} ${injuryBadge(p.injury)}</div></div></div></td>
      <td class="num pts-col">${fpLink(p.id, pts)}</td>
      ${upcoming ? statCells(p.season, "avg") : statCells(p.roundLine, "round")}
      <td>${gameCell(p.games)}</td>
      <td class="num">${fpLink(p.id, fmt1(p.avgPts))}</td>
    </tr>`;
  }).join("");
  const s = lineup.scoring;
  const foot = s ? `<tfoot><tr><td class="sticky"><strong>${t("total")}</strong></td><td class="num pts-col"><span class="team-pts">${fmt(s.total)}</span></td><td colspan="${STAT_DEFS.length + 2}"></td></tr></tfoot>` : "";
  const ptsHead = scored ? `<th class="num pts-col" title="${esc(t("teamPtsTitle"))}">${t("teamPts")}</th>` : `<th class="num">${t("fp")}</th>`;
  return `<div class="card table-scroll"><table class="grid roster stats-table">
    <thead><tr><th class="sticky">${t("player")}</th>${ptsHead}
      ${statHeads()}<th>${t("games")}</th><th class="num" title="${esc(t("avgTitle"))}">${t("avg")}</th></tr></thead>
    <tbody>${rows}</tbody>${foot}</table></div>`;
}

function roundSummary(data) {
  const { league, result: res, after, team } = data;
  const state = data.roundState;
  const firstGame = data.lineup.players.flatMap((p) => p.games).filter((g) => !g.completed).map((g) => g.at).sort()[0];
  const stateText = { finished: t("stateFinished"), live: t("stateLive"), upcoming: t("stateUpcoming") }[state];
  const stateBadge = state === "live" ? `<span class="badge live">${stateText}</span>` : `<span class="badge">${stateText}</span>`;
  let main = "";
  if (league.format === "head_to_head" && res.opponent !== undefined) {
    const opp = res.opponent
      ? `<a href="#/l/${league.id}/t/${res.opponent.id}?r=${data.round}">${esc(res.opponent.title)}</a>`
      : `<span class="dim">${t("leagueAvg")}</span>`;
    const score = state === "upcoming" ? '<span class="dim">- : -</span>'
      : `<span class="${res.result === "W" ? "win" : ""}">${fmt(res.points)}</span><span class="sep">:</span><span class="${res.result === "L" ? "win" : ""}">${fmt(res.opponentPoints)}</span>`;
    const verdict = { W: `<span class="res W">${t("won")}</span>`, L: `<span class="res L">${t("lost")}</span>`, T: `<span class="res T">${t("tie")}</span>` }[res.result] || "";
    main = `<div class="versus">
      <div class="side"><strong>${esc(team.title)}</strong></div>
      <div class="score">${score}</div>
      <div class="side right"><strong>${opp}</strong></div>
    </div><div class="verdict">${verdict}</div>`;
  }
  const tiles = [];
  if (league.format !== "head_to_head" && state !== "upcoming") {
    tiles.push([t("roundPoints"), fmt(res.points)], [t("roundRank"), res.roundPosition ?? "-"]);
  }
  if (after) {
    tiles.push([t("posAfter"), `${after.position}<small> / ${league.teamsCount || ""}</small>`]);
    if (league.format === "head_to_head") tiles.push([t("recordAfter"), `${after.wins}-${after.losses}${after.ties ? `-${after.ties}` : ""}`]);
  }
  const sc = data.lineup.scoring;
  if (sc) {
    tiles.push([t("optimal"), fmt(sc.optimal)]);
    tiles.push([t("lostToLineup"), sc.lost ? `−${fmt(sc.lost)}` : "0"]);
  }
  if (state === "live") tiles.push([t("leftToPlay"), res.left ?? 0]);
  const when_ = state === "upcoming" && firstGame ? `<span class="dim">${t("firstGame", { t: when(firstGame) })}</span>` : "";
  return `<div class="card round-card">
    <div class="round-card-head">${stateBadge}${when_}</div>
    ${main}
    ${tiles.length ? `<div class="tiles inner">${tiles.map(([l, v]) => `<div class="tile"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("")}</div>` : ""}
  </div>`;
}

function teamCharts(data) {
  const played = data.history.filter((h) => h.state !== "upcoming" && h.points !== undefined);
  if (!played.length) return { html: "", bind: () => {} };
  const xs = played.map((h) => t("roundShort", { n: h.round + 1 }));
  const pos = lineChart("chart-pos", {
    xs, invert: true, integer: true, yMin: 1, yMax: data.league.teamsCount || Math.max(...played.map((h) => h.position || 1)),
    series: [{ name: t("posSeries"), color: CHART_TEAM, values: played.map((h) => ({ y: h.position ?? null })) }],
    format: (v) => t("posValue", { v }),
  });
  const pts = lineChart("chart-pts", {
    xs, yMin: 0,
    series: [
      { name: data.team.title, color: CHART_TEAM, values: played.map((h) => ({ y: h.points })) },
      { name: t("leagueAvg"), color: CHART_AVG, values: played.map((h) => ({ y: h.leagueAvg ?? null })) },
    ],
  });
  const html = `<div class="charts">
    <div class="card chart-card"><h3 class="chart-title">${t("chartPos")}</h3>${pos.html}</div>
    <div class="card chart-card"><h3 class="chart-title">${t("chartPts")}</h3>${pts.html}</div>
  </div>`;
  return { html, bind: () => { pos.bind(); pts.bind(); } };
}

// The round's real games where this fantasy team has the most players.
function topGamesSection(fid, data) {
  const byGame = new Map();
  for (const p of data.lineup.players) {
    for (const g of p.games || []) {
      if (!p.club) continue;
      const [home, away] = g.home ? [p.club.abbr, g.opponent] : [g.opponent, p.club.abbr];
      const id = `${home}-${away}-${g.at.slice(0, 10)}`;
      const e = byGame.get(id) || { id, g, home, away, logos: {}, players: [] };
      e.logos[p.club.abbr] = p.club.logo;
      e.logos[g.opponent] = g.opponentLogo;
      e.players.push(p);
      byGame.set(id, e);
    }
  }
  const active = (e) => e.players.filter((p) => p.slot !== "inactive").length;
  const top = [...byGame.values()]
    .sort((x, y) => active(y) - active(x) || y.players.length - x.players.length || x.g.at.localeCompare(y.g.at))
    .slice(0, 3);
  if (!top.length) return "";
  const logo = (src) => (src ? `<img class="club-mini md" src="${esc(src)}" alt="" loading="lazy" onerror="this.remove()">` : "");
  const items = top.map((e) => {
    const names = e.players.map((p) => `<span class="${p.slot === "inactive" ? "dim" : ""}">${p.slotLabel ? `<b>${esc(p.slotLabel)}</b> ` : ""}${esc(p.name)}</span>`).join(", ");
    const g = e.g;
    const status = g.completed ? `${g.home ? g.score[0] : g.score[1]}:${g.home ? g.score[1] : g.score[0]}` : g.live ? "LIVE" : when(g.at);
    return `<button type="button" class="tg-item" data-open-game="${esc(e.id)}">
      <span class="tg-teams">${logo(e.logos[e.home])}<strong>${esc(e.home)}</strong><span class="dim">-</span><strong>${esc(e.away)}</strong>${logo(e.logos[e.away])}</span>
      <span class="tg-when">${g.day ? `<span class="day-tag d${g.day}">${t("dayShort", { n: g.day })}</span>` : ""}<span class="dim">${esc(status)}</span></span>
      <span class="tg-count"><b>${active(e)}</b> ${t("tgPlayers")}${e.players.length > active(e) ? ` <span class="dim">${t("tgInactive", { n: e.players.length - active(e) })}</span>` : ""}</span>
      <span class="tg-names">${names}</span>
    </button>`;
  }).join("");
  return `<h2 class="section-title">${t("tgTitle")}</h2>
    <div class="tg-list">${items}</div>`;
}

function historyTable(data) {
  const { league, history } = data;
  if (!history.length) return "";
  const h2h = league.format === "head_to_head";
  const base = `#/l/${league.id}/t/${data.team.id}`;
  const resShort = t("resShort");
  const rows = [...history].reverse().map((h) => {
    const sel = h.round === data.round ? " selected" : "";
    if (h2h) {
      const opp = h.opponent ? esc(h.opponent.title) : `<span class="dim">${t("leagueAvg")}</span>`;
      const res = h.result ? `<span class="res ${h.result}">${resShort[h.result]}</span>`
        : h.state === "live" ? `<span class="badge live">${t("live")}</span>` : `<span class="dim">${t("next")}</span>`;
      const score = h.state === "upcoming" ? '<span class="dim">-</span>' : `${fmt(h.points)} : ${fmt(h.opponentPoints)}`;
      return `<tr class="clickable${sel}" data-href="${base}?r=${h.round}"><td>${roundLabel(h.round)}</td><td>${opp}</td><td class="num">${score}</td><td class="num">${h.position ?? "-"}</td><td class="num">${res}</td></tr>`;
    }
    return `<tr class="clickable${sel}" data-href="${base}?r=${h.round}"><td>${roundLabel(h.round)}</td><td class="num pts-strong">${fmt(h.points)}</td><td class="num">${h.roundPosition ?? "-"}</td><td class="num">${h.position ?? "-"}</td></tr>`;
  }).join("");
  const head = h2h
    ? `<tr><th>${t("rounds")}</th><th>${t("opponent")}</th><th class="num">${t("score")}</th><th class="num">${t("position")}</th><th class="num"></th></tr>`
    : `<tr><th>${t("rounds")}</th><th class="num">${t("points")}</th><th class="num">${t("roundRank")}</th><th class="num">${t("position")}</th></tr>`;
  return `<h2 class="section-title">${t("seasonLog")}</h2>
    <div class="card table-scroll"><table class="grid history"><thead>${head}</thead><tbody>${rows}</tbody></table></div>`;
}

async function renderTeam(fid, tid, params, token, silent) {
  const round = params.get("r");
  if (!silent) setView(skeletonTable(6));
  let data;
  try {
    data = await api(`/api/league/${fid}/team/${tid}${round !== null ? `?round=${round}` : ""}`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league, standing: s, lineup } = data;
  const h2h = league.format === "head_to_head";
  const r = data.round;
  const base = `#/l/${fid}/t/${tid}`;

  const scored = data.history.filter((h) => h.state !== "upcoming" && h.points != null);
  const avgRound = scored.length ? scored.reduce((sum, h) => sum + h.points, 0) / scored.length : null;
  const seasonLine = [
    esc(data.team.owner),
    t("placeOf", { p: s.position, n: league.teamsCount || "" }),
    h2h ? `${s.wins}-${s.losses}${s.ties ? `-${s.ties}` : ""}` : null,
    `${fmt(s.pointsTotal)} ${t("ptsShort")}`,
    avgRound !== null ? `<span data-tip="${esc(t("avgRoundTip", { n: scored.length }))}">${t("avgRound", { v: fmt1(avgRound) })}</span>` : null,
  ].filter(Boolean).map((x) => `<span>${x}</span>`).join("");
  const label = (x) => `${roundLabel(x)}${x === league.currentRound ? ` ${t("current")}` : ""}`;
  const charts = teamCharts(data);
  const statsNote = data.roundState === "upcoming" ? t("statsAvgNote") : t("statsRoundNote", { n: r + 1 });

  setView(`
    <a class="back" href="#/l/${fid}">← ${esc(league.title)}</a>
    <div class="team-head">
      <div>
        <h1 class="page-title">${esc(data.team.title)}</h1>
        <div class="meta-line divided">${seasonLine}</div>
      </div>
    </div>
    ${charts.html}
    <div class="round-nav">
      ${roundArrows(base, r, league.firstRound, league.currentRound, label)}
      ${r !== league.currentRound ? `<a class="link small" href="${base}">${t("toCurrent")}</a>` : ""}
    </div>
    ${roundSummary(data)}
    <h2 class="section-title">${t("lineupTitle", { r: roundLabel(r) })}${lineup.formation && lineup.source !== "roster" ? ` <span class="dim small">${t("formation", { f: esc(lineup.formation) })}</span>` : ""}</h2>
    ${lineup.note ? `<p class="note warn-note">${esc(lineup.note)}</p>` : ""}
    ${lineup.players.length ? lineupTable(lineup, data.roundState) : stateBox(t("lineupNA"))}
    <p class="note">${statsNote} ${lineup.source !== "roster" ? t("multNote") : ""}</p>
    ${historyTable(data)}
    ${topGamesSection(fid, data)}`);

  charts.bind();
  bindRoundSelect(base);
  app.querySelectorAll("[data-open-game]").forEach((b) => b.addEventListener("click", () => {
    openGames.add(b.dataset.openGame);
    location.hash = `#/l/${fid}/games?r=${r}`;
  }));
  app.querySelectorAll("tr[data-href]").forEach((tr) => tr.addEventListener("click", () => (location.hash = tr.dataset.href)));
  scheduleRefresh(data.roundState === "live");
}

// ------------------------------------------------------------------ player modal

function currentLeagueId() {
  const { parts } = parseHash();
  return parts[0] === "l" ? parts[1] : null;
}

async function openPlayer(pid) {
  const fid = currentLeagueId();
  if (!fid) return;
  const token = ++modalToken;
  modalBody.innerHTML = `<div class="state">${t("loading")}</div>`;
  if (!modal.open) modal.showModal();
  let data;
  try {
    data = await api(`/api/league/${fid}/player/${pid}`);
  } catch (e) {
    if (token === modalToken) modalBody.innerHTML = stateBox(e.message, true);
    return;
  }
  if (token !== modalToken) return;
  modalBody.innerHTML = playerView(fid, data);
}

function advContext(x, minutes) {
  if (!x.context) return "";
  const c = x.context;
  return `<div class="adv-ctx">
      <span>${t("ctxAvg")} <b>${fmt1(c.avg)}</b></span>
      <span>${t(c.better === "lower" ? "ctxHighLower" : "ctxHigh")} <b>${fmt1(c.high)}</b></span>
      <span>${t(c.better === "lower" ? "ctxLowLower" : "ctxLow")} <b>${fmt1(c.low)}</b></span>
    </div>
    <div class="adv-ctx-note">${c.better === "lower" ? `${t("lowerBetter")} ` : ""}${t("ctxNote", { m: minutes })}</div>`;
}

// ---- player card: whole season / home / away

let currentPlayer = null;
const LOG_SKIP = ["usg"];  // the player's per-round table: usage only means something over a season  // data of the open player card (the split buttons re-render from it)

// Rounds the player played, newest first; home / away keeps rounds where every game was at home / away.
function playedRounds(data, which) {
  return data.gameLog.filter((g) => g.status === "played" && g.line
    && (which === "all" || (g.games.length && g.games.every((x) => x.home === (which === "home")))));
}

function splitStats(data, which) {
  const rows = playedRounds(data, which);
  const n = rows.length;
  const sum = (f) => rows.reduce((acc, g) => acc + (f(g) ?? 0), 0);
  const mean = (k) => (n ? sum((g) => g.line[k]) / n : null);
  const fpAvg = (list) => (list.length ? list.reduce((acc, g) => acc + (g.fp ?? 0), 0) / list.length : null);
  const pctOf = (made, att) => ({ made, att, pct: att ? Math.round((1000 * made) / att) / 10 : null });
  const shot = (m, a) => pctOf(sum((g) => g.line[m]), sum((g) => g.line[a]));
  const two = shot("p2m", "p2a"), three = shot("p3m", "p3a"), ft = shot("ftm", "fta");
  // one game in the round, so the round's line and FP belong to that game and its opponent
  const single = rows.filter((g) => g.games.length === 1);
  const doubleDouble = (l) => ["pts", "reb", "ast", "stl", "blk"].filter((k) => (l[k] || 0) >= 10).length >= 2;
  const top = data.defense?.top || 0;
  const vsDef = (strong) => single.filter((g) => g.games[0].oppDefRank != null
    && (strong ? g.games[0].oppDefRank <= top : g.games[0].oppDefRank > top));
  const strong = vsDef(true), weak = vsDef(false);
  return {
    games: n, fp: fpAvg(rows), last3: fpAvg(rows.slice(0, 3)), last5: fpAvg(rows.slice(0, 5)),
    dd: single.filter((g) => doubleDouble(g.line)).length,
    defense: { strong: { fp: fpAvg(strong), games: strong.length }, weak: { fp: fpAvg(weak), games: weak.length } },
    line: Object.fromEntries(["min", "pts", "reb", "dreb", "oreb", "ast", "stl", "blk", "eff"].map((k) => [k, mean(k)])),
    shooting: { fg: pctOf(two.made + three.made, two.att + three.att), two, three, ft, games: n },
  };
}

function splitButtons(data) {
  const opts = [["all", t("splitAll")], ["home", t("splitHome")], ["away", t("splitAway")]];
  return `<div class="seg" role="group" aria-label="${esc(t("splitLabel"))}">${opts.map(([w, label]) =>
    `<button type="button" class="seg-btn${w === "all" ? " on" : ""}" data-split="${w}" aria-pressed="${w === "all"}">${label}${
      w === "all" ? "" : ` <span class="seg-n">${playedRounds(data, w).length}</span>`}</button>`).join("")}</div>`;
}

function splitBody(data, which) {
  const p = data.player, tl = t("tiles");
  const st = splitStats(data, which);
  // home vs away: the difference to the other side, when both have games
  const other = which === "all" ? null : splitStats(data, which === "home" ? "away" : "home");
  const cmp = other && st.games && other.games;
  const diff = (a, b, suffix = "") => {
    if (!cmp || a == null || b == null) return "";
    const d = Math.round((a - b) * 10) / 10;
    if (!d) return `<small class="diff">±0${suffix}</small>`;
    return `<small class="diff ${d > 0 ? "up" : "down"}">${d > 0 ? "+" : "−"}${fmt1(Math.abs(d))}${suffix}</small>`;
  };
  // Whole season: BasketNews' own averages (the same as in the lists); home / away: from the game log.
  const all = which === "all";
  const line = all ? p.season || {} : st.line;
  const o = other?.line || {};
  const tiles = [
    [tl.avgFp, fpLink(p.id, fmt1(all ? p.avgPts : st.fp)) + diff(st.fp, other?.fp), "fp"],
    [tl.last3, fmt1(st.last3) + diff(st.last3, other?.last3), null, t("lastTip", { n: 3 })],
    [tl.last5, fmt1(st.last5) + diff(st.last5, other?.last5), null, t("lastTip", { n: 5 })],
    [tl.gp, all ? p.gamesPlayed : st.games],
    [tl.min, fmt1(line.min) + diff(line.min, o.min), "min"], [tl.pts, fmt1(line.pts) + diff(line.pts, o.pts), "pts"],
    [`${tl.reb} <span class="split-head">${t("rebSplitHead")}</span>`,
      line.reb == null ? "-" : `${fmt1(line.reb)}${cmp ? diff(line.reb, o.reb) : `<small class="split">${fmt1(line.dreb)}/${fmt1(line.oreb)}</small>`}`, "reb"],
    [tl.ast, fmt1(line.ast) + diff(line.ast, o.ast), "ast"], [tl.stl, fmt1(line.stl) + diff(line.stl, o.stl), "stl"],
    [tl.blk, fmt1(line.blk) + diff(line.blk, o.blk), "blk"], [tl.eff, fmt1(line.eff) + diff(line.eff, o.eff), "eff"],
    [tl.dd, String(st.dd), null, t("ddTip")],
  ];
  const html = tiles.map(([l, v, key, own]) => {
    const name = l.replace(/<[^>]+>/g, "").trim();
    const tip = own ? `\n${own}` : key ? (key === "reb" ? `\n${t("rebSplitTip")}` : "") + avgTip(key) : "";
    return `<div class="tile"${tip ? ` data-tip="${esc(name + tip)}"` : ""}><div class="label">${l}</div><div class="value">${v}</div></div>`;
  }).join("");
  const none = !all && !st.games ? `<p class="note">${t(which === "home" ? "splitNoHome" : "splitNoAway")}</p>` : "";
  const vs = cmp ? `<p class="note diff-note">${t(which === "home" ? "diffVsAway" : "diffVsHome")}</p>` : "";
  return `${none}<div class="tiles compact">${html}</div>${vs}${defenseSection(data, st.defense)}${shootingSection(all ? data.shooting : st.shooting, cmp ? other.shooting : null)}`;
}

// Average FP against the best and the worst half of the league's defenses (current ranks).
function defenseSection(data, d) {
  const n = data.defense?.teams, top = data.defense?.top;
  if (!n || !top || (!d.strong.games && !d.weak.games)) return "";
  const both = d.strong.games && d.weak.games;
  const tile = (label, x, other, a, b) => {
    const delta = both ? Math.round((x.fp - other.fp) * 10) / 10 : null;
    const diff = delta == null ? "" : delta
      ? `<small class="diff ${delta > 0 ? "up" : "down"}">${delta > 0 ? "+" : "−"}${fmt1(Math.abs(delta))}</small>` : `<small class="diff">±0</small>`;
    const value = x.games ? `${fmt1(x.fp)}${diff}` : '<span class="dim">-</span>';
    return `<div class="tile" data-tip="${esc(`${label}\n${t("defTip", { a, b })}`)}"><div class="label">${label}</div>
      <div class="value">${value}</div><div class="sub dim">${x.games ? t("defGames", { n: x.games }) : t("defNone")}</div></div>`;
  };
  return `<div class="def-block"><h3 class="section-title def-head">${t("defTitle")} <span class="dim small">${t("defSub", { n })}</span>
      <button type="button" class="mini-btn" data-defenses aria-expanded="false">${t("defShow")}</button></h3>
    <div class="def-list" hidden></div>
    <div class="tiles compact def-split">
      ${tile(t("defTop", { n: top }), d.strong, d.weak, 1, top)}
      ${tile(t("defBottom", { n: n - top }), d.weak, d.strong, top + 1, n)}
    </div>${both ? `<p class="note diff-note">${t("defDiffNote")}</p>` : ""}</div>`;
}

// The clubs behind the split: best and worst half by defensive rating, with the player's FP against each.
function defenseLists(d, data) {
  const vs = {};
  for (const g of data.gameLog) {
    if (g.status === "played" && g.games.length === 1) (vs[g.games[0].opponent] ||= []).push(g.fp ?? 0);
  }
  const row = (r) => {
    const fp = r.abbr && vs[r.abbr];
    const avg = fp ? fp.reduce((a, b) => a + b, 0) / fp.length : null;
    return `<li class="def-row"><span class="def-rank">${r.rank}</span>${clubMini(r)}<span class="def-name">${esc(r.name)}</span>
      ${fp ? `<span class="def-fp" data-tip="${esc(t("defFaced", { n: fp.length }))}">${fmt1(avg)} FP</span>` : ""}
      <span class="def-val dim" data-tip="${esc(t("defRating", { v: fmt1(r.value) }))}">${fmt1(r.value)}</span></li>`;
  };
  const top = d.rows.filter((r) => r.rank <= d.top), bottom = d.rows.filter((r) => r.rank > d.top);
  return `<div class="def-cols">
    <div><h4>${t("defListTop", { n: d.top })}</h4><ol class="def-ol">${top.map(row).join("")}</ol></div>
    <div><h4>${t("defListBottom", { n: d.teams - d.top })}</h4><ol class="def-ol">${bottom.map(row).join("")}</ol></div>
  </div>`;
}

async function toggleDefenses(btn) {
  const box = btn.closest(".def-block").querySelector(".def-list");
  const open = box.hidden;
  box.hidden = !open;
  btn.setAttribute("aria-expanded", String(open));
  btn.textContent = t(open ? "defHide" : "defShow");
  if (!open || box.dataset.loaded) return;
  const data = currentPlayer;
  box.innerHTML = `<div class="dim small">${t("loading")}</div>`;
  try {
    const d = await api(`/api/league/${data.league.id}/defenses`);
    if (data !== currentPlayer) return;
    box.innerHTML = defenseLists(d, data);
    box.dataset.loaded = "1";
  } catch (e) {
    box.innerHTML = stateBox(e.message, true);
  }
}

function shotDiff(x, y) {
  if (!y || x.pct == null || y.pct == null) return "";
  const d = Math.round((x.pct - y.pct) * 10) / 10;
  return d ? `<small class="diff ${d > 0 ? "up" : "down"}">${d > 0 ? "+" : "−"}${fmt1(Math.abs(d))}</small>` : `<small class="diff">±0</small>`;
}

// Season shooting: made / attempted totals summed from the round box scores.
function shootingSection(sh, vs = null) {
  if (!sh || !sh.games) return "";
  const rows = [["fg", t("shotFg")], ["two", t("shot2")], ["three", t("shot3")], ["ft", t("shotFt")]];
  return `<h3 class="section-title">${t("shootingTitle")} <span class="dim small">${t("shootingSub", { n: sh.games })}</span></h3>
    <div class="shooting">${rows.map(([key, label]) => {
      const x = sh[key];
      return `<div class="shot-row">
        <span class="shot-label" data-tip="${esc(label + avgTip({ fg: "fg", two: "p2", three: "p3", ft: "ft" }[key]))}">${label}</span>
        <span class="shot-value">${x.pct == null ? '<span class="dim">-</span>' : `<b>${fmt1(x.pct)}%</b>`}${shotDiff(x, vs?.[key])} <span class="dim">(${x.made}/${x.att})</span></span>
        <span class="adv-bar"><i style="width:${Math.max(0, Math.min(100, x.pct ?? 0))}%"></i></span>
      </div>`;
    }).join("")}</div>`;
}

function advancedSection(adv) {
  if (!adv) return `<h3 class="section-title">${t("advTitle")}</h3><p class="note">${t("advNone")}</p>`;
  const link = `<a class="link" href="${esc(adv.url)}" target="_blank" rel="noopener">${t("advLink")}</a>`;
  const groups = adv.groups.filter((g) => g.stats.length).map((g) => `
    <div class="adv-group">
      <h4>${esc(g.title)}</h4>
      ${g.stats.map((x) => `<div class="adv-row">
        <span class="adv-label"><abbr data-tip="${esc(x.title + (x.context ? `\n${t("avgTipPlain", { v: fmt1(x.context.avg) })}\n${t("avgTipWho", { m: adv.contextMinutes || 10 })}` : ""))}" aria-label="${esc(x.title)}">${esc(x.short)}</abbr>${x.context?.better === "lower" ? `<span class="adv-dir" data-tip="${esc(t("lowerBetter"))}" aria-label="${esc(t("lowerBetter"))}">↓</span>` : ""}
          <button type="button" class="info-btn" data-info aria-expanded="false" aria-label="${esc(t("advInfo"))}" data-tip="${esc(t("advInfo"))}">i</button></span>
        <span class="adv-value">${fmt1(x.value)}${x.level ? `<span class="lvl ${x.level}">${t("lvl")[x.level]}</span>` : ""}</span>
        <span class="adv-rank dim" data-tip="${esc(t("rankTip", { n: adv.ranked }))}">${x.rank ? `#${x.rank}` : ""}</span>
        <span class="adv-bar"><i style="width:${Math.max(0, Math.min(100, x.pct ?? 0))}%"></i></span>
        <div class="adv-desc" hidden><p>${esc(x.desc || "")}</p>${advContext(x, adv.contextMinutes || 10)}</div>
      </div>`).join("")}
    </div>`).join("");
  return `<h3 class="section-title">${t("advTitle")}</h3>
    <div class="adv-grid">${groups}</div>
    <p class="note">${t("advNote", { link, n: adv.ranked })}</p>`;
}

function playerView(fid, data) {
  const { player: p, owner, injury } = data;
  const cur = injury.current;
  const ownerHtml = owner
    ? `<a class="badge" href="#/l/${fid}/t/${owner.id}">${esc(t("teamBadge", { t: owner.title || "" }))}</a>`
    : `<span class="badge free">${t("freeAgent")}</span>`;

  const status = cur
    ? `<div class="status-box ${SEVERITY[cur.status] || "mild"}">
        <div class="status-top"><strong>${esc(cur.label)}</strong>${cur.return ? `<span>${esc(t("expectedReturn", { r: cur.return }))}</span>` : ""}</div>
        ${cur.comment ? `<div class="status-comment">${esc(cur.comment)}</div>` : ""}
      </div>`
    : `<div class="status-box ok"><strong>${t("healthy")}</strong><span class="dim">${t("notOnReport")}</span></div>`;

  currentPlayer = data;

  const episodes = injury.episodes.map((e) => {
    const reason = e.reason || t("noReason");
    const span = `${shortDay(e.start)} → ${e.ongoing ? t("now") : shortDay(e.end)}`;
    const missed = e.missedRounds.length ? ` · ${t("missed", { r: e.missedRounds.map((r) => t("roundShort", { n: r + 1 })).join(", ") })}` : "";
    const updates = e.updates.map((u) => `<li><span class="dim">${shortDay(u.date)}</span> ${esc(u.statusLabel)}${u.return ? ` (${esc(u.return)})` : ""}${u.comment ? `: ${esc(u.comment)}` : ""}</li>`).join("");
    return `<li class="episode ${e.kind}">
      <div class="ep-head"><span class="ep-kind">${e.kind === "injury" ? t("kindInjury") : t("kindOther")}</span><strong>${esc(reason[0].toUpperCase() + reason.slice(1))}</strong></div>
      <div class="ep-meta">${span} · ${e.days} ${t("daysShort")}${e.ongoing ? ` ${t("ongoing")}` : ""}${missed}</div>
      <ul class="ep-updates">${updates}</ul>
    </li>`;
  }).join("");

  const log = data.gameLog.map((g) => {
    const games = g.games.map((x) => `${x.home ? "vs" : "@"} ${esc(x.opponent)}${x.score ? ` ${x.score[0] > x.score[1] ? "W" : "L"} ${x.score[0]}:${x.score[1]}` : ""}`).join(", ") || '<span class="dim">-</span>';
    if (g.status === "played") {
      return `<tr><td class="sticky">${roundLabel(g.round)}</td><td>${games}</td><td class="num pts-strong">${fpLink(p.id, fmt(g.fp))}</td>${statCells(g.line, "round", LOG_SKIP)}</tr>`;
    }
    const why = { dnp: `${t("didNotPlay")}${g.reason ? `: ${esc(g.reason)}` : ""}`, "no-game": t("teamNoGame"), pending: t("notPlayedYet") }[g.status];
    return `<tr><td class="sticky">${roundLabel(g.round)}</td><td>${games}</td><td colspan="${STAT_DEFS.length + 1 - LOG_SKIP.length}" class="${g.status === "dnp" ? "dnp" : "dim"}">${why}</td></tr>`;
  }).join("");

  const next = data.nextGames?.length
    ? `${roundLabel(data.league.currentRound)}: ${data.nextGames.map((g) => `${g.home ? "vs" : "@"} ${esc(g.opponent)} ${g.score ? `${g.score[0]}:${g.score[1]}` : when(g.at)}`).join(", ")}`
    : "";
  const link = `<a class="link" href="${esc(injury.reportUrl || "#")}" target="_blank" rel="noopener">${t("injuryReport")}</a>`;

  return `
    <div class="pm-head">
      ${avatar(p, "lg")}
      <div class="pm-title">
        <h2>${esc(p.name)}</h2>
        <div class="meta-line divided">${clubCell(p.club)}<span>${t("pos")[p.position] || ""}</span>${p.number != null ? `<span>#${p.number}</span>` : ""}</div>
        <div class="meta-line" style="margin-top:8px">${ownerHtml}
          ${data.proballers ? `<a class="badge ext" href="${esc(data.proballers)}" target="_blank" rel="noopener" data-tip="${esc(t("proballersTitle"))}">${t("proballers")} ↗</a>` : ""}
          ${next ? `<span class="dim small">${next}</span>` : ""}</div>
      </div>
      <button class="close" type="button" data-close aria-label="${t("close")}">×</button>
    </div>
    ${status}
    ${splitButtons(data)}
    <div id="pv-split">${splitBody(data, "all")}</div>
    ${advancedSection(data.advanced)}
    <h3 class="section-title">${t("injuryHistory")}</h3>
    <p class="summary">${esc(injury.summary)}</p>
    ${episodes ? `<ul class="episodes">${episodes}</ul>` : ""}
    <p class="note">${t("historyNote", { link })}</p>
    <h3 class="section-title">${t("rounds")}</h3>
    <div class="card table-scroll"><table class="grid log stats-table">
      <thead><tr><th class="sticky">${t("rounds")}</th><th>${t("games")}</th><th class="num">FP</th>${statHeads(false, true, LOG_SKIP)}</tr></thead>
      <tbody>${log || `<tr><td colspan="${STAT_DEFS.length + 3}" class="dim">${t("noRoundsPlayed")}</td></tr>`}</tbody>
    </table></div>`;
}

modal.addEventListener("click", (e) => {
  const split = e.target.closest("[data-split]");
  if (split && currentPlayer) {
    document.getElementById("pv-split").innerHTML = splitBody(currentPlayer, split.dataset.split);
    modal.querySelectorAll("[data-split]").forEach((b) => {
      b.classList.toggle("on", b === split);
      b.setAttribute("aria-pressed", String(b === split));
    });
    return;
  }
  const defenses = e.target.closest("[data-defenses]");
  if (defenses) {
    toggleDefenses(defenses);
    return;
  }
  const info = e.target.closest("[data-info]");
  if (info) {
    const desc = info.closest(".adv-row").querySelector(".adv-desc");
    desc.hidden = !desc.hidden;
    info.setAttribute("aria-expanded", String(!desc.hidden));
    info.classList.toggle("on", !desc.hidden);
    return;
  }
  if (e.target === modal || e.target.closest("[data-close]")) modal.close();
  if (e.target.closest("a[href^='#']")) modal.close();
});

// ---- FP by round: a window of its own (it can open on top of the player card)

const chartModal = document.getElementById("chart-modal");
const chartBody = document.getElementById("chart-modal-body");
let chartToken = 0;

async function openFpChart(pid) {
  const fid = currentLeagueId();
  if (!fid) return;
  const token = ++chartToken;
  chartBody.innerHTML = `<div class="state">${t("loading")}</div>`;
  if (!chartModal.open) chartModal.showModal();
  let data;
  try {
    data = await api(`/api/league/${fid}/player/${pid}`);
  } catch (e) {
    if (token === chartToken) chartBody.innerHTML = stateBox(e.message, true);
    return;
  }
  if (token !== chartToken) return;
  const view = fpChartView(data);
  chartBody.innerHTML = view.html;
  view.bind();
}

function fpChartView(data) {
  const p = data.player;
  const log = [...data.gameLog].reverse();  // oldest round first
  const played = log.filter((g) => g.status === "played" && g.fp != null);
  const games = (g) => g.games.map((x) => `${x.home ? "vs" : "@"} ${x.opponent}`).join(", ");
  const why = { dnp: t("didNotPlay"), "no-game": t("teamNoGame"), pending: t("notPlayedYet") };
  const heads = log.map((g) => [roundLabel(g.round), games(g), g.status === "played" ? "" : why[g.status]].filter(Boolean).join(" · "));
  const head = `<div class="pm-head">
      ${avatar(p, "lg")}
      <div class="pm-title"><h2>${esc(p.name)}</h2>
        <div class="meta-line divided">${clubCell(p.club)}<span>${t("pos")[p.position] || ""}</span><span>${t("fpChartTitle")}</span></div></div>
      <button class="close" type="button" data-close aria-label="${t("close")}">×</button>
    </div>`;
  if (!played.length) return { html: `${head}<p class="note">${t("fpChartNone")}</p>`, bind: () => {} };
  const best = played.reduce((a, b) => (b.fp > a.fp ? b : a));
  const worst = played.reduce((a, b) => (b.fp < a.fp ? b : a));
  const chart = lineChart("fp-chart", {
    xs: log.map((g) => t("roundShort", { n: g.round + 1 })), heads,
    series: [
      { name: "FP", color: CHART_TEAM, values: log.map((g) => ({ y: g.status === "played" ? g.fp : null })) },
      { name: t("fpSeasonAvg"), color: CHART_AVG, values: log.map(() => ({ y: p.avgPts })) },
    ],
    format: (v) => `${fmt1(v)} FP`,
  });
  const tile = (label, value, sub = "") => `<div class="tile"><div class="label">${label}</div><div class="value">${value}</div>${sub ? `<div class="sub dim">${sub}</div>` : ""}</div>`;
  return {
    html: `${head}
      <div class="tiles compact">
        ${tile(t("tiles").avgFp, fmt1(p.avgPts))}
        ${tile(t("fpBest"), fmt1(best.fp), `${roundLabel(best.round)} · ${esc(games(best))}`)}
        ${tile(t("fpWorst"), fmt1(worst.fp), `${roundLabel(worst.round)} · ${esc(games(worst))}`)}
        ${tile(t("fpPlayed"), `${played.length}/${log.length}`)}
      </div>
      <div class="card chart-card fp-chart-card">${chart.html}</div>
      <p class="note">${t("fpChartNote")}</p>`,
    bind: chart.bind,
  };
}

chartModal.addEventListener("click", (e) => {
  if (e.target === chartModal || e.target.closest("[data-close]")) chartModal.close();
});

// Any fantasy-points value with data-fp-chart opens the chart (before the row's own click).
document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-fp-chart]");
  if (!link) return;
  e.preventDefault();
  e.stopPropagation();
  openFpChart(link.dataset.fpChart);
}, true);

app.addEventListener("click", (e) => {
  const info = e.target.closest("[data-award-info]");
  if (info) {
    const text = info.closest(".award").querySelector(".award-info");
    text.hidden = !text.hidden;
    info.setAttribute("aria-expanded", String(!text.hidden));
    info.classList.toggle("on", !text.hidden);
    return;
  }
  if (e.target.closest(".award-info")) return;
  const row = e.target.closest("[data-player]");
  if (row && !e.target.closest("a")) openPlayer(row.dataset.player);
});

// ------------------------------------------------------------------ router

async function route(silent = false) {
  const token = ++renderToken;
  closeOpenMenu?.();
  clearTimeout(refreshTimer);
  animateView = !silent;
  if (!silent) window.scrollTo(0, 0);
  const { parts, params } = parseHash();

  if (parts[0] !== "l" || !parts[1]) return renderHome(token);

  const fid = parts[1];
  if (!leaguesCache) {
    try { await loadLeagues(); } catch { /* nav is optional */ }
    if (token !== renderToken) return;
  }
  renderSwitcher(fid);

  if (parts[2] === "t" && parts[3]) return renderTeam(fid, parts[3], params, token, silent);
  if (parts[2] === "rounds") return renderRounds(fid, params, token, silent);
  if (parts[2] === "records") return renderRecords(fid, params, token, silent);
  if (parts[2] === "games") return renderGames(fid, params, token, silent);
  if (parts[2] === "players") return renderPlayerList(fid, "all", token, silent);
  if (parts[2] === "free-agents") return renderPlayerList(fid, "free", token, silent);
  if (parts[2] === "draft") return renderDraft(fid, params, token, silent);
  if (parts[2] === "transfers") return renderTransfers(fid, params, token, silent);
  if (parts[2] === "injuries") return renderInjuries(fid, params, token, silent);
  return renderStandings(fid, params, token, silent);
}

window.addEventListener("hashchange", () => route());
applyLangChrome();
route();
