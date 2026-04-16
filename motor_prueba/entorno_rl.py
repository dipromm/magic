# entorno_rl.py
import numpy as np 
from cartas import Creature, Land

class ObservadorRL:
    def __init__(self, max_cartas_mano=10, max_cartas_campo=10):
        # Fijamos un límite para que el vector siempre mida lo mismo (requisito de las Redes Neuronales)
        self.max_cartas_mano = max_cartas_mano
        self.max_cartas_campo = max_cartas_campo

    def _codificar_carta(self, carta):
        """Convierte una carta en un mini-vector de 4 números."""
        if carta is None:
            # Si no hay carta en este "hueco", devolvemos todo ceros (padding)
            return [0.0, 0.0, 0.0, 0.0]
            
        if isinstance(carta, Land):
            # Formato Tierra: [Tipo(1=Tierra), Maná que da, Está girada, 0]
            girada = 1.0 if carta.is_tapped else 0.0
            return [1.0, float(carta.mana_produced), girada, 0.0]
            
        elif isinstance(carta, Creature):
            # Formato Criatura: [Tipo(2=Criatura), Fuerza, Resistencia, Está girada/Mareada]
            estado_negativo = 1.0 if (carta.is_tapped or carta.summoning_sickness) else 0.0
            return [2.0, float(carta.power), float(carta.toughness), estado_negativo]
            
        return [0.0, 0.0, 0.0, 0.0]

    def obtener_estado(self, motor, jugador_idx):
        """
        Lee el juego y devuelve un array de numpy (Tensor) con la perspectiva de 'jugador_idx'.
        """
        yo = motor.players[jugador_idx]
        rival = motor.players[1 - jugador_idx]

        # 1. ESTADO GLOBAL (4 números)
        # [Mi Vida, Mi Maná, Vida Rival, Maná Rival]
        estado_global = [
            float(yo.life), 
            float(yo.count_available_mana()),
            float(rival.life),
            float(rival.count_available_mana())
        ]

        # 2. MI MANO (Vectorizamos cada carta hasta el máximo permitido)
        mi_mano_vector = []
        for i in range(self.max_cartas_mano):
            if i < len(yo.hand):
                mi_mano_vector.extend(self._codificar_carta(yo.hand[i]))
            else:
                mi_mano_vector.extend(self._codificar_carta(None)) # Rellenamos con ceros

        # 3. MI CAMPO DE BATALLA
        mi_campo_vector = []
        for i in range(self.max_cartas_campo):
            if i < len(yo.battlefield):
                mi_campo_vector.extend(self._codificar_carta(yo.battlefield[i]))
            else:
                mi_campo_vector.extend(self._codificar_carta(None))

        # 4. CAMPO DE BATALLA RIVAL
        campo_rival_vector = []
        for i in range(self.max_cartas_campo):
            if i < len(rival.battlefield):
                campo_rival_vector.extend(self._codificar_carta(rival.battlefield[i]))
            else:
                campo_rival_vector.extend(self._codificar_carta(None))

        # Juntamos todas las listas en una sola lista gigante
        vector_final = estado_global + mi_mano_vector + mi_campo_vector + campo_rival_vector
        
        # Lo convertimos a un array de Numpy (el formato estándar para PyTorch)
        return np.array(vector_final, dtype=np.float32)