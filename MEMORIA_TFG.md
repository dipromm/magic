# MEMORIA 3

## Evolución del módulo de Ingesta y Traducción Automática (Oracle Text -> JSON-LD)

### 1. Contexto y objetivo de la iteración

Durante esta iteración se pasó de una idea inicial de “traductor asistido por LLM” a un pipeline operativo orientado a ingeniería: entrada de Oracle Text, traducción estructurada, control de calidad semántico y actualización controlada de ontología.

El objetivo práctico no fue solo “generar JSON”, sino conseguir una salida que pudiera ser consumida por un motor de reglas sin depender de lógica ad-hoc por carta. Para ello, se priorizó una arquitectura con tres garantías:

1. **Trazabilidad formal:** OWL como base de conocimiento persistente y JSON como vista operativa.
2. **Gobernanza humana:** cuarentena + revisión interactiva cuando el modelo detecta lagunas.
3. **Escalabilidad semántica:** representación polimórfica de condiciones y cálculos para evitar constantes monolíticas.

### 2. Decisiones de arquitectura adoptadas

**Cambio de enfoque de ejecución (web -> CLI):** se descartó el enfoque inicial ligado a `google.adk`/UI y se adoptó un script de terminal autónomo (`traduccion/agente_mtg_cli.py`). Esta decisión redujo complejidad de ejecución y permitió controlar mejor el ciclo traducir-validar-corregir.

**Ontología como contrato de dominio:** en lugar de un diccionario fijo embebido, se tomó OWL como fuente de verdad y se generó JSON operativo desde ese grafo. Así, el modelo trabaja sobre un vocabulario explícito y versionable.

**HITL granular por propuesta:** se sustituyó una decisión global (aceptar/rechazar todo) por revisión bloque a bloque `[Y/N/C]`, permitiendo aceptar parcialmente propuestas y reinyectar correcciones específicas.

**Persistencia dual y atómica:** las propuestas aceptadas se escriben en OWL (subclases) y en JSON canónico, con guardado atómico y copias de seguridad para evitar corrupción de ficheros.

**Defensa en profundidad:** se combinó control por prompt (reglas/anti-ejemplos), parser robusto de JSON y validación determinista post-LLM para reducir errores estructurales y semánticos.

### 3. Implementación técnica realizada

La implementación quedó dividida en dos módulos principales:

- **`traduccion/agente_mtg_cli.py`**
  - Carga segura de credenciales (`POLIGPT_API_KEY`) y configuración del endpoint PoliGPT.
  - Construcción dinámica del prompt con la ontología activa.
  - Traducción Oracle Text -> JSON-LD vía `litellm`.
  - Extracción robusta del JSON (`_extraer_json`) y saneo de casos frecuentes de salida inválida (p.ej., truncación/comas finales).
  - Bucle de cuarentena con revisión por propuesta (`_collect_proposals`, `_review_proposals_by_block`).
  - Re-traducción iterativa con correcciones humanas.
  - Validación determinista de esquema y constantes (`_validar_esquema`) como segunda capa de control.

- **`traduccion/ontology_store.py`**
  - Carga del OWL con `rdflib`.
  - Descubrimiento automático de clases raíz del namespace.
  - Exportación recursiva de subclases a diccionario JSON.
  - Resolución de categorías y aplicación de propuestas aceptadas como nuevas subclases OWL.
  - Persistencia atómica en JSON y RDF con backup.

Además, se integraron pruebas con cartas de dificultad progresiva para ejercitar el camino feliz y los caminos de cuarentena (incluyendo casos con cálculos dinámicos y efectos no cubiertos inicialmente).

### 4. Justificación metodológica

El dominio de MTG combina alta variabilidad lingüística con semántica reglamentaria estricta. En ese contexto, ni un enfoque puramente generativo ni uno puramente manual resultan eficientes:

- Un enfoque **solo LLM** acelera, pero tiende a inventar estructuras o constantes si no se acota.
- Un enfoque **solo manual** mantiene rigor, pero no escala al volumen de cartas y variantes.

Por ello se adoptó un modelo híbrido **LLM + OWL + HITL**, donde:

1. El LLM interpreta texto natural y propone estructura.
2. La ontología delimita el vocabulario permitido.
3. El humano valida propuestas de crecimiento del dominio.

Este patrón transforma los errores del modelo en entradas útiles para enriquecer la ontología, en lugar de tratarlos como fallos terminales del pipeline.

### 5. Riesgos identificados y mitigación

| Riesgo | Mitigación |
|--------|-----------|
| Salida semánticamente incorrecta pese a “formato válido” | Reglas de prompt + validador determinista post-LLM + HITL |
| Invención de constantes monolíticas (no reutilizables) | Modelo polimórfico (`COMPARISON`, `dynamic_amount_object`) y anti-ejemplos explícitos |
| Variabilidad del formato de salida del LLM | Parser robusto, limpieza de trailing commas y control de truncación por tokens |
| Deriva entre OWL formal y JSON operativo | Exportado desde OWL, fusión conservadora y persistencia dual sincronizada |
| Rechazo/aceptación demasiado gruesa en cuarentena | Revisión granular por bloque con decisiones independientes |
| Falsos positivos por valores estructurales del motor | Catálogo de valores reservados alineado entre prompt y validador |

### 6. Mejora recomendada para siguiente iteración

Las mejoras inmediatas recomendadas son:

1. **Automatizar evaluación por lotes** con métricas estables (parseabilidad, tasa de cuarentena, iteraciones HITL por carta, precisión de propuestas).
2. **Congelar una versión estable del esquema V1.2.x** y mantener changelog de cambios de contrato.
3. **Añadir tests unitarios del validador** para prevenir regresiones al ampliar reglas.
4. **Definir política formal de aceptación ontológica** (criterios de entrada, nomenclatura y revisión).
5. **Conectar de forma completa JSON-LD -> objetos del motor** con pruebas de integración extremo a extremo.

### 7. Resultado de la iteración

El resultado de esta iteración es un pipeline funcional y trazable que:

- Traduce Oracle Text a JSON-LD con estructura compatible con el motor.
- Detecta lagunas de conocimiento y las canaliza mediante cuarentena.
- Permite expansión controlada de la ontología con persistencia real en OWL y JSON.
- Reduce errores semánticos mediante validación adicional no dependiente del LLM.

En términos de TFG, esta fase deja resuelto el puente entre PLN y representación formal ejecutable: el sistema ya no se limita a “extraer texto”, sino que gestiona conocimiento de dominio de forma incremental, auditada y técnicamente reproducible.

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

#### 8.12. Rediseño del contrato JSON-LD a V1.2 (de bloques planos a jerarquía explícita)

En esta fase se tomó la decisión más importante de toda la iteración: abandonar la estructura semiplana anterior y adoptar un contrato jerárquico estricto orientado al motor.  
El problema real no era solo de “formato”, sino de **semántica ejecutable**: con `resolution_blocks` era posible describir acciones, pero no distinguir con precisión qué era coste, qué era trigger y qué era efecto resolutivo. Esa ambigüedad impedía mapear de forma robusta la salida del LLM a objetos internos del Árbitro.

Las decisiones que consolidaron V1.2 fueron:

- Eliminar redundancias (`qualifiers`, `target_qualifiers`, `target_entity`) para evitar colisiones semánticas.
- Mover `is_optional` al nivel de `effects[]`, donde realmente aplica en reglas MTG (“you may”).
- Añadir `base_attributes` en raíz para desacoplar atributos base de lógica de habilidades.
- Fijar la jerarquía `Card -> abilities[] -> (costs/trigger/effects[]) -> target_info/parameters`.

Este cambio convirtió el JSON-LD en una **IR (representación intermedia) estable**: suficientemente expresiva para cartas complejas y lo bastante rígida para poder validarse.

#### 8.13. Cierre de huecos operativos: MOVE_ZONE y CREATE_TOKEN

Tras validar el esquema con cartas reales y revisión cruzada externa, se detectaron dos huecos que bloqueaban ejecución:

1. En `MOVE_ZONE`, el contrato decía dónde estaba el objetivo, pero no a dónde debía ir.
2. En `CREATE_TOKEN`, la definición del token quedaba implícita o textual, no estructurada.

Se corrigió con dos decisiones de contrato:

- `parameters.destination_zone` pasa a ser obligatorio para `MOVE_ZONE`.
- `parameters.token_definition` pasa a ser un objeto formal (power, toughness, colors, card_types, subtypes, keywords), prohibiendo usar `string_value` como pseudo-parser.

El resultado fue eliminar la necesidad de “interpretar texto” en runtime para estas dos mecánicas troncales.

#### 8.14. Modelado de cantidades dinámicas (COUNT / ATTRIBUTE_REFERENCE) y fin de los monolitos

Uno de los fallos más repetidos del LLM era inventar constantes monolíticas para cálculos (“PLUS_ONE”, “TARMOGOYF_COUNT”, etc.).  
Eso rompía el objetivo principal del módulo: programar bloques reutilizables, no cartas aisladas.

La solución fue convertir `amount` en campo polimórfico e introducir un objeto de cálculo dinámico con capacidad para:

- Contar entidades filtradas (`COUNT` + `query`).
- Leer atributos de referencias (`ATTRIBUTE_REFERENCE` + `source_ref`/`attribute`).
- Aplicar aritmética incremental (`offset`) y escalado (`multiplier`).
- Contar valores únicos por propiedad (`distinct_property`) para casos tipo Tarmogoyf.

Además, se añadieron reglas gatillo explícitas en el prompt para forzar cuándo abrir `dynamic_amount` y cuándo dejar un entero fijo.  
Con ello, el cálculo pasó de ser ad-hoc por carta a ser **composición de primitivas semánticas**.

#### 8.15. Control de alucinación: few-shot + anti-ejemplos + reglas de no-copia

Con el aumento de complejidad estructural, el zero-shot dejó de ser fiable.  
La mejora no fue “poner ejemplos sin más”, sino diseñar una tríada:

1. **Few-shot representativos** (simple, multi-habilidad y tokens).
2. **Anti-ejemplos INCORRECTO/CORRECTO** para patrones de error frecuentes.
3. **Reglas de generalización** para impedir copia literal de los ejemplos.

Los ejemplos elegidos (`Lightning Bolt`, `Aether Vial`, `Raise the Alarm`) no fueron casuales: cubren tres regiones distintas del espacio semántico del esquema.  
Esto redujo errores estructurales repetitivos (dirección de zonas, monolitos, token en string, target_count escalar), sin sacrificar capacidad de generalizar.

#### 8.16. Robustez de salida LLM: truncación y JSON malformado

Al crecer el prompt (esquema + reglas + checklist + ejemplos + anti-ejemplos), aparecieron fallos de infraestructura de salida:

- Respuestas truncadas por límite de tokens.
- JSON con trailing commas.

Se aplicó defensa en dos capas:

- Aumento de `max_tokens` y aviso explícito cuando `finish_reason == "length"`.
- Fallback de parseo con limpieza de comas residuales antes de relanzar `json.loads`.

Este ajuste no cambia semántica, pero sí incrementa mucho la **fiabilidad operativa** del pipeline en ejecución real.

#### 8.17. Verificación determinista post-LLM (`_validar_esquema`)

Hasta este punto, la validez dependía casi por completo del prompt y de la auto-disciplina del modelo.  
Se introdujo entonces una capa formal en Python para verificar estructura y consistencia de constantes.

La validación cubre, entre otros:

- Presencia y tipo de campos obligatorios en raíz, ability y effect.
- Estructura obligatoria de `parameters` y `target_count` como rango `{min,max}`.
- Reglas contextuales (`MOVE_ZONE` exige `destination_zone`, `CREATE_TOKEN` exige `token_definition`).
- Comprobación de constantes contra ontología + reservados.
- Validación de `keywords` raíz y de `token_definition.keywords`/`abilities`.

Se mantuvo como validación **no bloqueante** (warnings) para no romper el ciclo HITL, pero añade trazabilidad y rigor experimental.

#### 8.18. Valores reservados: separación entre sintaxis del motor y ontología de dominio

A medida que el esquema incorporó punteros dinámicos (`COUNT`, `ANY`, `ALL_ZONES`, `TARGET_1`, etc.), emergieron falsos positivos de cuarentena.  
La causa era conceptual: estos términos no son conocimiento de dominio MTG, sino sintaxis interna de ejecución.

La corrección fue sincronizar dos planos:

- Lista blanca en prompt (regla de reservados).
- Lista blanca en validador Python (`_RESERVED_VALUES`).

Esta sincronización fue crítica para evitar que el sistema “intente ontologizar” artefactos internos del motor.

#### 8.19. Separación explícita: keywords de carta vs habilidades complejas (incluyendo tokens)

El último ajuste de esta cadena resolvió una confusión de modelado muy frecuente:

- Las keyword abilities de la carta principal (Flying, Trample, etc.) no deben tratarse como habilidades estructuradas.
- Los tokens, en cambio, pueden tener keywords y también habilidades complejas.

Se formalizó con:

- `keywords` en raíz (convención `["NONE"]` cuando no aplica).
- `token_definition.abilities[]` además de `token_definition.keywords[]`.
- Reglas R15/R16, checklist ampliado y anti-ejemplos específicos.
- Validación Python coherente con el nuevo contrato.

Esta decisión reduce ruido estructural en cartas simples y, al mismo tiempo, amplía cobertura para tokens avanzados.

---

### 9. Evolución del prompt de sistema (resumen ejecutivo)

La evolución del prompt fue incremental, guiada por fallos observados en pruebas reales. El patrón fue siempre el mismo:  
**falla detectada -> regla explícita -> ejemplo/anti-ejemplo -> validación en Python**.

| Versión | Cambio principal | Motivación |
|---------|------------------|------------|
| V0 | Prompt estático con ontología semilla | Validar pipeline mínimo LLM -> JSON-LD |
| V0.1 | Inyección dinámica de ontología | Evitar hardcode y permitir evolución del diccionario |
| V0.2 | Regla de cuarentena | Frenar invención de constantes |
| V0.3 | Polimorfismo de condiciones | Evitar bloques monolíticos por carta |
| V0.4 | COMPARISON con scopes | Hacer evaluables las condiciones en motor |
| V0.5 | Valores reservados iniciales | Evitar falsas alarmas con sintaxis del motor |
| V0.6 | Validación total en prompt | Extender verificación a todos los campos |
| V1.2 | Esquema jerárquico Card/Abilities/Effects | Alinear salida con arquitectura del Árbitro |
| V1.2.1 | `dynamic_amount_object` | Cubrir cálculos complejos sin nuevas constantes ad-hoc |
| V1.2.2 | `destination_zone` + `token_definition` | Cerrar bloqueos de MOVE_ZONE/CREATE_TOKEN |
| V1.2.3 | Few-shot + anti-ejemplos | Reducir errores estructurales repetitivos |
| V1.2.4 | Reservados ampliados y sincronizados | Eliminar cuarentenas falsas por punteros dinámicos |
| V1.2.5 | `keywords` raíz + `token_definition.abilities[]` | Separar keywords simples y habilidades complejas |

### 10. Relación de ficheros y función en la arquitectura

| Fichero | Función dentro del módulo |
|---------|---------------------------|
| `traduccion/agente_mtg_cli.py` | Núcleo de traducción: prompt dinámico, llamada LLM, parseo robusto, cuarentena, HITL y validación post-LLM |
| `traduccion/ontology_store.py` | Sincronización OWL <-> JSON, persistencia segura y aplicación de propuestas aceptadas |
| `IA_JSON/ontologia_motor.json` | Diccionario operativo consumido por el prompt y la validación |
| `IA_OWL/OWL_tfg.rdf` | Fuente formal de verdad editable en Protégé |

### 11. Errores representativos y resolución aplicada

| # | Error observado | Causa raíz | Resolución |
|---|-----------------|-----------|------------|
| 1 | Sintaxis `$env:` en CMD | Mezcla de shell syntax | Documentación dual (PowerShell/CMD) en mensajes y guía |
| 2 | Activación PS1 bloqueada | Política de ejecución de PowerShell | Ajuste de `ExecutionPolicy` o alternativa de ejecución directa |
| 3 | Diccionario OWL vacío | Supuesto de `NamedIndividual` en lugar de jerarquías de clases | Extracción por `rdfs:subClassOf` recursiva |
| 4 | Categorías incompletas | Mapeo hardcodeado de raíces OWL | Descubrimiento dinámico de clases raíz |
| 5 | Cuarentena no salta cuando debería | Ejemplos no ontológicos + validación parcial | Ejemplos corregidos + validación total + reservados |
| 6 | TypeError al construir prompt | Bloque de string mal concatenado (`tuple`) | Reescritura de literal como string único |
| 7 | Monolitos en `amount` | Falta de modelo de cálculo dinámico | `dynamic_amount_object` + reglas de activación |
| 8 | Falsos positivos por `COUNT/ANY/...` | Confusión entre sintaxis del motor y ontología | Reservados ampliados y sincronizados en prompt+Python |
| 9 | JSON truncado | Límite de tokens insuficiente con prompt largo | Aumento de `max_tokens` + warning por truncación |
| 10 | JSON inválido por trailing commas | Salida LLM no estrictamente JSON | Limpieza regex + reparse defensivo |

### 12. Conclusiones metodológicas (en clave de TFG)

1. **La calidad no salió de una sola gran decisión, sino de iteraciones cortas con feedback real.**  
   Cada regla nueva del prompt respondió a un error observado en ejecución, no a diseño especulativo.

2. **El contrato semántico real es triple:** ontología (qué existe), prompt (cómo se genera) y validador (qué se acepta).  
   Si una de esas tres capas queda desalineada, aparece inestabilidad.

3. **HITL no se usó como “parche manual”, sino como mecanismo de crecimiento controlado del vocabulario.**  
   La cuarentena permite expandir ontología sin perder trazabilidad ni rigor.

4. **El objetivo de escalabilidad obligó a prohibir modelados por carta.**  
   COMPARISON + dynamic_amount + restricciones atómicas sustituyen constantes ad-hoc y reducen deuda técnica futura.

5. **Los ejemplos mejoran mucho la adherencia, pero solo son seguros si van acompañados de anti-ejemplos y validación.**  
   Esa combinación minimiza sesgo de copia y maximiza consistencia estructural.

6. **Separar keywords de habilidades complejas simplifica el modelo y mejora ejecución.**  
   Cartas simples generan JSON más limpio y tokens complejos siguen siendo representables.

