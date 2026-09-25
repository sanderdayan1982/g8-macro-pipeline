"""notify — aviso por Telegram desde un ejecutor SIN depender de GitHub (plan v1.1 §4.2, condición P2).

Lee el bot token y el chat de ficheros locales (p. ej. ~/.g8/telegram_bot_token, ~/.g8/telegram_chat_id)
o de variables de entorno. Nunca imprime el token (la URL se redacta). Un solo intento por mensaje: si
falla, el mensaje queda en la cola local de pendientes y se reintenta en la siguiente ejecución — así no
se duplica un mensaje que Telegram sí aceptó.

G8_NO_SEND=1 (o dry=True) → no envía nada: imprime y devuelve "DRY". Las pruebas lo usan siempre.
"""
import json
import os
import urllib.parse

from . import g8http


def _read(path):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def config(cfg_dir="~/.g8", env=None):
    env = os.environ if env is None else env
    tok = env.get("TELEGRAM_BOT_TOKEN") or _read(os.path.join(cfg_dir, "telegram_bot_token"))
    chat = env.get("TELEGRAM_CHAT_ID") or _read(os.path.join(cfg_dir, "telegram_chat_id"))
    return tok, chat


def send(text, cfg_dir="~/.g8", env=None, transport=None, dry=None, budget=None):
    """→ "SENT" | "DRY" | "NOT_CONFIGURED" | clase de fallo de g8http."""
    env = os.environ if env is None else env
    if dry is None:
        dry = env.get("G8_NO_SEND", "") == "1"
    if dry:
        print("[notify DRY] " + text.replace("\n", " | ")[:500])
        return "DRY"
    tok, chat = config(cfg_dir, env)
    if not tok or not chat:
        return "NOT_CONFIGURED"
    body = urllib.parse.urlencode({"chat_id": chat, "text": text[:3900], "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    res = g8http.fetch("https://api.telegram.org/bot%s/sendMessage" % tok, provider="telegram", method="POST",
                       headers={"Content-Type": "application/x-www-form-urlencoded"}, body=body,
                       transport=transport, budget=budget or g8http.Budget(60), read_timeout=30)
    if res.ok:
        try:
            if json.loads(res.body.decode("utf-8")).get("ok"):
                return "SENT"
        except Exception:                                   # noqa: BLE001
            pass
        return g8http.FAIL_INVALID
    return res.cls


class AlertBook(object):
    """Avisa solo en cambio del conjunto de alertas activas y recuerda una vez cada `remind_h` horas mientras
    sigan activas (una alerta enviada una vez no debe quedar olvidada). Guarda los mensajes no entregados."""

    def __init__(self, path, remind_h=24):
        self.path = path
        self.remind_h = remind_h
        try:
            with open(path, encoding="utf-8") as fh:
                self.state = json.load(fh)
        except (OSError, ValueError):
            self.state = {"active": {}, "pending": []}

    def plan(self, alerts, now_ts, no_remind=(), retired=()):
        """alerts: {clave: texto}. → lista de textos a enviar (nuevas, resueltas, recordatorios).
        no_remind: claves informativas que se avisan al aparecer o cambiar, sin recordatorio diario."""
        act = self.state.get("active", {})
        out = []
        for k, txt in sorted(alerts.items()):
            prev = act.get(k)
            if prev is None or prev.get("text") != txt or prev.get("sent_ts", 0) == 0:
                out.append(("NEW", k, txt))
            elif k not in no_remind and now_ts - prev.get("sent_ts", 0) >= self.remind_h * 3600:
                out.append(("REMIND", k, txt))
        for k in sorted(set(act) - set(alerts)):
            # desaparecer por retirada explícita no es una recuperación (R2-4)
            out.append(("RETIRED" if k in retired else "RESOLVED", k, act[k].get("text", k)))
        return out

    def commit(self, alerts, sent_keys, now_ts, undelivered):
        act = self.state.get("active", {})
        new_act = {}
        for k, txt in alerts.items():
            prev = act.get(k, {})
            new_act[k] = {"text": txt, "sent_ts": now_ts if k in sent_keys else prev.get("sent_ts", 0)}
        self.state = {"active": new_act, "pending": undelivered[-50:]}
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=1, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, self.path)
