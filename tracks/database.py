"""
Base local de "curvas por volta" por pista/configuracao.

Estrategia (ver docs/RESEARCH.md secao 6):
1. Procura por TrackID (unico por configuracao especifica, ex.: Nordschleife
   vs Combined vs GP sao TrackID diferentes) numa entrada manual/curada.
2. Se nao existir, usa TrackNumTurns do SDK como aproximacao e salva a
   entrada automaticamente (fonte + confianca marcadas), para poder ser
   corrigida a mao depois.
3. Se nem isso existir, usa um valor de seguranca fixo (fallback_corners_per_lap).

Nunca assume um numero "magico" de curvas sem deixar registrado de onde veio.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sr_estimator.tracks")


OFFICIAL_CORNERS_PATH = Path(__file__).resolve().parent / "official_corners.json"


def _load_official_corners() -> dict:
    """Base OFICIAL de curvas/volta (endpoint /data/track/get do iRacing).

    436 pistas, chaveadas pelo mesmo `track_id` que o SDK expoe como `TrackID`.
    Se o arquivo sumir, o app continua funcionando com TrackNumTurns do SDK.
    """
    try:
        raw = json.loads(OFFICIAL_CORNERS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Base oficial de curvas indisponivel (%s); caindo para o SDK", exc)
        return {}
    return {int(t["track_id"]): t for t in raw.get("tracks", []) if t.get("corners_per_lap")}


_OFFICIAL_CORNERS = _load_official_corners()

DEFAULT_PATH = Path(__file__).resolve().parent / "track_data.json"


@dataclass
class TrackRecord:
    track_id: int
    track_name: str
    config_name: str
    corners_per_lap: float
    source: str  # "manual" | "sdk_track_num_turns" | "fallback_generico"
    confidence: str  # "alta" | "baixa" | "muito_baixa"
    updated_at: str


class TrackDatabase:
    def __init__(self, path: Path = DEFAULT_PATH, fallback_corners_per_lap: int = 10) -> None:
        self._path = path
        self._fallback = fallback_corners_per_lap
        self._records: dict[int, TrackRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._records = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Falha ao ler track_data.json (%s), comecando vazio", exc)
            raw = []
        self._records = {int(r["track_id"]): TrackRecord(**r) for r in raw}

    def _save(self) -> None:
        payload = [asdict(r) for r in self._records.values()]
        self._path.write_text(json.dumps(payload, indent=4, ensure_ascii=False), encoding="utf-8")

    def get_corners_per_lap(
        self,
        track_id: int,
        track_name: str,
        config_name: str,
        sdk_num_turns: int,
    ) -> tuple[float, str, str]:
        """Retorna (corners_per_lap, source, confidence)."""
        existing = self._records.get(track_id)
        if existing is not None:
            return existing.corners_per_lap, existing.source, existing.confidence

        # 1a opcao: valor OFICIAL do iRacing (tracks/official_corners.json,
        # extraido do endpoint /data/track/get - 436 pistas). E' exatamente o
        # numero que o calculo de SR usa, entao nao ha por que preferir o SDK.
        official = _OFFICIAL_CORNERS.get(track_id)
        if official is not None:
            record = TrackRecord(
                track_id=track_id,
                track_name=track_name or official.get("track_name", ""),
                config_name=config_name or official.get("config_name", ""),
                corners_per_lap=float(official["corners_per_lap"]),
                source="iracing_data_api",
                confidence="alta",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            self._records[track_id] = record
            self._save()
            logger.info("Pista resolvida pela base oficial: %s (%s) -> %s curvas/volta",
                        record.track_name, record.config_name, record.corners_per_lap)
            if sdk_num_turns and sdk_num_turns > 0 and int(sdk_num_turns) != int(record.corners_per_lap):
                # Divergencia observada em Nordschleife Industriefahrten (SDK/
                # subsession 73 vs base oficial 154). Vale registrar em vez de
                # engolir: o valor da sessao ao vivo e' o que aquele evento usou.
                logger.warning(
                    "TrackNumTurns do SDK (%s) difere da base oficial (%s) para track_id=%s; "
                    "usando o do SDK por ser o valor daquela sessao",
                    sdk_num_turns, record.corners_per_lap, track_id,
                )
                record.corners_per_lap = float(sdk_num_turns)
                record.source = "sdk_track_num_turns_override"
                self._save()
            return record.corners_per_lap, record.source, record.confidence

        if sdk_num_turns and sdk_num_turns > 0:
            record = TrackRecord(
                track_id=track_id,
                track_name=track_name,
                config_name=config_name,
                corners_per_lap=float(sdk_num_turns),
                source="sdk_track_num_turns",
                confidence="baixa",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            self._records[track_id] = record
            self._save()
            logger.info("Pista nova aprendida via SDK: %s (%s) -> %s curvas/volta [confianca baixa]",
                        track_name, config_name, sdk_num_turns)
            return record.corners_per_lap, record.source, record.confidence

        logger.warning(
            "TrackNumTurns indisponivel para %s (%s); usando fallback generico de %s curvas/volta",
            track_name, config_name, self._fallback,
        )
        return float(self._fallback), "fallback_generico", "muito_baixa"

    def get_record(self, track_id: int) -> Optional[TrackRecord]:
        return self._records.get(track_id)

    def set_manual(
        self,
        track_id: int,
        track_name: str,
        config_name: str,
        corners_per_lap: float,
        source: str = "manual",
    ) -> None:
        """Permite correcao manual (edicao do arquivo tambem funciona) ou,
        com source="calibrado_historico", a atualizacao feita por
        sr/calibration.py::calibrate_track_corners a partir de sessoes reais
        reconciliadas - em ambos os casos vira confidence="alta" porque a
        fonte deixou de ser a aproximacao crua do SDK."""
        self._records[track_id] = TrackRecord(
            track_id=track_id,
            track_name=track_name,
            config_name=config_name,
            corners_per_lap=corners_per_lap,
            source=source,
            confidence="alta",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._save()
