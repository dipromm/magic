# main.py
import time
from motor import Game
from bot import RandomBot

from entorno_rl import ObservadorRL
import numpy as np

# En vez de Player, usamos RandomBot
p1 = RandomBot("Bot Rojo")
p2 = RandomBot("Bot Azul")
motor = Game(p1, p2)
motor.setup_game()

obs = ObservadorRL()
vision_bot_rojo = obs.obtener_estado(motor, 0) # 0 es el índice del Bot Rojo

print(f"La IA 've' la partida como un vector de {len(vision_bot_rojo)} números:")
print(vision_bot_rojo)

print("=== INICIANDO SIMULACIÓN DE IA vs IA ===")
motor.players[motor.current_turn].draw_card() 

game_over = False
turnos_jugados = 0

# Empezamos a medir el tiempo
start_time = time.time()

while not game_over:
    active_player = motor.players[motor.current_turn]
    defending_player = motor.players[1 - motor.current_turn]
    
    if active_player.life <= 0 or defending_player.life <= 0:
        game_over = True
        ganador = active_player if defending_player.life <= 0 else defending_player
        print(f"\n🏆 ¡FIN DE LA PARTIDA en {turnos_jugados} iteraciones!")
        print(f"Ganador: {ganador.name} (Vidas restantes: {ganador.life})")
        break

    # Evitamos bucles infinitos si los bots se atascan pasando el turno eternamente
    turnos_jugados += 1
    if turnos_jugados > 1000:
        print("\nEmpate técnico: Límite de turnos alcanzado.")
        break

    if motor.current_phase == "Declarar Bloqueadores":
        decision_maker = defending_player
    else:
        decision_maker = active_player
    
    opciones = motor.get_legal_actions(decision_maker)
    decision = decision_maker.choose_action(opciones)
    recompensa_obtenida, turno_terminado = motor.execute_action(decision_maker, decision)
    
    # Opcional: Imprimir la recompensa para ver cómo aprende
    if recompensa_obtenida > 0:
        print(f"🌟 ¡{decision_maker.name} gana +{recompensa_obtenida} puntos de recompensa!")
    
    # OPCIONAL: Descomenta esta línea si quieres que la partida vaya lento para poder leerla
    # time.sleep(0.5) 

end_time = time.time()
print(f"Tiempo de simulación: {end_time - start_time:.4f} segundos")
