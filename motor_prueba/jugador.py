import random
# jugador.py
from cartas import Land, Creature
from typing import Any, Optional

from logging_utils import NullLogger

class Player:
    def __init__(self, name, *, verbose: bool = True, logger: Optional[Any] = None):
        self.name = name
        self.life = 20
        self.mana_pool = 0
        self.deck = []
        self.hand = []
        self.battlefield = []
        self.graveyard = [] 
        self.exile = [] 
        self.lands_played_this_turn = 0
        self.verbose = verbose
        self.logger = logger or NullLogger()

    def draw_card(self, amount=1):
        for _ in range(amount):
            if len(self.deck) > 0:
                self.hand.append(self.deck.pop(0))
            else:
                if self.verbose:
                    print(f"{self.name} se ha quedado sin cartas. ¡Pierde la partida!")
                self.logger.log({"type": "deck_out", "player": self.name})
                self.life = 0

    # jugador.py (Añade o modifica estos métodos en tu clase Player)

    def count_available_mana(self):
        # Solo cuenta las tierras que están en el campo y NO están giradas
        return sum(1 for card in self.battlefield if isinstance(card, Land) and not card.is_tapped)

    def reset_for_new_turn(self):
        self.mana_pool = 0
        self.lands_played_this_turn = 0
        for card in self.battlefield:
            card.is_tapped = False # Endereza tanto tierras como criaturas
            if isinstance(card, Creature):
                card.summoning_sickness = False
                card.damage_taken = 0
    
    def choose_action(self, legal_actions):
        """
        Presenta las opciones al agente (humano o IA) y espera su decisión.
        """
        print(f"\nOpciones legales para {self.name}:")
        for i, action in enumerate(legal_actions):
            print(f"  [{i}] - {action['descripcion']}")
            
        while True:
            try:
                # Si es un humano, le pedimos que teclee un número
                eleccion = int(input("Elige una acción (introduce el número): "))
                if 0 <= eleccion < len(legal_actions):
                    return legal_actions[eleccion]
                else:
                    print("Número fuera de rango. Intenta de nuevo.")
            except ValueError:
                print("Por favor, introduce un número válido.")