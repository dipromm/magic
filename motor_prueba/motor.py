# motor.py
import random
from cartas import Land, Creature
from typing import Any, Optional

from logging_utils import NullLogger

class Game:
    def __init__(self, player1, player2, *, verbose: bool = True, logger: Optional[Any] = None):
        self.players = [player1, player2]
        self.current_turn = 0
        self.current_phase = "Principal" 
        self.atacantes_declarados = []   
        self.bloqueos_declarados = {}    # NUEVO: Diccionario para guardar {Bloqueador: Atacante}
        self.verbose = verbose
        self.logger = logger or NullLogger()
        self.dense_reward_scale = 1.0  # curriculum: 1=dense shaping pleno, 0=sin shaping
    
    def setup_game(self, *, deck_p0=None, deck_p1=None):
        decks = [deck_p0, deck_p1]
        for i, player in enumerate(self.players):
            if decks[i] is None:
                player.deck = [Land() for _ in range(20)] + [Creature("Oso", 2, 2, 2) for _ in range(20)]
            else:
                player.deck = list(decks[i])
            random.shuffle(player.deck)
            player.draw_card(7)

    def next_turn(self):
        self.current_turn = 1 - self.current_turn
        self.current_phase = "Principal" 
        self.atacantes_declarados = []   
        self.bloqueos_declarados = {}    # Limpiamos los bloqueos
        active_player = self.players[self.current_turn]
        active_player.reset_for_new_turn()
        active_player.draw_card()
        if self.verbose:
            print(f"\n--- Turno de {active_player.name} ---")
        self.logger.log(
            {
                "type": "turn",
                "env_id": getattr(self, "env_id", None),
                "current_turn": self.current_turn,
                "active_player": active_player.name,
            },
        )

    def resolve_combat(self, active_player, defending_player, attackers, blocks):
        if self.verbose:
            print("\n--- RESOLVIENDO COMBATE ---")
        for attacker in attackers:
            blocker = blocks.get(attacker) 
            
            if blocker:
                if self.verbose:
                    print(
                        f"Combate: {attacker.name} ({attacker.power}/{attacker.toughness}) vs {blocker.name} ({blocker.power}/{blocker.toughness})"
                    )
                attacker.damage_taken += blocker.power
                blocker.damage_taken += attacker.power
            else:
                if self.verbose:
                    print(f"¡{attacker.name} ataca directo! {defending_player.name} recibe {attacker.power} de daño.")
                defending_player.life -= attacker.power

        self._check_state_based_actions()

    def _check_state_based_actions(self):
        for player in self.players:
            survivors = []
            for card in player.battlefield:
                if isinstance(card, Creature) and card.damage_taken >= card.toughness:
                    # Evitamos emojis en consola (Windows cp1252).
                    if self.verbose:
                        print(f"Muere: {card.name} de {player.name} -> al cementerio.")
                    player.graveyard.append(card)
                else:
                    survivors.append(card)
            player.battlefield = survivors

    def get_legal_actions(self, player):
        legal_actions = []
        
        # --- FASE PRINCIPAL ---
        if self.current_phase == "Principal":
            legal_actions.append({"tipo": "pasar_fase", "descripcion": "Pasar a Declarar Atacantes"})
            
            available_mana = player.count_available_mana()
            for card in player.hand:
                if isinstance(card, Land) and player.lands_played_this_turn < 1:
                    legal_actions.append({"tipo": "jugar_carta", "carta_id": card.id, "descripcion": f"Jugar tierra: {card.name} [{card.id}]"})
                elif isinstance(card, Creature) and available_mana >= card.mana_cost:
                    legal_actions.append({"tipo": "jugar_carta", "carta_id": card.id, "descripcion": f"Jugar criatura: {card.name} (Coste: {card.mana_cost}) [{card.id}]"})
                    
        # --- FASE DE DECLARAR ATACANTES ---
        elif self.current_phase == "Declarar Atacantes":
            legal_actions.append({"tipo": "finalizar_ataques", "descripcion": "Confirmar ataques"})
            
            for card in player.battlefield:
                if isinstance(card, Creature) and not card.is_tapped and not card.summoning_sickness:
                    if card not in self.atacantes_declarados:
                        legal_actions.append({"tipo": "declarar_atacante", "carta_id": card.id, "descripcion": f"Atacar con: {card.name} ({card.power}/{card.toughness})"})
                        
        # --- FASE DE DECLARAR BLOQUEADORES (Solo para el defensor) ---
        elif self.current_phase == "Declarar Bloqueadores":
            legal_actions.append({"tipo": "resolver_combate", "descripcion": "Confirmar bloqueos y resolver daño"})
            
            # Buscamos criaturas del defensor que puedan bloquear
            for blocker in player.battlefield:
                if isinstance(blocker, Creature) and not blocker.is_tapped and blocker not in self.bloqueos_declarados:
                    # Le damos la opción de bloquear a cada uno de los atacantes declarados
                    for attacker in self.atacantes_declarados:
                        # Regla 1vs1: Comprobamos que el atacante no esté ya bloqueado por otra criatura
                        if attacker not in self.bloqueos_declarados.values():
                            legal_actions.append({
                                "tipo": "declarar_bloqueo", 
                                "blocker_id": blocker.id, 
                                "attacker_id": attacker.id,
                                "descripcion": f"Bloquear a {attacker.name} con {blocker.name} ({blocker.power}/{blocker.toughness})"
                            })
                
        return legal_actions

    def _dense_shaping(self, amount: float) -> float:
        """Shaping intermedio: 0 en sparse; en dense/curriculum escala por dense_reward_scale."""
        mode = getattr(self, "reward_mode", "dense")
        if mode == "sparse":
            return 0.0
        scale = float(getattr(self, "dense_reward_scale", 1.0))
        return scale * amount

    def execute_action(self, player, action):
        recompensa = 0.0 # Empezamos el turno ganando 0 puntos
        turno_terminado = False

        if action["tipo"] == "jugar_carta":
            exito = self.play_card(player, action["carta_id"])
            if exito:
                # Premiamos por usar cartas (bajar tierras o criaturas)
                recompensa += self._dense_shaping(0.1)
            
        elif action["tipo"] == "pasar_fase":
            self.current_phase = "Declarar Atacantes"
            if self.verbose:
                print("\n>>> Fase de Declarar Atacantes <<<")
            
        elif action["tipo"] == "declarar_atacante":
            card = next((c for c in player.battlefield if c.id == action["carta_id"]), None)
            if card:
                card.is_tapped = True
                self.atacantes_declarados.append(card)
                # Evitamos emojis en consola (Windows cp1252 puede romperse).
                if self.verbose:
                    print(f"\nAtaque: {player.name} declara como atacante a {card.name}.")
                # Premiamos la agresividad: atacar es bueno
                recompensa += self._dense_shaping(0.2)
            
        elif action["tipo"] == "finalizar_ataques":
            if len(self.atacantes_declarados) > 0:
                self.current_phase = "Declarar Bloqueadores"
                if self.verbose:
                    print("\n>>> Fase de Declarar Bloqueadores <<<")
            else:
                if self.verbose:
                    print("\nNo hay ataques. Fin del turno.")
                self.next_turn()
                turno_terminado = True

        elif action["tipo"] == "declarar_bloqueo":
            blocker = next((c for c in player.battlefield if c.id == action["blocker_id"]), None)
            attacker = next((c for c in self.players[self.current_turn].battlefield if c.id == action["attacker_id"]), None)
            
            if blocker and attacker:
                self.bloqueos_declarados[blocker] = attacker
                if self.verbose:
                    print(f"\nDefensa: {player.name} usará {blocker.name} para bloquear a {attacker.name}.")
                # Premiamos por defenderse y no comerse el daño directo
                recompensa += self._dense_shaping(0.2)

        elif action["tipo"] == "resolver_combate":
            blocks_format = {attacker: blocker for blocker, attacker in self.bloqueos_declarados.items()}
            defending_player = self.players[1 - self.current_turn]
            active_player = self.players[self.current_turn]
            
            # Guardamos la vida del defensor ANTES del combate
            vida_previa = defending_player.life
            
            self.resolve_combat(active_player, defending_player, self.atacantes_declarados, blocks_format)
            
            # Calculamos el daño real que ha pasado a la cabeza del rival
            daño_causado = vida_previa - defending_player.life
            if daño_causado > 0:
                recompensa += self._dense_shaping(daño_causado * 0.5)
                
            self.next_turn()
            turno_terminado = True

        return recompensa, turno_terminado
    
    def play_card(self, player, card_id):
        card = next((c for c in player.hand if c.id == card_id), None)
        if not card: return False 
        
        if isinstance(card, Land):
            player.hand.remove(card) 
            player.battlefield.append(card) 
            player.lands_played_this_turn += 1
            if self.verbose:
                print(f"\n-> {player.name} baja una {card.name}.")
            
        elif isinstance(card, Creature):
            mana_to_pay = card.mana_cost
            for field_card in player.battlefield:
                if isinstance(field_card, Land) and not field_card.is_tapped:
                    field_card.is_tapped = True 
                    mana_to_pay -= 1
                    if mana_to_pay == 0:
                        break
            
            player.hand.remove(card)
            player.battlefield.append(card)
            if self.verbose:
                print(f"\n-> {player.name} lanza {card.name} girando {card.mana_cost} tierras.")