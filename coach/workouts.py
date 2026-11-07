"""Libreria di sedute.

Bici: intensità in frazione della FTP, così il KICKR in modalità ERG segue i watt giusti
anche quando la FTP cambia. Ogni seduta si adatta ai minuti disponibili: prima toglie
ripetute, poi accorcia il lavoro, e il tempo che avanza diventa Z2.
Forza: bilanciere e manubri, carichi guidati dal RIR (ripetizioni lasciate in canna),
perché non conosciamo i tuoi massimali.
"""

from dataclasses import dataclass, field

# ---------------------------------------------------------------- modello


@dataclass
class Step:
    minutes: float
    pct: float                    # frazione della FTP (0.68 = 68%)
    pct_end: float | None = None  # se presente, rampa da pct a pct_end
    label: str = ""

    @property
    def is_ramp(self):
        return self.pct_end is not None and abs(self.pct_end - self.pct) > 1e-9

    def sq(self):
        """Media di (intensità)^2: serve per stimare il TSS."""
        if not self.is_ramp:
            return self.pct ** 2
        a, b = self.pct, self.pct_end
        return (a * a + a * b + b * b) / 3


@dataclass
class Repeat:
    times: int
    steps: list


@dataclass
class Exercise:
    name: str
    sets: int
    reps: str
    rest: str = ""
    note: str = ""


KEY_KINDS = {"vo2", "threshold", "sweetspot", "test"}


@dataclass
class Workout:
    key: str                  # voce del modello settimanale (es. "vo2_long")
    kind: str                 # famiglia: vo2, threshold, sweetspot, endurance, recovery, opener,
                              # test, strength, mobility, rest
    title: str
    purpose: str = ""
    segments: list = field(default_factory=list)
    exercises: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    duration_min: float = 0   # durata delle sedute senza bici
    reduced: bool = False
    summary: list | None = None    # descrizione su misura (ramp test)
    tss_override: float | None = None

    @property
    def is_bike(self):
        return bool(self.segments)

    @property
    def is_key(self):
        return self.kind in KEY_KINDS

    @property
    def minutes(self):
        if self.is_bike:
            return sum(seg_minutes(s) for s in self.segments)
        return self.duration_min

    @property
    def tss(self):
        if self.tss_override is not None:
            return self.tss_override
        if self.is_bike:
            return sum(s.minutes / 60 * s.sq() * 100 for s in flatten(self.segments))
        if self.kind == "strength":
            return self.duration_min * 0.6
        return 0.0


def seg_minutes(seg):
    if isinstance(seg, Repeat):
        return seg.times * sum(seg_minutes(s) for s in seg.steps)
    return seg.minutes


def flatten(segments):
    out = []
    for seg in segments:
        if isinstance(seg, Repeat):
            for _ in range(seg.times):
                out.extend(flatten(seg.steps))
        else:
            out.append(seg)
    return out


# ---------------------------------------------------------------- mattoncini bici

Z2 = 0.68
WARMUP_MIN = 10
COOL_MIN = 6


def fmt_min(minutes):
    secs = int(round(minutes * 60))
    m, s = divmod(secs, 60)
    if m and s:
        return "%d'%02d\"" % (m, s)
    return "%d'" % m if m else '%d"' % s


def _warmup():
    return [Step(6, 0.50, 0.72, "riscaldamento progressivo"), Step(2, 0.85, label="attivazione"),
            Step(2, 0.55, label="agile")]


def _fits(main_minutes, minutes, overlap):
    """riscaldamento + lavoro + defaticamento stanno nei minuti? L'ultimo recupero conta come defaticamento."""
    return WARMUP_MIN + main_minutes + max(2, COOL_MIN - overlap) <= minutes


def _assemble(main, minutes, overlap):
    """Riscaldamento + lavoro + Z2 di riempimento + defaticamento, fino a `minutes`."""
    used = WARMUP_MIN + sum(seg_minutes(s) for s in main)
    cool = max(2, COOL_MIN - overlap)
    fill = minutes - used - cool
    if fill >= 5:
        cool += fill - int(fill)
        fill = int(fill)
    segs = _warmup() + list(main)
    if fill >= 5:
        segs.append(Step(fill, Z2, label="Z2"))
    elif fill > 0:
        cool += fill
    segs.append(Step(cool, 0.60, 0.45, "defaticamento"))
    return segs


def intervals(key, kind, minutes, reps, on, pct, off, title, purpose, notes=(), min_reps=2, off_pct=0.50):
    while reps > min_reps and not _fits(reps * (on + off), minutes, off):
        reps -= 1
    # ancora troppo lunga: accorcio lavoro e recuperi, e solo alla fine tolgo l'ultima ripetuta
    while not _fits(reps * (on + off), minutes, off):
        if on > 2 and on >= off:
            on -= 1
        elif off > 2:
            off -= 1
        elif reps > 1:
            reps -= 1
        else:
            break
    main = [Repeat(reps, [Step(on, pct, label="lavoro"), Step(off, off_pct, label="recupero")])]
    return Workout(key, kind, title.format(reps=reps, on=fmt_min(on), pct=round(pct * 100)), purpose,
                   segments=_assemble(main, minutes, off), notes=list(notes))


def thirty_fifteen(key, minutes, sets, n, purpose, notes=(), between=3):
    def length(s, k):
        return s * (k * 0.75 + between)

    while sets > 1 and not _fits(length(sets, n), minutes, between):
        sets -= 1
    while n > 6 and not _fits(length(sets, n), minutes, between):
        n -= 1
    inner = Repeat(n, [Step(0.5, 1.20, label="forte"), Step(0.25, 0.50, label="piano")])
    main = [Repeat(sets, [inner, Step(between, 0.50, label="recupero")])]
    title = "VO2max 30/15: %d serie da %d" % (sets, n)
    return Workout(key, "vo2", title, purpose, segments=_assemble(main, minutes, between), notes=list(notes))


def over_under(key, minutes, sets, cycles, purpose, notes=(), rest=4):
    while sets > 1 and not _fits(sets * (cycles * 3 + rest), minutes, rest):
        sets -= 1
    while cycles > 2 and not _fits(sets * (cycles * 3 + rest), minutes, rest):
        cycles -= 1
    inner = Repeat(cycles, [Step(2, 0.95, label="sotto soglia"), Step(1, 1.05, label="sopra soglia")])
    main = [Repeat(sets, [inner, Step(rest, 0.50, label="recupero")])]
    title = "Over-under: %d blocchi da %d' (2' al 95%% + 1' al 105%%)" % (sets, cycles * 3)
    return Workout(key, "threshold", title, purpose, segments=_assemble(main, minutes, rest), notes=list(notes))


def endurance(key, minutes, title=None, notes=()):
    m = max(20, int(minutes))
    segs = [Step(5, 0.50, 0.65, "riscaldamento"), Step(m - 10, Z2, label="Z2"),
            Step(5, 0.62, 0.45, "defaticamento")]
    tips = ["Fatica da conversazione: se il battito sale oltre la tua zona 2, scendi di 10 W.",
            "Ogni 15' fai 1' agile a 100-110 rpm senza cambiare i watt."]
    if m >= 90:
        tips.append("Mangia 60-80 g di carboidrati all'ora e bevi 500-750 ml all'ora.")
    return Workout(key, "endurance", title or "Resistenza Z2 %d'" % m, PURPOSE["endurance"],
                   segments=segs, notes=list(notes) + tips)


def recovery(minutes, notes=()):
    m = max(20, min(40, int(minutes)))
    segs = [Step(3, 0.40, 0.50, "inizio"), Step(m - 6, 0.50, label="Z1"), Step(3, 0.50, 0.40, "fine")]
    return Workout("recovery", "recovery", "Scarico Z1 %d'" % m, PURPOSE["recovery"], segments=segs,
                   notes=list(notes) + ["Rapporto agile, nessuno sforzo. Se ti senti a pezzi, riposo completo."])


def ramp_test(ftp):
    segs = [Step(8, 0.45, 0.60, "riscaldamento"), Step(3, 0.50, label="agile")]
    for w in range(100, 561, 20):  # oltre l'esaurimento: ti fermi prima
        segs.append(Step(1, w / ftp, label="%d W" % w))
    segs.append(Step(8, 0.50, 0.40, "defaticamento"))
    return Workout(
        "test", "test", "Ramp test FTP", PURPOSE["test"], segments=segs, tss_override=45,
        summary=["11' riscaldamento (8' progressivo + 3' agile)",
                 "Rampa in ERG: 1' a 100 W, poi +20 W ogni minuto finché non riesci più a tenere 80 rpm",
                 "8' defaticamento"],
        notes=["Fallo fresco, dopo un giorno di riposo, con ventilatore e borraccia.",
               "Nuova FTP = 75% del tuo miglior minuto: il coach la legge da Garmin e aggiorna da solo zone e watt."])


# ---------------------------------------------------------------- forza


def _rir_note(rir):
    return "RIR %d: alla fine di ogni serie devono restarti %d ripetizioni in canna" % (rir, rir)


STRENGTH_NOTES = [
    "Riscaldamento: 5' di bici facile e 2 serie leggere del primo esercizio.",
    "Carico: quando chiudi tutte le serie con più margine del RIR indicato, aggiungi 2,5-5 kg la volta dopo.",
]


def strength(key, variant, w, reduced=False):
    if variant == "A":
        sets, reps, rir = [(3, "6", 3), (4, "5", 2), (4, "4", 2)][min(w, 2)]
        ex = [Exercise("Squat con bilanciere", sets, reps, "2-3'", _rir_note(rir)),
              Exercise("Stacco rumeno con bilanciere", 3, "6-8", "2'", "schiena neutra, anche indietro"),
              Exercise("Affondi bulgari con manubri", 3, "8 per gamba", "90\""),
              Exercise("Hip thrust con bilanciere", 3, "8-10", "90\""),
              Exercise("Rematore con manubrio", 3, "10 per braccio", "60\""),
              Exercise("Plank", 3, "40\"", "30\"")]
        title, minutes = "Forza A: pesante", 50
    elif variant == "B":
        ex = [Exercise("Step-up su panca con manubri", 3, "8 per gamba", "60\""),
              Exercise("Stacco a una gamba con manubrio", 3, "8 per gamba", "60\""),
              Exercise("Calf raise con manubri", 3, "15", "45\""),
              Exercise("Piegamenti", 3, "massimo meno 2", "60\""),
              Exercise("Dead bug", 3, "10 per lato", "30\""),
              Exercise("Plank laterale", 2, "30\" per lato", "30\"")]
        title, minutes = "Forza B: leggera, una gamba e core", 30
    elif variant == "M":
        sets, reps, rir = [(3, "5", 3), (3, "4", 2), (3, "4", 2)][min(w, 2)]
        ex = [Exercise("Squat con bilanciere", sets, reps, "3'", _rir_note(rir)),
              Exercise("Stacco rumeno con bilanciere", 2, "6", "2'"),
              Exercise("Affondi bulgari con manubri", 2, "6 per gamba", "90\""),
              Exercise("Rematore con manubrio", 2, "10 per braccio", "60\""),
              Exercise("Plank", 2, "40\"", "30\"")]
        title, minutes = "Forza di mantenimento", 35
    else:  # scarico
        ex = [Exercise("Squat con bilanciere", 2, "5", "2'", _rir_note(4)),
              Exercise("Stacco rumeno con bilanciere", 2, "6", "2'", _rir_note(4)),
              Exercise("Plank", 2, "40\"", "30\"")]
        title, minutes = "Forza di scarico", 25
    notes = list(STRENGTH_NOTES)
    if reduced:
        for e in ex:
            e.sets = max(1, e.sets - 1)
        notes.insert(0, "Oggi RIR +1: carichi un po' più leggeri del solito.")
        minutes = round(minutes * 0.75)
        title += " (ridotta)"
    return Workout(key, "strength", title, PURPOSE["strength"], exercises=ex, notes=notes,
                   duration_min=minutes, reduced=reduced)


def strength_explosive(key, w, reduced=False):
    wk = strength(key, "M", w, reduced)
    wk.exercises.insert(1, Exercise("Squat jump a corpo libero", 3, "5", "2'", "massima esplosività, atterraggio morbido"))
    wk.title = wk.title.replace("mantenimento", "mantenimento ed esplosività")
    wk.duration_min += 5
    return wk


def mobility(notes=()):
    ex = [Exercise("90/90 per le anche", 2, "60\" per lato"),
          Exercise("Cat-cow", 2, "10"),
          Exercise("Affondo con rotazione del busto", 2, "6 per lato"),
          Exercise("Allungamento flessori dell'anca", 2, "45\" per lato"),
          Exercise("Respirazione diaframmatica sdraiato", 1, "3'")]
    return Workout("mobility", "mobility", "Mobilità 20'", PURPOSE["mobility"], exercises=ex,
                   notes=list(notes), duration_min=20)


def rest(notes=()):
    return Workout("rest", "rest", "Riposo", PURPOSE["rest"],
                   notes=list(notes) + ["Facoltativo: 20-30' a piedi. Dormi almeno 7 ore e mezza."])


# ---------------------------------------------------------------- catalogo

PURPOSE = {
    "vo2_long": "Alza il VO2max: più minuti vicino al massimo consumo di ossigeno. Ripetute dure ma "
                "tutte uguali: se l'ultima cala più del 5%, la prossima volta tieni 5 W in meno.",
    "vo2_short": "30/15 (Rønnestad): accumuli più tempo vicino al VO2max che con le ripetute lunghe, "
                 "con meno fatica percepita.",
    "vo2_maint": "Mantiene il VO2max mentre il blocco lavora sulla soglia.",
    "threshold": "Alza la FTP: lavoro appena sotto o alla soglia, impegnativo ma sostenibile.",
    "over_under": "Insegna a smaltire il lattato sopra soglia senza scoppiare.",
    "sweetspot": "Tanto lavoro utile per la FTP con poca fatica (88-93% FTP), dentro un'uscita lunga.",
    "threshold_long": "Soglia lunga dentro un'uscita di resistenza: la prova generale della FTP.",
    "opener": "Settimana di scarico: poco volume e un richiamo di intensità per non perdere il ritmo.",
    "endurance": "Base aerobica: è quella che regge FTP e VO2max. Deve essere facile davvero.",
    "recovery": "Scarico attivo: gambe in movimento, nessuna fatica.",
    "test": "Misura la nuova FTP: tutte le zone si aggiornano da sole.",
    "strength": "Forza massima: più forza per pedalata e tendini più robusti. Ripetizioni basse, "
                "quindi niente massa in più.",
    "mobility": "Recupero: mobilità di anche e schiena, nessun carico.",
    "rest": "Il miglioramento arriva mentre recuperi.",
}

VO2_LONG = [(5, 3, 1.15, 3), (5, 4, 1.12, 3), (6, 4, 1.12, 3)]
VO2_MAINT = [(4, 4, 1.10, 3), (5, 4, 1.10, 3), (5, 4, 1.12, 3)]
VO2_SHORT = [(2, 10), (3, 10), (3, 13)]
THRESHOLD = {
    "vo2": [(3, 10, 0.95, 4), (3, 12, 0.95, 4), (2, 20, 0.95, 4)],
    "soglia": [(2, 15, 1.00, 5), (2, 20, 0.98, 4), (2, 20, 1.00, 4)],
    "misto": [(2, 15, 1.00, 5), (2, 20, 0.98, 4), (2, 20, 1.00, 4)],
}
OVER_UNDER = [(3, 3), (3, 4), (2, 6)]
SWEETSPOT = [(3, 15, 0.90, 5), (3, 20, 0.90, 5), (2, 30, 0.90, 6)]
THRESHOLD_LONG = [(3, 12, 1.00, 6), (3, 15, 0.98, 6), (2, 25, 0.95, 8)]

VO2_NOTES = ["Cadenza alta (95-105 rpm) nelle ripetute: a parità di watt affatica meno le gambe."]
THRESHOLD_NOTES = ["Respiro controllato: se a metà della ripetuta non riusciresti a dire una frase corta, "
                   "sei troppo forte."]


def build(key, block, w, minutes, ftp, reduced=False):
    """Costruisce la seduta `key` della settimana `w` (0-2 carico, 3 scarico) di un blocco."""
    wi = min(w, 2)
    if key == "rest":
        return rest()
    if minutes < 20:
        return rest(["Oggi hai meno di 20 minuti: riposo."])
    if key == "mobility":
        return mobility()
    if key.startswith("strength"):
        if key == "strength_m":
            wk = strength_explosive(key, wi, reduced) if block == "misto" else strength(key, "M", wi, reduced)
        else:
            wk = strength(key, {"strength_a": "A", "strength_b": "B"}.get(key, "D"), wi, reduced)
        if wk.duration_min > minutes:
            # poco tempo: tengo gli esercizi principali (sono in ordine di importanza)
            keep = max(2, int(len(wk.exercises) * minutes / wk.duration_min))
            wk.exercises = wk.exercises[:keep]
            wk.duration_min = minutes
            wk.notes.insert(0, "Oggi hai %d': solo gli esercizi principali." % minutes)
        return wk

    if reduced:
        # Prontezza gialla: meno volume, stessa qualità.
        minutes = min(minutes, max(45, round(minutes * 0.75)))

    def less(n, lo=2):
        return max(lo, round(n * 0.6)) if reduced else n

    def tag(wk):
        if reduced:
            wk.reduced = True
            wk.title += " (ridotta)"
        return wk

    if key == "vo2_long":
        r, on, p, off = VO2_LONG[wi]
        return tag(intervals(key, "vo2", minutes, less(r), on, p, off, "VO2max {reps}×{on} al {pct}%",
                             PURPOSE[key], VO2_NOTES))
    if key == "vo2_maint":
        r, on, p, off = VO2_MAINT[wi]
        return tag(intervals(key, "vo2", minutes, less(r), on, p, off,
                             "VO2max di mantenimento {reps}×{on} al {pct}%", PURPOSE[key], VO2_NOTES))
    if key == "vo2_short":
        sets, n = VO2_SHORT[wi]
        return tag(thirty_fifteen(key, minutes, less(sets, 1), n, PURPOSE[key], VO2_NOTES))
    if key == "threshold":
        r, on, p, off = THRESHOLD[block][wi]
        return tag(intervals(key, "threshold", minutes, less(r), on, p, off, "Soglia {reps}×{on} al {pct}%",
                             PURPOSE[key], THRESHOLD_NOTES))
    if key == "over_under":
        sets, cycles = OVER_UNDER[wi]
        return tag(over_under(key, minutes, less(sets), cycles, PURPOSE[key], THRESHOLD_NOTES))
    if key == "sweetspot":
        r, on, p, off = SWEETSPOT[wi]
        return tag(intervals(key, "sweetspot", minutes, less(r), on, p, off,
                             "Resistenza con sweet spot {reps}×{on} al {pct}%", PURPOSE[key], THRESHOLD_NOTES))
    if key == "threshold_long":
        r, on, p, off = THRESHOLD_LONG[wi]
        return tag(intervals(key, "threshold", minutes, less(r), on, p, off,
                             "Resistenza con soglia {reps}×{on} al {pct}%", PURPOSE[key], THRESHOLD_NOTES))
    if key == "opener":
        return intervals(key, "opener", min(minutes, 50), 3, 3, 1.10, 3, "Richiamo {reps}×{on} al {pct}%",
                         PURPOSE[key])
    if key == "test":
        if reduced:
            return endurance("endurance", min(minutes, 60),
                             notes=["Test rimandato: serve freschezza. Il coach lo ripropone domani se stai meglio."])
        return ramp_test(ftp)
    if key == "endurance":
        return tag(endurance(key, minutes))
    if key == "endurance_short":
        return tag(endurance(key, min(minutes, 45)))
    if key == "endurance_deload":
        return tag(endurance(key, min(minutes, 90)))
    if key == "recovery":
        return recovery(minutes)
    raise KeyError("Seduta sconosciuta: %s" % key)
