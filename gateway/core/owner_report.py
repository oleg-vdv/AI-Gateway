"""Ежемесячный отчёт владельцу (этап 1).

Цифры за календарный месяц из журнала аудита: сколько раз сотрудники
обращались к ИИ, сколько персональных данных шлюз скрыл и каких типов,
сколько запросов заблокировал, кто из сотрудников чаще других вставляет
данные клиентов. Значений персональных данных в отчёте нет — их нет и в
журнале, только типы и счётчики. Имена сотрудников — те метки, под
которыми они работают в чате и расширении.

Отчёт отдаётся тремя способами: JSON для админки, страница для печати
(в PDF — через «Печать» браузера) и сообщение в Telegram по расписанию.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from gateway.models import Verdict

from .audit import AuditLog

logger = logging.getLogger("aigate.report")

LABELS = {
    "IIN": "ИИН", "BIN": "БИН", "PERSON": "ФИО", "IBAN": "Счета", "CARD": "Карты",
    "SECRET": "Ключи и пароли", "AMOUNT": "Суммы", "CONFIDENTIAL": "Гриф «конфиденциально»",
    "PHONE": "Телефоны", "EMAIL": "Почта", "ID_DOC": "Документы",
    "BIRTHDATE": "Даты рождения", "ADDRESS": "Адреса", "PLATE": "Госномера",
}

CHANNELS = {
    "browser": "Расширение браузера", "egress": "Чат и приложения", "api": "API и файлы",
    "endpoint": "Агент рабочих станций",
}

MONTHS = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


def month_bounds(month: str, utc_offset_hours: int = 5) -> tuple[float, float]:
    """Начало и конец месяца «YYYY-MM» по местному времени (РК — UTC+5)."""
    tz = dt.timezone(dt.timedelta(hours=utc_offset_hours))
    year, mon = (int(x) for x in month.split("-"))
    start = dt.datetime(year, mon, 1, tzinfo=tz)
    end = dt.datetime(year + mon // 12, mon % 12 + 1, 1, tzinfo=tz)
    return start.timestamp(), end.timestamp()


def previous_month(today: dt.date) -> str:
    first = today.replace(day=1)
    return (first - dt.timedelta(days=1)).strftime("%Y-%m")


def build(audit: AuditLog, month: str, utc_offset_hours: int = 5) -> dict:
    since, until = month_bounds(month, utc_offset_hours)
    prev_since, _ = month_bounds(previous_month(dt.date.fromtimestamp(since + 86400 * 2)), utc_offset_hours)
    records = audit.read_all()
    current = [r for r in records if since <= r.ts < until]
    previous = [r for r in records if prev_since <= r.ts < since]

    def hidden(rs) -> int:
        return sum(sum(r.entity_counts.values()) for r in rs if r.verdict == Verdict.MASKED.value)

    by_type: dict[str, int] = {}
    by_user: dict[str, dict] = {}
    by_channel: dict[str, int] = {}
    for r in current:
        u = by_user.setdefault(r.user, {"requests": 0, "hidden": 0, "blocked": 0})
        u["requests"] += 1
        by_channel[r.channel] = by_channel.get(r.channel, 0) + 1
        if r.verdict == Verdict.MASKED.value:
            n = sum(r.entity_counts.values())
            u["hidden"] += n
            for t, c in r.entity_counts.items():
                by_type[t] = by_type.get(t, 0) + c
        elif r.verdict == Verdict.BLOCKED.value:
            u["blocked"] += 1

    chain_ok, broken_at = audit.verify_chain()
    top_users = sorted(by_user.items(), key=lambda kv: (-kv[1]["hidden"], -kv[1]["requests"]))[:10]
    return {
        "month": month,
        "requests": len(current),
        "users": len(by_user),
        "hidden": hidden(current),
        "hidden_previous": hidden(previous),
        "requests_previous": len(previous),
        "blocked": sum(1 for r in current if r.verdict == Verdict.BLOCKED.value),
        "fail_closed": sum(1 for r in current if r.verdict == Verdict.FAIL_CLOSED.value),
        "requests_with_data": sum(1 for r in current if r.verdict == Verdict.MASKED.value),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "by_channel": by_channel,
        "top_users": [{"user": u, **v} for u, v in top_users],
        "chain_integrity": chain_ok,
        "chain_broken_at": broken_at,
    }


def _month_title(month: str) -> str:
    year, mon = month.split("-")
    return f"{MONTHS[int(mon) - 1]} {year}"


def _change(now: int, before: int) -> str:
    if before == 0:
        return ""
    pct = round((now - before) * 100 / before)
    if pct == 0:
        return " (как в прошлом месяце)"
    return f" ({'+' if pct > 0 else '−'}{abs(pct)} % к прошлому месяцу)"


def render_text(r: dict) -> str:
    """Короткий текст для Telegram."""
    lines = [f"AI-Gate: отчёт за {_month_title(r['month'])}", ""]
    if not r["requests"]:
        lines.append("Обращений к ИИ через шлюз не было.")
        return "\n".join(lines)
    lines.append(f"Обращений к ИИ: {r['requests']} от {r['users']} сотрудников{_change(r['requests'], r['requests_previous'])}.")
    lines.append(
        f"Скрыто персональных данных: {r['hidden']}{_change(r['hidden'], r['hidden_previous'])}, "
        f"в {r['requests_with_data']} обращениях."
    )
    if r["by_type"]:
        lines.append("Чаще всего: " + ", ".join(f"{LABELS.get(t, t).lower()} — {c}" for t, c in list(r["by_type"].items())[:5]) + ".")
    if r["blocked"]:
        lines.append(f"Заблокировано запросов: {r['blocked']} (ключи, пароли или гриф).")
    if r["fail_closed"]:
        lines.append(f"Не проверено и не отправлено из-за ошибки: {r['fail_closed']}.")
    heavy = [u for u in r["top_users"] if u["hidden"]][:3]
    if heavy:
        lines.append("Больше всего данных клиентов вставляли: " + ", ".join(f"{u['user']} ({u['hidden']})" for u in heavy) + ".")
    lines.append("")
    lines.append("Журнал не изменялся." if r["chain_integrity"] else f"ВНИМАНИЕ: журнал изменён, запись №{r['chain_broken_at']}.")
    lines.append("Значений персональных данных в отчёте нет, только счётчики.")
    return "\n".join(lines)


def render_html(r: dict) -> str:
    """Страница для печати и сохранения в PDF."""
    e = html.escape
    rows_type = "".join(f"<tr><td>{e(LABELS.get(t, t))}</td><td>{c}</td></tr>" for t, c in r["by_type"].items())
    rows_users = "".join(
        f"<tr><td>{e(u['user'])}</td><td>{u['requests']}</td><td>{u['hidden']}</td><td>{u['blocked']}</td></tr>"
        for u in r["top_users"]
    )
    rows_channels = "".join(
        f"<tr><td>{e(CHANNELS.get(ch, ch))}</td><td>{n}</td></tr>" for ch, n in r["by_channel"].items()
    )
    integrity = (
        "Журнал аудита цел: ни одна запись не изменена и не удалена."
        if r["chain_integrity"]
        else f"Журнал аудита изменён начиная с записи №{r['chain_broken_at']}. Сообщите ответственному за ИБ."
    )
    empty = "<p>Обращений к ИИ через шлюз в этом месяце не было.</p>" if not r["requests"] else ""
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Отчёт AI-Gate за {e(_month_title(r['month']))}</title>
<style>
  :root {{ --graphite:#1c2733; --muted:#5b6876; --rule:#cfd6de; --ink:#4b3f9e; --alarm:#b3261e; }}
  body {{ margin:0; background:#fff; color:var(--graphite); font:15px/1.55 "Segoe UI","PT Sans",system-ui,sans-serif; }}
  .page {{ max-width:760px; margin:0 auto; padding:40px 24px; }}
  h1 {{ font-size:1.7rem; line-height:1.15; margin:0 0 4px; }}
  .sub {{ color:var(--muted); margin:0 0 28px; }}
  .figures {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:18px 28px; margin:0 0 30px; }}
  .figures div {{ border-left:3px solid var(--ink); padding-left:12px; }}
  .figures b {{ display:block; font-size:1.6rem; line-height:1.1; }}
  .figures span {{ color:var(--muted); font-size:.9rem; }}
  h2 {{ font-size:1.05rem; margin:26px 0 8px; }}
  table {{ width:100%; border-collapse:collapse; font-size:.93rem; }}
  th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--rule); }}
  th {{ color:var(--muted); font-weight:600; }}
  td:not(:first-child), th:not(:first-child) {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .integrity {{ margin-top:28px; padding:12px 14px; border:1px dashed var(--rule); border-radius:6px; }}
  .integrity.bad {{ color:var(--alarm); border-color:var(--alarm); }}
  .note {{ color:var(--muted); font-size:.85rem; margin-top:20px; }}
  @media print {{ .page {{ padding:0; }} }}
</style></head>
<body><div class="page">
  <h1>Как сотрудники работали с ИИ: {e(_month_title(r['month']))}</h1>
  <p class="sub">Отчёт шлюза AI-Gate для руководителя. Значений персональных данных здесь нет — только счётчики.</p>
  {empty}
  <div class="figures">
    <div><b>{r['requests']}</b><span>обращений к ИИ{e(_change(r['requests'], r['requests_previous']))}</span></div>
    <div><b>{r['users']}</b><span>сотрудников пользовались</span></div>
    <div><b>{r['hidden']}</b><span>значений скрыто{e(_change(r['hidden'], r['hidden_previous']))}</span></div>
    <div><b>{r['blocked']}</b><span>запросов заблокировано</span></div>
  </div>
  <h2>Какие данные скрыты</h2>
  <table><thead><tr><th>Тип</th><th>Сколько</th></tr></thead><tbody>{rows_type or '<tr><td>—</td><td>0</td></tr>'}</tbody></table>
  <h2>Сотрудники</h2>
  <table><thead><tr><th>Сотрудник</th><th>Обращений</th><th>Скрыто</th><th>Заблокировано</th></tr></thead><tbody>{rows_users or '<tr><td>—</td><td>0</td><td>0</td><td>0</td></tr>'}</tbody></table>
  <h2>Откуда обращались</h2>
  <table><thead><tr><th>Канал</th><th>Обращений</th></tr></thead><tbody>{rows_channels or '<tr><td>—</td><td>0</td></tr>'}</tbody></table>
  <p class="integrity{'' if r['chain_integrity'] else ' bad'}">{e(integrity)}</p>
  <p class="note">Чтобы сохранить в PDF: «Печать» → «Сохранить как PDF».</p>
</div></body></html>"""


def send_telegram(token: str, chat_id: str, text: str, timeout: float = 15.0) -> None:
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram отказал: {payload.get('description')}")


class MonthlyScheduler:
    """Раз в час проверяет, не пора ли отправить отчёт за прошлый месяц.

    Отправленный месяц запоминается в файле состояния, поэтому перезапуск
    шлюза не приводит к повторной отправке, а пропущенная (шлюз был
    выключен) уходит при следующем запуске.
    """

    def __init__(
        self,
        audit: AuditLog,
        state_path: Path,
        send,
        day: int = 1,
        utc_offset_hours: int = 5,
        interval_seconds: float = 3600,
    ):
        self.audit = audit
        self.state_path = state_path
        self.send = send
        self.day = day
        self.utc_offset_hours = utc_offset_hours
        self.interval = interval_seconds
        self._stop = threading.Event()

    def _last_sent(self) -> str:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8")).get("last_sent", "")
        except (OSError, ValueError):
            return ""

    def tick(self, now: dt.datetime | None = None) -> str | None:
        """Отправить отчёт, если пора. Возвращает отправленный месяц или None."""
        tz = dt.timezone(dt.timedelta(hours=self.utc_offset_hours))
        now = now or dt.datetime.now(tz)
        if now.day < self.day:
            return None
        month = previous_month(now.date())
        if self._last_sent() >= month:
            return None
        self.send(render_text(build(self.audit, month, self.utc_offset_hours)))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"last_sent": month}), encoding="utf-8")
        logger.info("Отчёт за %s отправлен", month)
        return month

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # сеть или Telegram недоступны — попробуем через час
                logger.warning("Отчёт не отправлен: %s", e)
            self._stop.wait(self.interval)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="monthly-report", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
