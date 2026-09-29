"""Lithuanian wording for the BasketNews.com injury report.

The .com report is kept up to date more often than the .lt one, so it stays the source
and its short, formulaic comments are translated here clause by clause. Injury phrases
are assembled from side + body part + injury type so that Lithuanian cases and genders
agree ("kairio kelio trauma", "dešinės šlaunies dvigalvio raumens plyšimas").
translate() returns None when a comment holds something it does not understand.
"""
import re

# Body parts: English -> (genitive, gender of the word that a side/adjective agrees with).
PARTS = {
    "knee": ("kelio", "m"),
    "kneecap": ("girnelės", "f"),
    "ankle": ("čiurnos", "f"),
    "foot": ("pėdos", "f"),
    "heel": ("kulno", "m"),
    "toe": ("kojos piršto", "f"),
    "big toe": ("kojos nykščio", "f"),
    "leg": ("kojos", "f"),
    "lower leg": ("blauzdos", "f"),
    "calf": ("blauzdos", "f"),
    "shin": ("blauzdos", "f"),
    "thigh": ("šlaunies", "f"),
    "thigh biceps": ("šlaunies dvigalvio raumens", "f"),
    "biceps femoris": ("šlaunies dvigalvio raumens", "f"),
    "hamstring": ("šlaunies užpakalinio raumens", "f"),
    "quad": ("šlaunies keturgalvio raumens", "f"),
    "quads": ("šlaunies keturgalvio raumens", "f"),
    "quadriceps": ("šlaunies keturgalvio raumens", "f"),
    "adductor": ("pritraukiamojo raumens", "m"),
    "adductor muscle": ("pritraukiamojo raumens", "m"),
    "groin": ("kirkšnies", "f"),
    "hip": ("klubo", "m"),
    "hip flexor": ("klubo lenkiamojo raumens", "m"),
    "glute": ("sėdmens raumens", "m"),
    "back": ("nugaros", "f"),
    "lower back": ("apatinės nugaros dalies", "f"),
    "spine": ("stuburo", "m"),
    "neck": ("kaklo", "m"),
    "shoulder": ("peties", "m"),
    "arm": ("rankos", "f"),
    "elbow": ("alkūnės", "f"),
    "wrist": ("riešo", "m"),
    "hand": ("rankos", "f"),
    "finger": ("piršto", "m"),
    "thumb": ("nykščio", "m"),
    "metacarpal": ("delnakaulio", "m"),
    "head": ("galvos", "f"),
    "face": ("veido", "m"),
    "nose": ("nosies", "f"),
    "eye": ("akies", "f"),
    "jaw": ("žandikaulio", "m"),
    "rib": ("šonkaulio", "m"),
    "ribs": ("šonkaulių", "m"),
    "chest": ("krūtinės", "f"),
    "abdominal": ("pilvo raumenų", "m"),
    "abdomen": ("pilvo", "m"),
    "oblique": ("įstrižinio pilvo raumens", "m"),
    "achilles": ("Achilo sausgyslės", "f"),
    "achilles tendon": ("Achilo sausgyslės", "f"),
    "patellar tendon": ("girnelės sausgyslės", "f"),
    "tendon": ("sausgyslės", "f"),
    "acl": ("kryžminio raiščio", "m"),
    "mcl": ("vidinio šoninio raiščio", "m"),
    "meniscus": ("menisko", "m"),
    "ligament": ("raiščio", "m"),
    "ligaments": ("raiščių", "m"),
    "muscle": ("raumens", "m"),
    "muscles": ("raumenų", "m"),
    "plantar fascia": ("pado fascijos", "f"),
}
# Adjectives in front of a body part: (masculine, feminine) genitive.
PART_ADJ = {
    "left": ("kairio", "kairės"), "right": ("dešinio", "dešinės"),
    "medial": ("vidinio", "vidinės"), "lateral": ("šoninio", "šoninės"),
    "lower": ("apatinio", "apatinės"), "upper": ("viršutinio", "viršutinės"),
    "first": ("pirmojo", "pirmosios"), "second": ("antrojo", "antrosios"),
    "third": ("trečiojo", "trečiosios"), "fourth": ("ketvirtojo", "ketvirtosios"),
    "fifth": ("penktojo", "penktosios"),
}
# Injury types: English -> (nominative, genitive, gender).
TYPES = {
    "injury": ("trauma", "traumos", "f"), "injured": ("trauma", "traumos", "f"),
    "issue": ("problema", "problemos", "f"), "problem": ("problema", "problemos", "f"),
    "issues": ("problemos", "problemų", "fp"), "problems": ("problemos", "problemų", "fp"),
    "strain": ("patempimas", "patempimo", "m"), "strained": ("patempimas", "patempimo", "m"),
    "pulled": ("patempimas", "patempimo", "m"),
    "sprain": ("patempimas", "patempimo", "m"), "sprained": ("patempimas", "patempimo", "m"),
    "tear": ("plyšimas", "plyšimo", "m"), "torn": ("plyšimas", "plyšimo", "m"),
    "rupture": ("plyšimas", "plyšimo", "m"), "ruptured": ("plyšimas", "plyšimo", "m"),
    "fracture": ("lūžis", "lūžio", "m"), "fractured": ("lūžis", "lūžio", "m"),
    "broken": ("lūžis", "lūžio", "m"), "break": ("lūžis", "lūžio", "m"),
    "stress fracture": ("stresinis lūžis", "stresinio lūžio", "m"),
    "contusion": ("sumušimas", "sumušimo", "m"), "bruise": ("sumušimas", "sumušimo", "m"),
    "bruised": ("sumušimas", "sumušimo", "m"), "bone bruise": ("kaulo sumušimas", "kaulo sumušimo", "m"),
    "soreness": ("skausmas", "skausmo", "m"), "sore": ("skausmas", "skausmo", "m"),
    "pain": ("skausmas", "skausmo", "m"),
    "discomfort": ("diskomfortas", "diskomforto", "m"),
    "inflammation": ("uždegimas", "uždegimo", "m"), "tendinitis": ("uždegimas", "uždegimo", "m"),
    "tendonitis": ("uždegimas", "uždegimo", "m"),
    "surgery": ("operacija", "operacijos", "f"), "operation": ("operacija", "operacijos", "f"),
    "dislocation": ("išnirimas", "išnirimo", "m"), "dislocated": ("išnirimas", "išnirimo", "m"),
    "spasm": ("spazmas", "spazmo", "m"), "spasms": ("spazmai", "spazmų", "mp"),
    "tightness": ("sustingimas", "sustingimo", "m"), "stiffness": ("sustingimas", "sustingimo", "m"),
    "edema": ("edema", "edemos", "f"), "overload": ("perkrova", "perkrovos", "f"),
    "fatigue": ("nuovargis", "nuovargio", "m"),
}
# Words in front of the injury type: (masc, fem, masc plural, fem plural) nominative and genitive.
QUALIFIERS = {
    "minor": (("lengvas", "lengva", "lengvi", "lengvos"), ("lengvo", "lengvos", "lengvų", "lengvų")),
    "mild": (("lengvas", "lengva", "lengvi", "lengvos"), ("lengvo", "lengvos", "lengvų", "lengvų")),
    "slight": (("lengvas", "lengva", "lengvi", "lengvos"), ("lengvo", "lengvos", "lengvų", "lengvų")),
    "light": (("lengvas", "lengva", "lengvi", "lengvos"), ("lengvo", "lengvos", "lengvų", "lengvų")),
    "small": (("nedidelis", "nedidelė", "nedideli", "nedidelės"), ("nedidelio", "nedidelės", "nedidelių", "nedidelių")),
    "severe": (("sunkus", "sunki", "sunkūs", "sunkios"), ("sunkaus", "sunkios", "sunkių", "sunkių")),
    "serious": (("rimtas", "rimta", "rimti", "rimtos"), ("rimto", "rimtos", "rimtų", "rimtų")),
    "partial": (("dalinis", "dalinė", "daliniai", "dalinės"), ("dalinio", "dalinės", "dalinių", "dalinių")),
    "undisclosed": (("neatskleistas", "neatskleista", "neatskleisti", "neatskleistos"),
                    ("neatskleisto", "neatskleistos", "neatskleistų", "neatskleistų")),
    "unspecified": (("nenurodytas", "nenurodyta", "nenurodyti", "nenurodytos"),
                    ("nenurodyto", "nenurodytos", "nenurodytų", "nenurodytų")),
    "new": (("naujas", "nauja", "nauji", "naujos"), ("naujo", "naujos", "naujų", "naujų")),
    "recurring": (("pasikartojantis", "pasikartojanti", "pasikartojantys", "pasikartojančios"),
                  ("pasikartojančio", "pasikartojančios", "pasikartojančių", "pasikartojančių")),
    "muscular": (("raumenų", "raumenų", "raumenų", "raumenų"), ("raumenų", "raumenų", "raumenų", "raumenų")),
}
GENDER_INDEX = {"m": 0, "f": 1, "mp": 2, "fp": 3}
FILLER = {"a", "an", "the", "his", "her", "of", "on", "to", "with"}

# Whole reasons that are not built from parts.
WHOLE = {
    "coach's decision": "trenerio sprendimas", "coaches decision": "trenerio sprendimas",
    "coach decision": "trenerio sprendimas", "coaching decision": "trenerio sprendimas",
    "technical decision": "trenerio sprendimas",
    "undisclosed": "neatskleista priežastis", "unknown": "nežinoma priežastis",
    "not injured": "ne trauma", "no injury": "ne trauma",
    "illness": "liga", "ill": "liga", "sick": "liga", "sickness": "liga", "flu": "gripas",
    "cold": "peršalimas", "virus": "virusas", "viral infection": "virusinė infekcija",
    "infection": "infekcija", "fever": "karščiavimas", "covid": "COVID-19", "covid-19": "COVID-19",
    "concussion": "smegenų sukrėtimas", "concussion protocol": "smegenų sukrėtimo protokolas",
    "personal reasons": "asmeninės priežastys", "personal": "asmeninės priežastys",
    "family reasons": "šeimos aplinkybės", "family matters": "šeimos aplinkybės",
    "paternity leave": "tėvystės atostogos", "birth of his child": "vaiko gimimas",
    "rest": "poilsis", "load management": "krūvio valdymas", "rest day": "poilsis",
    "suspension": "diskvalifikacija", "suspended": "diskvalifikuotas",
    "visa issues": "vizos problemos", "visa issue": "vizos problemos",
    "contract issues": "kontrakto reikalai", "transfer": "perėjimas į kitą klubą",
    "out of the squad": "neįtrauktas į sudėtį", "not in the squad": "neįtrauktas į sudėtį",
    "not included in the euroleague roster": "neįtrauktas į Eurolygos sudėtį",
    "not included in the eurocup roster": "neįtrauktas į Europos taurės sudėtį",
    "not included in the roster": "neįtrauktas į sudėtį",
    "not registered": "neregistruotas", "not registered yet": "dar neregistruotas",
    "not yet registered": "dar neregistruotas", "unregistered": "neregistruotas",
    "day-to-day": "būklė vertinama kasdien", "day to day": "būklė vertinama kasdien",
    "national team": "rinktinės reikalai", "national team duty": "rinktinės reikalai",
    "rehab": "reabilitacija", "rehabilitation": "reabilitacija", "recovery": "atsigavimas",
    "surgery": "operacija", "tendinitis": "sausgyslės uždegimas", "tendonitis": "sausgyslės uždegimas",
    "muscle issue": "raumenų problema", "muscle issues": "raumenų problemos",
    "muscle problem": "raumenų problema", "muscle problems": "raumenų problemos",
    "muscle injury": "raumens trauma", "muscular injury": "raumens trauma",
    "left the team": "paliko komandą", "left the club": "paliko klubą", "released": "atleistas",
    "traded": "iškeistas", "season-ending injury": "sezoną baigusi trauma",
}

MONTH_GEN = {"january": "sausio", "february": "vasario", "march": "kovo", "april": "balandžio", "may": "gegužės",
             "june": "birželio", "july": "liepos", "august": "rugpjūčio", "september": "rugsėjo",
             "october": "spalio", "november": "lapkričio", "december": "gruodžio"}
MONTH_NOM = {"january": "sausis", "february": "vasaris", "march": "kovas", "april": "balandis", "may": "gegužė",
             "june": "birželis", "july": "liepa", "august": "rugpjūtis", "september": "rugsėjis",
             "october": "spalis", "november": "lapkritis", "december": "gruodis"}
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "a couple of": 2, "a few": "kelias", "several": "kelias", "few": "kelias"}
# Accusative for "į ..." with EuroLeague / EuroCup cities; others stay as written.
CITY_ACC = {
    "kaunas": "Kauną", "vilnius": "Vilnių", "istanbul": "Stambulą", "belgrade": "Belgradą",
    "athens": "Atėnus", "piraeus": "Pirėjų", "madrid": "Madridą", "barcelona": "Barseloną",
    "valencia": "Valensiją", "vitoria": "Vitoriją", "milan": "Milaną", "bologna": "Boloniją",
    "munich": "Miuncheną", "berlin": "Berlyną", "paris": "Paryžių", "lyon": "Lioną",
    "villeurbanne": "Viljorbaną", "monaco": "Monaką", "tel aviv": "Tel Avivą", "dubai": "Dubajų",
    "zagreb": "Zagrebą", "ljubljana": "Liubliana", "podgorica": "Podgoricą", "bursa": "Bursą",
}
COUNTRY_GEN = {"greek": "Graikijos", "spanish": "Ispanijos", "turkish": "Turkijos", "israeli": "Izraelio",
               "serbian": "Serbijos", "french": "Prancūzijos", "italian": "Italijos", "german": "Vokietijos",
               "lithuanian": "Lietuvos", "croatian": "Kroatijos", "montenegrin": "Juodkalnijos",
               "slovenian": "Slovėnijos", "adriatic": "Adrijos"}
EVENT_LOC = {  # "played in ..." / "during ..." -> locative
    "domestic league": "šalies lygoje", "domestic cup": "šalies taurėje",
    "domestic league game": "šalies lygos rungtynėse", "domestic cup game": "šalies taurės rungtynėse",
    "euroleague": "Eurolygoje", "eurocup": "Europos taurėje", "national team": "rinktinėje",
    "preseason": "pasiruošimo rungtynėse", "pre-season": "pasiruošimo rungtynėse",
    "friendly": "kontrolinėse rungtynėse", "friendly game": "kontrolinėse rungtynėse",
    "practice": "treniruotėje", "training": "treniruotėje", "warm-up": "apšilime", "warmup": "apšilime",
    "aba league": "ABA lygoje", "acb": "ACB lygoje", "lkl": "LKL",
}
COMPETITION_GEN = {"super cup": "Supertaurės", "supercup": "Supertaurės", "cup": "taurės", "league": "lygos",
                   "euroleague": "Eurolygos", "eurocup": "Europos taurės"}
STAGE_LOC = {"final": "finale", "finals": "finale", "semifinal": "pusfinalyje", "semi-final": "pusfinalyje",
             "semifinals": "pusfinalyje", "1/2": "pusfinalyje", "quarterfinal": "ketvirtfinalyje",
             "quarter-final": "ketvirtfinalyje", "1/4": "ketvirtfinalyje", "game": "rungtynėse",
             "match": "rungtynėse", "third place game": "rungtynėse dėl 3 vietos"}


def _clean(text):
    return " ".join((text or "").split())


def _key(text):
    return _clean(text).strip(" .;:").lower().replace("’", "'")


def _cap(text):
    return text[:1].upper() + text[1:] if text else text


def _lower_first(text):
    # Keep proper nouns ("Achilo", "Eurolygos", "COVID-19") capitalised.
    if not text or text[:2].isupper() or text.split(" ")[0] in ("Achilo", "Eurolygos", "Europos", "ABA"):
        return text
    return text[:1].lower() + text[1:]


def _match_prefix(words, i, table, longest=3):
    for n in range(longest, 0, -1):
        key = " ".join(words[i:i + n])
        if len(words) >= i + n and key in table:
            return key, n
    return None, 0


def injury_phrase(text, case="nom"):
    """'Left knee ACL injury' -> 'kairio kelio kryžminio raiščio trauma'; None if unknown words remain."""
    parsed = _injury(text)
    return parsed[0 if case == "nom" else 1] if parsed else None


def _injury(text):
    """(nominative, genitive, gender) of an injury phrase, or None."""
    t = _key(text)
    t = re.sub(r"[(),]", " ", t)
    # "fractured fourth metacarpal in his left hand": the place goes first in Lithuanian.
    m = re.match(r"^(.*?)\s+in\s+(?:his|her|the)\s+(.+)$", t)
    if m:
        t = f"{m.group(2)} {m.group(1)}"
    words = [w for w in t.replace("-", " - ").split() if w != "-"]
    words = [w for w in words if w not in FILLER]
    if not words:
        return None
    quals, side, parts, kind = [], None, [], None
    pending_adj = []
    i = 0
    while i < len(words):
        key, n = _match_prefix(words, i, PARTS)
        if key:
            parts.append((PARTS[key], pending_adj))
            pending_adj = []
            i += n
            continue
        key, n = _match_prefix(words, i, TYPES, 2)
        if key and not kind:
            kind = TYPES[key]
            i += n
            continue
        w = words[i]
        if w in ("left", "right") and not side and not parts:
            side = w
        elif w in PART_ADJ:
            pending_adj.append(w)
        elif w in QUALIFIERS:
            quals.append(w)
        else:
            return None
        i += 1
    if pending_adj:
        return None
    if not parts and not kind:
        return None
    if not kind:
        kind = TYPES["injury"]
    nom, gen, gender = kind
    g = GENDER_INDEX[gender]
    place = []
    for idx, ((word, pg), adjs) in enumerate(parts):
        k = 0 if pg == "m" else 1
        if idx == 0 and side:
            place.append(PART_ADJ[side][k])
        place.extend(PART_ADJ[a][k] for a in adjs)
        place.append(word)
    phrase = [" ".join([QUALIFIERS[q][c][g] for q in quals] + place + [noun])
              for c, noun in ((0, nom), (1, gen))]
    return phrase[0], phrase[1], gender


def reason(text, case="nom"):
    """A short reason: whole phrase or an injury phrase."""
    k = _key(text)
    if not k:
        return None
    if k in WHOLE:
        whole = WHOLE[k]
        if case == "gen":
            return {"trauma": "traumos", "operacija": "operacijos", "liga": "ligos"}.get(whole)
        return whole
    k = re.sub(r"^(?:suffered|sustained|has|with)\s+(?:an?\s+)?", "", k)
    m = re.match(r"^(.*?)\s*\((?:out )?until (\w+)\)$", k)
    if m:
        base = injury_phrase(m.group(1), case)
        month = MONTH_GEN.get(m.group(2))
        return f"{base} (nežais iki {month})" if base and month else None
    return injury_phrase(k, case)


def _round_loc(n):
    return f"{n} ture"


def _duration(text, case="acc"):
    """'several weeks' -> 'kelias savaites'; 'three to four weeks' -> '3-4 savaičių' (gen)."""
    t = _key(text)
    m = re.match(r"^(\w+(?: \w+)?)(?:\s+to\s+|\s*-\s*)(\w+)\s+(weeks?|days?|months?)$", t)
    if m:
        a, b = NUMBERS.get(m.group(1), m.group(1)), NUMBERS.get(m.group(2), m.group(2))
        unit = {"week": ("savaites", "savaičių"), "day": ("dienas", "dienų"),
                "month": ("mėnesius", "mėnesių")}[m.group(3).rstrip("s")]
        return f"{a}-{b} {unit[0 if case == 'acc' else 1]}"
    m = re.match(r"^(a couple of|a few|several|few|\w+)\s+(weeks?|days?|months?)$", t)
    if m:
        n = NUMBERS.get(m.group(1), m.group(1))
        unit = m.group(2).rstrip("s")
        if n == "kelias":
            return {"week": ("kelias savaites", "kelių savaičių"), "day": ("kelias dienas", "kelių dienų"),
                    "month": ("kelis mėnesius", "kelių mėnesių")}[unit][0 if case == "acc" else 1]
        if isinstance(n, str) and not n.isdigit():
            return None
        n = int(n)
        forms = {"week": ("savaitę", "savaites", "savaičių", "savaitės"), "day": ("dieną", "dienas", "dienų", "dienos"),
                 "month": ("mėnesį", "mėnesius", "mėnesių", "mėnesio")}[unit]
        if case == "gen":
            return f"{n} {forms[3] if n == 1 else forms[2]}"
        if n % 10 == 1 and n % 100 != 11:
            return f"{n} {forms[0]}"
        if 2 <= n % 10 <= 9 and not 12 <= n % 100 <= 19:
            return f"{n} {forms[1]}"
        return f"{n} {forms[2]}"
    return None


def event_loc(text):
    """'Greek SuperCup semifinal' -> 'Graikijos Supertaurės pusfinalyje'."""
    t = _key(text)
    if t in EVENT_LOC:
        return EVENT_LOC[t]
    m = re.match(r"^round (\d+)(?: game)?(?: (?:vs\.?|against) (.+))?$", t)
    if m:
        team = _team(text, m.group(2))
        return f"{m.group(1)} turo rungtynėse" + (f" prieš {team}" if team else "")
    words = t.split()
    out = []
    if words and words[0] in COUNTRY_GEN:
        out.append(COUNTRY_GEN[words.pop(0)])
    rest = " ".join(words)
    comp = next((c for c in sorted(COMPETITION_GEN, key=len, reverse=True) if rest.startswith(c)), None)
    if not comp:
        return None
    out.append(COMPETITION_GEN[comp])
    stage = rest[len(comp):].strip()
    m = re.match(r"^(.*?)\s*\((\d\d\.\d\d)\)$", stage)
    date = None
    if m:
        stage, date = m.group(1), m.group(2)
    stage_lt = STAGE_LOC.get(stage or "game")
    if not stage_lt:
        return None
    out.append(stage_lt)
    return " ".join(out) + (f" ({date})" if date else "")


def _team(original, lowered):
    """Opponent name in its original spelling."""
    if not lowered:
        return None
    i = original.lower().rfind(lowered)
    return original[i:i + len(lowered)].strip(" .") if i >= 0 else lowered


def _city(original):
    return CITY_ACC.get(original.lower(), original.strip())


PARTICIPLE = {"m": ("patirtas", "patirto"), "f": ("patirta", "patirtos"),
              "mp": ("patirti", "patirtų"), "fp": ("patirtos", "patirtų")}


def _gender(text):
    parsed = _injury(re.sub(r"^(?:suffered|sustained|has|with)\s+(?:an?\s+)?", "", _key(text)))
    return parsed[2] if parsed else None


def _when(what, source, when, case="nom"):
    """'kairio riešo lūžis' + '1 turo rungtynėse' -> '..., patirtas 1 turo rungtynėse'."""
    g = _gender(source)
    if not g:
        return f"{what} ({when})"
    return f"{what}, {PARTICIPLE[g][0 if case == 'nom' else 1]} {when}"


# Clause patterns: (regex on the lowercased clause, builder(match, original clause) -> str | None)
def _dnp(m, _):
    where = []
    if m.group(1):
        where.append(_round_loc(m.group(1)))
    if m.group(2):
        where.append(EVENT_LOC["domestic " + m.group(2)])
    return "nežaidė " + " ir ".join(where)


def _played(m, orig):
    loc = event_loc(orig[m.start(1):m.end(1)])
    return f"žaidė {loc}" if loc else None


def _suffered(m, orig):
    what = reason(m.group(1))
    when = event_loc(orig[m.start(2):m.end(2)])
    return _when(what, m.group(1), when) if what and when else None


def _suffered_after(m, orig):
    what = reason(orig[m.start(1):m.end(1)])
    extra = reason(m.group(2)) if m.group(2) else None
    when = event_loc(orig[m.start(3):m.end(3)])
    if not what or not when or (m.group(2) and not extra):
        return None
    return _when(what + (f" ({extra})" if extra else ""), m.group(1), when)


def _sidelined(m, orig):
    how_long = _duration(m.group(1))
    what = reason(m.group(2), "gen")
    if not how_long or not what:
        return None
    text = f"klubo pranešimu, nežais {how_long} dėl {what}"
    if m.group(3):
        text = _when(text, m.group(2), f"{m.group(3)} turo rungtynėse", "gen")
    return text


def _reassessed(m, _):
    d = _duration(m.group(1), "gen")
    return f"būklė bus įvertinta po {d}" if d else None


def _after(prefix):
    def build(m, _):
        what = reason(m.group(1), "gen")
        return f"{prefix} po {what}" if what else None
    return build


CLAUSES = [
    (r"^dnp in round\s*(\d+)(?: and (?:the )?domestic (league|cup))?$", _dnp),
    (r"^dnp in (?:the )?(?:round\s*(\d+) and )?(?:the )?domestic (league|cup)$", _dnp),
    (r"^(?:he )?(?:also )?played in (?:the )?(.+)$", _played),
    (r"^(but )?did ?n[o']?t travel with (?:the )?team(?: (?:in|for|to) round\s*(\d+))?(?: to ([^()]+))?$",
     lambda m, o: ("bet " if m.group(1) else "") + "nekeliavo su komanda"
     + (f" į {_city(o[m.start(3):m.end(3)])}" if m.group(3) else "")
     + (f" ({m.group(2)} turas)" if m.group(2) and m.group(3) else f" į {m.group(2)} turo rungtynes" if m.group(2) else "")),
    (r"^(?:has been|was|is now|got) registered(?: for round\s*(\d+))?(?: and (.+))?$",
     lambda m, o: (lambda rest: None if m.group(2) and not rest else
                   "užregistruotas" + (f" {m.group(1)} turui" if m.group(1) else "") + (f" ir {_lower_first(rest)}" if rest else ""))
     (clause(o[m.start(2):m.end(2)]) if m.group(2) else None)),
    (r"^(?:traveled|travelled) with (?:the )?team(?: to ([^()]+))?$",
     lambda m, o: "išvyko su komanda" + (f" į {_city(o[m.start(1):m.end(1)])}" if m.group(1) else "")),
    (r"^(?:travels|travelling|traveling|is travelling|is traveling) with (?:the )?team(?: to ([^()]+))?$",
     lambda m, o: "keliauja su komanda" + (f" į {_city(o[m.start(1):m.end(1)])}" if m.group(1) else "")),
    (r"^(?:he )?will travel(?: with (?:the )?team)? to ([^()]+)$",
     lambda m, o: f"keliaus su komanda į {_city(o[m.start(1):m.end(1)])}"),
    (r"^(?:not expected|unlikely) to play in round\s*(\d+)$", lambda m, _: f"neturėtų žaisti {_round_loc(m.group(1))}"),
    (r"^expected to play in round\s*(\d+)$", lambda m, _: f"turėtų žaisti {_round_loc(m.group(1))}"),
    (r"^(?:could|may|might|should) return (?:for|in|by) round\s*(\d+)$",
     lambda m, _: f"gali sugrįžti {_round_loc(m.group(1))}"),
    (r"^expected (?:to return|back) (?:for|in|by) round\s*(\d+)$",
     lambda m, _: f"tikimasi, kad sugrįš {_round_loc(m.group(1))}"),
    (r"^(?:out|will miss(?: the rest of)?) (?:for )?(?:the )?(?:rest of the )?season$", lambda m, _: "nežais iki sezono pabaigos"),
    (r"^out until (\w+)$", lambda m, _: f"nežais iki {MONTH_GEN[m.group(1)]}" if m.group(1) in MONTH_GEN else None),
    (r"^(?:out|will miss|sidelined) (?:for )?(.+? (?:weeks?|days?|months?))$",
     lambda m, _: f"nežais {d}" if (d := _duration(m.group(1))) else None),
    (r"^(?:out|sidelined|will miss) (?:for )?(?:the )?(?:rest of the )?season with (?:an? )?(.+)$",
     lambda m, _: f"nežais iki sezono pabaigos dėl {r}" if (r := reason(m.group(1), "gen")) else None),
    (r"^(?:out|sidelined) (?:for )?(.+? (?:weeks?|days?|months?)) with (?:an? )?(.+)$",
     lambda m, _: f"nežais {d} dėl {r}" if (d := _duration(m.group(1))) and (r := reason(m.group(2), "gen")) else None),
    (r"^day[- ]to[- ]day(?: with (?:an? )?(.+))?$",
     lambda m, _: (f"{r}, būklė vertinama kasdien" if (r := reason(m.group(1))) else None) if m.group(1)
     else "būklė vertinama kasdien"),
    (r"^suffered (.+?) (?:in|during) (.+)$", _suffered),
    (r"^(.+?)(?: \((.+)\))? suffered (?:in|during) (.+)$", _suffered_after),
    (r"^(?:the club announced that )?\S+(?: \S+)? will be sidelined for (.+?) with (?:an? )?(.+?)"
     r"(?: suffered in round\s*(\d+))?$", _sidelined),
    (r"^(?:his )?status will be (?:re)?(?:assessed|evaluated) in (.+)$", _reassessed),
    (r"^continues? (?:his )?(?:rehab|rehabilitation|recovery) (?:after|from) (.+)$", _after("tęsia reabilitaciją")),
    (r"^(?:rehab|rehabilitation|recovery) (?:after|from) (.+)$", _after("reabilitacija")),
    (r"^(?:working back|recovering|returning) from (.+)$", _after("atsigauna")),
    (r"^(?:recovered|returned) from (.+)$", _after("atsigavo")),
    (r"^(?:underwent|had) (.+)$", lambda m, _: f"atlikta {r}" if (r := reason(m.group(1))) else None),
    (r"^(?:a )?new signing$", lambda m, _: "naujai pasirašęs žaidėjas"),
    (r"^(?:was )?announced as (?:a )?new signing(?: on (\d\d?\.\d\d?))?$",
     lambda m, _: "paskelbtas nauju žaidėju" + (f" ({m.group(1)})" if m.group(1) else "")),
    (r"^(?:but )?(?:has ?n[o']?t|has not|is not|isn'?t) (?:been )?registered(?: yet)?$", lambda m, _: "bet dar neregistruotas"),
]


def _split(text):
    """Top-level clauses with the separator that followed each: '.', ',' or '+'."""
    parts, depth, cur = [], 0, ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        if depth == 0 and ch in ".,+":
            nxt = text[i + 1:i + 2]
            # Keep decimals and dates ("09.26") and abbreviations ("vs.") inside the clause.
            if ch == "." and (nxt.isdigit() or cur.rstrip().lower().endswith(("vs", "jr", "st"))):
                cur += ch
                i += 1
                continue
            parts.append((cur.strip(), ch))
            cur = ""
        else:
            cur += ch
        i += 1
    if cur.strip():
        parts.append((cur.strip(), ""))
    return [(c, sep) for c, sep in parts if c]


def clause(text):
    t = _clean(text).strip(" .;:").replace("’", "'")
    k = t.lower()
    if not k:
        return ""
    # "... after (a) DNP in Round 1 (and domestic league)": translate the head, add the DNP note
    m = re.match(r"^(.*\S)\s+after (?:a )?dnp in round\s*(\d+)( and (?:the )?domestic league)?$", k)
    if m:
        head = clause(t[:m.end(1)])
        return f"{head} ({m.group(2)} ture{' ir šalies lygoje' if m.group(3) else ''} nežaidė)" if head else None
    if k in WHOLE:
        return WHOLE[k]
    for pattern, build in CLAUSES:
        m = re.match(pattern, k)
        if m:
            out = build(m, t)
            if out:
                return out
    # Trailing "(...)": translate both halves.
    m = re.match(r"^(.*?)\s*\(([^()]+)\)$", t)
    if m and m.group(1):
        head = clause(m.group(1))
        inner = m.group(2)
        tail = inner if re.fullmatch(r"\d\d\.\d\d", inner) else reason(inner) or clause(inner)
        if head and tail:
            return f"{head} ({_lower_first(tail)})"
        return None
    return reason(t)


def translate(text):
    """Lithuanian version of a whole report comment, or None."""
    t = _clean(text)
    if not t:
        return ""
    whole = clause(t)
    if whole:
        return _cap(whole)
    out, sentence_start = [], True
    for part, sep in _split(t):
        lt = clause(part)
        if lt is None:
            return None
        out.append(_cap(lt) if sentence_start else _lower_first(lt))
        out.append({".": ". ", ",": ", ", "+": " + ", "": ""}[sep])
        sentence_start = sep == "."
    return "".join(out).strip()


INJURY_WORDS = re.compile(r"\b(?:(?:left|right|minor|mild)\s+)?(?:[a-z]+\s+){0,2}(?:injury|strain|sprain|tear|fracture|surgery)\b",
                          re.I)


def gist(text):
    """Fallback for free text: the first injury phrase in it, translated ('an ankle injury' -> 'Čiurnos trauma')."""
    for m in INJURY_WORDS.finditer(text or ""):
        words = m.group(0).split()
        for start in range(len(words)):  # shortest prefix trim that parses
            lt = injury_phrase(" ".join(words[start:]))
            if lt:
                return _cap(lt)
    return None


def return_text(text):
    """Report's 'Return' column: 'Round 2-4' -> '2-4 turai'."""
    t = _clean(text)
    m = re.fullmatch(r"Rounds?\s*(\d+)(?:\s*[-–]\s*(\d+))?", t, re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)} turai" if m.group(2) else f"{m.group(1)} turas"
    k = t.lower().strip(" .")
    fixed = {"indefinitely": "Neribotam laikui", "long-term": "Ilgam laikui", "long term": "Ilgam laikui",
             "season": "Iki sezono pabaigos", "end of season": "Iki sezono pabaigos", "out for season": "Iki sezono pabaigos",
             "unknown": "Nežinoma", "tbd": "Nežinoma", "day-to-day": "Vertinama kasdien", "next game": "Kitose rungtynėse",
             "next week": "Kitą savaitę", "soon": "Netrukus"}
    if k in fixed:
        return fixed[k]
    m = re.fullmatch(r"(early|mid|late|end of)[- ](\w+)", k)
    if m and m.group(2) in MONTH_GEN:
        return f"{MONTH_GEN[m.group(2)].capitalize()} {dict(early='pradžia', mid='vidurys', late='pabaiga')[m.group(1).replace('end of', 'late')]}"
    if k in MONTH_NOM:
        return MONTH_NOM[k].capitalize()
    d = _duration(k)
    if d:
        return _cap(d)
    return t
