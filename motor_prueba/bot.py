# bot.py
import random
from jugador import Player

class RandomBot(Player):
    def __init__(self, name="IA Bot Aleatorio"):
        super().__init__(name)
        
    def choose_action(self, legal_actions):
        """
        Elige una acción 100% al azar de la lista, sin preferencias.
        El verdadero punto de partida para una IA.
        """
        eleccion = random.choice(legal_actions)
            
        print(f"\n🤖 {self.name} decide: {eleccion['descripcion']}")
        return eleccion