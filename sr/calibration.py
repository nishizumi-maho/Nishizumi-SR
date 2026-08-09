"""
Analise do historico de sessoes reconciliadas e (opcionalmente) reajuste da
janela movel do modelo em sr/model.py. O mapeamento CPI->SR NAO e' calibravel
aqui: ele foi medido contra a API /data do iRacing (ver sr/license_map.py).

Nada aqui roda automaticamente durante uma sessao ao vivo - e' uma ferramenta
separada (ver `python -m sr.calibration` no fim deste arquivo) porque:
1. o briefing pede explicitamente para NAO usar machine learning de inicio e
   para tratar isso como um passo manual/posterior (secao 12);
2. reajustar parametros silenciosamente a cada sessao tornaria a estimativa
   dificil de auditar, o que contraria o objetivo de transparencia (secao 10).

Uso tipico depois de acumular sessoes reconciliadas:
    python -m sr.calibration
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sr.license_map import class_index_from_string
from sr.model import session_weight
from sr.model import SRModelParams

logger = logging.getLogger("sr_estimator.calibration")

MIN_SESSIONS_FOR_FIT = 8
MIN_SESSIONS_PER_TRACK = 5

# Os pesos por tipo de sessao vem de sr.model.SESSION_WEIGHTS (tabela oficial
# do Sporting Code 3.6.1.1) e valem para curvas E incidentes.


@dataclass
class CalibrationStats:
    n: int
    mean_error: float
    mean_absolute_error: float
    bias: float  # igual a mean_error; mantido separado por clareza semantica


@dataclass
class TrackCalibrationResult:
    track_id: int
    track_name: str
    old_corners_per_lap: float
    new_corners_per_lap: float
    n_sessions: int


def confidence_level(n_reconciled_sessions: int) -> str:
    if n_reconciled_sessions < 5:
        return "Low"
    if n_reconciled_sessions < 20:
        return "Medium"
    return "High"


def _session_multiplier(record: dict) -> float:
    """Peso do Sporting Code 3.6.1.1 - vale para curvas E incidentes."""
    return session_weight(record.get("session_type"))


def compute_stats(history: list[dict]) -> Optional[CalibrationStats]:
    reconciled = [r for r in history if r.get("reconciled") and r.get("error") is not None]
    if not reconciled:
        return None
    errors = [r["error"] for r in reconciled]
    n = len(errors)
    mean_error = sum(errors) / n
    mae = sum(abs(e) for e in errors) / n
    return CalibrationStats(n=n, mean_error=mean_error, mean_absolute_error=mae, bias=mean_error)


def fit_window_corners(history: list[dict]) -> Optional[SRModelParams]:
    """Calibra a janela movel (`window_corners`) contra o historico real.

    O modelo v3 nao tem mais um "ganho" livre: a relacao CPI->SR foi medida
    contra a API /data do iRacing e reproduz o numero oficial com MAE de
    0.009 SR (ver sr/license_map.py). O unico passo ainda aproximado e' quanto
    uma sessao desloca o CPI acumulado - e' isso que se calibra aqui, por
    busca direta no erro de SR previsto (a relacao nao e' linear, entao
    minimos quadrados fechado nao se aplica como no v2).
    """
    reconciled = [r for r in history if r.get("reconciled") and r.get("actual_delta") is not None]
    if len(reconciled) < MIN_SESSIONS_FOR_FIT:
        logger.info(
            "Apenas %s sessao(oes) reconciliada(s); minimo de %s para recalibrar. Mantendo parametros atuais.",
            len(reconciled), MIN_SESSIONS_FOR_FIT,
        )
        return None

    from sr.model import project_sr

    def sse(window: float) -> float:
        params = SRModelParams(window_corners=window)
        total = 0.0
        for r in reconciled:
            sr0 = r.get("initial_sr")
            if sr0 is None or r.get("corners", 0) <= 0:
                continue
            idx = class_index_from_string(r.get("license_class", ""))
            if idx is None:
                continue
            mult = _session_multiplier(r)
            _, delta, _ = project_sr(sr0, idx, r["corners"] * mult,
                                     r["incidents"] * mult, params=params)
            total += (delta - r["actual_delta"]) ** 2
        return total

    best_w, best_v = None, None
    for window in range(600, 6001, 25):
        v = sse(float(window))
        if best_v is None or v < best_v:
            best_w, best_v = float(window), v

    if best_w is None:
        logger.warning("Nao foi possivel calibrar a janela: nenhum registro utilizavel.")
        return None

    logger.info("Recalibracao sugerida: window_corners=%.0f (baseado em %s sessoes)",
                best_w, len(reconciled))
    return SRModelParams(window_corners=best_w)


def calibrate_track_corners(history: list[dict], track_db, window_corners: float) -> list[TrackCalibrationResult]:
    """Para pistas com sessoes reconciliadas suficientes, ajusta corners_per_lap
    (a aproximacao local do Corner Multiplier real - fato confirmado, valores
    nao publicos, ver docs/RESEARCH.md secao 1 e 6) pelo fator de escala medio
    que teria feito o modelo bater com o actual_delta observado naquela pista,
    dado o window_corners ja calibrado. Escreve de volta via
    TrackDatabase.set_manual(..., source="calibrado_historico").

    So' roda quando chamado explicitamente (python -m sr.calibration) - nunca
    durante uma sessao ao vivo, mesma regra do resto deste arquivo.
    """
    reconciled = [
        r for r in history
        if r.get("reconciled") and r.get("actual_delta") is not None and r.get("track_id") is not None
    ]

    by_track: dict[int, list[dict]] = {}
    for r in reconciled:
        by_track.setdefault(int(r["track_id"]), []).append(r)

    results: list[TrackCalibrationResult] = []
    if window_corners <= 0:
        return results
    from sr.model import project_sr

    for track_id, records in by_track.items():
        if len(records) < MIN_SESSIONS_PER_TRACK:
            continue

        existing = track_db.get_record(track_id)
        if existing is None or existing.corners_per_lap <= 0:
            continue

        # Procura o fator de escala das curvas desta pista que minimiza o erro
        # de SR previsto. Substitui a inversao algebrica do v2, que so' era
        # possivel porque o modelo antigo era linear.
        params = SRModelParams(window_corners=window_corners)
        usable = [r for r in records
                  if r["corners"] > 0 and r.get("initial_sr") is not None
                  and class_index_from_string(r.get("license_class", "")) is not None]
        if not usable:
            continue

        def error_for(scale: float) -> float:
            total = 0.0
            for r in usable:
                idx = class_index_from_string(r.get("license_class", ""))
                mult = _session_multiplier(r)
                _, delta, _ = project_sr(r["initial_sr"], idx,
                                         r["corners"] * scale * mult,
                                         r["incidents"] * mult, params=params)
                total += (delta - r["actual_delta"]) ** 2
            return total

        best_scale, best_err = 1.0, None
        for step in range(30, 301):
            cand = step / 100.0
            err = error_for(cand)
            if best_err is None or err < best_err:
                best_scale, best_err = cand, err

        # Nunca deixa a calibracao mais que triplicar ou reduzir a 1/3 sem
        # revisao manual - mesma filosofia de clamp de sanidade do resto do projeto.
        scale = max(0.3, min(3.0, best_scale))

        new_corners = round(existing.corners_per_lap * scale, 1)
        if abs(new_corners - existing.corners_per_lap) < 0.05:
            continue

        track_db.set_manual(
            track_id, existing.track_name, existing.config_name, new_corners, source="calibrado_historico"
        )
        results.append(TrackCalibrationResult(
            track_id=track_id,
            track_name=existing.track_name,
            old_corners_per_lap=existing.corners_per_lap,
            new_corners_per_lap=new_corners,
            n_sessions=len(records),
        ))

    return results


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from sr.history import HistoryStore
    from tracks.database import TrackDatabase

    history = HistoryStore().all()
    stats = compute_stats(history)
    if stats is None:
        print("Nenhuma sessao reconciliada ainda. Rode algumas sessoes primeiro.")
        return

    print(f"Sessoes reconciliadas: {stats.n}")
    print(f"Confianca: {confidence_level(stats.n)}")
    print(f"Erro medio (bias): {stats.mean_error:+.3f}")
    print(f"Erro absoluto medio (MAE): {stats.mean_absolute_error:.3f}")

    fitted = fit_window_corners(history)
    if fitted is None:
        return

    print(f"\nSugestao de novo parametro (edite config/settings.json para aplicar):")
    print(f"  window_corners = {fitted.window_corners:.0f}")

    track_db = TrackDatabase()
    track_results = calibrate_track_corners(history, track_db, fitted.window_corners)
    if track_results:
        print(f"\n{len(track_results)} pista(s) recalibrada(s) em tracks/track_data.json:")
        for res in track_results:
            print(f"  {res.track_name} (id={res.track_id}): "
                  f"{res.old_corners_per_lap:.1f} -> {res.new_corners_per_lap:.1f} curvas/volta "
                  f"({res.n_sessions} sessoes)")
    else:
        print("\nNenhuma pista com sessoes suficientes ainda pra recalibrar "
              f"(minimo {MIN_SESSIONS_PER_TRACK} sessoes reconciliadas na mesma pista).")


if __name__ == "__main__":
    _main()
