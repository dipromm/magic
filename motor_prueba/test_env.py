# test_env.py
import random
from env_magic import MagicEnv

env = MagicEnv()
env.reset()

print("=== EMPEZANDO SIMULACIÓN PETTINGZOO ===")

for agent in env.agent_iter():
    observation, reward, termination, truncation, info = env.last()
    
    if termination or truncation:
        action = None
        env.step(action)
        continue
        
    # Extraemos cuántas opciones legales tiene la IA en este milisegundo
    opciones_legales = env.current_legal_actions
    num_opciones = len(opciones_legales)
    
    # La IA elige un número al azar
    action = random.randint(0, num_opciones - 1)
    
    # Extraemos el texto de la acción para que TÚ sepas qué ha hecho la IA
    descripcion_accion = opciones_legales[action]['descripcion']
    print(f"\n[{agent}] elige la opción {action}: {descripcion_accion}")
    
    # Ejecutamos el paso (aquí es donde saltará el mensaje de 🌟 si gana puntos)
    env.step(action)
    
    # Salida de emergencia si la partida acaba
    if any(env.terminations.values()):
        print("\n🏆 ¡Partida terminada!")
        
        # Mostramos los puntos finales (incluyendo el +10 por ganar)
        print(f"Puntos finales player_0: {env.rewards['player_0']}")
        print(f"Puntos finales player_1: {env.rewards['player_1']}")
        break