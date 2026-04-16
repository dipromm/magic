from pyedhrec import EDHRec

def probar_mazos_reales():
    print("Iniciando conexión con EDHREC...")
    edhrec = EDHRec()
    
    # Seguimos con tu comandante
    comandante = "Valgavoth, Harrower of Souls"
    
    try:
        print(f"Buscando listas de mazos conocidos para: {comandante}")
        # Utilizamos exactamente el método que has encontrado en la documentación
        mazos = edhrec.get_commander_decks(comandante)
        
        print("\n¡ÉXITO! Datos obtenidos.")
        
        # Evaluamos la estructura de los datos devueltos
        if isinstance(mazos, list):
            print(f"Se han extraído {len(mazos)} mazos para este comandante.")
            print("-" * 40)
            print("Mostrando un fragmento del PRIMER mazo encontrado:")
            print(mazos[0])
        else:
            print("El formato devuelto no es una lista normal. Contenido bruto:")
            print(mazos)
            
    except Exception as e:
        print("\nFALLO. La librería ha tenido un error al ejecutar este método.")
        print(f"Error técnico: {e}")

if __name__ == "__main__":
    probar_mazos_reales()