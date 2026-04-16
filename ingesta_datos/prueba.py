import requests
import json

def descargar_mazo_bruto(id_mazo):
    print(f"Descargando TODOS los datos del mazo ID: {id_mazo}...")
    url = f"https://archidekt.com/api/decks/{id_mazo}/"
    
    cabeceras = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Accept": "application/json"
    }
    
    try:
        res = requests.get(url, headers=cabeceras)
        
        if res.status_code == 200:
            datos_completos = res.json()
            
            # Guardamos el JSON crudo directamente
            nombre_archivo = f"mazo_bruto_{id_mazo}.json"
            with open(nombre_archivo, "w", encoding="utf-8") as archivo:
                json.dump(datos_completos, archivo, indent=4, ensure_ascii=False)
                
            print(f"¡Listo! Tienes todo el contenido guardado en '{nombre_archivo}'.")
            
        else:
            print(f"Error al descargar: HTTP {res.status_code}")
            
    except Exception as e:
        print(f"Error técnico: {e}")

if __name__ == "__main__":
    # ---> PON AQUÍ EL ID DEL MAZO QUE SABES QUE TIENE MAYBEBOARD <---
    ID_DEL_MAZO = 11899154
    
    descargar_mazo_bruto(ID_DEL_MAZO)