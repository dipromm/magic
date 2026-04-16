import requests
import json
import time

excepciones_singleton = [
    "Relentless Rats", "Shadowborn Apostle", "Rat Colony",
    "Persistent Petitioners", "Dragon's Approach", "Nazgûl",
    "Slime Against Humanity", "Hare Apparent", "Seven Dwarves",
    "Tempest Hawk", "Templar Knight", "Cid, Timeless Artificer"
]

tierras_basicas = [
    "Plains", "Island", "Swamp", "Mountain", "Forest",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp", 
    "Snow-Covered Mountain", "Snow-Covered Forest", "Wastes"
]

def construir_dataset():
    print("Iniciando Extracción Masiva Inteligente para Dataset de IA...")
    
    cabeceras = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Accept": "application/json"
    }
    
    mazos_validos = []
    limite_mazos_a_guardar = 20
    pagina = 1
    
    while len(mazos_validos) < limite_mazos_a_guardar:
        print(f"\n--- Buscando en la página {pagina} de Archidekt ---")
        url_busqueda = f"https://archidekt.com/api/decks/v3/?deckFormat=3&commanderName=Valgavoth,+Harrower+of+Souls&orderBy=-viewCount&pageSize=50&page={pagina}"
        
        try:
            res_busqueda = requests.get(url_busqueda, headers=cabeceras)
            if res_busqueda.status_code != 200:
                print(f"Error en búsqueda: HTTP {res_busqueda.status_code}")
                break
                
            resultados = res_busqueda.json().get("results", [])
            if not resultados:
                print("Ya no hay más mazos disponibles.")
                break
                
            for mazo_info in resultados:
                if len(mazos_validos) >= limite_mazos_a_guardar:
                    break
                    
                id_mazo = mazo_info.get("id")
                nombre_mazo = mazo_info.get("name")
                
                print(f"Evaluando mazo '{nombre_mazo}' (ID: {id_mazo})...")
                url_cartas = f"https://archidekt.com/api/decks/{id_mazo}/"
                res_cartas = requests.get(url_cartas, headers=cabeceras)
                
                if res_cartas.status_code == 200:
                    datos_mazo = res_cartas.json()
                    cartas_crudas = datos_mazo.get("cards", [])
                    
                    definicion_categorias = datos_mazo.get("categories", [])
                    categorias_en_mazo = set()
                    categorias_fuera_de_mazo = set()
                    
                    for cat in definicion_categorias:
                        nombre_cat = cat.get("name")
                        if cat.get("includedInDeck") == True:
                            categorias_en_mazo.add(nombre_cat)
                        else:
                            categorias_fuera_de_mazo.add(nombre_cat)
                    
                    mazo_principal = []
                    piscina_mutacion = []
                    total_cartas_principal = 0
                    total_cartas_mutacion = 0
                    cartas_conflicto = [] #Cartas con ambas categorias
                    
                    for c in cartas_crudas:
                        oracle = c.get("card", {}).get("oracleCard", {})
                        
                        # error nonetype ('or []')
                        categorias_carta = c.get("categories") or []
                        
                        carta_limpia = {
                            "cantidad": c.get("quantity", 1),
                            "manaCost": oracle.get("manaCost", ""),
                            "manaProduction": oracle.get("manaProduction", {}),
                            "name": oracle.get("name", "Desconocida"),
                            "power": oracle.get("power", ""),
                            "salt": oracle.get("salt", 0.0),
                            "subTypes": oracle.get("subTypes") or [],
                            "superTypes": oracle.get("superTypes") or [],
                            "keywords": oracle.get("keywords") or [],
                            "text": oracle.get("text", ""),
                            "tokens": oracle.get("tokens") or [],
                            "toughness": oracle.get("toughness", ""),
                            "types": oracle.get("types") or [],
                            "loyalty": oracle.get("loyalty", None),
                            "canlanderPoints": oracle.get("canlanderPoints", None),
                            "isPDHCommander": oracle.get("isPDHCommander", False),
                            "defaultCategory": oracle.get("defaultCategory", None),
                            "gameChanger": oracle.get("gameChanger", False),
                            "extraTurns": oracle.get("extraTurns", False),
                            "tutor": oracle.get("tutor", False),
                            "massLandDenial": oracle.get("massLandDenial", False),
                            "twoCardComboSingelton": oracle.get("twoCardComboSingelton", False),
                            "twoCardComboIds": oracle.get("twoCardComboIds") or [],
                            "atomicCombos": oracle.get("atomicCombos") or [],
                            "potentialCombos": oracle.get("potentialCombos") or []
                        }
                        
                        esta_fuera = any(cat in categorias_fuera_de_mazo for cat in categorias_carta)
                        esta_dentro = any(cat in categorias_en_mazo for cat in categorias_carta)
                        
                        # Cartas que tienen conflicto
                        if esta_fuera and esta_dentro:
                            cartas_conflicto.append(f"{carta_limpia['cantidad']}x {carta_limpia['name']}")
                        
                        
                        # Si está fuera lo mantenemos como maybeboard pero aceptamos el mazo
                        if esta_fuera:
                            piscina_mutacion.append(carta_limpia)
                            total_cartas_mutacion += carta_limpia["cantidad"]
                        elif esta_dentro:
                            mazo_principal.append(carta_limpia)
                            total_cartas_principal += carta_limpia["cantidad"]
                        else:
                            piscina_mutacion.append(carta_limpia)
                            total_cartas_mutacion += carta_limpia["cantidad"]

                    
                        
                    rompe_singleton = False
                    for carta in mazo_principal:
                        if carta["cantidad"] > 1:
                            nombre_carta = carta["name"]
                            es_tierra_basica = nombre_carta in tierras_basicas
                            es_excepcion = nombre_carta in excepciones_singleton
                            
                            if not es_tierra_basica and not es_excepcion:
                                print(f"  [x] Descartado: Rompe regla Singleton con {carta['cantidad']} '{nombre_carta}'.")
                                rompe_singleton = True
                                break 
                                
                    if rompe_singleton:
                        time.sleep(1)
                        continue

                    
                    if cartas_conflicto:
                            print(f"      -> Cartas cruzadas (válidas pero penalizadas al maybeboard): {', '.join(cartas_conflicto)}")    
                    print(f"  [V] Guardando con {total_cartas_principal} cartas principales y {len(piscina_mutacion)} grupos de cartas extra en mutación.")
                    mazo_final = {
                        "id_archidekt": id_mazo,
                        "nombre": nombre_mazo,
                        "edhBracket": datos_mazo.get("edhBracket"),
                        "mazo_principal": mazo_principal,
                        "piscina_de_mutacion": piscina_mutacion
                    }
                    mazos_validos.append(mazo_final)
                    
                else:
                    print(f"  [!] Error al descargar detalles: HTTP {res_cartas.status_code}")
                    
                time.sleep(1) 
                
        except Exception as e:
            print(f"Error técnico crítico: {e}")
            break
            
        pagina += 1
        
    if mazos_validos:
        nombre_archivo = "dataset_valgavoth_arquitectura_ia.json"
        print(f"\n=====================================")
        print(f"Guardando {len(mazos_validos)} mazos válidos en '{nombre_archivo}'...")
        with open(nombre_archivo, "w", encoding="utf-8") as archivo:
            json.dump(mazos_validos, archivo, indent=4, ensure_ascii=False)
        print("¡Operación completada! Dataset estructurado listo para tu IA.")
    else:
        print("\nNo se ha logrado encontrar ningún mazo válido.")

if __name__ == "__main__":
    construir_dataset()