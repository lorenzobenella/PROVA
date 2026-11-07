"""Prontezza del mattino: verde / giallo / rosso a partire da HRV, sonno, battito a riposo,
Body Battery e fatica accumulata (TSB).

Si parte da 100 e si tolgono punti per ogni segnale negativo:
verde >= 80 (allenamento come previsto), gialla 60-79 (versione ridotta), rossa < 60 (recupero).
"""

import statistics
from dataclasses import dataclass, field
from datetime import timedelta

GREEN, YELLOW, RED = "verde", "gialla", "rossa"


@dataclass
class Readiness:
    level: str
    score: int
    reasons: list = field(default_factory=list)   # cosa ha tolto punti
    good: list = field(default_factory=list)      # segnali positivi
    provisional: bool = False                     # mancano sonno e HRV di stanotte

    @property
    def emoji(self):
        return {GREEN: "🟢", YELLOW: "🟡", RED: "🔴"}[self.level]


def _fmt(x, nd=1):
    return ("%.*f" % (nd, x)).replace(".", ",")


def assess(day, wellness, tsb):
    score = 100
    reasons, good = [], []
    w = wellness.get(day)

    if w and w.hrv:
        status = (w.hrv_status or "").upper()
        band = " (fascia %d-%d)" % (w.hrv_low, w.hrv_high) if w.hrv_low and w.hrv_high else ""
        if status == "POOR":
            score -= 35
            reasons.append("HRV molto bassa: %d ms%s" % (w.hrv, band))
        elif status == "LOW":
            score -= 25
            reasons.append("HRV bassa da giorni: %d ms%s" % (w.hrv, band))
        elif w.hrv_low and w.hrv < w.hrv_low:
            score -= 15
            reasons.append("HRV sotto la tua fascia: %d ms%s" % (w.hrv, band))
        elif status == "UNBALANCED":
            score -= 8
            reasons.append("HRV instabile: %d ms%s" % (w.hrv, band))
        else:
            good.append("HRV nella norma: %d ms%s" % (w.hrv, band))

    if w and (w.sleep_h or w.sleep_score):
        h, s = w.sleep_h, w.sleep_score
        h_pen = 0 if h is None else 25 if h < 5 else 15 if h < 6 else 8 if h < 6.5 else 0
        s_pen = 0 if s is None else 20 if s < 50 else 10 if s < 60 else 0
        pen = max(h_pen, s_pen)
        text = "sonno %s h" % _fmt(h) if h else "sonno"
        if s:
            text += " (punteggio %d)" % s
        if pen:
            score -= pen
            reasons.append(text[0].upper() + text[1:] + ": poco o di cattiva qualità")
        else:
            good.append(text[0].upper() + text[1:])
        nights = [wellness[d].sleep_h for d in (day - timedelta(days=k) for k in range(3))
                  if d in wellness and wellness[d].sleep_h]
        if len(nights) >= 2 and statistics.mean(nights) < 6:
            score -= 10
            reasons.append("Debito di sonno: media %s h nelle ultime %d notti"
                           % (_fmt(statistics.mean(nights)), len(nights)))

    if w and w.rhr:
        past = [wellness[d].rhr for d in (day - timedelta(days=k) for k in range(1, 15))
                if d in wellness and wellness[d].rhr]
        if len(past) >= 7:
            ref = statistics.median(past)
            diff = w.rhr - ref
            if diff >= 6:
                score -= 15
                reasons.append("Battito a riposo alto: %d bpm (di solito %d)" % (w.rhr, ref))
            elif diff >= 4:
                score -= 8
                reasons.append("Battito a riposo un po' alto: %d bpm (di solito %d)" % (w.rhr, ref))
            elif diff <= 1:
                good.append("Battito a riposo normale: %d bpm" % w.rhr)

    if w and w.body_battery is not None:
        if w.body_battery < 35:
            score -= 12
            reasons.append("Body Battery bassa: %d" % w.body_battery)
        elif w.body_battery < 50:
            score -= 6
            reasons.append("Body Battery sotto 50: %d" % w.body_battery)

    if tsb is not None:
        if tsb < -30:
            score -= 25
            reasons.append("Fatica accumulata alta (forma %s)" % _fmt(tsb, 0))
        elif tsb < -20:
            score -= 10
            reasons.append("Fatica accumulata in crescita (forma %s)" % _fmt(tsb, 0))

    score = max(0, score)
    level = GREEN if score >= 80 else YELLOW if score >= 60 else RED
    provisional = not (w and w.has_morning_data)
    return Readiness(level=level, score=score, reasons=reasons, good=good, provisional=provisional)
