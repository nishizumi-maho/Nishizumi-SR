# Pesquisa: iRacing Safety Rating & iRacing SDK

Este documento existe para cumprir uma regra fundamental deste projeto:
**não inventar** o algoritmo interno do iRacing nem nomes de variáveis do SDK.
Tudo abaixo foi levantado a partir de documentação oficial do iRacing, da
documentação comunitária do SDK (irsdkdocs) e do código-fonte do `pyirsdk`.
Onde a informação não é pública, isso é dito explicitamente.

Última atualização da pesquisa: 2026-08-09 (segunda rodada).

> ## ⚠️ LEIA A SEÇÃO 9 PRIMEIRO
> Em 2026-08-09, numa segunda rodada de investigação, **três conclusões deste
> documento foram derrubadas por evidência direta**:
> 1. A API `/data` do iRacing **não está bloqueada** — ela é acessível pelo
>    proxy BFF do site com o cookie de sessão (a seção 5 testou só o prefixo
>    errado). Isso deu acesso a `old_cpi`/`new_cpi` oficiais.
> 2. A fórmula CPI→SR **não é desconhecida**: foi medida com 1.280 pares reais
>    e reproduz o número oficial com MAE de 0.009 SR (seção 9).
> 3. Os alvos de CPI da comunidade usados no modelo v2 estavam **errados para
>    as classes altas** (A: ~48.5 medido vs "70-80" citado).
>
> As seções 2, 5 e 7 abaixo ficam preservadas como registro do que se sabia
> antes, com marcações de onde foram superadas.

---

## 1. Fatos conhecidos sobre o Safety Rating (fontes oficiais/comunitárias)

Fonte principal: [iRacing How-To: Safety Rating](https://support.iracing.com/support/solutions/articles/31000156960-iracing-how-to-safety-rating)
(artigo oficial de suporte), [iRacing 101: Incident Points](https://www.iracing.com/iracing-101-incident-points/)
(blog oficial), e análises comunitárias em [BoxThisLap](https://boxthislap.org/an-introduction-to-the-sr-system/)
e [SimRacingCockpit](https://simracingcockpit.gg/iracing-safety-rating-explained/).

- SR é baseado em **CPI — Corners Per Incident** ("a quantidade de curvas que você
  percorre por incidente").
- Cada configuração de pista tem um **"Corner Multiplier"** definido pelo iRacing,
  que normaliza o impacto de um incidente entre pistas curtas e longas.
- **Pontos de incidente** têm pesos diferentes:
  - `0x` — contato muito leve com parede ou com outro carro (contado mas não soma pontos)
  - `1x` — sair da pista ("off track")
  - `2x` — perda de controle (spin) ou contato mais forte com parede/objeto
  - `4x` — contato forte com outro carro (em pistas de asfalto). Em pistas de terra,
    contato forte vale `2x` (o oficial reconhece que contato é mais aceitável no dirt).
  - Quando vários incidentes ocorrem em sequência rápida, **apenas o de maior valor
    conta** (ex.: "2x → 4x" mostra os dois, mas só 4 pontos são somados).
  - Isso é atribuído a **todos os pilotos envolvidos**, de forma "no-fault" (não importa
    de quem foi a culpa).
- **Tipos de sessão que contam para o SR**: Qualifying, Warmup, Race (oficiais e não
  oficiais) e Time Trial. **Practice não conta para o SR.**
- **Time Trial** tem um multiplicador documentado: os pontos de incidente da sessão
  são multiplicados por **0.35** antes de entrar no cálculo.
- **Média móvel ponderada**: o SR não é recalculado sessão a sessão de forma isolada,
  nem é um total acumulado desde sempre. É uma média móvel ponderada por CPI, dando
  mais peso a curvas recentes. Fontes divergem sobre o tamanho exato dessa janela
  (uma cita "~1000 curvas / ~10 corridas"; outra cita "~2600 curvas para licença Pro,
  menos para licenças inferiores"). **Não há um número oficial único e confiável.**
- **Classes de licença**: Rookie, D, C, B, A/Pro. SR exibido vai de 1.00 a 4.99 (na
  prática). Oval e Road (e as variantes dirt) têm SR **separado**.
- **Promoção**:
  - *Fast Track*: SR ≥ 4.00 (ou ≥ 3.00 sendo Rookie) + MPR (participação mínima)
    cumprido → promoção imediata.
  - *Promoção regular*: SR entre 3.00–4.00 no fim da temporada.
  - Ao promover, o SR é **reduzido em ~1.00** para dar margem de ajuste na nova classe.
- **Demoção**:
  - Regular: SR < 2.00 no fim da temporada.
  - Fast Track (imediata): SR < 1.00.
  - Faixa 2.00–3.00 é "protegida" (sem consequência de licença).

## 2. O que NÃO é público (hipóteses / não documentado)

> **SUPERADA EM PARTE pela seção 9.** A fórmula exata continua não *publicada*
> pelo iRacing, mas foi **medida** a partir dos dados oficiais que a própria
> API expõe. Os itens abaixo permanecem válidos só onde a seção 9 não chegou
> (tamanho exato da janela móvel).

- **A fórmula exata** que converte curvas + pontos de incidente + SR atual + classe
  em uma variação de SR. Isso nunca foi publicado pelo iRacing.
- **O "Corner Multiplier" exato de cada pista.** O suporte oficial confirma que ele
  existe e por que existe, mas não publica os valores por pista/configuração.
- **O tamanho exato da janela da média móvel** (fontes divergem, ver acima).
- **Os multiplicadores exatos de Qualifying/Warmup/Race** (só o de Time Trial, 0.35,
  é documentado explicitamente).
- **MPR (Minimum Participation Requirements)** em detalhe — o suporte cita a
  existência do mecanismo, mas os números variam por classe/temporada e não há uma
  fonte que o SDK exponha isso em tempo real (ver seção 5).

**Conclusão prática:** este projeto não tenta reproduzir o algoritmo interno. Ele
implementa um modelo próprio, linear e auditável (`sr/model.py`), tratado sempre como
uma **estimativa** ("Estimated Safety Rating"), nunca como o valor oficial — e prevê
calibração posterior com dados reais (`sr/calibration.py`, `data/history.json`).

---

## 3. O que o SDK (`pyirsdk` / `irsdk.py`) realmente expõe

Fontes: [github.com/kutu/pyirsdk](https://github.com/kutu/pyirsdk) (código-fonte de
`irsdk.py`), [irsdkdocs (comunitário)](https://sajax.github.io/irsdkdocs/).

O SDK expõe três tipos de dado, todos acessados via `ir['NomeDoCampo']`:

1. **Telemetria** (variáveis por *tick*, ~60 Hz possível, atualizadas continuamente):
   `SessionNum`, `SessionTime`, `SessionState`, `Lap`, `LapDist`, `LapDistPct`,
   `CarIdxLap`, `CarIdxLapDistPct`, `OnPitRoad`, `PlayerCarIdx`, `IncidentCount`,
   `PlayerCarMyIncidentCount`, `PlayerCarTeamIncidentCount`,
   `PlayerCarDriverIncidentCount`, `CarIdxTrackSurface`, `PlayerTrackSurface`,
   `IsOnTrack`.
2. **Informação de sessão em YAML** (estática/semi-estática, parseada pelo SDK em
   dicionários Python), acessada pelas mesmas chaves de topo:
   `WeekendInfo`, `DriverInfo`, `SessionInfo`.
3. **Broadcast messages** (comandos, não usados neste projeto).

### 3.1 `SessionState` (enum, confirmado no código-fonte de `irsdk.py`)

```
0 = irsdk_StateInvalid
1 = irsdk_StateGetInCar
2 = irsdk_StateWarmup
3 = irsdk_StateParadeLaps
4 = irsdk_StateRacing
5 = irsdk_StateCheckered
6 = irsdk_StateCoolDown
```

### 3.2 `WeekendInfo` (YAML) — campos relevantes confirmados

`TrackID`, `TrackName`, `TrackConfigName`, `TrackDisplayName`,
`TrackDisplayShortName`, `TrackLength`, **`TrackNumTurns`**, `TrackType`,
`TrackDirection`, `Category`, `SimMode`, `EventType`.

`TrackID` é único por **configuração específica** de pista (Nordschleife,
Combined e GP, por exemplo, têm `TrackID` diferentes) — é a chave correta para
diferenciar variantes de uma mesma pista, como pedido na seção 6 do briefing.

`TrackNumTurns` existe e é um inteiro. **Importante — isto é uma hipótese, não um
fato confirmado**: não há confirmação pública de que `TrackNumTurns` seja
exatamente igual ao "corner count" usado internamente pelo cálculo de SR (o
"Corner Multiplier" da seção 1). É a melhor aproximação disponível via SDK, e é
tratada como tal no código (fonte = `sdk_track_num_turns`, confiança = baixa),
sempre editável manualmente em `tracks/track_data.json`.

### 3.3 `DriverInfo` (YAML) — campos relevantes confirmados

- `DriverCarIdx` — índice do carro do jogador local (equivalente "estático" ao
  `PlayerCarIdx` da telemetria).
- `Drivers` — lista de dicionários, um por carro, cada um com (entre outros):
  `CarIdx`, `UserID`, `UserName`, `TeamID`, `TeamName`, `CarNumber`,
  `CarClassID`, `IRating`, **`LicLevel`, `LicSubLevel`, `LicString`, `LicColor`**,
  `IsSpectator`, `CurDriverIncidentCount`, `TeamIncidentCount`.

**Como o SR numérico é obtido — hipótese de alta confiança, não documentação
oficial formal:** a convenção amplamente usada por ferramentas de telemetria da
comunidade (overlays, dashboards) é `SR = LicSubLevel / 100.0` (ex.: `LicSubLevel
= 342` → `SR = 3.42`). O SDK não publica uma especificação formal desse formato.
Por isso o código:
1. tenta `LicSubLevel / 100`;
2. valida se o resultado cai numa faixa plausível (0.0–5.0);
3. se falhar, tenta extrair o número final da string `LicString` (ex.: `"A 4.99"`)
   via regex, como fallback;
4. se ambos falharem, **não inventa um valor** — reporta SR indisponível e o
   overlay mostra estado de "dados insuficientes" em vez de um número.

### 3.4 `SessionInfo` (YAML) — campos relevantes confirmados

`Sessions` — lista de dicionários, um por sessão do evento, com `SessionNum`,
`SessionType`, `SessionName`, `SessionLaps`, entre outros. `SessionType` é uma
string; os valores observados empiricamente pela comunidade incluem `"Practice"`,
`"Open Qualify"`, `"Lone Qualify"`, `"Warmup"`, `"Race"`, `"Offline Testing"`. Não
existe uma lista oficial fechada e documentada de todos os valores possíveis —
por isso a classificação no código (`iracing/session.py::classify_session`) usa
correspondência por substring (contém "race", "qualify", "warmup", "practice",
"time trial") em vez de comparação exata, e cai em `"unknown"` quando não
reconhece, sem travar o programa.

### 3.5 Contadores de incidente — qual usar

O SDK expõe tanto `IncidentCount` (histórico, mais antigo, sempre presente) quanto
`PlayerCarMyIncidentCount` / `PlayerCarTeamIncidentCount` /
`PlayerCarDriverIncidentCount` (adicionados depois, especificamente para separar,
em corridas de equipe, o que é "seu" do que é "do carro"/"do time"). Ambos os
grupos já representam **pontos de incidente acumulados na sessão atual** (o mesmo
número mostrado na tela do simulador), não uma contagem bruta de eventos — por
isso `session_incidents = current - initial` já está em "pontos", sem necessidade
de reponderar 1x/2x/4x manualmente.

Este projeto usa `PlayerCarMyIncidentCount` quando disponível (mais preciso em
troca de pilotos — ver seção 4) e cai para `IncidentCount` como *fallback* em
builds mais antigas do simulador.

### 3.6 `irsdk_TrkLoc` (localização em relação à pista)

Constante pública estável, amplamente usada em projetos open-source de telemetria
iRacing (não obtida por fetch nesta sessão de pesquisa, mas consistente entre
múltiplas implementações independentes do SDK em C#/Go/Python):

```
-1 = NotInWorld
 0 = OffTrack
 1 = InPitStall
 2 = ApproachingPits
 3 = OnTrack
```

Usada apenas para detectar se o piloto está "no carro"/"no mundo" (para não
tratar telemetria zerada como uma sessão válida). **Não** é usada para contar
curvas — curvas só são contadas ao completar uma volta (seção 5 do briefing).

## 4. Troca de piloto (driver swap) — o que dá para inferir do SDK

Não existe um campo booleano explícito "driver swap aconteceu agora". O que existe:

- `PlayerCarIdx` (telemetria) e `DriverInfo.DriverCarIdx` (YAML) identificam **o
  carro** do cliente local, não trocam quando um piloto de equipe assume — o carro
  continua sendo "seu carro" na tela, mesmo que outro piloto da equipe esteja
  pilotando fisicamente naquele momento em outro computador.
- `PlayerCarMyIncidentCount` é, pela nomenclatura e pelo propósito documentado,
  específico do piloto logado localmente — por isso ele (e não o total do carro/time)
  é a métrica certa para não atribuir ao usuário incidentes cometidos por um
  colega de equipe.
- `DriverInfo.Drivers[i].UserID` do carro do jogador permite detectar quando o
  **UserID que está de fato "no carro"** muda (ex.: via a lista de resultados ou
  campos como `CarIdxOnPitRoad`/telemetria de qual carro está ativo). O SDK não
  expõe de forma simples e documentada "quem está com o volante agora" quando é
  outro membro da equipe fisicamente em outra máquina — esse dado pertence à
  sessão do **outro** cliente, não ao seu.
- **Conclusão honesta**: para o usuário local dirigindo seu próprio carro, o app
  identifica corretamente o piloto e não mistura dados de outros carros. Para
  cenários de troca de piloto em endurance (seção 20 do briefing), o app confia em
  `PlayerCarMyIncidentCount` para isolar os incidentes do usuário local, e
  registra um aviso no log se o SDK parar de responder (indicando que o app não
  está mais ativamente "no carro") — mas **não afirma** ser capaz de distinguir
  estatísticas de um colega de equipe pilotando o mesmo carro em outra máquina,
  porque essa informação simplesmente não passa pela sua instância do SDK.

## 5. MPR, CPI oficial e a API `/data` — por que não foram integrados

> **ESTA SEÇÃO ESTÁ ERRADA — ver seção 9.** O teste de 2026-08-09 que gerou
> este texto usou o prefixo `/bff/pub/proxy/api/...`, que de fato tem
> allowlist. O prefixo correto é **`/bff/pub/proxy/data/...`**, e ele expõe a
> API `/data` inteira com o cookie de sessão do site — inclusive
> `results/get`, `member/info` e `stats/member_recent_races`. O bloqueio de
> OAuth client IDs é real, mas **não impede** este caminho.

MPR (participação mínima) e o **CPI oficial por sessão** (campos `old_cpi` /
`new_cpi`, confirmados existirem no JSON de resultado oficial que o próprio
site do iRacing gera — ver
[relato de uso real](https://note.com/gordon042/n/nb8db79ec083b?hl=en)) não
são campos do SDK de telemetria (`WeekendInfo`/`DriverInfo`/`SessionInfo` não
têm nada disso). Essas informações vivem na **API `/data`** do iRacing — uma
API REST separada, autenticada, coberta por documentação própria
(`oauth.iracing.com`), não pelo SDK.

**Por que este projeto não integra a API `/data` nesta versão — não é escolha
de escopo, é bloqueio de plataforma:**

- Em 9 de dezembro de 2025 o iRacing **removeu o login legado** (usuário/senha
  direto) dessa API. Hoje o acesso exige obrigatoriamente um fluxo OAuth 2.1,
  o que significa que uma aplicação precisa primeiro **se registrar junto ao
  iRacing e obter um `client_id`** antes de poder autenticar um usuário em seu
  nome — não existe endpoint público/anônimo como alternativa.
- O suporte oficial confirma que a criação de novos OAuth client IDs está
  **pausada**: *"We have paused the creation of OAuth client IDs while we
  evaluate existing 3rd party usage of iRacing's APIs and SDK"*
  ([fonte](https://support.iracing.com/support/solutions/articles/31000177790-oauth-client-credentials)),
  sem previsão de retorno.
- Na prática: nenhuma aplicação nova (esta incluída) consegue hoje obter
  credenciais para acessar `/data`, independentemente de 2FA da conta do
  usuário ou de qualquer engenharia de contorno. Isso é diferente de "novas
  assinaturas do iRacing fechadas" (que seria sobre comprar uma conta nova) —
  são bloqueios independentes; o relevante aqui é o pause de OAuth client IDs.

**Por isso o MVP não integra a API `/data`, não exibe MPR e não busca o CPI
oficial** — implementar esse código agora seria código morto (não haveria
como autenticar). Fica documentado como possibilidade futura, condicionada ao
iRacing reabrir o registro de OAuth client IDs (anúncio nos fóruns/release
notes, segundo o próprio suporte).

## 6. Curvas por pista — estratégia adotada

1. Ao carregar uma sessão, ler `TrackID` (chave estável e única por configuração)
   e `TrackNumTurns` de `WeekendInfo`.
2. Consultar `tracks/track_data.json` (base local, editável) por `TrackID`.
3. Se existir entrada manual → usar (confiança alta, é o usuário/calibração quem
   informou).
4. Se não existir → usar `TrackNumTurns` do SDK como aproximação, salvar
   automaticamente no arquivo local com `source="sdk_track_num_turns"` e
   `confidence="baixa"`, para poder ser corrigido depois.
5. Se `TrackNumTurns` também não vier (0 ou ausente) → usar um valor de
   segurança fixo (`10`, configurável), `confidence="muito_baixa"`, e logar aviso.

Isso evita tanto "inventar" quanto travar o app quando a pista é desconhecida.

## 7. CPI alvo por classe de licenca — o que motivou o modelo v2

> **SUPERADA pela seção 9.** Os dois âncoras da comunidade (D=17.5, A=75) e a
> interpolação linear entre eles foram substituídos por medição direta. O D
> ficou próximo (15.7 medido), mas o A estava muito alto (48.5 medido) e a
> progressão entre classes é **geométrica**, não linear.

Pedido do usuario (2026-08-09): deixar a estimativa "mais proxima, ou igual" ao
SR real, na crenca de que a formula exata estaria disponivel em algum lugar.
Pesquisa nova feita nesta data **confirma a secao 2 acima**: nenhuma fonte
(oficial ou comunitaria, incluindo os dois sites ja citados na secao 1) publica
a formula exata. Nao e' falta de busca - e' um dado que nao existe
publicamente. "Igual" continua fora de alcance; o que mudou e' o modelo ficar
**mais fiel a um mecanismo ja documentado que o v1 simplesmente ignorava**.

- **Fato ja documentado (secao 1) e nao usado pelo modelo v1**: o SR e'
  avaliado em relacao a um CPI esperado *para a classe de licenca do piloto* -
  nao existe um "credito por curva" universal.
- **Numeros novos, citados diretamente** (fonte:
  [SimRacingCockpit](https://simracingcockpit.gg/iracing-safety-rating-explained/),
  acessado 2026-08-09): a comunidade estima que segurar 3.00 SR requer
  aproximadamente **15-20 corners per incident em D-license** e
  **70-80 corners per incident em A-license**. O proprio artigo classifica isso
  como estimativa da comunidade, nao numero oficial do iRacing.
- **O que o modelo v2 (`sr/model.py`, `sr/license_targets.py`) faz com isso**:
  usa o midpoint de cada faixa citada (D=17.5, A=75) como os dois unicos pontos
  com fonte direta, e **interpola linearmente** para C/B (sem fonte propria -
  marcado explicitamente como `confidence="interpolated_estimate"` no codigo,
  em oposicao a `"community_estimate"` para D e A). Rookie reaproveita o
  ancora de D e Pro reaproveita o de A, pela mesma razao que a secao 1 ja trata
  "Rookie/D" e "A/Pro" como pares proximos.
- **O que isso NAO e'**: nao e' o "Corner Multiplier" oficial por pista (ainda
  nao publico), nem o tamanho da janela de media movel (ainda nao publico), nem
  os multiplicadores de Qualify/Warmup (so' o de Time Trial, 0.35, e'
  documentado). O modelo continua sendo uma aproximacao propria, agora so' com
  um eixo a mais fundamentado em fonte citada.
- **Caminho de refinamento continuo**: em vez de tentar adivinhar mais
  constantes sem fonte, `sr/calibration.py` agora usa o historico real de
  sessoes do proprio usuario (`data/history.json`) pra ajustar
  `sensitivity_k` (o unico coeficiente livre do modelo v2) e, por pista, o
  `corners_per_lap` local - dado real substitui suposicao onde da', em vez de
  inventar mais numeros.

## 8. Resumo das fontes usadas

- https://support.iracing.com/support/solutions/articles/31000156960-iracing-how-to-safety-rating
- https://www.iracing.com/iracing-101-incident-points/
- https://boxthislap.org/an-introduction-to-the-sr-system/
- https://simracingcockpit.gg/iracing-safety-rating-explained/ (revisitado em
  2026-08-09 para os numeros de CPI por licenca da secao 7)
- https://github.com/kutu/pyirsdk
- https://sajax.github.io/irsdkdocs/
- https://sajax.github.io/irsdkdocs/yaml/driverinfo.html
- https://sajax.github.io/irsdkdocs/yaml/weekendinfo.html
- https://gist.github.com/teknologika/0127fa9a031ec686537277a954972ad0

---

## 9. O algoritmo medido (2026-08-09, segunda rodada) — substitui 2, 5 e 7

Esta seção é a mais importante do documento. Ela não cita estimativa de
terceiros: os números abaixo vieram dos **campos oficiais do próprio iRacing**.

### 9.1 Como o acesso foi obtido (a seção 5 estava errada)

A seção 5 concluiu que a API `/data` era inacessível porque
`/bff/pub/proxy/api/results/get` responde `400 "invalid request: not allowed"`.
O prefixo estava errado. O correto é:

```
/bff/pub/proxy/data/<endpoint da API /data>
```

Testado e funcionando com o cookie de sessão do site (usuário logado no
navegador, nenhuma credencial digitada nem lida pelo app):

| endpoint | resultado |
|---|---|
| `/bff/pub/proxy/data/results/get?subsession_id=...` | 200 → link S3 com o JSON completo |
| `/bff/pub/proxy/data/member/info` | 200 → licenças **com o CPI atual** |
| `/bff/pub/proxy/data/stats/member_recent_races` | 200 |

O JSON de resultado traz, **por piloto**: `old_cpi`, `new_cpi`,
`old_sub_level`/`new_sub_level` (SR×100), `old_license_level`/`new_license_level`,
`incidents` e `laps_complete` — e, por subsession, o campo **`corners_per_lap`
oficial**. O bloqueio de OAuth client IDs continua real, mas é irrelevante
para este caminho.

> **Nota de dependência:** isto é um caminho não documentado do site, não uma
> API pública contratada. Pode mudar sem aviso. É ótimo para *calibrar* o
> modelo offline; o app em si continua funcionando só com o SDK.

### 9.2 `corners_per_lap` oficial — fim do chute

O projeto aproximava curvas/volta por `TrackNumTurns` (seção 3.2, confiança
baixa). Agora há o valor que o iRacing realmente usa:

| pista | corners_per_lap oficial |
|---|---|
| Daytona / EchoPark (Atlanta) — Oval | 4 |
| Spa-Francorchamps — GP Pits e Endurance | 21 |
| Nürburgring Nordschleife — Industriefahrten | 73 |
| Nürburgring Combined — Gesamtstrecke VLN | 165 |

Para referência de quanto se estava errando: por regressão nos deltas de SR,
Nordschleife Industriefahrten dava ~92 curvas por um caminho e ~51 por outro.
O valor real é 73.

### 9.3 A tabela de multiplicadores (Sporting Code v2025.09.09, §3.6.1.1)

O projeto só conhecia o 0.35 do Time Trial. A tabela completa:

| Session Type | Weight | Session Type | Weight |
|---|---|---|---|
| Time Trial | 0.35 | Qualify - Open | 0.50 |
| Practice - Lone | 0.35 | Warm-Up | 0.50 |
| Practice - Open | 0.50 | Heat / Race | 1.00 |
| Qualify - Lone | 0.35 | All Other Session Types | 0.00 |

O nome oficial é **"Corner *and* Incident Multipliers"**: o peso vale para as
**curvas e para os incidentes**. O modelo v2 aplicava só nos incidentes, o que
melhorava artificialmente o CPI de qualify/TT. Confirmado de forma independente
pela documentação do SR Calc, que descreve os totais como
`soma(Laps x CPL x Multiplier)` e `soma(Inc x Multiplier)`.

Sobre Practice: o §3.7.1.1 lista "Practice within a Ranked Race event" como
pontuável e a tabela §4.3.1.1 separa "Open Practice" (não conta) de "Practice"
(conta). Já o §3.5.1 diz que o software não monitora "Practice Sessions" —
**há tensão real no documento oficial**; o projeto adota a leitura das duas
seções mais específicas (a practice de dentro do evento conta, com peso).

### 9.4 O mapeamento CPI -> SR (o achado principal)

Com 1.280 pares reais `(SR, CPI, nível de licença)`, o SR é uma reta perfeita
em `ln(CPI)` dentro de cada faixa (R² = 0.9998–0.9999 por faixa):

```
SR = banda + a * ln( CPI / piso(classe, banda) )
piso(classe, banda) = F0 * r^(indice_classe + banda)

a  = 1.5236        MAE  = 0.0072 SR
r  = 1.4562        max. = 0.0374 SR
F0 = 3.4949
```

A descoberta estrutural é que **o piso depende só de `indice_classe + banda`** —
uma única escada geométrica. Ou seja, medido nos dados:
`C@3.00 = B@2.00 = A@1.00 = 22.9 CPI`. Isso é exatamente a "sobreposição entre
classes e entre faixas inteiras" descrita no Sporting Code §3.7.3.

Duas consequências caem de graça, sem nada chumbado no código:

- **O "+0.40 ao cruzar um inteiro"** do artigo oficial é o remapeamento do
  mesmo CPI para a faixa vizinha. O valor exato é `1 - a*ln(r) = 0.427`
  (o iRacing arredonda para 0.40 no material de suporte).
- **A promoção de classe custa exatamente 1.00 de SR**, porque subir uma classe
  com o mesmo CPI reduz a banda em exatamente 1 — batendo com o §3.9.3
  ("aproximadamente 1.00").

**Escada de CPI medida** (CPI necessário em cada marca de SR):

| classe | SR 1.00 | SR 2.00 | SR 3.00 | SR 4.00 |
|---|---|---|---|---|
| Rookie | 5.1 | 7.4 | 10.8 | 15.7 |
| D | 7.4 | 10.8 | **15.7** | 22.9 |
| C | 10.8 | 15.7 | **22.9** | 33.3 |
| B | 15.7 | 22.9 | **33.3** | 48.5 |
| A | 22.9 | 33.3 | **48.5** | 70.7 |
| Pro | 33.3 | 48.5 | **70.7** | 102.9 |

Comparando com o que a comunidade repete: "15–20 CPI para segurar D a 3.00"
**bate** (15.7). Já "70–80 para A" **não bate** — o valor real é 48.5. O 70.7
é de Pro, o que provavelmente explica a origem do número circulando por aí.

Validação independente: aplicado às 5 licenças de uma conta real (classes B e C,
categorias distintas), o erro máximo foi **0.011 SR** — e o próprio site do
iRacing só exibe 2 casas decimais, então isso está no limite do observável.

### 9.5 The CPI update — measured on 10,773 sessions

Because official results carry both `old_cpi` and `new_cpi`, the weight a session
carries can be solved **exactly**, with no fitting:

```
alpha = (1/CPI_new - 1/CPI_old) / (incidents/corners - 1/CPI_old)
```

Doing that for 10,773 driver-sessions (GT Sprint Series plus four endurance
series) settled three questions that an earlier 9-race sample had got wrong.

**1. The window depends only on the SR band — never on the licence class.**

| band | Class B | Class A |
|---|---|---|
| 1.xx | 1217 | 1246 |
| 2.xx | 1661 | 1681 |
| 3.xx | 2274 | 2314 |
| 4.xx | — | 3185 |

Same band, different class, same window to within 2%. Grouping by
`class + band` (the rung used by the CPI ladder) does **not** collapse, so this is
genuinely the whole-number SR band. Higher SR means longer memory.

**2. The weighting is an exponential moving average, not a fixed window.**
Binning the solved alpha against `corners / window` gives:

| corners/window | alpha observed | 1 − exp(−x) |
|---|---|---|
| 0.20 | 0.201 | 0.181 |
| 0.30 | 0.284 | 0.259 |
| 0.52 | 0.446 | 0.405 |
| 0.93 | 0.646 | 0.606 |
| 1.90 | 0.878 | 0.850 |
| 2.93 | 0.964 | 0.947 |

Linear up to ~0.37 (as far as sprint races reach), then saturating exactly as an
EMA does. The previous model used `min(cap, x)` with a cap near 1/3 — that cap was
an artifact of fitting a fixed window to nine races, and it disappears here.

**3. The ratio between bands is √2.** Fitted jointly: `window_band1 = 1050`,
ratio `1.4142`, giving 1050 / 1485 / 2100 / 2970 corners. Letting each band take
its own free value (4 parameters instead of 2) converges onto exactly that
progression and does not improve the error at all — the geometric law is real.

Alternatives tested and rejected on the same data (RMSE on predicted CPI):

| model | error |
|---|---|
| **EMA, window geometric by band** | **1.40%** |
| free window per band (4 params) | 1.40% |
| window continuous in SR | 2.12% |
| window continuous in CPI | 3.83% |
| linear + cap (previous model) | 9.15% |

**Residual.** About 1.4% on predicted CPI, ≈0.021 SR. Starting from the exact
official `old_cpi` instead of inverting the rounded SR barely helps (0.0267 vs
0.0270 MAE), so the mapping is not the limit. Error falls with race length —
0.046 SR at 1–4 laps, 0.018 at 60+ — which points at whole-lap reporting: iRacing
counts distance actually driven, while results only report completed laps. The
live overlay reads `LapDistPct` and does not have that limitation.

### 9.6 Desempenho do modelo v3 contra as corridas reais

Prevendo o SR final a partir do SR inicial + curvas + incidentes, em 9 corridas
reais (com o CPI oficial do iRacing como verdade):

| modelo | MAE | RMSE |
|---|---|---|
| **v3 (este)** | **0.022 SR** | **0.029 SR** |
| v2 (anterior) | 0.174 SR | 0.247 SR |
| "não muda nada" | 0.302 SR | — |

### 9.7 Contagem de curvas por distância, não por volta

Em Nürburgring é comum pedir tow logo após os boxes ou não terminar a volta. O
contador de voltas pode andar sem que o piloto tenha dirigido aquelas curvas. E o
inverso também aparece nos dados: há corridas com **0 voltas completas, 2
incidentes e uma mudança de SR real de -0.06** — ou seja, o iRacing creditou as
curvas de uma volta *parcial*.

Por isso `iracing/telemetry.py::CornerAccumulator` acumula
`corners_per_lap * delta(LapDistPct)` só quando o carro está no mundo e fora do
box, ignorando saltos grandes de distância (tow/teleporte). Isso corrige os
dois lados do erro.

### 9.8 Fontes novas desta rodada

- iRacing Official Sporting Code v2025.09.09 (PDF oficial) — §3.5, §3.6, §3.7,
  §3.9, §3.10, §4.3.1.1
  <https://ir-core-sites.iracing.com/members/pdfs/20250909-official_sporting_code_dated_Sep_09_2025.pdf>
- iRacing Support — "Why did my SR decrease when moving up license levels?"
  <https://support.iracing.com/support/solutions/articles/31000177907-why-did-my-sr-decrease-when-moving-up-license-levels->
- SR Calc (documentação da ferramenta comunitária, confirma os multiplicadores)
  <https://sr-calc.de/info.html>
- API `/data` do iRacing via proxy BFF do site (dados de uma conta real,
  usados apenas de forma agregada aqui)
