"""
Persistencia do historico de sessoes (data/history.json) e reconciliacao com
o SR real observado depois.

Por que "reconciliacao" e nao captura direta do SR final:
o SDK nao garante que o SR exibido (LicSubLevel) ja reflita o resultado da
sessao que acabou de terminar no exato instante em que ela termina - o
iRacing pode levar um instante para processar o resultado. O que E' confiavel
e' que, quando uma NOVA sessao comeca (ou o app reconecta antes de qualquer
volta rodada), o SR lido naquele momento ja incorpora o resultado de
qualquer sessao pontuavel anterior. Por isso: toda sessao pontuavel termina
como registro "pendente" (actual_delta=None) e e' reconciliada na proxima vez
que um SR inicial fresco for lido.

Limitacao documentada: SR e' separado por categoria (oval/road/dirt) e este
mecanismo simples reconcilia com o ULTIMO pendente, sem checar categoria. Para
quem corre so uma categoria por dia isso e' preciso; para quem alterna
categorias no mesmo dia, pode reconciliar com a sessao errada. Ver
docs/RESEARCH.md e README (proximos passos) para o refinamento planejado.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sr_estimator.history")

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "history.json"


@dataclass
class SessionRecord:
    date: str
    track: str
    track_id: Optional[int]
    car: str
    license_class: str
    initial_sr: Optional[float]
    final_sr: Optional[float]
    actual_delta: Optional[float]
    estimated_delta: float
    laps: int
    corners: float
    incidents: int
    cpi: Optional[float]
    session_type: str
    error: Optional[float] = None
    reconciled: bool = False


class HistoryStore:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self._path = path
        self._records: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._records = []
            return
        try:
            self._records = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Falha ao ler history.json (%s), comecando vazio", exc)
            self._records = []

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._records, indent=4, ensure_ascii=False), encoding="utf-8")

    def all(self) -> list[dict]:
        return list(self._records)

    def append(self, record: SessionRecord) -> None:
        self._records.append(asdict(record))
        self._save()
        logger.info("Sessao salva no historico (pendente de reconciliacao com SR real)")

    def find_last_pending(self) -> Optional[dict]:
        for record in reversed(self._records):
            if not record.get("reconciled"):
                return record
        return None

    def reconcile_last_pending(self, final_sr: float) -> Optional[dict]:
        pending = self.find_last_pending()
        if pending is None or pending.get("initial_sr") is None:
            return None

        actual_delta = round(final_sr - pending["initial_sr"], 3)
        error = round(actual_delta - pending["estimated_delta"], 3)
        pending["final_sr"] = round(final_sr, 3)
        pending["actual_delta"] = actual_delta
        pending["error"] = error
        pending["reconciled"] = True
        self._save()
        logger.info(
            "Sessao reconciliada: estimado %+.2f | real %+.2f | erro %.2f",
            pending["estimated_delta"], actual_delta, error,
        )
        return pending


def build_record(
    track_display_name: str,
    track_id: Optional[int],
    car_name: str,
    lic_string: str,
    initial_sr: Optional[float],
    estimate,
    session_type: str,
) -> SessionRecord:
    return SessionRecord(
        date=datetime.now(timezone.utc).isoformat(),
        track=track_display_name,
        track_id=track_id,
        car=car_name,
        license_class=lic_string,
        initial_sr=initial_sr,
        final_sr=None,
        actual_delta=None,
        estimated_delta=estimate.session_delta,
        laps=estimate.laps_completed,
        corners=estimate.corners,
        incidents=estimate.incidents,
        cpi=estimate.cpi,
        session_type=session_type,
        error=None,
        reconciled=False,
    )
