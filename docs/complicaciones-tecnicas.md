# Complicaciones técnicas — Bot de Trading (paper-first)

Este documento consolida **todas** las complicaciones técnicas relevantes que enfrentó el proyecto, con su causa raíz, cómo se detectaron y cómo se resolvieron. Está pensado como guion de defensa oral: ante la pregunta *"¿qué complicaciones tuviste?"*, acá está la lista completa con el detalle suficiente para responder con criterio, no de memoria.

**Última actualización**: 2026-06-11. Complementa `docs/project-overview.md` (arquitectura y estado) y `decisiones-tecnicas.md` (**56 ADRs**). La suite del repo tiene **640** tests en CI (`pytest --collect-only`).

Cada complicación sigue la misma estructura: **Síntoma**, **Causa raíz**, **Cómo se detectó**, **Resolución**, **Estado** y **Lección**.

No reemplaza el registro de decisiones (`decisiones-tecnicas.md`): los ADR citados son la fuente de verdad versionada. Acá las complicaciones se narran como problemas vividos, no como decisiones.

## Tabla resumen

| # | Complicación | Capa | Estado |
|---|--------------|------|--------|
| 1 | IOL histórico 401 — permiso de cuenta | Datos / Auth | Resuelto |
| 2 | URL incorrecta en el script de diagnóstico | Tooling / Diagnóstico | Identificado |
| 3 | Bug de mapeo de keys en el connector IOL | Datos / Normalización | **Resuelto** (**ADR-056**, verificado 2026-06-11) |
| 4 | El fallback Byma enmascaraba el bug de IOL | Datos / Observabilidad | Mitigado |
| 5 | Crash por hueco de datos (TXAR) en `mark_to_market` | Ledger / Valuación | Resuelto |
| 6 | Mezcla de monedas USD/ARS (contaminación sistémica) | Datos / Señal | Resuelto |
| 7 | Veredicto de señal inflado por datos sucios | Medición de señal | Resuelto (revelado por #6) |
| 8 | Breadth insuficiente para medir la señal | Medición de señal | **En progreso** (universo ampliado; falta re-medición U2) |
| 9 | Paper-live CI caído (secretos, F3, feriados, LFS) | Operación / CI | **Resuelto** (verificado 2026-06-02) |
| 10 | Calendario de producción reemplazado por stub de tests | Config / Riesgo | **Resuelto** (**ADR-054**) |
| 11 | Cobertura XBUE truncada + ratio CEDEAR sin ajustar | Datos / Valuación sim | **Resuelto**: ratio CEDEAR ajustado; truncamiento XBUE era síntoma de #3 (**ADR-056**) |
| 12 | Fallback Byma no disparaba ante respuesta vacía de IOL (CEDEARs en cero) | Datos / Connector | **Resuelto** (**ADR-056**, verificado 2026-06-11) |

Hilo conductor: varias de estas complicaciones estaban **encadenadas** — una tapaba a la otra. El 401 de IOL (#1) ocultaba el bug de mapeo (#3), que a su vez quedaba enmascarado por el fallback silencioso a Byma (#4). Al arreglar el mapeo (#3, **ADR-056**) apareció un **segundo** bug de fallback (#12): IOL devuelve vacío para varios símbolos y el connector no caía a Byma, así que el "corte XBUE 2026-06-02" (#11) resultó ser otro síntoma de #3, no una limitación real. La mezcla de monedas (#6) inflaba un veredicto de señal (#7) que, al limpiarse, dejó al descubierto el problema real de fondo: falta de amplitud del universo (#8). En operación, la caída del CI paper-live (#9) mezcló secretos ausentes, F3 y feriados sin barras — resuelto con runbook **ADR-050**. La auditoría jun 2026 (#10, **ADR-054** / **ADR-055**) y la primera sim de cartera (#11) mostraron que calendario y datos AR incompletos distorsionan PnL tanto como bugs de código. Resolver una capa fue, repetidamente, la condición para ver la siguiente.

> **Patrón transversal — el test verde que mentía.** Tres de estas complicaciones (#3, #6, #12) **pasaron CI en verde** porque el test estaba escrito desde la misma suposición equivocada que el código. La lección, ahora convención (**ADR-057**): *un test verde no garantiza nada si el test fue escrito desde la misma suposición equivocada que el código; el test tiene que afirmar el comportamiento DESEADO, no replicar lo que el código hace.* Ver detalle al cierre del documento.

---

## 1. IOL histórico 401 — permiso de cuenta

- **Síntoma**: el `login` devolvía **200** y el `refresh` de token también **200**, pero **todos** los endpoints autenticados (`estadocuenta`, `portafolio`, `cotización`, `seriehistorica`) respondían **401 "Authorization has been denied"**. Tokens válidos, datos inaccesibles.
- **Causa raíz**: la cuenta IOL **no tenía habilitado el acceso a la API**. No era un bug de código ni faltaban credenciales: era un permiso a nivel de cuenta del broker. El flujo OAuth funcionaba; lo que faltaba era el permiso de datos.
- **Cómo se detectó**: con `diagnose_iol_auth.py` y un test directo contra varios endpoints reales. El patrón "login OK + refresh OK + 401 en todo lo autenticado" descartó credenciales/expiración y apuntó a permisos.
- **Resolución**: el usuario activó los permisos de API de la cuenta IOL. Tras configurar `IOL_USER` / `IOL_PASS` en GitHub Secrets, el workflow paper-live volvió a verde (`workflow_dispatch` 2026-06-02; **ADR-050**).
- **Estado**: **Resuelto** (permisos de cuenta + secretos en CI).
- **Lección**: un `login` exitoso **no implica** permisos de datos. Hay que probar **endpoints reales** (no solo el login) para distinguir "autenticación OK" de "autorización OK". Las variables de entorno locales **no** alimentan GitHub Actions.

---

## 2. URL incorrecta en el script de diagnóstico

- **Síntoma**: el paso de portafolio del script de diagnóstico devolvía **404** en vez de un 401/200 informativo, lo que **invalidaba el discriminador** auth-vs-permiso (no se podía saber si el problema era de token o de ruta).
- **Causa raíz**: el script pegaba a `/api/micuenta/miportafolio`, una ruta que **no existe** en la API de IOL. Un 404 de ruteo no dice nada sobre permisos.
- **Cómo se detectó**: el 404 venía con un mensaje de **ruteo** (no de autorización), distinto del 401 esperado. Esa diferencia delató que el endpoint estaba mal.
- **Resolución**: usar los endpoints reales: `/api/v2/estadocuenta` y `/api/v2/portafolio/argentina`.
- **Estado**: **Identificado** — `scripts/diagnose_iol_auth.py` aún apunta a `/api/micuenta/miportafolio` (404); el discriminador auth-vs-permiso sigue degradado hasta corregir el script.
- **Lección**: verificar las **URLs contra la API real** antes de usar el resultado como diagnóstico. Una herramienta de diagnóstico con una URL mala miente sobre la causa.

---

## 3. Bug de mapeo de keys en el connector IOL

- **Síntoma**: con los permisos ya arreglados (ver #1), IOL respondía **200 con datos válidos**, pero el connector devolvía `history_count = 0`: no entraba ninguna fila.
- **Causa raíz**: desajuste de **nombres de campos** entre la respuesta de IOL y lo que espera el normalizador. `data/connectors/ar_connector.py::_normalize_iol` exigía las keys `fecha` y `volumen`, mientras el endpoint `seriehistorica` real devuelve `fechaHora` y `volumenNominal`. El mismatch lanzaba `DataError "Missing keys"` por cada barra → el connector retornaba `[]` para **todos** los símbolos AR.
- **Cómo se detectó**: logs del run paper-live #27 (2026-06-11) con `data_error: Missing keys in IOL response item: {'volumen', 'fecha'}` en todos los símbolos AR. Que faltaran **solo** esas dos (y no `apertura`/`maximo`/etc.) delató que IOL las manda con **otro nombre**, no que la respuesta estuviera rota.
- **Resolución** (**ADR-056**, commit `7e9477a`): `_normalize_iol` tolera **alias por campo** (primer nombre presente gana): `fechaHora|fecha`, `volumenNominal|volumen`, `ultimoPrecio|cierre`, etc. El volumen usa solo `volumenNominal|volumen` (nominales), **nunca** `montoOperado` ($), para no corromper el notional del ranking. El `DataError` ahora **lista las keys recibidas** → un futuro cambio de IOL es autodiagnosticable de un vistazo, no un "falta volumen" opaco.
- **Verificación**: run `workflow_dispatch` 2026-06-11 → **40/41** símbolos con `source=iol`; AR-nativos con `rows_by_source={byma:0, iol:3}` (datos 100% de IOL). El "corte XBUE 2026-06-02" era consecuencia de este bug y desapareció (AR-nativos frescos al 2026-06-10).
- **Estado**: **Resuelto**.
- **Lección**: el test de este parseo **usaba las keys equivocadas** (`fecha`/`volumen`) — las mismas que asumía el código —, así que pasaba en verde mientras producción no traía una fila. Un test que comparte la suposición del código no prueba nada (ver patrón transversal y **ADR-057**). Y un error de datos debe **volcar lo que recibió**: un mensaje opaco esconde la causa.

---

## 4. El fallback Byma enmascaraba el bug de IOL

- **Síntoma**: aparentemente "todo funcionaba" — la data AR se cargaba sin errores. Pero IOL reportaba `history_count = 0` en silencio y **toda** la data AR venía en realidad por Byma, sin que nadie notara que IOL **nunca** aportaba una fila.
- **Causa raíz**: un **fallback silencioso**. Ante el fallo de IOL (el bug #3), Byma rellenaba sin dejar señal visible de que la fuente primaria había fallado. El sistema "andaba", pero por la fuente equivocada.
- **Cómo se detectó**: investigando el caso de IOL 200 con 0 filas (#3) — al rastrear de dónde salían realmente las barras AR, se vio que **siempre** eran de Byma.
- **Resolución**: observabilidad de fuente. La tabla `fetch_log` y la atribución de fuente (**ADR-049**) hacen visible qué fuente aportó cada barra (`source` / `effective_source`, `rows_by_source`, `partial_fallback`), de modo que un IOL en 0 ya no pasa desapercibido.
- **Estado**: **Mitigado** por observabilidad (el bug subyacente #3 ya está **resuelto** en **ADR-056**; la trazabilidad fue, además, lo que permitió verificar el fix —`source=iol`— y descubrir el segundo bug #12).
- **Lección**: los **fallbacks silenciosos esconden fallas**. Un fallback sin trazabilidad de fuente convierte un componente roto en un componente "que parece andar". Hace falta saber **siempre** de qué fuente vino el dato — y esa misma trazabilidad es la que después confirma que el arreglo funcionó.

---

## 5. Crash por hueco de datos (TXAR) en `mark_to_market`

- **Síntoma**: `PortfolioLedger.mark_to_market` lanzaba `ValueError: missing close price` y **abortaba la corrida completa** de validación (`run_validation_wf`) cuando una posición abierta no tenía barra ese día (caso testigo: `TXAR`).
- **Causa raíz**: la valuación intentaba marcar **todas** las posiciones a precio de cierre sin **manejar huecos**. Un solo símbolo sin barra ese día (feriado, ilíquido, hueco de feed) tiraba abajo toda la valuación.
- **Cómo se detectó**: la corrida de validación crasheaba con el `ValueError` apenas un símbolo abierto no tenía cierre ese día.
- **Resolución**: **carry-forward** del último close conocido (o `avg_cost` si nunca se vio precio), marcando la valuación como `stale` (**ADR-051**). El snapshot expone `stale_marks` y un flag `stale` por posición, así el hueco queda valuado pero **auditado**.
- **Estado**: **Resuelto**.
- **Lección**: un **dato faltante no es un dato cero**, y tampoco debería ser un crash. El harness de evaluación tiene que **sobrevivir a datos sucios** (huecos, feriados, ilíquidos) sin abortar; un hueco se arrastra y se marca, no se inventa ni se rompe.

---

## 6. Mezcla de monedas USD/ARS (contaminación sistémica)

- **Síntoma**: retornos **físicamente imposibles** en símbolos dual-listed — el caso testigo fue **KO con "+30000%"** (22519 ARS / 74 USD − 1). El problema contaminaba a la vez el sim/KPIs **y** la medición de señal.
- **Causa raíz**: los lectores de `ohlcv` reconstruían series por símbolo con `SELECT ... WHERE symbol = ? AND ts BETWEEN ...` **sin filtrar venue**. Para los **13 símbolos dual-listed** (AAPL, GGAL, IWM, JNJ, JPM, KO, MELI, MSFT, PG, QQQ, SPY, WMT, XOM), eso colapsaba **dos monedas** —USD (XNYS) y ARS (XBUE)— en una sola serie con semántica *last-write-wins* por timestamp. El retorno entre un cierre USD y uno ARS del mismo ticker es ruido sistémico, no un dato. **No era** un split mal aplicado ni un error de escala (ambas hipótesis se descartaron con evidencia): era pura **falta de filtro de venue**.
- **Cómo se detectó**: el retorno absurdo de KO ("+30000%") disparó la investigación. Se formularon hipótesis (split, escala) y se descartaron contra la data: el salto coincidía exactamente con el ratio del tipo de cambio implícito, no con un factor de split.
- **Resolución**: `data/venue_policy.py` como **fuente única** de la relación market tag ↔ venue ↔ moneda, y filtro por venue en los **tres** lectores afectados (`reporting/signal_ic.py`, `scripts/run_short_term_pre_gate.py`, `validation/stages/short_pre_gate.py`) (**ADR-052**). Regla dura: el venue se fija **por serie**, no día por día; si falta la barra del venue correcto un día, ese día se **omite** (nunca se rellena con la otra moneda, porque eso recrearía el bug).
- **Estado**: **Resuelto**.
- **Lección**: **verificar las hipótesis con evidencia** antes de "arreglar". Acá los datos corrigieron **dos** suposiciones (no era split, no era escala). Y a nivel diseño: un dato en otra moneda **no es el mismo dato** — mezclar USD y ARS no es ruido tolerable, es corrupción sistémica.

---

## 7. Veredicto de señal inflado por datos sucios

- **Síntoma**: el IC (information coefficient) de la señal a horizonte h=1 daba **0.146** — un edge aparentemente bueno. Al limpiar los datos cayó a **0.087**: **~40 % del "edge" era artificial**.
- **Causa raíz**: la misma contaminación de monedas de #6. El salto artificial USD↔ARS de los dual-listed se colaba como momentum y **inflaba el IC**, sin que ningún test lo detectara (los unit tests usaban un único venue por símbolo, así que la mezcla nunca aparecía en pruebas).
- **Cómo se detectó**: comparación **antes/después** del fix de venue (#6). El IC cayó de 0.146 a 0.087 al eliminar la contaminación — esa caída cuantificó cuánto del edge era falso.
- **Resolución**: la cura es el fix de #6 (`data/venue_policy.py` + filtro de venue, **ADR-052**). Medir sobre datos limpios da el número real.
- **Estado**: **Resuelto** (revelado y corregido junto con #6).
- **Lección**: **medir sobre datos sucios da conclusiones falsas y peligrosamente optimistas**. Un edge inflado es peor que ningún edge: invita a arriesgar capital sobre una ilusión. Limpiar los datos antes de medir no es opcional.

---

## 8. Breadth insuficiente para medir la señal

- **Síntoma**: tras limpiar la contaminación de monedas (#6), la **cross-section es muy fina**: mediana de **~1 símbolo/día**, y solo **89 de 278 días** alcanzan ≥5 nombres. No hay suficientes nombres por día como para rankear un cross-section.
- **Causa raíz**: el universo activo, una vez filtrado correctamente por venue y liquidez, deja muy pocos símbolos comparables el mismo día. Las whitelists además estaban **concentradas por sector** (bancos + energía en Merval, tech en CEDEARs), reduciendo aún más la diversidad efectiva de la cross-section.
- **Cómo se detectó**: al medir la señal sobre datos limpios (post-#6), las métricas de cobertura mostraron la mediana de ~1 símbolo/día y el conteo de 89/278 días con ≥5 nombres. El veredicto quedó **inconcluso por falta de amplitud**, no por falta de señal.
- **Resolución**: **ampliar el universo** en +10 símbolos diversificados por industria (**ADR-053**) y medir sobre la **cross-section completa pre-filtro (U2)**. La ampliación busca que haya suficientes nombres descorrelacionados por día para que un ranking tenga sentido.

  Símbolos agregados (datos 2025-03-20 → 2026-06-02):

  | Mercado | Símbolos |
  |---------|----------|
  | Merval (`AR`, XBUE) | `CRES`, `TECO2`, `LOMA`, `MIRG`, `IRSA` |
  | CEDEARs (señal USD / tag `US`) | `V`, `UNH`, `CAT`, `PEP`, `NFLX` |

  Herramientas para la re-medición (capa offline, sin ejecución):

  - `scripts/run_signal_ic_now.py` — IC, hit rate@K, quantile spread sobre `data/market.db`
  - `scripts/run_scenario.py` — escenarios what-if con overrides de `short_term_engine`
  - `reporting/data_quality_envelope.py` — confianza (`stale_marks`, `imputed_pct`)
  - `notebooks/pre_gate_diagnostic.ipynb` — cobertura OHLCV y calidad IOL vía `fetch_log`

- **Estado**: **En progreso** (universo ya ampliado en YAML y DB; **pendiente** re-correr U2 y comparar breadth vs baseline post-ADR-052).
- **Lección**: **no se puede rankear una lista de un elemento**. La **amplitud del universo es prerequisito** de la medición de señal: antes de concluir nada sobre el edge, hace falta una cross-section lo bastante ancha y diversa.

---

## 9. Paper-live CI caído (secretos, F3, feriados, LFS)

- **Síntoma**: el workflow `paper_live_daily.yml` falló de forma continua desde 2026-05-26. Cadena: secretos IOL vacíos en GitHub → sin barras del día → gap de snapshots → **F3** (`exit 2` si gap > 3 días hábiles) → conflicto en puntero LFS de `data/market.db` al hacer pull local.
- **Causa raíz**: varias causas encadenadas, no un solo bug de código: credenciales solo en el PC del operador (no en GitHub Actions), feriado AR sin barras que abortaba todo el catch-up, y merge de punteros LFS editados a mano.
- **Cómo se detectó**: logs de Actions + issue automático del workflow (**ADR-040**); runbook documentado en **ADR-050**.
- **Resolución**:
  1. Configurar `IOL_USER` / `IOL_PASS` en GitHub Secrets.
  2. `run_paper_live.py`: días del gap sin barras → warning y continuar (**ADR-050**).
  3. Recuperación F3 en tandas de ≤3 días (`workflow_dispatch` con `date` o local + push).
  4. Conflictos LFS: `git checkout --ours|--theirs data/market.db`, nunca editar `<<<<<<<` en el puntero.
  5. Backfill previo: `fetch_daily --lookback 120` si la DB quedó vieja.
- **Estado**: **Resuelto** — verificación 2026-06-02 (`workflow_dispatch` success, sin gap pendiente).
- **Lección**: operación diaria requiere **secretos en CI**, runbook F3 explícito y trato de LFS como puntero, no como texto. Un feriado sin barras no debe tumbar una semana de recuperación.

---

## 10. Calendario de produccion reemplazado por stub de tests (jun 2026)

- **Sintoma**: `config/calendars/trading_days.v1.yaml` tenia solo **4 dias** (2026-04-14..17) — suficiente para unit tests que fijan `date(2026, 4, 15)`, insuficiente para backtests/paper-live de meses. Si el archivo faltaba, `run_paper_live.py` seguia con `calendar_store=None` y asumia sesion US todos los dias.
- **Causa raiz**: el stub de tests quedo en la ruta de config de produccion; el orquestador degradaba en silencio cuando no habia YAML (**auditoria C2**).
- **Como se detecto**: auditoria tecnica jun 2026 + simulaciones de defensa oral que renombraron el YAML a `.defensa-bak` para esquivar el stub.
- **Resolucion** (**ADR-054**):
  1. Regenerar calendario completo: `python scripts/build_trading_days_yaml.py` (~1000 sesiones US + ~980 dias AR, rango 2024-2027).
  2. Mover stub a `tests/fixtures/calendars/trading_days_stub.v1.yaml`.
  3. `load_required_calendar_store()` en `run_paper_live.py` — fail-fast `exit 1` si falta; `--no-calendar` solo para tests.
  4. Golden replay: `tests/fixtures/replay_golden/` antes de persistir capital en DB (**T0.2**).
- **Estado**: **Resuelto** en codigo y docs (**ADR-054**).
- **Leccion**: nunca mezclar fixtures de tests con `config/` operativo; degradacion silenciosa de guardrails es peor que un crash.

---

## 11. Cobertura OHLCV XBUE truncada y ratio CEDEAR sin ajustar (jun 2026)

- **Sintoma**: simulaciones what-if de cartera (`run_whatif_sim.py`) con fin > **2026-06-02** muestran equity colapsado (~-48%) pese a operacion aparentemente normal; SPY CEDEAR aparece con perdida ficticia grande.
- **Causa raiz**:
  1. **Datos AR**: toda la OHLCV **XBUE** en `market.db` termina **2026-06-02** (XNYS llega a 2026-06-09). Dias posteriores sin barra AR hacen que `mark_to_market` carry-forward use precio **XNYS** (USD) para posiciones compradas en **ARS** — mezcla de monedas en valuacion.
  2. **Ratio CEDEAR**: SPY en XBUE salta de ~50325 ARS (2026-03-02) a ~18880 ARS (2026-06-01) sin corporate action registrada, mientras en USD sube — perdida aparente por cambio de ratio, no por mercado.
- **Como se detecto**: primera corrida de `run_whatif_sim.py` (500k / 1M ARS, mar-jun 2026); al acotar fin a 2026-06-02 y aplicar overlay XBUE en resumen, retornos pasan a +6–9%. El cuantil de la perdida ficticia de SPY (-30.965 ARS en 500k, -92.670 en 1M) y la paradoja "1M rinde menos que 500k" (mas SPY proporcional por lotaje) apuntaron al ratio como artefacto, no a mala estrategia.
- **Resolucion del ratio CEDEAR** (**resuelto**, commit `7c86403`):
  - `scripts/adjust_cedear_ratio.py` — back-adjust **idempotente**: OHLC ÷ factor y volumen × factor para las filas previas a la fecha ex; registra el evento en `corporate_actions` (tipo `cedear_ratio`, inerte para los motores) como marca de idempotencia y auditoria. Aplicado a SPY 1:3 (2026-05-29): 77 filas ajustadas, serie ahora continua.
  - **Guardrail** en `data/normalizer.py`: un cierre que mas que duplica o cae a menos de la mitad del cierre valido anterior se descarta con `suspect_ratio_jump`. Antes, el filtro de outliers (mediana ×10) dejaba pasar un salto de 3×; ahora un cambio de ratio no registrado frena el simbolo en vez de contaminar en silencio.
  - Verificado: escaneo de los 33 simbolos XBUE — SPY era el unico con el salto; produccion `paper_live` **nunca** tuvo posiciones SPY (cero impacto en snapshots reales).
- **Truncamiento XBUE — resuelto vía #3**: el corte en 2026-06-02 NO era una limitación del feed, era el síntoma del bug de mapeo de IOL (#3). Tras **ADR-056**, los AR-nativos llegan al 2026-06-10. `run_whatif_sim.py` puede extender su `--end` a medida que el fetch backfillea.
- **Estado**: **Resuelto** — ratio CEDEAR ajustado (datos + guardrail + test); truncamiento XBUE era #3 (**ADR-056**).
- **Leccion**: antes de interpretar PnL de sims en pesos, verificar `MAX(ts)` por venue y corporate actions; un hueco de datos AR no es "mala estrategia", es mala valuacion. Y un filtro de outliers basado en mediana **no** detecta un cambio de ratio: hace falta un control explicito de salto dia-a-dia.

---

## 12. Fallback Byma no disparaba ante respuesta vacía de IOL (CEDEARs en cero)

- **Síntoma**: tras arreglar el mapeo de keys (#3), los CEDEARs (SPY, AAPL, KO…) y algunos Merval (BMA, LOMA) **seguían sin datos recientes**: `fetch_log` mostraba `rows_by_source={byma:0, iol:0}` y su última barra XBUE quedaba en 2026-06-02.
- **Causa raíz**: `fetch_ar_ohlcv_with_trace` caía al fallback Byma **solo** cuando IOL fallaba por **red** (`result is None`). Si IOL respondía `200` con **lista vacía** (`result == []`), el connector aceptaba ese vacío como respuesta final y retornaba `[]` **sin** consultar Byma. IOL es **selectivo**: no sirve varios símbolos en `seriehistorica` y devuelve `[]` para ellos.
- **Cómo se detectó**: como yfinance (Byma) es público y no requiere credenciales, se probó directo: `SPY.BA`, `AAPL.BA`, `KO.BA`, `BMA.BA` **tenían 7 barras hasta 2026-06-11**. El dato existía; el fallback no lo traía. Que BMA (Merval) también cayera vacío descartó la hipótesis "CEDEAR vs Merval": el corte lo decide IOL símbolo por símbolo.
- **Resolución** (**ADR-056**, commit `0b55c34`): se eliminó el retorno temprano ante IOL vacío; ahora cae al fallback Byma (salvo `iol_only`, que respeta su contrato). La atribución de fuente (**ADR-049**) mantiene el fallback visible.
- **Estado**: **Resuelto**.
- **Lección**: aceptar el **vacío de la fuente primaria** como respuesta final esconde datos que la secundaria tiene — es un primo del fallback silencioso (#4), pero al revés: no es que el fallback enmascare, es que **no se activa**. Y de nuevo, **dos tests afirmaban `result == []`** ante IOL vacío: codificaban el bug como contrato. Se reescribieron para afirmar la conducta deseada ("cae a Byma"). Ver patrón transversal y **ADR-057**.

---

## Nota de workflow — `git stash` ≠ working tree

Una complicación menor, no de código sino de **flujo de trabajo**: parte del trabajo del fix de venue quedó un tiempo guardado en un `git stash` que **no aparece en el panel de Cursor** (un stash no es el working tree). Eso generó confusión sobre "dónde estaba el código", porque el editor mostraba el working tree limpio mientras los cambios vivían en el stash.

- **Lección**: `stash` ≠ working tree. Al recuperar, usar `git stash apply` (no `pop`) deja una **red de seguridad**: el stash sigue existiendo por si algo sale mal al aplicarlo.

---

## Documentos relacionados

| Documento | Rol |
|-----------|-----|
| `decisiones-tecnicas.md` | ADRs versionados (56); fuente de verdad de decisiones |
| `docs/project-overview.md` | Arquitectura, riesgo, paper-live, validación — guion de defensa oral |
| `POLICY.md` | Política operativa humana (umbrales, ramp-up, F3) |
| `README.md` | Estado actual del repo y comandos útiles |
| `CHANGELOG.md` | Cambios recientes (ADR-051–057, runbook ADR-050; fix ratio CEDEAR, lockfile, mapeo+fallback IOL) |

---

## Patrón transversal: el test verde que mentía

> *"Aprendí que un test verde no garantiza nada si el test fue escrito desde la misma suposición equivocada que el código. El test tiene que afirmar el comportamiento DESEADO, no replicar lo que el código hace."*

Esta es, quizás, la lección más valiosa del proyecto, porque se repitió **tres veces** y siempre con el mismo disfraz: CI en verde, falsa sensación de seguridad, y abajo un dato silenciosamente equivocado. Codificada como convención en **ADR-057**.

| # | El bug | Lo que afirmaba el test (suposición) | Lo que debía afirmar (comportamiento deseado) |
|---|--------|--------------------------------------|----------------------------------------------|
| **#6** | Mezcla de monedas USD/ARS | Cada fixture usaba **un solo venue** por símbolo → la mezcla nunca aparecía en pruebas; el IC inflado (0.146) se veía sano | Que una serie con barras de **dos venues** se filtre por moneda y no produzca retornos imposibles |
| **#3** | Mapeo de keys IOL | El fixture `_IOL_PAYLOAD` traía `fecha`/`volumen` — **las keys que el código asumía**, no las que devuelve la API (`fechaHora`/`volumenNominal`) | Que el parser acepte el **contrato real** de IOL (y tolere alias) |
| **#12** | Fallback ante IOL vacío | Dos tests afirmaban **`result == []`** ante IOL vacío/error — *afirmaban el bug como si fuera el contrato* | Que ante IOL vacío el connector **caiga a Byma** y traiga el dato |

**El hilo común**: en los tres, el test y el código compartían la misma creencia equivocada, así que el test no podía detectar el error — se daba la mano a sí mismo. La cura no es *más* cobertura (cobertura sobre suposiciones equivocadas es ruido), sino cambiar la **intención del assert**: del síntoma del bug ("retorna vacío") a la acción de negocio esperada ("trae el dato de la otra fuente"). Como apoyo, los errores de runtime ahora **vuelcan lo que recibieron** (p. ej. el `DataError` de IOL lista las keys), para que la realidad contradiga a la suposición de forma ruidosa, no silenciosa.

### Cómo usar este documento en defensa oral

1. Abrir con la **tabla resumen** y el hilo conductor (complicaciones encadenadas).
2. Profundizar en **#6–#8** si preguntan por calidad de datos y medición de señal (mezcla USD/ARS → IC inflado → breadth).
3. Usar **#9** y **ADR-050** si preguntan por operación diaria real (paper-live en GitHub Actions).
4. Usar **#3 + #12** (connector IOL) para mostrar diagnóstico encadenado: arreglar un bug destapó el siguiente, y la trazabilidad de fuente fue la que lo hizo visible.
5. **Cerrar con el "patrón transversal del test verde"** (arriba): es la lección de ingeniería más madura del proyecto y la que mejor demuestra criterio propio, no solo ejecución.
