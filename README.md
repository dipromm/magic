# TFG - Entrenamiento RL para un motor simplificado de Magic

Este repositorio contiene un prototipo de investigacion para:

- Simular partidas 1v1 de Magic en un motor simplificado.
- Exponer ese motor como entorno RL (PettingZoo + Gymnasium).
- Entrenar agentes con PPO (Tianshou).
- Evaluar checkpoints y visualizar metricas de entrenamiento.
- Generar datasets auxiliares desde Archidekt / EDHREC.

---

## 1. Estructura del proyecto

- `motor_prueba/`: nucleo del sistema (motor, entorno RL, entrenamiento, evaluacion, visualizacion).
- `ingesta_datos/`: scripts para extraer datos de mazos/cartas desde APIs externas.
- `dataset_cartas/`: datasets locales pesados (ignorado por git).
- `IA_OWL/`: recursos semanticos (`OWL_tfg.rdf`).
- `IA_JSON/`, `cartas/`, `traduccion/`: carpetas reservadas para trabajo futuro.

---

## 2. Requisitos

## Software base

- Python 3.10+ (recomendado 3.11).
- PowerShell en Windows (los ejemplos usan PowerShell).

## Librerias Python

Dependencias principales detectadas en el codigo:

- `torch`
- `tianshou`
- `pettingzoo`
- `gymnasium`
- `numpy`
- `requests`
- `matplotlib`
- `tensorboard`
- `pyedhrec` (solo para script de prueba EDHREC)

Opcionales para grafo de red:

- `torchviz`
- `graphviz` (tambien requiere binario Graphviz instalado en Windows)

---

## 3. Preparacion del entorno (paso a paso)

Desde la raiz del proyecto (`c:\Proyectos\tfg`):

```powershell
# 1) Crear entorno virtual (si no existe)
python -m venv venv_tfg

# 2) Activar entorno virtual
.\venv_tfg\Scripts\Activate.ps1

# 3) Actualizar pip
python -m pip install --upgrade pip

# 4) Instalar dependencias principales
pip install torch tianshou pettingzoo gymnasium numpy requests matplotlib tensorboard pyedhrec

# 5) (Opcional) para visualizar grafo de red neuronal
pip install torchviz graphviz
```

Nota: si usas GPU CUDA, instala `torch` siguiendo la matriz oficial de PyTorch para tu version de CUDA.

---

## 4. Flujo recomendado de uso

Orden sugerido para trabajar de principio a fin:

1. (Opcional) Generar/actualizar dataset en `ingesta_datos/`.
2. Entrenar un modelo en `motor_prueba/train.py`.
3. Revisar logs y curvas (`analyze_logs.py`, `visualize_training.py`, TensorBoard).
4. Evaluar checkpoints entre si (`match_checkpoints.py`).
5. Jugar humano vs IA (`play_human_vs_ai.py`).
6. Visualizar arquitectura de la red (`visualize_network.py`).

Para evitar rutas inconsistentes en logs/checkpoints, ejecuta los scripts de `motor_prueba/` desde esa misma carpeta:

```powershell
Set-Location .\motor_prueba
```

---

## 5. Scripts de `ingesta_datos/`

Estos scripts sirven para construir o inspeccionar datos externos (Archidekt/EDHREC).

### 5.1 `test_archidekt.py`

Para que sirve:
- Descarga mazos de Archidekt para un comandante concreto.
- Filtra mazos para respetar singleton (con excepciones).
- Guarda un dataset final (`dataset_valgavoth_arquitectura_ia.json`).

Comando:

```powershell
python .\ingesta_datos\test_archidekt.py
```

Salida esperada:
- Archivo `dataset_valgavoth_arquitectura_ia.json` (en la carpeta donde ejecutes el script).

### 5.2 `fetch_archidekt_by_cardname.py`

Para que sirve:
- Busca cartas por nombre usando Archidekt (via mazos publicos).
- Guarda un JSON por carta y un indice consolidado.

Comando:

```powershell
python .\ingesta_datos\fetch_archidekt_by_cardname.py
```

Salida esperada:
- Carpeta `ingesta_datos\salida_archidekt_cartas\` con `*.json` y `_indice.json`.

### 5.3 `prueba.py`

Para que sirve:
- Descargar el JSON bruto de un mazo Archidekt por ID (debug/exploracion).

Comando:

```powershell
python .\ingesta_datos\prueba.py
```

Nota:
- Debes ajustar el valor `ID_DEL_MAZO` dentro del archivo antes de ejecutarlo.

### 5.4 `test_edhrec.py`

Para que sirve:
- Verificar conexion y formato de respuesta de EDHREC para un comandante.

Comando:

```powershell
python .\ingesta_datos\test_edhrec.py
```

---

## 6. Scripts de `motor_prueba/`

### 6.1 Entrenamiento principal - `train.py`

Para que sirve:
- Entrena una politica PPO multiagente sobre `MagicEnv`.
- Guarda logs JSONL + TensorBoard.
- Guarda checkpoint `.pth` (policy + critic).

Comando minimo:

```powershell
python .\train.py
```

Comando recomendado (controlado):

```powershell
python .\train.py `
  --run-name run_base `
  --max-epochs 30 `
  --epoch-steps 5000 `
  --test-episodes 10 `
  --reward-mode dense
```

Entrenar usando dataset externo:

```powershell
python .\train.py `
  --run-name run_dataset `
  --dataset-path ..\ingesta_datos\dataset_valgavoth_arquitectura_ia.json `
  --reward-mode curriculum `
  --curriculum-steps 150000
```

Opciones importantes:
- `--reward-mode`: `dense`, `sparse`, `curriculum`.
- `--disable-mask`: desactiva action masking.
- `--warm-start <ckpt>`: carga checkpoint previo para continuar.
- `--anchor-checkpoint <ckpt>`: entrena contra oponente congelado.
- `--checkpoint-dir`: carpeta de checkpoints de salida.

Archivos generados:
- `logs\<run>.jsonl`
- `logs\tb\<run>\` (TensorBoard)
- `modelos\*.pth` (o la carpeta indicada)
- `logs\runs_manifest.jsonl` (si no pasas `--no-manifest`)

### 6.2 Analisis rapido de logs - `analyze_logs.py`

Para que sirve:
- Calcula metricas basicas desde JSONL: pasos, reward sum, invalid actions.

Comando interactivo (elige log por menu):

```powershell
python .\analyze_logs.py
```

Comando directo:

```powershell
python .\analyze_logs.py .\logs\run_base.jsonl
```

### 6.3 Visualizacion de entrenamiento - `visualize_training.py`

Para que sirve:
- Genera graficas PNG de `reward`, `invalid_ratio`, `episode_length`, `winrate`.
- Puede usar JSONL y/o TensorBoard.
- Tiene modo dashboard y modo interactivo.

Comando rapido por run:

```powershell
python .\visualize_training.py run_base --metric reward --x time --out .\logs\plots\run_base_reward.png
```

Modo dashboard:

```powershell
python .\visualize_training.py run_base --dashboard --out .\logs\plots\run_base_dashboard.png
```

Modo asistido (sin argumentos):

```powershell
python .\visualize_training.py
```

### 6.4 Comparar checkpoints - `match_checkpoints.py`

Para que sirve:
- Ejecuta partidas IA vs IA entre dos checkpoints.
- Reporta victorias, empates y winrate.

Comando:

```powershell
python .\match_checkpoints.py `
  --p0-ckpt .\modelos\modelo_A.pth `
  --p1-ckpt .\modelos\modelo_B.pth `
  --games 50 `
  --swap
```

Notas:
- `--swap` alterna quien juega como `player_0` para reducir sesgo por asiento.
- Resultado detallado por consola + log en `logs\match_checkpoints.jsonl`.

### 6.5 Humano vs IA - `play_human_vs_ai.py`

Para que sirve:
- Jugar una partida por consola contra un checkpoint entrenado.
- El humano elige acciones por indice de accion legal.

Comando:

```powershell
python .\play_human_vs_ai.py `
  --ckpt .\modelos\modelo_A.pth `
  --human-seat p0 `
  --verbose-env
```

Opciones utiles:
- `--human-seat p0|p1`
- `--stochastic` (IA no greedy)
- `--quiet-ai` (no mostrar accion elegida por IA)
- `--dataset-path` para jugar sobre mazos de dataset

### 6.6 Visualizar red neuronal - `visualize_network.py`

Para que sirve:
- Exporta grafo del actor y/o critic segun la arquitectura usada en entrenamiento.

Comando:

```powershell
python .\visualize_network.py --which both --out-dir .\logs\net_graphs --device cpu --requires-grad
```

Salida esperada:
- PNGs (`actor_graph.png`, `critic_graph.png`) o `.gv` si falla render.

### 6.7 Scripts de prueba interna

- `test_env.py`: smoke test del entorno PettingZoo con acciones aleatorias.
- `main.py`: simulacion antigua IA-vs-IA con `RandomBot` (util para debug rapido).

---

## 7. Ejecutar TensorBoard

Desde la raiz del repo:

```powershell
tensorboard --logdir .\motor_prueba\logs\tb
```

Luego abre en navegador la URL que muestre la consola (normalmente `http://localhost:6006`).

---

## 8. Como funciona internamente (resumen tecnico)

- `motor_prueba\motor.py`:
  implementa reglas simplificadas de turnos/fases, acciones legales, combate y recompensa.
- `motor_prueba\entorno_rl.py`:
  adapta el motor a API AEC de PettingZoo, con action masking y logging por paso.
- `motor_prueba\entorno_rl.py` + `motor_prueba\env_magic.py`:
  codifican estado del juego como vector fijo de 124 valores (`ObservadorRL`).
- `motor_prueba\train.py`:
  construye actor/critic, entrena PPO, registra metricas y guarda checkpoints.
- `motor_prueba\train_modes.py`:
  utilidades para warm-start, checkpoint bundle policy+critic, curriculum y ancla congelada.
- `motor_prueba\deck_loader.py`:
  convierte dataset de Archidekt a mazos compatibles con el motor PoC.

---

## 9. Solucion de problemas

- Error de imports:
  confirma que el entorno virtual esta activado y dependencias instaladas.
- `torchviz`/`graphviz` falla:
  instala Graphviz del sistema y prueba de nuevo.
- No aparecen curvas:
  verifica que exista `motor_prueba\logs\<run>.jsonl` y/o `motor_prueba\logs\tb\<run>`.
- Checkpoint no carga:
  revisa ruta, formato `.pth` y compatibilidad de arquitectura.
- Ejecucion lenta:
  reduce `--max-epochs`, `--epoch-steps`, `--test-episodes` y evita `--verbose-env`.

---

## 10. Ejemplo rapido end-to-end

```powershell
# 1) Entrenar
Set-Location .\motor_prueba
python .\train.py --run-name demo_run --max-epochs 5 --epoch-steps 2000

# 2) Graficar reward
python .\visualize_training.py demo_run --metric reward --x time --out .\logs\plots\demo_run_reward.png

# 3) Jugar contra el checkpoint generado
python .\play_human_vs_ai.py --ckpt .\modelos\demo_run_policy.pth --human-seat p0
```

Si el nombre final del checkpoint cambia (por sufijos `_2`, `_3`, etc.), usa el nombre real impreso al final de `train.py`.
