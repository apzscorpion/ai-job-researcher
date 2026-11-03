"""
Resource guard: keeps the local models from cooking this PC.

Every 30 s it reads total CPU, RAM and (when LibreHardwareMonitor runs) CPU temperature. When the PC is
hot or busy — or other apps alone are using a lot of CPU — it unloads Ollama's models and pauses local
model work (llm.chat / llm.embed return "" so callers fall back to the cloud AIs or try later).
It resumes once two readings in a row are calm.

status() -> {"cpu", "ram", "temp", "other_cpu", "paused", "reason"}
check() -> None    (the scheduler calls this every 30 s)
"""

import asyncio
import logging
import subprocess
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PAUSE_CPU, PAUSE_OTHER_CPU, PAUSE_RAM, PAUSE_TEMP = 85.0, 60.0, 90.0, 85.0
RESUME_CPU, RESUME_RAM, RESUME_TEMP = 60.0, 80.0, 75.0
_MODEL_PROCS = ("ollama", "llama-server", "ollama_llama_server")

_state: Dict[str, Any] = {"cpu": None, "ram": None, "temp": None, "other_cpu": None, "paused": False, "reason": ""}
_calm = 0
_lhm: Optional[bool] = None  # LibreHardwareMonitor's WMI namespace exists (checked once)


def paused() -> bool:
    return bool(_state["paused"])


def status() -> Dict[str, Any]:
    return dict(_state)


def _temperature() -> Optional[float]:
    """Hottest CPU sensor from LibreHardwareMonitor (needs it running as admin); None when unavailable."""
    global _lhm
    if _lhm is False:
        return None
    script = ("Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor -ErrorAction Stop | "
              "Where-Object { $_.SensorType -eq 'Temperature' -and $_.Identifier -like '*cpu*' } | "
              "Measure-Object -Property Value -Maximum | Select-Object -ExpandProperty Maximum")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        _lhm = bool(out)
        return round(float(out), 1) if out else None
    except Exception:
        _lhm = False
        return None


def _read() -> Dict[str, Any]:
    import psutil

    procs = [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().removesuffix(".exe") in _MODEL_PROCS]
    for p in procs:
        p.cpu_percent(None)  # prime per-process counters for the 1 s window below
    cpu = psutil.cpu_percent(interval=1.0)
    model_cpu = 0.0
    for p in procs:
        try:
            model_cpu += p.cpu_percent(None) / psutil.cpu_count()
        except psutil.Error:
            pass
    return {"cpu": round(cpu), "ram": round(psutil.virtual_memory().percent), "temp": _temperature(),
            "other_cpu": round(max(0.0, cpu - model_cpu))}


def decide(r: Dict[str, Any], is_paused: bool, calm: int):
    """(paused, reason, calm count) for a reading; pure so it can be tested."""
    temp = r.get("temp")
    hot = [why for bad, why in (
        (r["cpu"] >= PAUSE_CPU, f"CPU {r['cpu']}%"),
        (r["other_cpu"] >= PAUSE_OTHER_CPU, f"other apps use {r['other_cpu']}% CPU"),
        (r["ram"] >= PAUSE_RAM, f"RAM {r['ram']}%"),
        (temp is not None and temp >= PAUSE_TEMP, f"CPU {temp}°C")) if bad]
    if hot:
        return True, ", ".join(hot), 0
    if not is_paused:
        return False, "", 0
    quiet = r["cpu"] < RESUME_CPU and r["ram"] < RESUME_RAM and (temp is None or temp < RESUME_TEMP)
    calm = calm + 1 if quiet else 0
    return (False, "", 0) if calm >= 2 else (True, _state["reason"], calm)


async def _unload_models() -> None:
    import httpx

    from app.config import OLLAMA_URL

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for m in (await client.get(f"{OLLAMA_URL}/api/ps")).json().get("models", []):
                await client.post(f"{OLLAMA_URL}/api/generate", json={"model": m["name"], "keep_alive": 0})
    except Exception as exc:
        logger.info("resources: couldn't unload Ollama models: %s", exc)


async def check() -> None:
    global _calm
    r = await asyncio.to_thread(_read)
    was = _state["paused"]
    now, reason, _calm = decide(r, was, _calm)
    _state.update(r, paused=now, reason=reason)
    if now and not was:
        logger.warning("resources: pausing local models (%s)", reason)
    if now:
        await _unload_models()  # every check while paused: a call already in flight may have loaded one again
    elif was:
        logger.info("resources: PC is calm again (CPU %s%%, RAM %s%%) — local models resume", r["cpu"], r["ram"])
