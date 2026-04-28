# MEMORIA DEL PROYECTO

---

# MEMORIA 1 — Diseño e Implementación del Entorno de Simulación (Prueba de Concepto)

## 1. Contexto y objetivo de la iteración

La primera fase de desarrollo del proyecto se centró en validar la viabilidad del pipeline completo de Aprendizaje por Refuerzo (RL) aplicado a la simulación de partidas de Magic: The Gathering. El planteamiento del proyecto contemplaba un motor de reglas completo capaz de gestionar la totalidad del reglamento, pero implementar directamente dicha complejidad habría supuesto un riesgo arquitectónico elevado: si la red neuronal no aprendía, habría resultado difícil aislar si el fallo procedía del modelo de RL o de un error lógico en el motor de reglas.

Por este motivo se adoptó la estrategia de construir primero una **Prueba de Concepto (PoC)** o Motor Reducido (`motor_prueba/`), cuyo objetivo no era simular partidas perfectas, sino validar la integridad de la comunicación entre la lógica orientada a objetos del motor y el framework de entrenamiento Tianshou. Las reglas se simplificaron a interacciones mecánicas fundamentales — robar cartas, jugar tierras, invocar criaturas y resolver fases de combate directas — y el esfuerzo de esta iteración se concentró en cinco ejes: el diseño del espacio de observación y acción, la ingeniería de la señal de recompensa, la infraestructura de métricas y trazabilidad, la adaptación del pipeline de entrenamiento, y las herramientas de visualización.

## 2. Espacio de observación y enmascaramiento de acciones

### 2.1. Observación numérica como vector de estado

Para que un agente de RL pueda operar sobre el estado del juego, es necesario traducir la información del tablero a una representación tensorial estandarizada. Se diseñó un vector unidimensional de **124 valores flotantes** que codifica, de forma normalizada, las variables críticas del estado en un instante de tiempo *t*: vidas de ambos jugadores, reserva de maná, cartas en mano (hasta 10 slots × 4 atributos), criaturas propias en el campo de batalla (hasta 10 slots × 4 atributos) y criaturas del oponente (misma estructura). Esta codificación se construye mediante la clase `ObservadorRL` (`entorno_rl.py`) y se expone como un `gymnasium.spaces.Box` de forma `(124,)`.

### 2.2. Máscara de acciones (Action Masking)

En juegos combinatorios con un elevado número de acciones ilegales por estado, permitir que la red neuronal explore acciones inválidas resulta en un desperdicio masivo de capacidad de aprendizaje. El entorno `MagicEnv` implementa un sistema de **enmascaramiento real**: en cada transición de prioridad, el motor evalúa el estado y genera un vector binario de tamaño 50 (correspondiente al espacio de acción `Discrete(50)`) que marca con un 1 las acciones legales y con un 0 las ilegales. Este vector se devuelve como parte de la observación dentro de un `gymnasium.spaces.Dict`:

```python
observation_space = Dict({
    "observation": Box(low=-inf, high=inf, shape=(124,), dtype=float32),
    "action_mask": Box(low=0, high=1, shape=(50,), dtype=int8),
})
```

Para que esta máscara sea efectiva de extremo a extremo, no basta con generarla en el entorno: debe llegar hasta la decisión final del actor. Se implementó `MaskedProbabilisticActorPolicy`, una subclase de la política probabilística de Tianshou que multiplica las probabilidades de salida del actor por `batch.obs.mask`, renormalizando la distribución resultante. Si todas las acciones quedan enmascaradas (caso degenerado), la política recurre a una distribución uniforme como fallback. El efecto es que las acciones ilegales tienen probabilidad estrictamente cero de ser seleccionadas, concentrando la exploración exclusivamente en el subconjunto de jugadas válidas.

### 2.3. Métrica de acciones inválidas

Como complemento cuantitativo al enmascaramiento, se incorporó un contador de acciones inválidas por agente (`invalid_action_count` en `env_magic.py`). Cada intento de ejecutar una acción fuera del rango legal se registra en el diccionario `info` del paso y se propaga al log JSONL. Esta métrica permite medir empíricamente el impacto del action masking: en entrenamientos sin máscara, la ratio de acciones inválidas es significativamente superior, lo que se traduce directamente en ineficiencia del aprendizaje. La inclusión de esta métrica resulta especialmente relevante para la discusión experimental del proyecto, ya que ofrece evidencia numérica del valor ingenieril de la decisión de enmascaramiento.

## 3. Ingeniería de recompensa: señal densa frente a señal escasa

El diseño de la función de recompensa constituye una de las decisiones más determinantes en cualquier sistema de RL. En el contexto de juegos por turnos con victoria terminal, existen dos paradigmas fundamentales:

- **Recompensa escasa (*sparse*):** la señal se concentra casi exclusivamente en el resultado final de la partida (+10 por victoria, −10 por derrota). Este enfoque garantiza la alineación entre la señal de aprendizaje y el objetivo real (ganar), pero proporciona muy poca información intermedia al agente, lo que puede ralentizar considerablemente la convergencia.

- **Recompensa densa (*dense*):** se inyectan pequeñas recompensas intermedias por acciones que, heurísticamente, se consideran deseables: +0.1 por jugar una carta con éxito, +0.2 por declarar un atacante o un bloqueador, y una fracción proporcional al daño infligido durante el combate (+0.5 × daño_causado). Estas micro-recompensas proporcionan gradientes de aprendizaje más ricos, pero introducen el riesgo de desalineación: el agente puede optimizar la acumulación de recompensa intermedia sin que ello se traduzca en una mayor probabilidad de victoria.

Se implementó el parámetro `reward_mode` en la clase `Game` (`motor.py`), configurable mediante la CLI de entrenamiento (`--reward-mode`), con la posibilidad de escalar las recompensas densas mediante `dense_reward_scale`. Las recompensas terminales (+10/−10) se aplican siempre, independientemente del modo seleccionado. Esta parametrización permite realizar experimentos controlados comparando ambos paradigmas bajo condiciones idénticas, una línea experimental que resultó central en iteraciones posteriores.

## 4. Infraestructura de métricas y trazabilidad

### 4.1. Logger JSONL estructurado

Se diseñó un sistema de logging basado en ficheros JSONL (JSON Lines), donde cada línea constituye un evento autónomo con tipo, timestamp y payload. La implementación reside en `logging_utils.py`, que expone una factoría `make_logger()` y dos clases: `JsonlLogger` (escritura incremental a disco) y `NullLogger` (no-op para entornos de test).

El log registra eventos a múltiples granularidades: pasos individuales (`step`), transiciones de turno, y — de especial importancia — eventos de **fin de episodio** (`episode_end`). Estos últimos contienen el ganador (`winner`), un indicador booleano de victoria del asiento 0 (`win0`), la longitud del episodio en pasos (`episode_len_steps`) y, en iteraciones posteriores, información adicional como vidas finales y estado de truncación.

### 4.2. Marcadores de época

Para poder agrupar métricas derivadas del JSONL por unidades de entrenamiento, se incorporaron eventos `epoch_start` y `epoch_end` mediante callbacks de Tianshou (`training_fn`, `test_fn`). Esto permite calcular, por ejemplo, la ratio de acciones inválidas por época o el winrate agregado de un bloque de episodios de test.

### 4.3. Control de verbosidad

El motor original producía abundante salida por consola durante la ejecución de partidas, lo cual degradaba significativamente el rendimiento del entrenamiento cuando se instanciaban múltiples entornos en paralelo. Se añadió un flag `verbose=False` (propagado a `motor.py`, `jugador.py` y `env_magic.py`) que silencia la traza textual sin suprimir el registro estructurado del JSONL. Esta separación entre traza de depuración y registro analítico es una práctica estándar en ingeniería de software que resulta especialmente relevante en pipelines de experimentación.

## 5. Adaptación del pipeline de entrenamiento a entornos multi-agente

### 5.1. Extractor de observaciones (ObsExtractorNet)

Tianshou gestiona entornos multi-agente (PettingZoo) empaquetando las observaciones en un objeto `Batch` que incluye metadatos como el identificador del agente (`agent_id`, de tipo string). Si la red neuronal recibe directamente este objeto, se produce un error al intentar convertir un string a tensor. Se implementó `ObsExtractorNet`, un wrapper de `torch.nn.Module` que intercepta la observación estructurada y extrae exclusivamente el sub-tensor numérico antes de pasarlo a la red backbone (`Net`).

### 5.2. Arquitectura de la red

La red neuronal se construye con un backbone `Net(state_shape=124, hidden_sizes=[128, 128])` que alimenta dos cabezas independientes: un actor discreto (`DiscreteActor`, 50 acciones de salida) y un crítico escalar (`DiscreteCritic`). El actor produce una distribución categórica sobre las acciones, modificada por la máscara antes del muestreo; el crítico estima la función de valor para el cálculo de las ventajas en PPO.

### 5.3. Parametrización por CLI

Se diseñó `train.py` como un script completamente parametrizable por línea de comandos, exponiendo flags para: nombre del run (`--run-name`), número de épocas (`--max-epochs`), pasos por época (`--epoch-steps`), número de entornos de entrenamiento y test (`--train-envs`, `--test-envs`), modo de recompensa (`--reward-mode`), desactivación de la máscara (`--disable-mask`), semilla (`--seed`), y rutas de datos y checkpoints. Esta parametrización permite ejecutar experimentos controlados y reproducibles sin modificar código, un requisito fundamental tanto para la metodología experimental del proyecto como para la trazabilidad de resultados.

### 5.4. Integración con TensorBoard

Se incorporó `TensorboardLogger` de Tianshou para que cada run de entrenamiento escriba escalares (recompensa de test, pérdida de política, entropía, etc.) en `logs/tb/<run_name>/`. Esta integración proporciona visualización en tiempo real del progreso del entrenamiento a través de la interfaz web de TensorBoard y complementa el registro JSONL con métricas agregadas por Tianshou.

## 6. Integración con datos reales (Archidekt)

Para demostrar la escalabilidad del PoC hacia datos reales sin exigir soporte completo de todas las reglas, se construyó un módulo de carga de mazos (`deck_loader.py`) que consume los datasets JSON obtenidos de Archidekt (generados por el módulo `ingesta_datos/`) y genera mazos compatibles con el motor PoC. El loader filtra las cartas del dataset y construye objetos `Land` y `Creature` reconocibles por el motor, descartando tipos de carta no soportados en esta fase (artefactos, encantamientos, instantáneos, conjuros). El entorno `MagicEnv` acepta un parámetro `dataset_path` que, cuando se proporciona, sustituye los mazos generados aleatoriamente por mazos construidos a partir de datos reales de jugadores humanos.

## 7. Herramientas de visualización

### 7.1. Graficador de métricas de entrenamiento

Se desarrolló `visualize_training.py`, una herramienta que genera gráficas PNG a partir de los datos de entrenamiento. La herramienta soporta cuatro métricas principales: **reward** (desde TensorBoard o JSONL), **invalid_ratio** (calculada desde el JSONL), **episode_length** y **winrate** (ambas derivadas de los eventos `episode_end` del JSONL). Dispone de un modo interactivo (selección de log, métrica, eje temporal, formato de salida y suavizado) y un modo CLI completo para automatización.

### 7.2. Grafo de la arquitectura de red

Se implementó `visualize_network.py`, que genera un diagrama del grafo computacional del actor y/o el crítico utilizando `torchviz` y Graphviz. Cuando estas dependencias no están disponibles en el sistema, la herramienta produce un fallback textual o un fichero `.gv` editable. La documentación de uso se recogió en `README_viz.md`.

## 8. Resultado de la iteración

El resultado de esta primera iteración fue un pipeline de RL completo y funcional: un entorno PettingZoo con observación tensorial, enmascaramiento de acciones, señal de recompensa configurable, infraestructura de logging dual (JSONL + TensorBoard), integración con datos reales y herramientas de visualización. Este PoC validó la integridad de la comunicación entre el motor OOP y el framework de entrenamiento, sentando las bases estables necesarias para las iteraciones de consolidación y ampliación posteriores.

---

# MEMORIA 2 — Consolidación del Entrenamiento y Evaluación Experimental

## 1. Contexto y objetivo de la iteración

Con el pipeline PoC operativo, la segunda iteración se orientó hacia la consolidación del sistema de entrenamiento, la corrección de artefactos métricos que distorsionaban la interpretación de resultados, la ampliación del instrumental de evaluación y la obtención de los primeros hallazgos experimentales significativos. El objetivo no era añadir nuevas mecánicas de juego, sino endurecer la infraestructura existente hasta alcanzar un nivel de fiabilidad que permitiese extraer conclusiones válidas de los experimentos.

## 2. Refinamiento del entrenamiento (train.py)

### 2.1. Configuración del algoritmo PPO

El entrenamiento se consolidó sobre **PPO (Proximal Policy Optimization)** ejecutado a través de Tianshou 2.x, con el entorno multi-agente gestionado mediante `PettingZooEnv`. El wrapper encapsula `MagicEnv` (que hereda de `pettingzoo.AECEnv`) y gestiona la alternancia transparente de turno entre los dos agentes.

El action masking se mantuvo como componente opcional y desactivable (`--disable-mask`) para poder cuantificar experimentalmente su impacto. Cuando está activo, la política `MaskedProbabilisticActorPolicy` anula las probabilidades de las acciones ilegales y renormaliza la distribución categórica, garantizando que el agente solo explore jugadas válidas.

### 2.2. Logging duplicado y correcciones

Se consolidó la estrategia de **logging dual**:

- **JSONL por run** (`logs/<run>.jsonl`): registra eventos granulares — pasos, turnos, `episode_end` con longitud del episodio, ganador, `win0`, indicador de truncación, vidas finales y contadores de acciones inválidas.
- **TensorBoard** (`logs/tb/<run>/`): escalares de Tianshou (recompensa de test, pérdidas, entropía). Se resolvieron problemas de creación de carpetas y se implementó cierre explícito y flush del `SummaryWriter` para asegurar la presencia de ficheros de eventos válidos tras cada run.

### 2.3. Truncación de episodios

Se implementó el parámetro `--max-episode-steps` para forzar la finalización de partidas que excedieran un número máximo de pasos. Sin esta truncación, partidas en las que ningún agente lograba reducir la vida del oponente a cero podían prolongarse indefinidamente, impidiendo el registro de eventos `episode_end` y, por tanto, el cómputo de métricas por episodio (winrate, longitud media). Los episodios truncados se resuelven mediante una heurística de diferencia de vidas: el jugador con más vida se considera ganador; en caso de empate, el resultado se registra como empate. Se reconoce explícitamente que un episodio truncado no equivale a una "victoria limpia" y esta distinción se refleja en los logs.

### 2.4. Corrección del artefacto de recompensa en modo sparse

Se detectó y documentó un artefacto métrico significativo: con recompensa sparse y terminación de partida tipo +10/−10, la reducción por defecto de los retornos multi-agente (media aritmética entre ambos jugadores) producía un valor de test_reward ≈ 0, independientemente de la calidad del juego. Este resultado se debía a que la media de (+10, −10) es cero por construcción en un juego de suma cero a dos jugadores. La estadística de test se modificó para utilizar `mean(|retorno|)` por episodio, una medida interpretable que distingue entre "episodios sin señal terminal" y "episodios con resultado definido cuya media se cancela por simetría".

### 2.5. Filtrado de ruido en consola

Los mensajes de advertencia repetidos de Tianshou relativos a secuencias 2D se filtraron mediante la clase `_LineFilterStream`, evitando que la salida del entrenamiento quedase saturada y ocultase información relevante sobre el progreso.

## 3. Refinamiento del entorno

La clase `Game` (`motor.py`) se estabilizó con la lógica de recompensa densa parametrizada por `dense_reward_scale`: premios por jugar carta, declarar atacantes y bloqueadores, y una fracción proporcional al daño infligido. En modo sparse, el shaping se desactiva completamente (factor 0), manteniéndose únicamente la señal terminal.

El entorno `MagicEnv` (`env_magic.py`) se amplió para registrar `episode_end` con metadatos de truncación: cuando un episodio alcanza el límite de pasos sin muerte natural, el resultado se interpreta por diferencia de vidas o se registra como empate, lo que permite calcular métricas por episodio incluso en ausencia de un resultado terminal definitivo.

## 4. Herramientas de visualización y análisis consolidadas

### 4.1. Visualización de entrenamiento

`visualize_training.py` se amplió con las siguientes mejoras:

- **Selección de fuente de recompensa** (`--reward-source`): permite elegir explícitamente entre la curva de test de TensorBoard (limpia, agregada por Tianshou) y la recompensa por paso del JSONL (granular pero ruidosa). Esta distinción es crítica para no comparar gráficas que miden magnitudes diferentes.
- **Suavizado** (`--smooth`): media móvil configurable para estabilizar curvas escalonadas o con alta varianza, especialmente útil en modo sparse donde las recompensas se concentran en picos discretos.
- **Corrección de bug**: un `return` prematuro impedía guardar el PNG en ciertos modos de reward por epoch; se corrigió para garantizar la persistencia de todos los artefactos gráficos.
- **Modo interactivo y CLI**: selección de log, métrica, eje temporal y formato de salida; argumento posicional `run` para automatización.

### 4.2. Visualización de la arquitectura de red

`visualize_network.py` genera diagramas del actor (124 → 128 → 128 → 50 acciones, con softmax como parte de la política categórica bajo PPO) y del crítico (misma arquitectura con salida escalar). Se documentó explícitamente que la capa softmax del actor no es un "sustituto" del algoritmo PPO, sino el mecanismo estándar de conversión de logits a distribución categórica dentro de una política probabilística.

### 4.3. Análisis rápido de logs

`analyze_logs.py` proporciona análisis agregado sobre ficheros JSONL existentes: estadísticas de pasos, distribución de recompensas y contadores de acciones inválidas por run.

## 5. Evaluación entre checkpoints (match_checkpoints.py)

Para complementar el entrenamiento con evaluación empírica directa, se desarrolló `match_checkpoints.py`, una herramienta que enfrenta dos políticas guardadas (ficheros `.pth`) en partidas sin entrenamiento. El script acepta argumentos `--p0-ckpt` y `--p1-ckpt` para especificar las políticas de cada asiento, y ejecuta un bucle de partidas sobre `MagicEnv` nativo (sin el wrapper de Tianshou, que presentaba problemas de API con agentes terminados y llamadas `step(None)` en el loop de juego).

La opción `--swap` permite alternar qué checkpoint juega como Player 0 y cuál como Player 1 entre lotes de partidas, mitigando el sesgo de ventaja de turno. Al finalizar, el script genera un resumen con victorias de cada política, empates, truncados y winrate desglosado por asiento.

## 6. Decisiones de diseño adoptadas

| Decisión | Justificación |
|----------|---------------|
| **Truncación de episodio** | Priorizar la disponibilidad de métricas por episodio (winrate, longitud) frente a partidas que nunca terminan; aceptar la imprecisión de los resultados truncados a cambio de datos analizables. |
| **Winrate en self-play** | Medir victorias de `player_0`; en self-play simétrico el valor oscila alrededor del 50% salvo sesgos, lo que lo hace útil para detectar asimetrías pero insuficiente para afirmar "mejora absoluta" sin un baseline externo. |
| **Dos fuentes de reward en gráficos** | TensorBoard (test, agregada por Tianshou) y JSONL (paso a paso) miden magnitudes distintas; hacer explícita la elección de fuente para no comparar curvas incomparables. |
| **Dense vs sparse como eje experimental** | Diseñar ambos modos como variable controlada para un estudio ablativo formal, no como decisión fija. |
| **Evaluación head-to-head** | Complementar el self-play con duelos entre políticas distintas (dense vs sparse) para responder directamente a "¿cuál juega mejor en este motor?". |

## 7. Hallazgos experimentales y lecciones aprendidas

Los primeros resultados experimentales revelaron dinámicas que, si bien no eran inesperadas desde la perspectiva teórica del RL, resultaron valiosas para calibrar el diseño del proyecto:

1. **Plateau de longitud de episodio con recompensa densa.** En self-play, la longitud media de los episodios tendió a estabilizarse cerca del máximo permitido por la truncación. Este comportamiento es compatible con un estancamiento por intercambio simétrico de micro-recompensas (ambos agentes aprenden a atacar y bloquear, compensándose mutuamente) y no implica necesariamente que el agente "no aprenda".

2. **Desalineación de la recompensa densa.** Los incentivos intermedios por atacar, bloquear y jugar cartas se pagan mutuamente en combates simétricos, de modo que la señal densa puede optimizarse sin que ello se traduzca en mayor probabilidad de victoria. Este hallazgo subraya el riesgo clásico del *reward shaping* en entornos competitivos.

3. **Artefacto de agregación en sparse.** El valor `test_reward ≈ 0` observado en modo sparse con reducción `mean(agentes)` no implicaba episodios sin recompensa terminal, sino que era un artefacto de la media aritmética en un juego de suma cero a dos jugadores. Este hallazgo motivó la corrección descrita en §2.4.

4. **Resultados del duelo dense vs sparse.** El enfrentamiento directo entre checkpoints arrojó una victoria clara de la política sparse (36 victorias frente a 14 de dense sobre 50 partidas, sin truncados, con swap). Este resultado constituye evidencia empírica de que, en el setup del PoC y con las condiciones experimentales descritas (misma máscara, mismos límites de pasos, misma duración de entrenamiento), la política entrenada con señal terminal resultó más competitiva que la entrenada con shaping denso. Se matiza que la generalización de este resultado requeriría variación de semillas, duraciones de entreno y arquitecturas.

## 8. Resultado de la iteración

Esta iteración consolidó el pipeline de entrenamiento hasta un nivel de fiabilidad apto para experimentación formal: métricas corregidas y sin artefactos de agregación, logging robusto, herramientas de evaluación directa entre políticas, y primeros hallazgos experimentales documentados. El sistema quedó preparado para la introducción de modos avanzados de entrenamiento y la ampliación del protocolo experimental.

---

# MEMORIA 3 — Reproducibilidad, Hiperparámetros y Modos Avanzados de Entrenamiento

## 1. Contexto y objetivo de la iteración

Además del motor de juego y del entorno PettingZoo, esta iteración reforzó el ciclo completo de experimentación → registro → análisis. Los objetivos fueron: guardar modelos de forma reproducible y no destructiva, documentar cada run con metadatos recuperables, exponer los hiperparámetros de PPO sin modificar código, introducir modos de señal de recompensa transicionales (curriculum), diseñar escenarios de entrenamiento alternativos al self-play (warm-start y oponente fijo), y mejorar la interpretación de métricas derivadas del JSONL.

## 2. Checkpoints: formato, unicidad y compatibilidad

### 2.1. Problema del formato anterior

El sistema original dependía de un único fichero `.pth` que almacenaba exclusivamente el `state_dict` del actor y se sobrescribía en cada entrenamiento. Este enfoque presentaba dos carencias: la pérdida del estado del crítico (necesario para reanudar un entrenamiento de forma coherente, ya que PPO utiliza las estimaciones del crítico para calcular las ventajas) y la destrucción de checkpoints anteriores al guardar uno nuevo.

### 2.2. Checkpoint como bundle

Se diseñó un nuevo formato de checkpoint (`train_modes.py`) que encapsula en un único fichero `.pth` un diccionario con tres campos:

- `format`: cadena de versión (`magic_policy_critic_v1`) que permite detectar el formato durante la carga.
- `policy`: `state_dict` completo del actor.
- `critic`: `state_dict` completo del crítico.

La función `load_magic_checkpoint` mantiene compatibilidad hacia atrás: si el fichero no contiene un bundle (es decir, es un `state_dict` plano del actor generado por versiones anteriores), se restaura únicamente la policy y se informa de que el crítico no pudo cargarse. Para scripts de inferencia o juego humano, `load_policy_weights_for_inference` extrae solo el actor desde bundle o `.pth` plano indistintamente.

### 2.3. Ruta única y no destructiva

Se implementó una función `_unique_checkpoint_path` que genera rutas bajo el directorio `--checkpoint-dir` (por defecto `modelos/`) con el patrón `{run}_policy.pth`. Si el fichero ya existe, se añaden sufijos numéricos (`_2`, `_3`, etc.) para no sobrescribir entrenamientos anteriores. Esta decisión garantiza que ningún experimento previo se pierda por una ejecución accidental con el mismo nombre de run.

## 3. Manifiesto de runs (logs/runs_manifest.jsonl)

Cada entrenamiento completado añade automáticamente una línea JSON al fichero `logs/runs_manifest.jsonl` con la totalidad de los hiperparámetros y rutas del run: checkpoint generado, ficheros de log (JSONL y TensorBoard), dataset utilizado, semilla, número de épocas, entornos paralelos, estado del action masking, y configuración de PPO (learning rate, epsilon de clip, coeficiente de entropía, gamma). En modos avanzados, el manifiesto incluye adicionalmente `curriculum_steps`, `warm_start`, `anchor_checkpoint`, `anchor_seat` y `anchor_swap_epochs`.

El flag `--no-manifest` desactiva la escritura del manifiesto para runs de prueba o depuración que no se desean registrar. Este mecanismo constituye un sistema de experiment tracking ligero, sin base de datos ni infraestructura externa, adecuado para el alcance de un TFG.

## 4. Hiperparámetros de PPO configurables por CLI

Se expusieron por línea de comandos los hiperparámetros que anteriormente estaban fijados en código, manteniendo los mismos valores por defecto para no alterar el comportamiento de experimentos anteriores:

| Parámetro | Flag | Default | Función |
|-----------|------|---------|---------|
| Learning rate | `--lr` | 1e-3 | Tasa de aprendizaje del optimizador Adam |
| Epsilon de clip | `--eps-clip` | 0.2 | Radio de recorte de la ratio de probabilidades en PPO |
| Coeficiente de entropía | `--entropy-coef` | 0.01 | Peso del término de entropía en la función de pérdida; valores altos fomentan la exploración, mitigando políticas excesivamente deterministas |

En el caso del oponente ancla congelado (`FrozenPPO`), el coeficiente de entropía se fija en 0.0 y el learning rate del optimizador resulta irrelevante en la práctica, dado que `_update_with_batch` no aplica gradientes. La exposición de estos parámetros permite la realización de estudios ablativos sistemáticos (tablas de sensibilidad LR/clip/entropía) sin modificar ni una línea de código fuente.

## 5. Modos de recompensa: denso, disperso y curriculum

### 5.1. Modo curriculum

A los modos dense y sparse ya existentes se añadió un tercero: **curriculum**. En este modo, la escala del shaping denso se atenúa linealmente desde 1.0 hasta 0.0 conforme avanzan los pasos de entorno, controlado por el parámetro `--curriculum-steps`. La señal terminal (±10) se mantiene intacta durante todo el entrenamiento. El objetivo es proporcionar al agente un "andamiaje" inicial de recompensas intermedias que se desvanece progresivamente, forzando la transición hacia una optimización orientada exclusivamente al resultado terminal.

En cada `epoch_start` del JSONL se registra el valor actual de `dense_shaping_scale`, lo que permite reconstruir retrospectivamente la curva de decaimiento y correlacionarla con la evolución de otras métricas.

### 5.2. Estadística de test adaptada

Durante la fase "casi sparse" del curriculum (cuando `dense_shaping_scale < 0.5`) y en modo sparse puro, la estadística de test utiliza `mean(|return|)` por episodio entre agentes, evitando el artefacto de media ≈ 0 documentado en la MEMORIA 2, §2.4.

## 6. Modos de entrenamiento: warm-start y oponente ancla

### 6.1. Warm-start (--warm-start)

El modo warm-start permite inicializar los pesos de la red (policy + critic) desde un checkpoint previo y continuar con self-play habitual (dos copias del algoritmo aprendiendo simultáneamente mediante `MultiAgentOnPolicyAlgorithm`). Este modo permite partir de una política ya funcional y acortar el tiempo hasta la primera señal de aprendizaje significativa, sin alterar la dinámica simétrica del entrenamiento.

### 6.2. Oponente ancla (--anchor-checkpoint, FrozenPPO)

El modo ancla implementa un escenario de entrenamiento asimétrico: un agente entrena con PPO estándar (el "aprendiz") mientras el otro replica las forward passes pero no actualiza pesos (el "ancla"). La clase `FrozenPPO` hereda de `PPO` y sobrescribe `_update_with_batch` para que no aplique gradientes, manteniendo la política del ancla congelada durante todo el entrenamiento. Este modo permite evaluar el aprendizaje de un agente contra una línea base estable y conocida, complementando el self-play puro con un protocolo de evaluación más controlado.

### 6.3. Asiento de la ancla e intercambio periódico

El flag `--anchor-seat` (`p0`/`p1`) determina qué agente actúa como ancla al inicio del entrenamiento. Para reducir el sesgo por orden de turno, el flag `--anchor-swap-epochs` permite intercambiar periódicamente los roles de aprendiz y ancla entre asientos cada N transiciones de epoch. La función `swap_anchor_learner_seats` (`train_modes.py`) realiza el intercambio, y `flip_learner_plays_p0_all` actualiza el flag `learner_plays_p0` en todos los entornos para que el logging (p.ej. `win0`) siga refiriéndose consistentemente al aprendiz. Cada intercambio se registra como un evento `anchor_swap` en el JSONL.

### 6.4. Exclusión mutua

Los modos warm-start y ancla son mutuamente excluyentes: el parser de argumentos emite un error si se proporcionan ambos simultáneamente. Esta restricción refleja la diferencia conceptual entre "continuar una política existente en self-play" y "entrenar contra una referencia fija o semi-aleatoria mediante swaps".

## 7. Registro estructurado ampliado

### 7.1. Timestamps UTC

Todos los eventos JSONL incluyen un campo `ts` con timestamp en formato UTC, generado automáticamente por `append_jsonl`. Esto permite correlación temporal precisa entre eventos y análisis offline ordenado cronológicamente.

### 7.2. Logging de entropía PPO

Se implementó `_TensorboardLoggerWithJsonlEntropy`, una subclase de `TensorboardLogger` que, además de escribir en TensorBoard, emite líneas `ppo_entropy` en el JSONL por cada update del algoritmo, con `update_interval=1`. En runs cortos, Tianshou podía no registrar la entropía por su intervalo de logging por defecto; esta modificación garantiza que el dato esté disponible incluso en experimentos breves.

### 7.3. Metadatos de epoch enriquecidos

En modo ancla, el evento `epoch_start` incluye `learner_plays_p0` (booleano que indica qué asiento físico ocupa el aprendiz). En modo curriculum, incluye `dense_shaping_scale`. Estos metadatos permiten al analista reconstruir el contexto exacto de cada época durante el análisis post-entrenamiento.

## 8. Resultado de la iteración

El sistema alcanzó un nivel de madurez experimental equivalente al de un framework de experiment tracking ligero: reproducibilidad garantizada por checkpoints completos y manifiesto de runs, flexibilidad experimental por hiperparámetros expuestos y modos de entrenamiento diversificados, y trazabilidad enriquecida por metadatos contextuales en el logging. La infraestructura quedó preparada para soportar protocolos experimentales complejos (curriculum, ancla con swap, estudios ablativos) sin requerir modificaciones de código.

---

# MEMORIA 4 — Ampliación del Pipeline de Evaluación, Dashboard Integrado y Juego Interactivo

## 1. Contexto y objetivo de la iteración

Esta iteración completó el ciclo experimentación–evaluación–interpretación con tres líneas de trabajo: la implementación detallada de los modos avanzados diseñados en la MEMORIA 3 (con especial atención a la sincronía entre identidad de agente y asiento físico), la consolidación de las herramientas de visualización en un dashboard integrado, y la incorporación de un modo de juego humano contra la IA como validación cualitativa del comportamiento aprendido.

## 2. Implementación de checkpoints y modos avanzados

### 2.1. Checkpoints actor + crítico y directorio modelos/

El formato bundle diseñado en la MEMORIA 3 se implementó en `train_modes.py` con las funciones `save_magic_checkpoint` y `load_magic_checkpoint`. El directorio por defecto para los artefactos del run es `modelos/`, configurable mediante `--checkpoint-dir`. Tras cada entrenamiento exitoso, los metadatos del checkpoint se registran en `logs/runs_manifest.jsonl`. La compatibilidad con cargas antiguas (ficheros que contenían únicamente el `state_dict` del actor) se preservó: en carga, si no se detecta el bundle, solo se restaura la policy.

### 2.2. Warm-start

La implementación de `--warm-start` carga los pesos del checkpoint especificado en la misma arquitectura policy+critic tras construir ambas redes, y continúa con self-play habitual. Ambas copias del algoritmo en `MultiAgentOnPolicyAlgorithm` parten del mismo estado inicial, lo que permite evaluar la capacidad de la política de continuar mejorando desde un punto previamente alcanzado.

### 2.3. Modo ancla y FrozenPPO

`FrozenPPO` se implementó como subclase de `PPO` que sobrescribe `_update_with_batch` para devolver estadísticas de entrenamiento válidas (necesarias para que Tianshou no interrumpa el bucle) sin aplicar ningún gradiente. El optimizador asociado recibe un learning rate irrelevante, y el coeficiente de entropía se fija en 0.0 para que la política congelada sea completamente determinista (sujeta únicamente a la máscara de acciones).

El ensamblaje multi-agente asigna una instancia de `PPO` al aprendiz y una instancia de `FrozenPPO` al ancla, ambas dentro de `MultiAgentOnPolicyAlgorithm`. La asignación inicial de asientos se determina por `--anchor-seat`.

### 2.4. Sincronía entre identidad de agente y asiento físico

La gestión del intercambio periódico de asientos (`--anchor-swap-epochs`) requirió resolver un problema de coherencia en el logging: tras un swap, el agente que antes era P0 pasa a ser P1 y viceversa, pero las métricas registradas en `episode_end` (como `win0`) se refieren al asiento físico, no a la identidad lógica del agente. Para mantener la interpretabilidad, se implementaron las siguientes medidas:

- **Actualización de flag en entornos**: `flip_learner_plays_p0_all` propaga el estado de `learner_plays_p0` a todos los entornos vectorizados tras cada swap.
- **Metadatos por epoch**: el evento `epoch_start` en modo ancla registra `learner_plays_p0` para que el analista sepa, en cada momento, qué asiento físico ocupa el aprendiz.
- **Métricas por rol**: en `episode_end`, cuando el modo ancla está activo, se añaden campos `reward_learner`, `reward_anchor`, `win_learner` y `win_anchor`, independientes del asiento físico. Esto permite graficar el rendimiento del aprendiz frente al ancla sin confundir política con etiqueta P0/P1.

### 2.5. Curriculum dense → sparse

La transición lineal del shaping denso se implementó en `training_fn` de `train.py`: en cada transición de época, se calcula la escala densa como `max(0, 1 - collect_step / curriculum_steps)` y se propaga a todos los entornos mediante `apply_dense_reward_scale`. El motor (`motor.py`) aplica la escala multiplicando las recompensas intermedias por el factor recibido, mientras que la señal terminal permanece inalterada. En el JSONL, el evento `epoch_start` registra `dense_shaping_scale` para reconstrucción retrospectiva de la curva.

## 3. Dashboard integrado de visualización

### 3.1. Panel principal

`visualize_training.py` se amplió con un modo `--dashboard` que genera un panel multipanel con las métricas clave del run: reward, invalid_ratio, episode_length, winrate y, cuando aplica, la curva de evolución del curriculum (escala densa por época).

### 3.2. Corrección de artefactos en la curva de curriculum

Se detectó y corrigió un artefacto visual en la gráfica de curriculum: cuando un run se reanudaba, podían existir múltiples eventos `epoch_start` para una misma epoch, produciendo abscisas no monótonas que generaban un trazado en zigzag. La corrección (`_epoch_curriculum_series`) filtra los duplicados tomando el primer evento por epoch y ordena las abscisas antes de dibujar, sin alterar la lógica del motor de reglas ni los datos subyacentes.

### 3.3. Integración de TensorBoard en el dashboard

Se implementó resolución automática de la carpeta del run (subdirectorio que contiene ficheros `.tfevents`), una heurística de detección de tags de entropía (`ent_loss`), y manejo robusto ante la ausencia de datos: si no existen escalares de entropía para un run, se muestra un mensaje informativo en la figura y se listan los tags disponibles en consola. La función `_resolve_tensorboard_event_dir` gestiona las rutas típicas `logs/tb/<run>/` y variantes anidadas.

### 3.4. Filtrado por entorno (--episode-env)

Para métricas basadas en `episode_end` (winrate y longitud de episodio), se añadió el parámetro `--episode-env` con tres opciones:

- **`all`**: incluye todos los episodios, independientemente de su procedencia.
- **`train`**: filtra por `env_id < 1000` (entornos de recolección de experiencia).
- **`test`**: filtra por `env_id ≥ 1000` (entornos de evaluación).

Esta separación evita mezclar en una misma curva episodios de recolección (donde la política explora activamente) y episodios de evaluación (donde la política se ejecuta de forma más determinista), lo cual distorsionaría la interpretación de las métricas.

## 4. Juego humano vs IA (play_human_vs_ai.py)

Se desarrolló un modo de juego interactivo en consola que permite a un jugador humano enfrentarse a una política cargada desde un checkpoint `.pth`. El script reutiliza la infraestructura de carga de pesos (`load_policy_weights_for_inference`) y la construcción de política (`build_policy`) de `match_checkpoints.py`, y ejecuta un bucle de partida donde:

- En cada turno del humano, se muestran las acciones legales disponibles y se solicita una selección numérica.
- En cada turno de la IA, la política selecciona una acción según la distribución categórica (opcionalmente estocástica con `--stochastic` o determinista tomando el argmax).
- El estado del tablero se muestra de forma legible entre turnos.

Los flags configurables incluyen `--ckpt` (checkpoint a cargar), `--human-seat` (P0 o P1), `--quiet-ai` (silenciar la traza de decisión de la IA) y `--log-path` (guardar la partida en JSONL). Esta herramienta sirve como validación cualitativa del comportamiento aprendido por el agente y como demostración funcional del sistema completo, complementando las evaluaciones cuantitativas de `match_checkpoints.py` con la perspectiva subjetiva de un jugador humano.

## 5. Resultado de la iteración

Esta iteración cerró el bucle completo del pipeline de RL: desde la parametrización y ejecución del entrenamiento, pasando por el registro trazable de cada run, hasta la evaluación cuantitativa (duelos entre checkpoints con swap), la interpretación visual (dashboard multipanel con filtrado por entorno) y la validación cualitativa (juego humano interactivo). El sistema resultante constituye una plataforma experimental auto-contenida para el estudio de agentes de Aprendizaje por Refuerzo en entornos de juego por turnos.

---

# MEMORIA 5 — Evolución del módulo de Ingesta y Traducción Automática (Oracle Text -> JSON-LD)

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

