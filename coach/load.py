"""Carico di allenamento: TSS per attivita' e CTL / ATL / TSB (modello di Coggan)."""

from collections import defaultdict
from datetime import timedelta

STRENGTH_TSS_PER_MIN = 0.6   # ~36 TSS/ora: la forza affatica le gambe, va contata
OTHER_TSS_PER_HOUR = 40
BIKE_NO_DATA_TSS_PER_HOUR = 50


def activity_tss(act, ftp, lthr=None):
    hours = act.duration_s / 3600
    if hours <= 0 or act.sport == "light":
        return 0.0
    if act.sport == "strength":
        return act.duration_s / 60 * STRENGTH_TSS_PER_MIN
    if act.sport == "bike":
        if act.tss:
            return act.tss
        if act.np and ftp:
            return hours * (act.np / ftp) ** 2 * 100
        if act.avg_hr and lthr:
            return hours * (act.avg_hr / lthr) ** 2 * 100
        return hours * BIKE_NO_DATA_TSS_PER_HOUR
    return hours * OTHER_TSS_PER_HOUR


def daily_tss(activities, ftp_at, lthr=None):
    out = defaultdict(float)
    for act in activities:
        out[act.day] += activity_tss(act, ftp_at(act.day), lthr)
    return out


def pmc(tss_by_day, start, end):
    """{giorno: (ctl, atl)} a fine giornata, da start a end compresi."""
    out = {}
    ctl = atl = 0.0
    d = start
    while d <= end:
        load = tss_by_day.get(d, 0.0)
        ctl += (load - ctl) / 42
        atl += (load - atl) / 7
        out[d] = (ctl, atl)
        d += timedelta(days=1)
    return out
