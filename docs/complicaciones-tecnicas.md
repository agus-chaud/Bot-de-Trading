# Complicaciones técnicas — Bot de Trading (paper-first)

Este documento consolida **todas** las complicaciones técnicas relevantes que enfrentó el proyecto, con su causa raíz, cómo se detectaron y cómo se resolvieron. Está pensado como guion de defensa oral: ante la pregunta *"¿qué complicaciones tuviste?"*, acá está la lista completa con el detalle suficiente para responder con criterio, no de memoria.

**Última actualización**: 2026-06-09. Complementa `docs/project-overview.md` (arquitectura y estado) y `decisiones-tecnicas.md` (**53 ADRs**). La suite del repo tiene **601** tests en CI.

Cada complicación sigue la misma estructura: **Síntoma**, **Causa raíz**, **Cómo se detectó**, **Resolución**, **Estado** y **Lección**.

No reemplaza el registro de decisiones (`decisiones-tecnicas.md`): los ADR citados son la fuente de verdad versionada. Acá las complicaciones se narran como problemas vividos, no como decisiones.

## Tabla resumen

| # | Complicación | Capa | Estado |
|---|--------------|------|--------|
| 1 | IOL histórico 401 — permiso de cuenta | Datos / Auth | Resuelto |
| 2 | URL incorrecta en el script de diagnóstico | Tooling / Diagnóstico | Identificado |
| 3 | Bug de mapeo de keys en el connector IOL | Datos / Normalización | **Pendiente** (mitigado por fallback) |
| 4 | El fallback Byma enmascaraba el bug de IOL | Datos / Observabilidad | Mitigado |
| 5 | Crash por hueco de datos (TXAR) en `mark_to_market` | Ledger / Valuación | Resuelto |
| 6 | Mezcla de monedas USD/ARS (contaminación sistémica) | Datos / Señal | Resuelto |
| 7 | Veredicto de señal inflado por datos sucios | Medición de señal | Resuelto (revelado por #6) |
| 8 | Breadth insuficiente para medir la señal | Medición de señal | **En progreso** (universo ampliado; falta re-medición U2) |
| 9 | Paper-live CI caído (secretos, F3, feriados, LFS) | Operación / CI | **Resuelto** (verificado 2026-06-02) |

Hilo conductor: varias de estas complicaciones estaban **encadenadas** — una tapaba a la otra. El 401 de IOL (#1) ocultaba el bug de mapeo (#3), que a su vez quedaba enmascarado por el fallback silencioso a Byma (#4). La mezcla de monedas (#6) inflaba un veredicto de señal (#7) que, al limpiarse, dejó al descubierto el problema real de fondo: falta de amplitud del universo (#8). En operación, la caída del CI paper-live (#9) mezcló secretos ausentes, F3 y feriados sin barras — resuelto con runbook **ADR-050**. Resolver una capa fue, repetidamente, la condición para ver la siguiente.

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
- **Causa raíz**: desajuste de **nombres de campos** entre la respuesta de IOL y lo que espera el normalizador. `data/connectors/ar_connector.py::_normalize_iol` espera las keys `fecha` y `volumen`, mientras la API devuelve `fechaHora` y `volumenNominal` / `montoOperado`. El mismatch lanza `DataError`, el connector retorna `[]`, **y la rama `DataError` no cae al fallback Byma** (solo lo hace la rama de red), con lo cual IOL nunca aporta datos.
- **Cómo se detectó**: al cargar los 5 símbolos Merval nuevos, IOL devolvía **200** pero `history_count = 0`. La contradicción "200 sin filas" forzó mirar el normalizador.
- **Resolución**: **PENDIENTE**. Workaround actual: el **fallback Byma** cubre la data AR, así que el pipeline no se rompe; pero IOL sigue sin aportar.
- **Estado**: **Pendiente** (mitigado por el fallback Byma).
- **Lección**: una **segunda capa** de problema puede quedar oculta detrás de la primera. El 401 (#1) tapaba este bug: hasta que no hubo permisos, nunca se llegó a ejecutar el mapeo, así que el desajuste de keys era invisible.

---

## 4. El fallback Byma enmascaraba el bug de IOL

- **Síntoma**: aparentemente "todo funcionaba" — la data AR se cargaba sin errores. Pero IOL reportaba `history_count = 0` en silencio y **toda** la data AR venía en realidad por Byma, sin que nadie notara que IOL **nunca** aportaba una fila.
- **Causa raíz**: un **fallback silencioso**. Ante el fallo de IOL (el bug #3), Byma rellenaba sin dejar señal visible de que la fuente primaria había fallado. El sistema "andaba", pero por la fuente equivocada.
- **Cómo se detectó**: investigando el caso de IOL 200 con 0 filas (#3) — al rastrear de dónde salían realmente las barras AR, se vio que **siempre** eran de Byma.
- **Resolución**: observabilidad de fuente. La tabla `fetch_log` y la atribución de fuente (**ADR-049**) hacen visible qué fuente aportó cada barra (`source` / `effective_source`, `rows_by_source`, `partial_fallback`), de modo que un IOL en 0 ya no pasa desapercibido.
- **Estado**: **Mitigado** por observabilidad (el bug subyacente #3 sigue pendiente, pero ya no es invisible).
- **Lección**: los **fallbacks silenciosos esconden fallas**. Un fallback sin trazabilidad de fuente convierte un componente roto en un componente "que parece andar". Hace falta saber **siempre** de qué fuente vino el dato.

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
- **Estado**: **Resuelto** en codigo y docs; pendiente merge `main` → `paper-live-data` para que CI use el fix.
- **Leccion**: nunca mezclar fixtures de tests con `config/` operativo; degradacion silenciosa de guardrails es peor que un crash.

---

## Nota de workflow — `git stash` ≠ working tree

Una complicación menor, no de código sino de **flujo de trabajo**: parte del trabajo del fix de venue quedó un tiempo guardado en un `git stash` que **no aparece en el panel de Cursor** (un stash no es el working tree). Eso generó confusión sobre "dónde estaba el código", porque el editor mostraba el working tree limpio mientras los cambios vivían en el stash.

- **Lección**: `stash` ≠ working tree. Al recuperar, usar `git stash apply` (no `pop`) deja una **red de seguridad**: el stash sigue existiendo por si algo sale mal al aplicarlo.

---

## Documentos relacionados

| Documento | Rol |
|-----------|-----|
| `decisiones-tecnicas.md` | ADRs versionados (54); fuente de verdad de decisiones |
| `docs/project-overview.md` | Arquitectura, riesgo, paper-live, validación — guion de defensa oral |
| `POLICY.md` | Política operativa humana (umbrales, ramp-up, F3) |
| `README.md` | Estado actual del repo y comandos útiles |
| `CHANGELOG.md` | Cambios recientes (ADR-051–054, runbook ADR-050) |

### Cómo usar este documento en defensa oral

1. Abrir con la **tabla resumen** y el hilo conductor (complicaciones encadenadas).
2. Profundizar en **#6–#8** si preguntan por calidad de datos y medición de señal (mezcla USD/ARS → IC inflado → breadth).
3. Usar **#9** y **ADR-050** si preguntan por operación diaria real (paper-live en GitHub Actions).
4. Cerrar con **#3 pendiente** (bug IOL de mapeo) para mostrar honestidad técnica sobre deuda viva.
