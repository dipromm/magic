# Visualización del entrenamiento y red neuronal (TFG)

Este directorio incluye utilidades para:

- Curvas del entrenamiento (reward, invalid actions ratio, longitud de episodio, winrate).
- Diagrama del grafo real de la red neuronal.

## 1) Requisitos de visualización

### TensorBoard (recomendado)
- `tensorboard` (suele venir como dependencia de Tianshou).

### Visualización de curvas
- `matplotlib` para generar PNG/SVG.

### Diagrama del grafo real (red neuronal)
- `torchviz`
- `graphviz` (binario del sistema) para poder renderizar PNG/SVG

Ejemplo de instalación (Python):
```powershell
pip install torchviz
```

Además, instala Graphviz en tu sistema (si `dot.render()` falla).

## 2) Cómo ejecutar visualizaciones

### Curvas
```powershell
python visualize_training.py --log-jsonl logs/<run>.jsonl --tb-dir logs/tb/<run> --metric reward --x time --out logs/plots/<run>_reward.png
```

Métricas disponibles:
- `reward`
- `invalid_ratio`
- `episode_length`
- `winrate`

Ejes disponibles:
- `time`
- `epoch`
- `env_step` (para `reward` y `invalid_ratio` cuando hay marcadores en el JSONL)

### Grafo de la red neuronal
```powershell
python visualize_network.py --which both --out-dir logs/net_graphs --device cpu --requires-grad
```

Si falta `torchviz` o Graphviz del sistema, el script mostrará un fallback textual en consola y/o guardará el código `.gv`.

