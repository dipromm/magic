# MEMORIA 3

## Evolución del módulo de Ingesta y Traducción Automática (Oracle Text -> JSON-LD)

### 1. Contexto y objetivo de la iteración

Durante esta iteración se consolidó el módulo de traducción semántica de cartas de Magic: The Gathering como un proceso reproducible orientado a consola (CLI), con soporte de revisión humana y sincronización de conocimiento con ontología formal. El objetivo fue transformar texto Oracle en una representación estructurada JSON-LD utilizable por el motor de reglas, minimizando ambigüedad semántica y maximizando trazabilidad técnica.

### 2. Decisiones de arquitectura adoptadas

**Cambio de enfoque de ejecución:** se descartó temporalmente el acoplamiento a interfaz web de agente para priorizar un script autónomo CLI (`traduccion/agente_mtg_cli.py`), más simple de depurar y validar en fase de investigación.

**Ontología como autoridad de dominio:** el sistema opera con una ontología cargada en JSON (derivada de OWL), inyectada en el prompt como diccionario cerrado de constantes permitidas.

**Integración Human-in-the-Loop (HITL):** ante lagunas ontológicas, la carta entra en cuarentena y se solicita intervención del investigador para aceptar/rechazar/corregir propuestas.

**Persistencia dual de conocimiento:** las propuestas aceptadas se integran tanto en JSON como en OWL para mantener consistencia entre capa operativa (inferencia) y capa formal (modelo semántico).

### 3. Implementación técnica realizada

En `traduccion/agente_mtg_cli.py` se implementó:

- **Configuración robusta de credenciales:** validación explícita de `POLIGPT_API_KEY` y fallo temprano con instrucciones de entorno si falta.

- **Construcción dinámica del prompt de sistema** con la ontología vigente (`build_system_prompt(...)`), imponiendo salida estricta en JSON-LD y reglas de cuarentena.

- **Traducción LLM→JSON** mediante llamada a modelo (`litellm`) y parser defensivo (`_extraer_json`) para aislar el primer objeto JSON balanceado.

- **Bucle de revisión por bloques semánticos:**
  - Detección de `requires_human_review`.
  - Consolidación de propuestas (`ontology_proposal` / `missing_building_blocks`).
  - Revisión interactiva por bloque (Y/N/C).
  - Re-traducción con feedback humano cuando hay correcciones.

- **Actualización del estado de sesión** (`SessionState`) con:
  - Carga o reconstrucción del diccionario desde OWL/JSON.
  - Persistencia atómica de cambios aceptados.
  - Reconstrucción automática del prompt tras cada actualización de ontología.

- Casos de prueba iniciales en ejecución CLI con cartas de distinta complejidad (ej.: Lightning Bolt, Aether Vial, Thoughtseize, Path to Exile).

### 4. Justificación metodológica

El diseño híbrido LLM + HITL se adoptó porque el dominio de MTG contiene excepciones semánticas y mecánicas históricas que hacen inviable una cobertura perfecta en fases tempranas. Este enfoque permite:

- Avanzar en cobertura funcional sin bloquear desarrollo.
- Capturar "nuevos bloques de construcción" de forma controlada.
- Convertir la revisión manual en mejora incremental de la ontología.

### 5. Riesgos identificados y mitigación

| Riesgo | Mitigación |
|--------|-----------|
| Salida del modelo no completamente conforme al diccionario | Cuarentena y revisión humana por bloque |
| Variabilidad en formato de salida del LLM | Extracción robusta de JSON |
| Deriva semántica entre OWL y JSON | Persistencia dual y regeneración del prompt desde diccionario actualizado |

### 6. Mejora recomendada para siguiente iteración

Se propone añadir un validador determinista post-LLM (en Python) que compruebe automáticamente que cada constante usada en `resolution_blocks` pertenece a la ontología cargada. Con ello, la autoridad semántica pasaría de "prompt-only" a "prompt + verificación formal", reforzando reproducibilidad experimental y rigor académico.

### 7. Resultado de la iteración

Se obtuvo un pipeline funcional de traducción semántica con revisión humana integrada, trazabilidad de propuestas ontológicas y capacidad de evolución incremental del conocimiento formal del sistema, alineado con el objetivo del TFG de unir PLN, ontologías OWL y motor de reglas ejecutable.

---

### 8. Iteraciones de desarrollo detalladas

El desarrollo del módulo de traducción no fue lineal: siguió un proceso iterativo donde cada decisión de diseño se validó empíricamente contra el LLM y el OWL existente, revelando errores de modelado, lagunas en el prompt y desajustes entre la representación formal y la operativa. A continuación se documenta cada iteración en orden cronológico, con el error o necesidad que la motivó y la solución adoptada.

#### 8.1. Descarte de google.adk y UI: hacia un CLI autónomo

**Contexto.** El primer planteamiento del módulo de traducción se basaba en `google.adk` (Agent Development Kit), una librería de agentes conversacionales utilizada en otro proyecto de la universidad (`agente_resumenes_cursor`). La implementación inicial preveía un agente ADK con UI web (`adk web`) para interactuar con el LLM.

**Problema identificado.** Durante la fase de diseño se determinó que `google.adk` añadía una capa de abstracción innecesaria para el caso de uso. El sistema no requería memoria conversacional ni interfaz web: la traducción es una operación de transformación determinista (texto Oracle → JSON-LD) con un único turno de interacción por carta. Además, la UI de ADK impedía la integración programática con el flujo de cuarentena y HITL, que requiere control fino del bucle de ejecución.

**Decisión.** Se descartó `google.adk` y cualquier dependencia de interfaz gráfica. Se diseñó un script CLI puro (`traduccion/agente_mtg_cli.py`) que invoca `litellm.completion()` directamente, sin agente intermedio. Esto redujo las dependencias del módulo a dos paquetes (`litellm`, `rdflib`) y permitió un control total del flujo: prompt → LLM → extracción JSON → evaluación cuarentena → HITL → persistencia → re-traducción.

**Impacto.** El CLI resultante es más simple de depurar, reproducible sin infraestructura adicional y apto para integración futura en pipelines automatizados (p.ej. procesamiento por lotes de cartas).

#### 8.2. Gestión segura de credenciales

**Problema.** El proyecto de referencia (`agente_resumenes_cursor/recoleccion_sesion.py`) tenía la API key de PoliGPT hardcodeada como constante (`_DEFAULT_POLIGPT_API_KEY = "sk-..."`). Reproducir este patrón en el TFG suponía un riesgo de seguridad, especialmente al versionar el código con Git.

**Solución.** Se implementó un esquema basado en variables de entorno: el script lee `POLIGPT_API_KEY` del entorno y aborta con un mensaje de instrucciones explícito si no la encuentra. Se documentaron los comandos para cada shell (PowerShell, CMD, Bash).

**Error colateral detectado.** Durante la primera prueba, el usuario ejecutó la sintaxis de PowerShell (`$env:POLIGPT_API_KEY = "..."`) en un terminal CMD, produciendo un error `The filename, directory name, or volume label syntax is incorrect`. Se resolvió documentando ambas sintaxis en el mensaje de error del propio script.

#### 8.3. Entorno de ejecución: venv frente a Anaconda

**Contexto.** El usuario utilizaba Anaconda por costumbre académica, pero no tenía un entorno Conda configurado para este proyecto, solo un venv estándar.

**Decisión.** Se recomendó continuar con venv nativo de Python: es más ligero, no requiere instalación adicional, y las únicas dependencias del módulo (`litellm`, `rdflib`) son paquetes pip estándar sin necesidad de canales Conda. Esto simplificó la reproducibilidad del entorno.

#### 8.4. Primera implementación: ontología embedida como semilla

**Implementación inicial.** La primera versión de `agente_mtg_cli.py` contenía un diccionario Python hardcodeado (`ONTOLOGIA_BASE`) con un subconjunto mínimo de constantes (efectos, triggers, targets) como semilla de prueba. El prompt del sistema se construía serializando este diccionario a JSON e inyectándolo directamente.

**Resultado.** La IA tradujo correctamente Lightning Bolt con la semilla, validando que el flujo LLM → JSON-LD funcionaba. Sin embargo, este enfoque tenía un problema fundamental: la ontología embedida no estaba sincronizada con el OWL formal (`IA_OWL/OWL_tfg.rdf`) que el usuario ya mantenía en Protégé.

#### 8.5. Integración con OWL existente y creación de ontology_store.py

**Motivación.** El usuario ya disponía de una ontología OWL (formato RDF/XML) con taxonomía de tipos de carta, contadores, zonas, fases y keyword abilities modelados como jerarquías de clases en Protégé. El objetivo era que el OWL actuase como fuente de verdad formal y que el JSON fuera una derivación operativa automática.

**Diseño adoptado.** Se creó el módulo `traduccion/ontology_store.py` con las siguientes responsabilidades:

- Carga del grafo RDF mediante `rdflib.Graph.parse()` en formato XML.
- Exportación automática del diccionario JSON a partir de las jerarquías `rdfs:subClassOf` del OWL.
- Fusión conservadora: si el JSON existente tiene entradas que el OWL no tiene (p.ej. añadidas manualmente), se preservan mediante unión de conjuntos.
- Persistencia atómica: escritura de JSON y RDF a ficheros temporales con `os.replace()` para evitar corrupción ante interrupciones. Backup automático con timestamp antes de sobreescribir el OWL.
- Integración de propuestas aceptadas: al aceptar una propuesta del HITL, se añade la nueva constante como `owl:Class` + `rdfs:subClassOf` en el grafo y se actualiza el diccionario JSON.

**Fichero .gitignore.** Se añadió la excepción `!IA_JSON/**/*.json` al `.gitignore` (que tenía una regla genérica `*.json`) para permitir el versionado del diccionario canónico `ontologia_motor.json`.

#### 8.6. Error crítico: incompatibilidad de modelado OWL (NamedIndividual vs. subClassOf)

**Síntoma.** Tras integrar el OWL real, el sistema solo funcionaba con los datos de la semilla embedida. El diccionario exportado desde el OWL aparecía vacío: `{ "Effect": [], "Trigger_event": [], ... }`.

**Causa raíz (doble).** El error tenía dos componentes:

1. **Desajuste de nombres de clase.** El código buscaba clases como `NS.EffectType`, `NS.TriggerType`, `NS.TargetType`, pero el OWL definía `NS.Effect`, `NS.Trigger_event`, `NS.TargetType`. La convención de nombres no coincidía.

2. **Desajuste de paradigma de modelado.** El código original asumía que los términos del vocabulario serían `owl:NamedIndividual` (instancias) de las categorías padre. Sin embargo, el usuario había modelado todo en Protégé como jerarquías de clases (`owl:Class` con `rdfs:subClassOf`). Por ejemplo, `Deal_damage` no era un individuo de tipo `Effect`, sino una subclase de `Effect`.

**Solución.** Se reescribió el motor de extracción:

- `_individuals_of()` se reemplazó por `_subclasses_of()` → `_all_descendants()` (recorrido recursivo de la jerarquía `rdfs:subClassOf`).
- Se corrigieron los nombres de clase para coincidir con el OWL real.
- Se implementó `apply_accepted_proposals()` para escribir nuevas constantes como `owl:Class` + `rdfs:subClassOf` en vez de `owl:NamedIndividual`.

**Lección.** La discordancia entre el paradigma de modelado asumido por el código y el utilizado en Protégé es un error sutil pero bloqueante. Documentar explícitamente la convención de modelado OWL adoptada es esencial para la mantenibilidad.

#### 8.7. Descubrimiento dinámico de categorías OWL

**Problema.** Tras corregir el error anterior, la extracción funcionaba pero estaba limitada a un conjunto hardcodeado de categorías padre (`Effect`, `Trigger_event`, `TargetType`, etc.) definido en un diccionario `_OWL_PARENT_TO_JSON`. Si el usuario añadía nuevas categorías raíz en Protégé (p.ej. `Cost`, `Status`, `GameEntity`), el código no las descubriría.

**Solución.** Se implementó `_find_root_classes(g)`, que identifica automáticamente todas las clases raíz del namespace de la ontología. Una clase raíz es aquella que:

- Pertenece al namespace `http://www.tfg.mtg.simulacion/ontology/`.
- No es `rdfs:subClassOf` de ninguna otra clase del mismo namespace.

Con esto, `export_dictionary_from_owl()` recorre dinámicamente todas las raíces y exporta sus descendientes, sin necesidad de mantener una lista hardcodeada.

**Resultado.** El diccionario pasó de extraer 0 entradas a reflejar correctamente las 12 categorías y 85+ constantes definidas en el OWL del usuario.

#### 8.8. Refinamiento del HITL: revisión granular por bloque semántico

**Problema.** La primera implementación del HITL presentaba todas las propuestas como un bloque monolítico y ofrecía una decisión global [Y]/[N]/[C] para el conjunto completo. Esto obligaba al usuario a aceptar o rechazar todo, sin posibilidad de aceptar un efecto nuevo pero rechazar un trigger incorrecto en la misma carta.

**Solución.** Se implementó `_review_proposals_by_block()`, que itera sobre cada propuesta individualmente presentando:

- El keyword detectado, la categoría OWL destino, el tipo de bloque (`block_kind`) y la constante propuesta.
- Tres opciones por propuesta: [Y] aceptar, [N] rechazar, [C] comentar/corregir.

Las propuestas aceptadas se persisten inmediatamente en JSON y OWL. Los comentarios se acumulan y se inyectan como contexto adicional en la re-traducción. Esto permite que el ciclo HITL sea incremental: cada carta puede necesitar varios ciclos de cuarentena → revisión → re-traducción hasta que su ontología esté completa.

#### 8.9. Modelado polimórfico de condiciones de estado (COMPARISON)

**Motivación.** Al traducir Aether Vial ("creature card with mana value equal to the number of charge counters"), el LLM generaba constantes monolíticas inventadas como `ManaValueEqualsChargeCounters` o `TARGET_MANA_VALUE`. Estas constantes no son reutilizables: cada carta con una condición diferente requeriría su propia constante ad-hoc, haciendo inviable el motor de reglas.

**Decisión de diseño.** Se adoptó un modelo polimórfico para `state_conditions` basado en comparaciones descompuestas:

```json
{
  "condition_type": "COMPARISON",
  "left_operand": "ConvertedManaCost",
  "left_scope": "TARGET",
  "operator": "EQUALS",
  "right_operand": "Charge",
  "right_scope": "SOURCE"
}
```

Donde:

- `left_operand` / `right_operand` referencian constantes existentes en la ontología (como `ConvertedManaCost` de `CardAtribute` o `Charge` de `Counters`), o valores numéricos.
- `left_scope` / `right_scope` indican a qué entidad del juego se aplica el operando (`TARGET`, `SOURCE`, `CONTROLLER`, `OPPONENT`).
- `operator` define la relación (`EQUALS`, `GREATER_THAN`, etc.).

**Justificación.** Este diseño permite que el motor de Python evalúe cualquier condición como una expresión genérica sin código específico por carta. La combinatoria de condiciones posibles queda cubierta por la composición de operandos atómicos existentes en la ontología.

#### 8.10. Error: el LLM dejó de activar cuarentena tras los cambios de polimorfismo

**Síntoma.** Tras añadir la regla de polimorfismo, el LLM traducía todas las cartas sin activar cuarentena ni proponer constantes nuevas, incluso para cartas que requerían efectos no existentes en la ontología.

**Causa raíz (triple).**

1. **Operandos de ejemplo fuera de la ontología.** La regla de polimorfismo usaba como ejemplo `TARGET_MANA_VALUE` y `SOURCE_CHARGE_COUNTERS`, que no existían en la ontología. El LLM los copiaba literalmente sin verificarlos.

2. **Ausencia de validación en campos secundarios.** Las reglas de cuarentena solo obligaban a verificar `effect_type`, `trigger` y `target_type`. Campos como `activation_cost[].cost_type`, `trigger_condition.event_condition_type`, `state_conditions[].left_operand` y `state_conditions[].right_operand` no estaban sujetos a validación ontológica.

3. **NONE tratado como constante ontológica.** El valor especial `NONE` (usado cuando un campo opcional no aplica, p.ej. `trigger: NONE` para instantes) no estaba declarado como valor reservado. El LLM lo interpretaba como una constante que debía existir en la ontología y proponía añadirlo como subclase de `Trigger_event`, lo cual es semánticamente incorrecto.

**Solución (tres correcciones).**

1. Se actualizaron los ejemplos de la regla de polimorfismo para usar constantes reales de la ontología (`ConvertedManaCost`, `Charge`) en vez de nombres inventados.
2. Se añadió la **Regla de Validación Total** (regla 9 del prompt): obliga al LLM a verificar contra la ontología absolutamente toda constante usada en cualquier campo del JSON-LD, incluyendo costes, condiciones, operandos, zonas, fases y tipos de contador.
3. Se definió una **lista explícita de valores reservados** que no necesitan existir en la ontología: `NONE`, `true`, `false`, `COMPARISON`, los operadores (`EQUALS`, `GREATER_THAN`, etc.), los scopes (`TARGET`, `SOURCE`, `CONTROLLER`, `OPPONENT`) y los valores numéricos.

#### 8.11. Descubrimiento: cartas de ejemplo con cobertura completa

**Síntoma residual.** Tras todas las correcciones, el sistema parecía seguir sin activar cuarentena. Sin embargo, al analizar la ejecución real, se descubrió que Lightning Bolt y Aether Vial se traducían correctamente sin necesidad de cuarentena, porque todas sus constantes (`Deal_damage`, `Target_any`, `Upkeep_begin`, `Add_counter`, `Charge`, `Put_creature_from_hand`, `ConvertedManaCost`, `Tap`) ya existían en la ontología.

**Solución.** Se añadieron cartas de prueba con mecánicas no cubiertas por la ontología:

- **Thoughtseize:** requiere `Discard`, `Lose_life`, `Reveal_hand` (ninguno existía en `Effect`).
- **Path to Exile:** requiere `Exile`, `Search_library`, `Put_permanent_onto_battlefield_tapped`, `Shuffle`.

Con estas cartas, el sistema activó cuarentena correctamente, propuso cada constante faltante por separado con su descripción técnica genérica, y el flujo HITL [Y]/[N]/[C] funcionó según lo diseñado.

**Lección.** La selección de casos de prueba es crítica en validación de sistemas basados en LLM: cartas "demasiado simples" no ejercitan los caminos de cuarentena y dan una falsa sensación de fallo.

#### 8.12. Rediseño del esquema JSON-LD a V1.2 — Arquitectura jerárquica

**Motivación.** El esquema original utilizaba una estructura plana basada en `resolution_blocks` con campos como `target_type` y `trigger_condition` al mismo nivel. Este diseño presentaba tres problemas fundamentales:

1. **Ambigüedad semántica:** la relación entre costes, triggers y efectos era implícita. Un bloque con `cost_type: TAP` y `effect_type: DEAL_DAMAGE` no dejaba claro si el tapeo era un coste de la habilidad o un efecto secundario.
2. **Escalabilidad limitada:** cartas con múltiples habilidades independientes (como Aether Vial, que tiene una triggered y una activated) no podían representarse sin contorsiones semánticas.
3. **Incompatibilidad con el motor de bloques de construcción:** el Árbitro necesitaba distinguir entre habilidades (objetos de alto nivel con su propio ciclo de vida), efectos (acciones atómicas dentro de una habilidad) y costes (requisitos de activación).

**Proceso de diseño.** El nuevo esquema se diseñó en colaboración iterativa entre el investigador (que consultó además con Gemini para validación cruzada) y el asistente. Se debatieron varias estructuras candidatas:

- Se propuso inicialmente un esquema con `qualifiers` y `restrictions` separados en `target_info`. Tras análisis, se determinó que ambos campos eran redundantes: `restrictions` (con `logical_operator`, `types` y `modifiers`) subsumía por completo la funcionalidad de `qualifiers`. Se eliminó `qualifiers`.
- Se propuso `target_entity` como campo independiente, pero se descartó al verificar que `restrictions.types` ya cubría exactamente la misma funcionalidad de filtrado por tipo de entidad.
- Se decidió mover `is_optional` del nivel de `ability` al nivel de cada `effect`, porque en MTG la opcionalidad ("you may") aplica a la resolución de un efecto concreto, no a la existencia de la habilidad.
- Se añadió `base_attributes` como objeto en la raíz (`power`, `toughness`, `loyalty`, `color_indicator`) para que el motor pudiese instanciar las estadísticas base sin recorrer las habilidades.

**Esquema V1.2 resultante.** La nueva jerarquía quedó definida como:

```
Card (raíz)
├── name, mana_cost, supertypes[], card_types[], subtypes[]
├── keywords[]
├── base_attributes { power, toughness, loyalty, color_indicator }
├── requires_human_review, ontology_proposal[]
└── abilities[]
    ├── ability_order, ability_type, zones_active[], modal_choices
    ├── costs[] { cost_type, amount, restrictions }
    ├── trigger { event_type, conditions[] }
    └── effects[]
        ├── effect_order, mode_id, is_optional, effect_type
        ├── target_info { requires_target, target_owner, target_zone, target_count, restrictions }
        ├── effect_conditions[]
        └── parameters { amount, duration, chooser, string_value, destination_zone, token_definition }
```

**Impacto.** El esquema V1.2 desambigua completamente la relación entre costes, triggers y efectos. Permite representar cartas con cualquier número de habilidades independientes, cada una con sus propios costes, disparadores y efectos secuenciales. La estructura jerárquica facilita además la instanciación directa en objetos Python por el motor.

#### 8.13. Incorporación de MOVE_ZONE y CREATE_TOKEN como efectos especializados

**Motivación.** Durante la validación cruzada con Gemini, se identificaron dos bloqueos operativos que el esquema V1.1 no resolvía:

1. **MOVE_ZONE sin destino.** Cuando el `effect_type` es `MOVE_ZONE` (p.ej. Unsummon: "devuelve la criatura objetivo a la mano de su propietario"), `target_info.target_zone` indica la zona de ORIGEN (donde está la criatura ahora: `BATTLEFIELD`), pero no existía campo para indicar el DESTINO (`HAND`). El motor no sabría a dónde mover la carta.

2. **CREATE_TOKEN sin definición estructurada.** Cartas como Raise the Alarm ("Create two 1/1 white Soldier creature tokens") requieren especificar las características del token (poder, resistencia, colores, tipos, subtipos). Si el LLM codificaba estos datos en `string_value` como texto libre ("1/1 white Soldier"), el motor necesitaría un parser adicional para interpretar la cadena, anulando la ventaja de la representación estructurada.

**Soluciones adoptadas.**

- Se añadió `parameters.destination_zone` con la semántica: zona de DESTINO a donde se mueve la carta. La regla R4 del prompt obliga a que si `effect_type` es `MOVE_ZONE`, `destination_zone` sea no nulo. `target_info.target_zone` sigue siendo exclusivamente la zona de ORIGEN.
- Se añadió `parameters.token_definition` como objeto estructurado con campos `power`, `toughness`, `colors[]`, `card_types[]`, `subtypes[]` y `keywords[]`. La regla R5 prohíbe codificar datos de tokens en `string_value` y exige que `token_definition` sea un objeto completo cuando `effect_type` es `CREATE_TOKEN`.

**Lección.** La validación cruzada con un segundo modelo de lenguaje (Gemini) resultó ser una técnica efectiva de revisión arquitectónica. El modelo detectó lagunas operativas que no habían surgido en la revisión manual del esquema, funcionando como un "segundo par de ojos" automatizado.

#### 8.14. Cálculos dinámicos: el objeto dynamic_amount

**Problema.** Al traducir cartas con mecánicas dependientes del estado del juego, el LLM generaba constantes monolíticas inventadas:

- Tarmogoyf ("power is equal to the number of card types among cards in all graveyards") → `"amount": "TARMOGOYF_COUNT"`.
- Aetherflux Reservoir ("...plus 1") → `"amount": "PLUS_ONE"`.
- Lord of the Pit ("deals damage equal to its power") → `"amount": "POWER_OF_SOURCE"`.

Estas constantes ad-hoc violan el principio de polimorfismo: cada carta nueva requeriría su propia constante y su propia implementación en Python. Con miles de cartas, el enfoque es inviable.

**Diseño adoptado.** Se hizo el campo `amount` polimórfico, aceptando cuatro tipos de valor:

| Tipo | Cuándo usar | Ejemplo |
|------|------------|---------|
| `integer` | Valor fijo conocido | `"amount": 3` (Lightning Bolt) |
| `"X"` | Variable X del coste de maná | `"amount": "X"` |
| `null` | No aplica | `"amount": null` |
| `dynamic_amount_object` | Cálculo en tiempo de juego | Ver estructura abajo |

El `dynamic_amount_object` se diseñó con los siguientes campos:

```json
{
  "dynamic_calculation": "COUNT | ATTRIBUTE_REFERENCE",
  "distinct_property": "CARD_TYPES | NAMES | MANA_VALUES | COLORS | null",
  "multiplier": "integer | null",
  "offset": "integer | null",
  "source_ref": "SOURCE | TARGET_1 | CONTROLLER | OPPONENT | null",
  "attribute": "Power | Toughness | ConvertedManaCost | null",
  "query": {
    "target_zone": "BATTLEFIELD | GRAVEYARD | EXILE | ALL_ZONES | null",
    "target_owner": "CONTROLLER | OPPONENT | ANY | null",
    "restrictions": { "logical_operator": "...", "types": [...], "modifiers": [...] }
  }
}
```

**Campos clave y su justificación:**

- `dynamic_calculation: COUNT` indica que se debe contar objetos filtrados por `query`. Cubre "number of creatures you control", "number of lands in all graveyards", etc.
- `dynamic_calculation: ATTRIBUTE_REFERENCE` indica que se lee un atributo de una entidad concreta. Cubre "equal to its power", "equal to target's toughness", etc.
- `offset` resuelve el caso de operaciones aritméticas simples como "+1" o "-2". Tarmogoyf define su toughness como "that number plus 1", lo que se modela como el mismo `COUNT` pero con `offset: 1`.
- `distinct_property` resuelve el caso de contar valores únicos. Tarmogoyf NO cuenta cartas: cuenta *tipos de carta distintos* entre todas las cartas de todos los cementerios. Esto se modela con `distinct_property: CARD_TYPES` en vez de contar cartas directamente.
- `multiplier` cubre "twice the number of..." (`multiplier: 2`).
- `target_owner: ANY` y `target_zone: ALL_ZONES` se introdujeron como valores reservados para cubrir efectos que no pertenecen a un único jugador o zona.

**Reglas de detección (R14).** Para que el LLM sepa cuándo abrir un `dynamic_amount_object` en vez de usar un entero, se documentaron frases gatillo en el Oracle Text:

- "...equal to the number of..." → `COUNT`
- "...where X is..." → `COUNT`
- "...equal to its [power/toughness/mana value]..." → `ATTRIBUTE_REFERENCE`
- Asteriscos en `base_attributes` (*/*) → `DEFINE_STATS` con `dynamic_calculation`
- "...plus N" / "...minus N" tras un cálculo → `offset`

#### 8.15. Few-shot examples y anti-ejemplos para combatir alucinaciones

**Problema.** A pesar de las 14 reglas semánticas del prompt, el LLM seguía cometiendo errores de modelado: inventaba constantes monolíticas, confundía la dirección de `target_zone` en `MOVE_ZONE`, o codificaba tokens como strings en `string_value`. El esquema V1.2 tenía suficiente profundidad de anidamiento (abilities → effects → target_info → restrictions) como para que el LLM se desorientase sin referencias concretas.

**Riesgo del few-shot prompting.** Se identificó un riesgo potencial: que el LLM se limitase a copiar literalmente los patrones de los ejemplos sin adaptarlos a cartas nuevas (sesgo de anclaje). Para mitigarlo, se adoptaron tres estrategias:

1. **Diversidad de patrones:** se seleccionaron 3 ejemplos que cubren patrones estructuralmente distintos:
   - **Lightning Bolt** (Instant simple): un solo efecto `SPELL` con `DEAL_DAMAGE`, target con `restrictions` OR entre `CREATURE`, `PLAYER` y `PLANESWALKER`. Demuestra el caso más simple.
   - **Aether Vial** (Artifact con dos habilidades): `TRIGGERED` (upkeep + `ADD_COUNTER`) y `ACTIVATED` (tap + `MOVE_ZONE` + `COMPARISON`). Demuestra múltiples habilidades, condiciones y `destination_zone`.
   - **Raise the Alarm** (Instant con tokens): `SPELL` con `CREATE_TOKEN` y `token_definition` estructurado. Demuestra la creación de fichas.

2. **Anti-ejemplos explícitos:** se añadió una sección `=== ANTI-EJEMPLOS (INCORRECTO vs CORRECTO) ===` con 8 pares que muestran errores frecuentes y su corrección. Esta técnica, utilizada en ingeniería de prompts avanzada, explota la tendencia del LLM a evitar patrones que se le muestran como incorrectos. Los pares cubren:
   - Constantes monolíticas en `amount` (ej. `COUNT_LANDS_CONTROLLED`).
   - Referencias a atributos como constantes (ej. `POWER_OF_SOURCE`).
   - Operaciones matemáticas como constantes (ej. `PLUS_ONE`, `TARMOGOYF_COUNT`).
   - Tokens como strings en vez de objetos.
   - Pertenencia en `modifiers` en vez de `target_owner`.
   - `target_count` como entero en vez de objeto `{min, max}`.
   - Keywords modeladas como abilities en vez de en `keywords` raíz.
   - Token sin `keywords` ni `abilities` en `token_definition`.

3. **Reglas explícitas de generalización:** el prompt instruye al LLM a usar los ejemplos como guía de estructura, no como plantilla de constantes, enfatizando que debe verificar cada constante contra la ontología inyectada.

**Impacto.** La combinación de few-shot + anti-ejemplos + reglas redujo significativamente los errores estructurales del LLM, especialmente en el modelado de `dynamic_calculation` y `token_definition`, donde previamente fallaba con alta frecuencia.

#### 8.16. Robustez del parser JSON y gestión de tokens LLM

**Problema 1: truncación de respuesta.** Con el esquema V1.2 (significativamente más verboso que el esquema plano original) y los 3 ejemplos few-shot inyectados en el prompt, las respuestas del LLM empezaron a ser truncadas. El modelo generaba JSON válido parcialmente, cortado a mitad de un objeto porque alcanzaba el límite de tokens de salida.

**Solución.** Se duplicó `_MAX_TOKENS` de 4096 a 8192 y se añadió detección explícita de truncación: tras recibir la respuesta de `litellm`, se verifica `choice.finish_reason`. Si es `"length"`, se imprime un warning visible indicando que la respuesta fue truncada y que el JSON puede estar incompleto.

**Problema 2: trailing commas.** El LLM generaba JSON con comas finales antes de cierre de arrays u objetos (`[1, 2, 3,]`), que es sintaxis inválida en JSON estricto pero común en la salida de modelos de lenguaje.

**Solución.** Se implementó `_limpiar_trailing_commas()`, una función de limpieza basada en expresiones regulares que elimina comas seguidas de cierre de llave o corchete. Esta función se aplica como fallback: `_extraer_json()` primero intenta `json.loads()` directo, y si falla con `JSONDecodeError`, aplica la limpieza y reintenta el parsing.

**Impacto.** Estos dos cambios eliminaron los fallos de parsing que habían aparecido tras la ampliación del prompt, haciendo el pipeline robusto frente a las idiosincrasias del formato de salida del LLM.

#### 8.17. Validación determinista post-LLM: `_validar_esquema`

**Contexto.** En la sección 6 de esta memoria se propuso como mejora futura un "validador determinista post-LLM". Esta iteración implementó esa mejora.

**Problema.** La autoridad semántica del sistema residía exclusivamente en el prompt: si el LLM ignoraba una regla o usaba una constante inexistente, la única defensa era la cuarentena (que depende de que el propio LLM se auto-detecte). Esto creaba una dependencia circular: el sistema confiaba en que el LLM validase su propia salida.

**Solución.** Se implementó `_validar_esquema(resultado, diccionario)`, una función Python determinista que verifica la estructura y constantes del JSON-LD contra el esquema V1.2 y la ontología cargada. Las validaciones incluyen:

| Validación | Qué verifica |
|-----------|-------------|
| Campos raíz obligatorios | `name`, `card_types`, `abilities` presentes |
| `keywords` raíz | Presente, es lista, no vacío (`["NONE"]` si no aplica) |
| `base_attributes` | Presente como objeto |
| Estructura de abilities | `ability_order`, `ability_type`, `effects` presentes |
| Estructura de effects | `effect_order`, `effect_type`, `target_info`, `parameters` presentes |
| `target_count` | Debe ser objeto `{min, max}`, no entero |
| `parameters` completo | Las 6 claves obligatorias: `amount`, `duration`, `chooser`, `string_value`, `destination_zone`, `token_definition` |
| MOVE_ZONE | `destination_zone` no nulo |
| CREATE_TOKEN | `token_definition` no nulo, incluye `keywords` y `abilities` |
| Constantes ontológicas | Todo `effect_type`, `ability_type`, `event_type`, etc. debe existir en la ontología o ser valor reservado |

**Diseño no bloqueante.** La validación emite warnings pero no detiene el flujo HITL. Esto permite que el investigador vea los errores y decida si la traducción es aceptable con correcciones menores o si requiere re-traducción. La decisión de diseño respeta el principio de que el humano mantiene la autoridad final.

**Impacto.** La autoridad semántica pasó de "prompt-only" a "prompt + verificación formal", alineándose con el rigor académico del TFG. El validador actúa como una red de seguridad que captura errores que el prompt no logra prevenir, sin reemplazar el juicio humano.

#### 8.18. Ampliación de valores reservados y sincronización con el validador

**Problema.** Tras implementar el esquema V1.2 con sus nuevos campos estructurales (`COUNT`, `ATTRIBUTE_REFERENCE`, `ANY`, `ALL_ZONES`, `WHILE_STATIC_ACTIVE`, `TARGET_1`, `TARGET_2`, `PERMANENT`, `WHILE_CONDITION`), el sistema empezó a generar falsos positivos de cuarentena. El LLM usaba correctamente estos valores como instrucciones del motor, pero al no encontrarlos en la ontología OWL, activaba cuarentena y proponía añadirlos como constantes ontológicas.

**Causa raíz.** Los valores estructurales del esquema V1.2 son **punteros dinámicos e instrucciones del motor**, no conceptos estáticos de MTG. `COUNT` no es un efecto ni un trigger: es una instrucción para que el motor calcule una cantidad. No pertenecen a la ontología porque no modelan conocimiento de dominio.

**Solución.** Se actualizó la lista blanca de valores reservados en dos ubicaciones sincronizadas:

1. **En el prompt** (Regla 8 — VALORES RESERVADOS): la lista se amplió para incluir `TARGET_1`, `TARGET_2`, `ANY`, `ALL_ZONES`, `COUNT`, `ATTRIBUTE_REFERENCE`, `PERMANENT`, `UNTIL_END_OF_TURN`, `WHILE_STATIC_ACTIVE`, `WHILE_CONDITION`, `CARD_TYPES`, `NAMES`, `MANA_VALUES`, `COLORS`, `X`, `*`, `AND`, `OR`, `TRUE`, `FALSE` y cualquier entero.
2. **En Python** (`_RESERVED_VALUES` frozenset): la misma lista, asegurando que `_validar_esquema` no marque estos valores como constantes faltantes.

**Lección.** La introducción de nuevos campos estructurales en el esquema requiere actualizar simultáneamente tres puntos: el esquema del prompt, la regla de valores reservados del prompt y la lista blanca del validador Python. La falta de sincronización entre estos tres puntos genera falsos positivos que degradan la confianza del investigador en el sistema.

#### 8.19. Keywords en raíz y habilidades complejas de tokens

**Problema 1: keywords de la carta principal.** El esquema V1.2 no distinguía entre keyword abilities (Flying, Trample, Haste) y habilidades complejas. Una carta con Flying y una habilidad triggered debía modelar ambas como objetos en `abilities[]`. Esto obligaba al LLM a crear habilidades STATIC artificiales para keywords simples, aumentando la complejidad del JSON y la probabilidad de error.

**Problema 2: tokens con habilidades complejas.** El `token_definition` solo incluía `keywords[]` para habilidades de palabra clave. Sin embargo, existen tokens con habilidades complejas (triggered, activated o static con texto de reglas), como los tokens de Food ("2, T, Sacrifice this artifact: You gain 3 life"). El esquema no podía representar estas habilidades.

**Decisión de convención.** Se consultó al investigador sobre la representación de "sin keywords": se decidió usar `["NONE"]` en vez de un array vacío `[]`, consistente con la convención ya adoptada para `supertypes`. Esto evita ambigüedad entre "no se analizaron los keywords" y "la carta no tiene keywords".

**Soluciones implementadas.**

1. **`keywords` en raíz:** se añadió un array `keywords` justo después de `subtypes` en el esquema raíz. Las keyword abilities de la carta se listan como strings (ej. `["FLYING", "TRAMPLE"]`). Cuando la carta no tiene keywords, se usa `["NONE"]`.

2. **`token_definition.abilities[]`:** se añadió un array `abilities` dentro de `token_definition` con la misma estructura que `abilities[]` de la raíz. Cuando el token no tiene habilidades complejas, se usa `[]`.

3. **Reglas R15 y R16:** se añadieron dos reglas semánticas al prompt:
   - **R15 (KEYWORDS RAÍZ):** las keyword abilities NO se modelan como objetos en `abilities[]`; se listan en `keywords` raíz. `abilities[]` queda reservado para habilidades con costes, disparadores o efectos estructurados.
   - **R16 (HABILIDADES DE TOKENS):** los keywords del token van en `token_definition.keywords[]` y las habilidades complejas en `token_definition.abilities[]`.

4. **Checklist ampliado:** se extendió de 8 a 11 puntos de autovalidación, añadiendo verificaciones para `keywords` raíz presente y no vacío, separación correcta entre keywords y abilities, y completitud de `token_definition` (claves `keywords` y `abilities`).

5. **Anti-ejemplos 7 y 8:** se añadieron dos pares INCORRECTO/CORRECTO: keyword modelada como ability STATIC vs. listada en `keywords` raíz, y `token_definition` sin claves `keywords`/`abilities` vs. con ambas.

6. **Validador Python actualizado:** `_validar_esquema` ahora verifica que `keywords` existe en raíz, es lista, y no está vacío; y que en `CREATE_TOKEN`, `token_definition` incluye tanto `keywords` (lista) como `abilities` (lista).

**Impacto.** El JSON generado para cartas con keywords se simplificó significativamente: una carta con Flying y una habilidad activated ahora tiene `"keywords": ["FLYING"]` en raíz y un solo objeto en `abilities[]` para la habilidad activated, en vez de dos objetos en `abilities[]`. Esto reduce la complejidad del JSON y facilita la instanciación en el motor.

---

### 9. Evolución del prompt de sistema

El prompt del sistema experimentó una evolución significativa a lo largo de las iteraciones. Se documenta aquí como referencia de ingeniería de prompts aplicada.

| Versión | Cambio principal | Motivación |
|---------|-----------------|-----------|
| V0 (semilla) | Prompt estático con ontología hardcodeada | Validar que el LLM genera JSON-LD correcto |
| V0.1 | Inyección dinámica de ontología desde JSON | Permitir que la ontología evolucione sin tocar código |
| V0.2 | Regla de cuarentena (regla 4) | Evitar constantes inventadas en campos principales |
| V0.3 | Regla de polimorfismo (regla 7) | Evitar constantes monolíticas para condiciones complejas |
| V0.4 | Estructura COMPARISON con scope | Modelar condiciones como expresiones evaluables |
| V0.5 | Valores reservados (regla 8) | Evitar falsos positivos de cuarentena con NONE, operadores, etc. |
| V0.6 | Validación total (regla 9) | Extender la verificación ontológica a todos los campos del JSON-LD |
| V1.2 | Rediseño completo del esquema con abilities, effects, costs, targets, restricciones | Alinear la estructura JSON-LD con el motor de bloques de construcción del TFG |
| V1.2.1 | dynamic_amount_object con COUNT, ATTRIBUTE_REFERENCE, offset, distinct_property | Eliminar constantes monolíticas para cálculos dependientes del estado del juego |
| V1.2.2 | MOVE_ZONE (destination_zone) y CREATE_TOKEN (token_definition) | Resolver bloqueos operativos en efectos de movimiento y creación de fichas |
| V1.2.3 | Few-shot examples (3 cartas) + anti-ejemplos (8 pares) | Combatir alucinaciones del LLM mediante demostración práctica y prohibición explícita |
| V1.2.4 | Ampliación de valores reservados (COUNT, ANY, ALL_ZONES, TARGET_1, etc.) | Eliminar falsos positivos de cuarentena con valores estructurales del motor |
| V1.2.5 | Keywords en raíz + token_definition.abilities[] + reglas R15/R16 | Separar keywords simples de habilidades complejas; soportar tokens con habilidades |

### 10. Relación de ficheros producidos

| Fichero | Propósito |
|---------|----------|
| `traduccion/agente_mtg_cli.py` | Script CLI principal: traducción, cuarentena, HITL, re-traducción, validación post-LLM |
| `traduccion/ontology_store.py` | Sincronización bidireccional OWL ↔ JSON, persistencia atómica |
| `IA_JSON/ontologia_motor.json` | Diccionario ontológico operativo (derivado del OWL, consumido por el LLM) |
| `IA_OWL/OWL_tfg.rdf` | Ontología formal en RDF/XML (fuente de verdad, editable en Protégé) |

### 11. Resumen de errores, causas y soluciones

| # | Error | Causa raíz | Solución |
|---|-------|-----------|---------|
| 1 | Sintaxis PowerShell en CMD | `$env:` es exclusivo de PowerShell | Documentar ambas sintaxis en el mensaje de error del script |
| 2 | Heredoc de Python no funciona en Windows | `python - <<'PY'` es sintaxis Bash | Escribir ficheros directamente en vez de usar heredocs |
| 3 | OWL exporta diccionario vacío | Código buscaba `owl:NamedIndividual`, OWL usa `rdfs:subClassOf` entre clases | Reescribir extracción con recorrido recursivo de subclases |
| 4 | Clases OWL no encontradas | Nombres incorrectos (`EffectType` vs `Effect`) | Corregir nombres para coincidir con el OWL real |
| 5 | Solo se extraían categorías hardcodeadas | Lista fija de categorías padre en el código | Descubrimiento dinámico de clases raíz con `_find_root_classes()` |
| 6 | LLM no activa cuarentena | Operandos de ejemplo inventados; validación parcial; NONE como constante | Tres correcciones: ejemplos reales, validación total, valores reservados |
| 7 | Cartas de test no ejercitan cuarentena | Lightning Bolt y Aether Vial completamente cubiertos por la ontología | Añadir Thoughtseize y Path to Exile como casos de prueba |
| 8 | `TypeError: sequence item 5: expected str instance, tuple found` | Bloque `sec_reservados` generaba una tupla de strings en vez de un string concatenado | Reescribir el literal como una sola cadena con concatenación explícita |
| 9 | Constantes monolíticas para cálculos (`COUNT_LANDS_CONTROLLED`, `PLUS_ONE`) | LLM no tenía mecanismo para modelar cálculos dinámicos | Diseño de `dynamic_amount_object` con `COUNT`, `ATTRIBUTE_REFERENCE`, `offset`, `distinct_property` |
| 10 | Falsos positivos de cuarentena con valores estructurales (COUNT, ANY, ALL_ZONES) | Valores del motor no incluidos en la lista blanca de reservados | Ampliación sincronizada de valores reservados en prompt y validador Python |
| 11 | JSON truncado por límite de tokens | Prompt V1.2 + few-shot excedían capacidad de respuesta a 4096 tokens | Duplicar `_MAX_TOKENS` a 8192 + detección de `finish_reason == "length"` |
| 12 | JSON con trailing commas | El LLM genera comas finales antes de cierre de arrays/objetos | Función `_limpiar_trailing_commas()` con regex como fallback de parsing |
| 13 | Ejecución de scripts deshabilitada en PowerShell | Política de ejecución `Restricted` por defecto en Windows | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

### 12. Observaciones metodológicas para la discusión

1. **La ingeniería de prompts es un proceso empírico iterativo.** Cada regla del prompt se añadió como respuesta a un fallo observado del LLM, no como diseño a priori. Esto es consistente con la literatura sobre alineamiento de modelos de lenguaje: las instrucciones deben ser progresivamente más explícitas y exhaustivas para cerrar ambigüedades. El prompt evolucionó de 6 líneas (V0) a más de 1000 líneas (V1.2.5) a lo largo de 15+ iteraciones.

2. **La ontología actúa como contrato formal entre LLM y motor.** El patrón de inyectar la ontología completa en el prompt y exigir que toda constante sea verificable contra ella convierte al OWL en un contrato de interfaz: el LLM no puede generar nada que el motor no pueda interpretar. Esto es funcionalmente equivalente a un sistema de tipos estático en tiempo de compilación.

3. **El HITL como mecanismo de crecimiento controlado del conocimiento.** El flujo cuarentena → propuesta → revisión humana → persistencia en OWL+JSON implementa un patrón de aprendizaje semi-supervisado donde el humano actúa como oráculo de validación. Cada aceptación amplía permanentemente la cobertura del sistema sin riesgo de contaminación semántica.

4. **El polimorfismo de condiciones es una decisión de escalabilidad.** Sin el modelo `COMPARISON`, cada carta con una condición nueva requeriría una constante específica y código Python ad-hoc. Con el modelo de operandos atómicos, el motor puede evaluar cualquier comparación como una expresión genérica, reduciendo el problema de O(n cartas) a O(k operandos).

5. **El polimorfismo de cálculos dinámicos extiende la escalabilidad a operaciones aritméticas.** Sin el `dynamic_amount_object`, efectos como "deals damage equal to its power plus 1" requerirían constantes ad-hoc por carta. Con `COUNT`/`ATTRIBUTE_REFERENCE` + `offset` + `distinct_property`, el motor descompone cualquier cálculo en primitivas evaluables. Esto reduce la programación de cartas individuales a la composición de bloques atómicos reutilizables.

6. **La defensa en profundidad mejora la fiabilidad del sistema.** El sistema emplea tres capas de validación: (a) reglas en el prompt para guiar al LLM, (b) anti-ejemplos para prevenir errores frecuentes, y (c) validación determinista post-LLM en Python. Ninguna capa es suficiente por sí sola, pero su combinación reduce significativamente la tasa de errores no detectados. Este patrón es análogo a la defensa en profundidad en seguridad informática.

7. **La separación de keywords y abilities refleja la taxonomía real de MTG.** En las Comprehensive Rules de Magic, las keyword abilities son una categoría distinta de las habilidades activadas, disparadas y estáticas. El esquema V1.2.5 refleja fielmente esta taxonomía al nivel de la representación JSON-LD, facilitando que el motor implemente keywords como modificadores de estado simples (sin coste, trigger ni efecto explícito) y abilities como máquinas de estado completas.

8. **La validación cruzada con múltiples LLMs es una técnica efectiva de revisión arquitectónica.** El uso de Gemini como "segundo revisor" del esquema permitió detectar lagunas operativas (MOVE_ZONE, CREATE_TOKEN) que no habían surgido en la revisión manual. Esta técnica tiene un coste marginal bajo y un retorno alto cuando se aplica a esquemas de datos complejos.

9. **La selección de casos de prueba requiere análisis de cobertura de rutas.** Cartas "simples" (como Lightning Bolt) validan el camino feliz pero no ejercitan cuarentena, validación de constantes ni cálculos dinámicos. Un conjunto de prueba efectivo debe incluir cartas que activen cada rama del sistema: cuarentena (constantes faltantes), cálculos dinámicos (COUNT, ATTRIBUTE_REFERENCE), modales (Choose one), tokens (CREATE_TOKEN), movimiento (MOVE_ZONE) y condiciones (COMPARISON).
