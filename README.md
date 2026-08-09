# Nishizumi SR

**Overlay de Safety Rating ao vivo para iRacing + calculadora web.**

- 🌐 Calculadora online: <https://nishizumi-maho.github.io/Nishizumi-SR/>
- 🖥️ Overlay desktop: este repositorio (`python main.py`)

Os dois usam **exatamente a mesma formula** (conferido caso a caso: as duas
implementacoes batem ate a 4a casa decimal).

---

## iRacing Safety Rating Live Estimator

Overlay transparente para Windows que mostra, durante uma sessao do iRacing, uma
**estimativa** de quanto Safety Rating (SR) voce esta ganhando ou perdendo — chamada
sempre de **Estimated Safety Rating**, nunca de "SR oficial".

> **Antes de tudo, leia [`docs/RESEARCH.md`](docs/RESEARCH.md)** — em especial a
> **secao 9**, que documenta o que foi efetivamente **medido** contra os dados
> oficiais do iRacing (e o que continua sendo aproximacao). O iRacing nunca
> publicou a formula; o que este projeto tem e' uma reconstrucao a partir dos
> campos oficiais `old_cpi`/`new_cpi`, que reproduz o SR com MAE de 0.009 SR no
> mapeamento CPI→SR. A parte ainda aproximada (a janela movel) esta marcada
> como tal — o app continua chamando o numero de **Estimated** Safety Rating.

Testado nesta maquina com o iRacing aberto de verdade: o app conectou via SDK,
identificou a sessao (Race), o piloto (`PlayerCarIdx`), o SR inicial (3.74) e a
pista (Nurburgring Combined Short B, aprendendo 165 curvas/volta automaticamente
via `TrackNumTurns`), e renderizou o overlay corretamente na tela.

## 1. Instalacao

Requisitos: Windows, Python 3.10+, iRacing instalado.

```bash
pip install -r requirements.txt
```

Pacotes usados e por que:
- `pyirsdk` — leitura da memoria compartilhada do iRacing (telemetria + YAML de sessao).
- `PySide6` — interface do overlay (pedido explicitamente no lugar de Tkinter).
- `numpy` — usado apenas em `sr/calibration.py` para o ajuste linear (minimos quadrados).
- `keyboard` — hotkeys globais (funcionam mesmo com o iRacing em foco).
- `PyYAML` — dependencia do `pyirsdk` para parsear o YAML de sessao.

## 2. Executar

Com o iRacing aberto (em qualquer sessao — ou mesmo so no menu principal):

```bash
python main.py
```

Um painel semitransparente aparece no canto superior esquerdo da tela (posicao
inicial `x=20, y=20`, configuravel). Enquanto o iRacing nao esta aberto ou nao
ha sessao valida, o overlay mostra `Waiting for iRacing...` /
`Waiting for session...` em vez de qualquer numero — nunca mostra uma
estimativa "chutada".

Como nao ha barra de titulo (overlay sem bordas de proposito), para fechar o
app use o **icone na bandeja do sistema** (perto do relogio) → "Sair", ou feche
o processo Python.

### Hotkeys (globais, funcionam com o iRacing em foco)

| Tecla | Acao |
|---|---|
| `F8` | Mostrar/ocultar o overlay |
| `F9` | Travar/destravar (destravado = pode arrastar com o mouse para reposicionar; travado = click-through, nao atrapalha o mouse no jogo) |
| `F10` | Alternar entre os 3 modos: compact / normal / detailed |

Configuravel em `config/settings.json` → `hotkeys`. Se o registro das hotkeys
falhar (por exemplo, por permissao), o app continua funcionando normalmente e
os mesmos controles ficam disponiveis pelo icone da bandeja.

> Nota de plataforma: se o iRacing estiver rodando como Administrador, o
> aplicativo tambem precisa rodar como Administrador para que as hotkeys
> globais (`keyboard`) funcionem — limitacao normal do Windows para hooks de
> teclado entre processos com privilegios diferentes.

### Testar no iRacing

1. Rode `python main.py` com o iRacing aberto.
2. Entre em uma sessao (Practice, Qualify, Race ou Time Trial).
3. Confira `logs/sr_estimator_<timestamp>.log` — deve aparecer `Connected to
   iRacing`, `Session detected: ...`, `PlayerCarIdx: ...`, `Initial SR: ...`.
4. Complete uma volta — o log deve mostrar `Lap completed`, `Corners: +N`,
   `Incidents`, `CPI`, `Estimated SR`, e o overlay deve atualizar.
5. Em Practice, o overlay mostra um aviso de que a sessao nao conta para o SR
   (fato documentado — ver `docs/RESEARCH.md` secao 1), mas ainda mostra
   curvas/incidentes de forma informativa.
6. Ao terminar a sessao (bandeira quadriculada / cool down), o overlay mostra
   "SESSION COMPLETE" por alguns segundos e a sessao e salva em
   `data/history.json` como pendente. Na proxima vez que uma sessao nova
   comecar (mesmo dia ou em outra execucao do app), o SR inicial fresco lido
   naquele momento e usado para reconciliar automaticamente o registro
   pendente anterior e calcular o erro real da estimativa.

## 3. Estrutura do projeto

```
main.py                  # orquestra tudo: polling, deteccao de eventos, overlay
requirements.txt

iracing/
    sdk.py                # wrapper fino sobre pyirsdk (conexao, leitura defensiva)
    session.py             # parse de WeekendInfo/DriverInfo/SessionInfo (YAML)
    telemetry.py            # leitura por tick + deteccao de volta completada

sr/
    license_map.py          # mapeamento CPI<->SR MEDIDO contra a API /data (o nucleo)
    model.py                 # janela movel + remapeamento de faixa + pesos de sessao
    license_targets.py        # OBSOLETO: ponte para license_map.py (numeros antigos superados)
    calculator.py            # acumulador de curvas/incidentes/CPI por sessao
    history.py                # persistencia + reconciliacao com SR real
    calibration.py             # estatisticas de erro + ajuste manual dos parametros (global e por pista)

tracks/
    database.py               # resolve curvas/volta por pista (TrackID -> curvas)
    track_data.json             # base local, aprendida automaticamente + editavel

overlay/
    window.py                    # janela PySide6 (transparente, click-through, hotkeys)
    widgets.py                     # formatacao de texto por modo (compact/normal/detailed)
    styles.py                       # cores e QSS

config/
    settings.py                      # carregamento/persistencia de config/settings.json
    settings.json

data/
    history.json                      # historico de sessoes para calibracao

docs/
    RESEARCH.md                        # fatos vs. hipoteses (leia antes de mexer no modelo)

logs/                                   # um arquivo por execucao, criado automaticamente
```

## 4. O modelo de estimativa (v3 — resumo)

> **Mudanca importante (2026-08-09):** o modelo deixou de ser uma reta
> inventada com um coeficiente arbitrario. A relacao CPI→SR foi **medida**
> contra os dados oficiais do iRacing (1.280 pares reais de SR/CPI/licenca
> obtidos pela API `/data`) e reproduz o numero oficial com **MAE de 0.009 SR**.
> Ver `docs/RESEARCH.md` secao 9 — e leia essa secao antes de mexer no modelo.

Tres passos (`sr/license_map.py` + `sr/model.py`):

```
1. inverter    CPI_atual = cpi_from_sr(SR_lido_do_SDK, classe, banda)   # exato
2. atualizar   alpha  = min(alpha_max, curvas / janela)
               1/CPI' = (1-alpha)/CPI_atual + alpha * (incidentes/curvas)
3. remapear    SR' = banda + a * ln(CPI' / piso(classe, banda))
               (com troca de faixa quando sai de [banda, banda+1))
```

Curvas e incidentes entram ja multiplicados pelo peso da sessao
(Sporting Code §3.6.1.1: Race 1.00, Warm-Up/Qualify-Open/Practice-Open 0.50,
Time Trial/Qualify-Lone/Practice-Lone 0.35). O nome oficial e "Corner **and**
Incident Multipliers" — o peso vale para os dois lados, nao so' para os
incidentes como fazia o v2.

**O salto de ±0.40 ao cruzar 2.00/3.00 nao esta chumbado no codigo**: ele
emerge do passo 3, porque as faixas inteiras se sobrepoem numa escada
geometrica de razao 1.4562. O valor exato que sai da geometria e 0.427; o
iRacing arredonda para 0.40 no artigo de suporte. Pela mesma razao, a promocao
de classe custa exatamente 1.00 de SR.

Precisao medida contra as 9 corridas reais desta conta:

| modelo | MAE | RMSE |
|---|---|---|
| **v3 (atual)** | **0.022 SR** | **0.029 SR** |
| v2 (anterior) | 0.174 SR | 0.247 SR |

O unico parametro ainda nao exato e a **janela movel** (`window_corners` e
`alpha_max` em `config/settings.json`) — ver `docs/RESEARCH.md` §9.5.

Curvas por volta: o valor **oficial** do iRacing (`corners_per_lap` da API
`/data`) e' a fonte preferida — ex.: Daytona Oval 4, Spa 21, Nordschleife
Industriefahrten 73, Nurburgring Combined 165. Sem isso, `tracks/database.py`
cai para `TrackNumTurns` do SDK (aproximacao, confianca baixa).

As curvas sao contadas por **distancia percorrida**
(`corners_per_lap x delta(LapDistPct)`), nao por volta contada — assim um tow
logo apos os boxes ou uma volta abandonada nao credita curvas que o piloto nao
dirigiu, e uma volta parcial credita as que ele dirigiu. Ver
`iracing/telemetry.py::CornerAccumulator` e `docs/RESEARCH.md` §9.7.

CPI para segurar 3.00 SR, **medido** (nao mais estimativa da comunidade):
Rookie 10.8 | D 15.7 | C 22.9 | B 33.3 | A 48.5 | Pro 70.7.

## 5. Calibracao com resultados reais

Cada sessao pontuavel (Race/Qualify/Warmup/Time Trial) termina como um registro
pendente em `data/history.json`. Quando o SDK consegue ler um SR fresco no
inicio da proxima sessao, esse registro e' reconciliado automaticamente:
`actual_delta`, `final_sr` e `error` sao preenchidos.

Depois de acumular sessoes reconciliadas, rode:

```bash
python -m sr.calibration
```

Isso mostra erro medio, erro absoluto medio, o nivel de confianca atual
(Low/Medium/High, baseado no numero de sessoes) e sugere um novo valor para
`sensitivity_k` (ajuste manual — o app nao se recalibra sozinho, de proposito,
para manter tudo auditavel). Alem disso, para pistas com pelo menos 5 sessoes
reconciliadas naquela mesma pista, o comando **ja atualiza automaticamente**
`tracks/track_data.json` (`corners_per_lap`, `source="calibrado_historico"`) —
mesma logica que o app ja usa pra aprender pistas novas via SDK, so' que agora
com dado real em vez de aproximacao.

**Limitacao conhecida:** a reconciliacao usa sempre o ultimo registro pendente,
sem checar categoria (oval/road/dirt). Para quem so corre uma categoria por dia
isso e preciso; para quem alterna categorias no mesmo dia, pode reconciliar com
a sessao errada (ver `sr/history.py` para detalhes e proximos passos).

## 6. O que NAO esta implementado nesta versao (proximos passos / MVP3)

Documentado explicitamente em vez de fingir que existe:

- **Leitura automatica do CPI oficial pelo app**: `old_cpi`/`new_cpi` nao
  existem no SDK de telemetria, mas **estao acessiveis** na API `/data` via o
  proxy BFF do site (`/bff/pub/proxy/data/...`) com o cookie de sessao — foi
  assim que o modelo v3 foi calibrado (`docs/RESEARCH.md` §9.1). O que nao esta
  implementado e' o app **buscar isso sozinho**: hoje o caminho depende de um
  navegador logado, e e' rota interna do site (pode mudar sem aviso), entao fica
  como passo de calibracao manual em vez de dependencia em tempo de execucao.
  O registro de novos OAuth client IDs continua pausado — mas isso deixou de
  ser bloqueio para calibrar.
- **Promocao de licenca**: os limiares (4.00 Fast Track, 3.00 Rookie, faixa de
  democao <2.00/<1.00) sao publicos e poderiam ser exibidos comparando com o
  SR atual — nao incluido na v1 para manter o escopo focado no estimador;
  facil de adicionar depois usando `PlayerInfo.safety_rating`.
- **VR/OpenXR/SteamVR**: overlay funciona em desktop. Para VR, a estrategia
  mais realista e' compositar via OpenXR ou usar uma ferramenta de overlay VR
  de terceiros que capture uma janela desktop — nao implementado aqui, mas a
  separacao entre calculo (`sr/`, `iracing/`) e apresentacao (`overlay/`) foi
  pensada para permitir trocar so a camada de apresentacao no futuro.
- **Troca de piloto entre maquinas diferentes (endurance)**: o app usa
  `PlayerCarMyIncidentCount`, que ja isola os incidentes do piloto local mesmo
  em carros de equipe — mas o SDK nao expoe quem esta fisicamente no volante
  em outra maquina, entao o app so pode falar com seguranca sobre o piloto
  logado localmente (ver `docs/RESEARCH.md` secao 4).
- **Reconciliacao por categoria** (oval/road/dirt separados) no historico.

## 7. Performance

Telemetria e lida a cada 100ms (configuravel), mas o calculo da estimativa so
roda ao completar uma volta — a cada tick, so um snapshot barato (sem log, sem
recalculo pesado) e feito para manter a UI atualizada. Parse do YAML de sessao
(mais caro) so acontece quando `SessionNum` muda. Em teste local, o processo
usou ~73MB de RAM e CPU desprezivel durante polling continuo.
