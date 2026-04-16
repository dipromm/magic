# cartas.py
import uuid

class Card:
    def __init__(self, name, card_type):
        # Generamos un ID único y corto para esta instancia específica de la carta
        self.id = uuid.uuid4().hex[:6] 
        self.name = name
        self.card_type = card_type

class Land(Card):
    def __init__(self, name="Tierra Basica"):
        super().__init__(name, "Land")
        self.mana_produced = 1
        self.is_tapped = False # NUEVO: Las tierras también se giran al dar maná

class Creature(Card):
    def __init__(self, name, mana_cost, power, toughness):
        super().__init__(name, "Creature")
        self.mana_cost = mana_cost
        self.power = power
        self.toughness = toughness
        self.is_tapped = False
        self.summoning_sickness = True
        self.damage_taken = 0