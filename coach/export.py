"""Uscite: descrizione in italiano, file .zwo / .erg per il KICKR, PIANO.md e testo dei messaggi."""

import hashlib
import html
from xml.sax.saxutils import quoteattr

from . import workouts as wo
from .planner import day_label

STATUS_ICON = {"fatto": "✅", "fatto in parte": "◐", "saltato": "✗", "da fare": "·", "riposo": "–"}


def watts(pct, ftp):
    return int(5 * round(pct * ftp / 5))


def fmt(x, nd=1):
    return "–" if x is None else ("%.*f" % (nd, x)).replace(".", ",")


# ---------------------------------------------------------------- descrizione

def _short(step, ftp):
    text = "%s a %d W" % (wo.fmt_min(step.minutes), watts(step.pct, ftp))
    return text + (" (%d%%)" % round(step.pct * 100) if step.pct >= 0.8 else "")


def _repeat(rep, ftp):
    first = rep.steps[0]
    if isinstance(first, wo.Repeat) and len(rep.steps) == 2:
        # serie di ripetute con un recupero dopo ogni serie (30/15, over-under)
        inner, gap = _repeat(first, ftp), _short(rep.steps[1], ftp)
        if rep.times == 1:
            return "%s, poi %s" % (inner, gap)
        return "%d serie di [%s], con %s dopo ogni serie" % (rep.times, inner, gap)
    parts = [_repeat(s, ftp) if isinstance(s, wo.Repeat) else _short(s, ftp) for s in rep.steps]
    if rep.times == 1:
        return " + ".join(parts)
    return "%d × (%s)" % (rep.times, " + ".join(parts))


def describe(workout, ftp):
    """Righe leggibili della seduta, con i watt calcolati sulla FTP attuale."""
    if workout.summary:
        return list(workout.summary)
    lines = []
    if workout.is_bike:
        for seg in workout.segments:
            if isinstance(seg, wo.Repeat):
                lines.append(_repeat(seg, ftp))
            elif seg.is_ramp:
                lines.append("%s %s %d→%d W" % (wo.fmt_min(seg.minutes), seg.label,
                                                 watts(seg.pct, ftp), watts(seg.pct_end, ftp)))
            else:
                lines.append("%s %s a %d W (%d%%)" % (wo.fmt_min(seg.minutes), seg.label,
                                                      watts(seg.pct, ftp), round(seg.pct * 100)))
    for e in workout.exercises:
        text = "%s: %d × %s" % (e.name, e.sets, e.reps)
        if e.rest:
            text += ", recupero %s" % e.rest
        if e.note:
            text += " (%s)" % e.note
        lines.append(text)
    return lines


def headline(workout):
    if workout.kind == "rest":
        return workout.title
    parts = [workout.title]
    if "%d'" % round(workout.minutes) not in workout.title:
        parts.append("%d'" % round(workout.minutes))
    if workout.tss:
        parts.append("~%d TSS" % round(workout.tss))
    return " · ".join(parts)


def plan_hash(plan, ftp):
    text = "|".join([plan.workout.title] + describe(plan.workout, ftp))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- file per i rulli

def _secs(minutes):
    return int(round(minutes * 60))


def zwo(workout, name, description=""):
    """Formato Zwift (.zwo): lo leggono anche MyWhoosh, TrainingPeaks Virtual, Rouvy e altri."""
    rows = []
    flat_top = workout.segments

    def emit(seg, first, last):
        if isinstance(seg, wo.Repeat):
            simple = (len(seg.steps) == 2 and all(isinstance(s, wo.Step) and not s.is_ramp for s in seg.steps))
            if simple:
                on, off = seg.steps
                rows.append('<IntervalsT Repeat="%d" OnDuration="%d" OffDuration="%d" OnPower="%.3f" OffPower="%.3f"/>'
                            % (seg.times, _secs(on.minutes), _secs(off.minutes), on.pct, off.pct))
            else:
                for _ in range(seg.times):
                    for s in seg.steps:
                        emit(s, False, False)
            return
        if seg.is_ramp:
            tag = "Warmup" if first else "Cooldown" if last else "Ramp"
            rows.append('<%s Duration="%d" PowerLow="%.3f" PowerHigh="%.3f"/>'
                        % (tag, _secs(seg.minutes), seg.pct, seg.pct_end))
        else:
            rows.append('<SteadyState Duration="%d" Power="%.3f"/>' % (_secs(seg.minutes), seg.pct))

    for i, seg in enumerate(flat_top):
        emit(seg, i == 0, i == len(flat_top) - 1)
    body = "\n".join("    " + r for r in rows)
    return ("<workout_file>\n  <author>Allenatore</author>\n  <name>%s</name>\n  <description>%s</description>\n"
            "  <sportType>bike</sportType>\n  <tags/>\n  <workout>\n%s\n  </workout>\n</workout_file>\n"
            % (html.escape(name), html.escape(description), body))


def erg(workout, ftp, name):
    """Formato ERG con watt assoluti (minuti, watt): lo leggono quasi tutte le app per rulli."""
    points, t = [], 0.0
    for s in wo.flatten(workout.segments):
        end = s.pct_end if s.is_ramp else s.pct
        points.append((t, watts(s.pct, ftp)))
        t += s.minutes
        points.append((t, watts(end, ftp)))
    data = "\n".join("%.2f\t%d" % p for p in points)
    return ("[COURSE HEADER]\nVERSION = 2\nUNITS = METRIC\nDESCRIPTION = %s\nFILE NAME = %s\nMINUTES WATTS\n"
            "[END COURSE HEADER]\n[COURSE DATA]\n%s\n[END COURSE DATA]\n" % (name, name, data))


def file_stem(plan):
    return "%s_%s" % (plan.day.isoformat(), plan.workout.key)


# ---------------------------------------------------------------- PIANO.md

def _readiness_md(r):
    if r is None:
        return ""
    head = "%s **Prontezza %s** (%d/100)" % (r.emoji, r.level, r.score)
    if r.provisional:
        head += " · *provvisoria: sonno e HRV di stanotte non ancora sincronizzati*"
    lines = [head]
    lines += ["- %s" % x for x in r.reasons]
    lines += ["- %s" % x for x in r.good]
    return "\n".join(lines)


def _done_md(plan, ftp):
    acts = [a for a in plan.activities if a.sport != "light"]
    if not acts:
        return ""
    parts = []
    for a in acts:
        text = "%s %d'" % (a.name or a.sport, round(a.duration_s / 60))
        if a.np:
            text += " · NP %d W" % a.np
        parts.append(text)
    return "; ".join(parts) + " · TSS %d" % round(plan.done_tss)


def markdown(today_plan, upcoming, week, ind, warnings, new_tests, ftp):
    p = today_plan
    wk = p.workout
    out = ["# Piano di allenamento", "",
           "**Oggi, %s** · %s" % (day_label(p.day), p.phase), ""]
    if p.readiness:
        out += [_readiness_md(p.readiness), ""]
    out += ["## Oggi: %s" % headline(wk), ""]
    if p.status != "da fare":
        done = _done_md(p, ftp)
        out += ["**Stato: %s**%s" % (p.status, " · " + done if done else ""), ""]
    for c in p.changes:
        out.append("> %s" % c)
    if p.changes:
        out.append("")
    if wk.purpose:
        out += ["*%s*" % wk.purpose, ""]
    out += ["- %s" % line for line in describe(wk, ftp)]
    if wk.notes:
        out += [""] + ["%s" % ("Nota: " + n) for n in wk.notes]
    if wk.is_bike:
        stem = file_stem(p)
        out += ["", "File per i rulli: [`%s.zwo`](allenamenti/%s.zwo) · [`%s.erg`](allenamenti/%s.erg)"
                % (stem, stem, stem, stem)]
    out += ["", "## Prossimi giorni", "", "| Giorno | Seduta | Durata | TSS |", "| --- | --- | --- | --- |"]
    for u in upcoming:
        out.append("| %s | %s | %s | %s |" % (
            day_label(u.day), u.workout.title, "%d'" % round(u.workout.minutes) if u.workout.minutes else "–",
            round(u.workout.tss) if u.workout.tss else "–"))
    out += ["", "Le sedute dei prossimi giorni sono quelle di base: ogni mattina il coach le adatta a sonno, HRV e fatica."]
    if week:
        out += ["", "## Questa settimana", "", "| Giorno | Previsto | Stato | Fatto |", "| --- | --- | --- | --- |"]
        for d in week:
            out.append("| %s | %s | %s %s | %s |" % (day_label(d.day), d.workout.title,
                                                     STATUS_ICON.get(d.status, ""), d.status, _done_md(d, ftp) or "–"))
    out += ["", "## Indicatori", ""]
    out.append("- FTP: **%d W** (%s) · %s W/kg a %s kg (%s)" % (
        ind["ftp"], ind["ftp_src"], fmt(ind["wkg"], 2), fmt(ind["kg"]), ind["kg_src"]))
    if ind["ftp_est"]:
        out.append("- FTP stimata dal miglior 20' degli ultimi 90 giorni: %d W (miglior 20': %d W)"
                   % (ind["ftp_est"], ind["b20"]))
    if ind["b5"]:
        out.append("- Miglior 5' degli ultimi 90 giorni: %d W → VO2max stimato %s ml/kg/min"
                   % (ind["b5"], fmt(ind["vo2_est"])))
    out.append("- Fitness (CTL) %s · fatica (ATL) %s · forma (TSB) %s"
               % (fmt(ind["ctl"]), fmt(ind["atl"]), fmt(ind["tsb"])))
    if ind["hours_4w"] is not None:
        out.append("- Bici nelle ultime 4 settimane: %s h/settimana · questa settimana il piano prevede %s h (bici + forza)"
                   % (fmt(ind["hours_4w"]), fmt(ind["hours_planned"])))
    out += ["", "## Zone di potenza (FTP %d W)" % ftp, ""]
    zones = [("Z1 recupero", 0, 0.55), ("Z2 resistenza", 0.56, 0.75), ("Z3 medio", 0.76, 0.87),
             ("Sweet spot", 0.88, 0.93), ("Z4 soglia", 0.94, 1.05), ("Z5 VO2max", 1.06, 1.20),
             ("Z6 anaerobico", 1.21, 1.50)]
    for name, lo, hi in zones:
        out.append("- %s: %s–%d W" % (name, "%d" % watts(lo, ftp) if lo else "0", watts(hi, ftp)))
    for d, w, ok in new_tests:
        out += ["", "**Ramp test del %s: FTP %d W** %s" % (
            day_label(d), w, "(applicata)" if ok else "(non applicata: troppo diversa dalla precedente, "
                                                     "controlla e aggiungila a mano in garmin-dashboard/ftp_tests.json)")]
    if warnings:
        out += ["", "## Avvisi", ""] + ["- %s" % x for x in warnings]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- messaggi (Telegram / ntfy)

def _e(text):
    return html.escape(text, quote=False)


def message_today(plan, ftp, updated=False):
    r = plan.readiness
    head = "%s <b>%s, %s</b>" % (r.emoji if r else "🚴", "Cambio di programma" if updated else "Oggi",
                                  day_label(plan.day))
    lines = [head]
    if r:
        lines.append("Prontezza %s (%d/100)%s" % (r.level, r.score, " · provvisoria" if r.provisional else ""))
        lines += ["• " + _e(x) for x in r.reasons[:3]]
    lines += ["", "<b>%s</b>" % _e(headline(plan.workout))]
    lines += [_e(c) for c in plan.changes]
    lines += ["• " + _e(x) for x in describe(plan.workout, ftp)]
    lines += [_e(n) for n in plan.workout.notes[:2]]
    return "\n".join(lines)


def message_preview(plan, ftp):
    lines = ["🗓 <b>Domani, %s</b>" % day_label(plan.day), "<b>%s</b>" % _e(headline(plan.workout))]
    lines += ["• " + _e(x) for x in describe(plan.workout, ftp)]
    if plan.workout.kind != "rest":
        lines.append("Domattina confermo o adatto dopo aver letto sonno e HRV.")
    return "\n".join(lines)


def message_done(plan, ftp, tomorrow):
    lines = ["✅ <b>Fatto: %s</b>" % _e(plan.workout.title), _e(_done_md(plan, ftp))]
    lines += ["", "🗓 Domani: <b>%s</b>" % _e(headline(tomorrow.workout))]
    lines += ["• " + _e(x) for x in describe(tomorrow.workout, ftp)]
    return "\n".join(lines)


def message_test(day, watts_, ok, old):
    if ok:
        return "🏁 <b>Ramp test del %s: FTP %d W</b> (prima %d W). Zone e watt aggiornati." % (day_label(day), watts_, old)
    return ("🏁 Ramp test del %s: il calcolo dà %d W, troppo diverso da %d W per applicarlo da solo. "
            "Se è giusto, aggiungilo in garmin-dashboard/ftp_tests.json." % (day_label(day), watts_, old))
