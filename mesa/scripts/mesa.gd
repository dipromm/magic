extends Node3D

## Mesa en perspectiva. Las acciones salen del servidor; esta escena solo las reproduce.

const CartaScript := preload("res://scripts/carta.gd")

const PIP := {
	"WHITE": "W",
	"BLUE": "U",
	"BLACK": "B",
	"RED": "R",
	"GREEN": "G",
	"COLORLESS": "C",
}
const TINTA := {
	"WHITE": Color(0.95, 0.91, 0.78),
	"BLUE": Color(0.45, 0.66, 0.98),
	"BLACK": Color(0.62, 0.56, 0.72),
	"RED": Color(0.95, 0.42, 0.34),
	"GREEN": Color(0.48, 0.82, 0.46),
	"COLORLESS": Color(0.78, 0.78, 0.8),
}
const MODOS := {
	"menu": "",
	"objetivo": "elige objetivo",
	"x": "elige X",
	"scry": "scry",
	"offspring": "offspring",
	"elegir_bloqueador": "elige bloqueador",
	"elegir_atacante": "elige atacante",
	"opcional": "efecto opcional",
}

var _url := "http://127.0.0.1:8765"
var _dir_cartas := ""
var _estado: Dictionary = {}
var _acciones: Array = []
var _cartas := {}
var _texturas := {}
var _dorso: Texture2D
var _ocupado := false
var _hover: Node3D
var _antes := {}
var _danos: Array = []
var _zonas := {}
var _captura := false
var _captura_hecha := false
var _frames_listos := 0
var _espera_vacia := 0.0

var _http: HTTPRequest
var _fase: Label
var _aviso: Label
var _vida_tu: Label
var _vida_rival: Label
var _pips_tu: HBoxContainer
var _pips_rival: HBoxContainer
var _mazo_tu: Label
var _mazo_rival: Label
var _caja_acciones: VBoxContainer
var _caja_historial: VBoxContainer
var _visor: Control
var _visor_titulo: Label
var _visor_rejilla: GridContainer
var _camara: Camera3D
var _marco: Node3D


func _ready() -> void:
	_url = OS.get_environment("MESA_URL") if OS.get_environment("MESA_URL") != "" else _url
	_captura = OS.get_environment("MESA_CAPTURA") == "1"
	_dorso = _hacer_dorso()
	_mundo()
	_interfaz()
	_http = HTTPRequest.new()
	_http.timeout = 30.0
	add_child(_http)
	_http.request_completed.connect(_al_responder)
	_pedir_estado()


func _mundo() -> void:
	var entorno := WorldEnvironment.new()
	var ambiente := Environment.new()
	ambiente.background_mode = Environment.BG_COLOR
	ambiente.background_color = Color(0.015, 0.018, 0.02)
	ambiente.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	ambiente.ambient_light_color = Color(0.22, 0.18, 0.14)
	ambiente.ambient_light_energy = 0.55
	entorno.environment = ambiente
	add_child(entorno)

	var sol := DirectionalLight3D.new()
	sol.rotation_degrees = Vector3(-62, -28, 0)
	sol.light_color = Color(1.0, 0.9, 0.74)
	sol.light_energy = 1.45
	sol.shadow_enabled = true
	sol.shadow_blur = 1.6
	add_child(sol)
	var relleno := OmniLight3D.new()
	relleno.position = Vector3(0, 3.2, -1.2)
	relleno.light_color = Color(1.0, 0.82, 0.62)
	relleno.light_energy = 0.55
	relleno.omni_range = 14.0
	add_child(relleno)

	var tapete := MeshInstance3D.new()
	var plano := PlaneMesh.new()
	plano.size = Vector2(18, 12)
	tapete.mesh = plano
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.04, 0.062, 0.048)
	mat.roughness = 0.94
	tapete.material_override = mat
	add_child(tapete)
	_ovalo()

	_camara = Camera3D.new()
	_camara.position = Vector3(0, 4.7, -8.5)
	_camara.fov = 36
	add_child(_camara)
	_camara.look_at(Vector3(0, 0, 0.15))

	_marco = Node3D.new()
	add_child(_marco)
	var mat_marco := StandardMaterial3D.new()
	mat_marco.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat_marco.albedo_color = Color(0.95, 0.78, 0.38)
	for _i in 4:
		var lado := MeshInstance3D.new()
		var caja := BoxMesh.new()
		caja.size = Vector3(1, 0.008, 0.016)
		lado.mesh = caja
		lado.material_override = mat_marco
		_marco.add_child(lado)
	_marco.visible = false


func _ovalo() -> void:
	var malla := ImmediateMesh.new()
	malla.surface_begin(Mesh.PRIMITIVE_TRIANGLES)
	var segmentos := 72
	var rx := 3.55
	var rz := 2.05
	var grosor := 0.07
	for i in segmentos:
		var a0 := TAU * float(i) / float(segmentos)
		var a1 := TAU * float(i + 1) / float(segmentos)
		var e0 := Vector3(cos(a0) * rx, 0.015, sin(a0) * rz)
		var e1 := Vector3(cos(a1) * rx, 0.015, sin(a1) * rz)
		var i0 := Vector3(cos(a0) * (rx - grosor), 0.015, sin(a0) * (rz - grosor))
		var i1 := Vector3(cos(a1) * (rx - grosor), 0.015, sin(a1) * (rz - grosor))
		malla.surface_add_vertex(e0)
		malla.surface_add_vertex(e1)
		malla.surface_add_vertex(i0)
		malla.surface_add_vertex(i0)
		malla.surface_add_vertex(e1)
		malla.surface_add_vertex(i1)
	malla.surface_end()
	var nodo := MeshInstance3D.new()
	nodo.mesh = malla
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = Color(0.55, 0.45, 0.26, 0.85)
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	nodo.material_override = mat
	add_child(nodo)


func _interfaz() -> void:
	var capa := CanvasLayer.new()
	add_child(capa)
	var tema := Theme.new()
	var panel := _estilo_panel()
	tema.set_stylebox("panel", "PanelContainer", panel)
	tema.set_color("font_color", "Label", Color(0.94, 0.91, 0.84))
	tema.set_font_size("font_size", "Label", 18)
	tema.set_color("font_color", "Button", Color(0.95, 0.92, 0.84))
	tema.set_color("font_hover_color", "Button", Color(1, 0.97, 0.88))
	tema.set_font_size("font_size", "Button", 16)
	tema.set_stylebox("normal", "Button", _estilo_boton(0.0))
	tema.set_stylebox("hover", "Button", _estilo_boton(0.45))
	tema.set_stylebox("pressed", "Button", _estilo_boton(0.75))
	tema.set_stylebox("focus", "Button", _estilo_boton(0.2))
	tema.set_stylebox("disabled", "Button", _estilo_boton(0.0))

	var raiz := Control.new()
	raiz.set_anchors_preset(Control.PRESET_FULL_RECT)
	raiz.mouse_filter = Control.MOUSE_FILTER_IGNORE
	raiz.theme = tema
	capa.add_child(raiz)

	_fase = Label.new()
	_fase.anchor_left = 0.26
	_fase.anchor_right = 0.74
	_fase.anchor_top = 0.012
	_fase.anchor_bottom = 0.1
	_fase.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_fase.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_fase.add_theme_font_size_override("font_size", 28)
	_fase.text = "Conectando con la mesa…"
	raiz.add_child(_fase)

	_aviso = Label.new()
	_aviso.anchor_left = 0.26
	_aviso.anchor_right = 0.74
	_aviso.anchor_top = 0.095
	_aviso.anchor_bottom = 0.14
	_aviso.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_aviso.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_aviso.add_theme_font_size_override("font_size", 14)
	_aviso.add_theme_color_override("font_color", Color(0.93, 0.78, 0.45))
	raiz.add_child(_aviso)

	var tu := _panel_anclado(raiz, 0.012, 0.72, 0.25, 0.98)
	_vida_tu = _linea(tu, "Tú", 26)
	_pips_tu = HBoxContainer.new()
	tu.add_child(_pips_tu)
	_mazo_tu = _linea(tu, "", 15)
	_botones_zona(tu, "tu")

	var rival := _panel_anclado(raiz, 0.012, 0.145, 0.25, 0.4)
	_vida_rival = _linea(rival, "Rival", 26)
	_pips_rival = HBoxContainer.new()
	rival.add_child(_pips_rival)
	_mazo_rival = _linea(rival, "", 15)
	_botones_zona(rival, "rival")

	var acciones := _panel_anclado(raiz, 0.012, 0.42, 0.25, 0.7)
	_linea(acciones, "Acciones", 18)
	var scroll_a := ScrollContainer.new()
	scroll_a.size_flags_vertical = Control.SIZE_EXPAND_FILL
	acciones.add_child(scroll_a)
	_caja_acciones = VBoxContainer.new()
	_caja_acciones.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll_a.add_child(_caja_acciones)

	var historia := _panel_anclado(raiz, 0.755, 0.145, 0.988, 0.98)
	_linea(historia, "Historial", 18)
	var scroll_h := ScrollContainer.new()
	scroll_h.size_flags_vertical = Control.SIZE_EXPAND_FILL
	historia.add_child(scroll_h)
	_caja_historial = VBoxContainer.new()
	_caja_historial.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll_h.add_child(_caja_historial)

	_visor = ColorRect.new()
	_visor.color = Color(0.02, 0.02, 0.025, 0.72)
	_visor.set_anchors_preset(Control.PRESET_FULL_RECT)
	_visor.visible = false
	_visor.gui_input.connect(_al_fondo_visor)
	raiz.add_child(_visor)
	var caja := PanelContainer.new()
	caja.anchor_left = 0.18
	caja.anchor_right = 0.82
	caja.anchor_top = 0.12
	caja.anchor_bottom = 0.9
	_visor.add_child(caja)
	var columna := VBoxContainer.new()
	caja.add_child(columna)
	var cabeza := HBoxContainer.new()
	columna.add_child(cabeza)
	_visor_titulo = Label.new()
	_visor_titulo.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	cabeza.add_child(_visor_titulo)
	var cerrar := Button.new()
	cerrar.text = "Cerrar"
	cerrar.pressed.connect(_cerrar_visor)
	cabeza.add_child(cerrar)
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	columna.add_child(scroll)
	_visor_rejilla = GridContainer.new()
	_visor_rejilla.columns = 5
	scroll.add_child(_visor_rejilla)


func _panel_anclado(padre: Control, x0: float, y0: float, x1: float, y1: float) -> VBoxContainer:
	var panel := PanelContainer.new()
	panel.anchor_left = x0
	panel.anchor_top = y0
	panel.anchor_right = x1
	panel.anchor_bottom = y1
	panel.offset_left = 0
	panel.offset_top = 0
	panel.offset_right = 0
	panel.offset_bottom = 0
	padre.add_child(panel)
	var caja := VBoxContainer.new()
	caja.size_flags_vertical = Control.SIZE_EXPAND_FILL
	panel.add_child(caja)
	return caja


func _linea(padre: Node, texto: String, tamano: int) -> Label:
	var etiqueta := Label.new()
	etiqueta.text = texto
	etiqueta.add_theme_font_size_override("font_size", tamano)
	padre.add_child(etiqueta)
	return etiqueta


func _botones_zona(padre: Node, quien: String) -> void:
	var fila := HBoxContainer.new()
	padre.add_child(fila)
	for par in [["Cementerio", "cementerio"], ["Exilio", "exilio"]]:
		var boton := Button.new()
		boton.text = par[0]
		boton.pressed.connect(_abrir_zona.bind(quien, par[1]))
		fila.add_child(boton)


func _estilo_panel() -> StyleBoxFlat:
	var estilo := StyleBoxFlat.new()
	estilo.bg_color = Color(0.05, 0.055, 0.07, 0.78)
	estilo.border_color = Color(0.72, 0.64, 0.42, 0.45)
	estilo.set_border_width_all(1)
	estilo.set_corner_radius_all(10)
	estilo.content_margin_left = 12
	estilo.content_margin_right = 12
	estilo.content_margin_top = 10
	estilo.content_margin_bottom = 10
	return estilo


func _estilo_boton(brillo: float) -> StyleBoxFlat:
	var estilo := StyleBoxFlat.new()
	estilo.bg_color = Color(0.09, 0.1, 0.12, 0.94).lerp(Color(0.32, 0.26, 0.12, 0.96), brillo)
	estilo.border_color = Color(0.75, 0.66, 0.4, 0.35 + brillo * 0.5)
	estilo.set_border_width_all(1)
	estilo.set_corner_radius_all(6)
	estilo.content_margin_left = 8
	estilo.content_margin_right = 8
	estilo.content_margin_top = 5
	estilo.content_margin_bottom = 5
	return estilo


func _hacer_dorso() -> Texture2D:
	var imagen := Image.create(80, 112, false, Image.FORMAT_RGB8)
	imagen.fill(Color(0.08, 0.12, 0.22))
	for x in range(80):
		for y in range(112):
			if x < 6 or x > 73 or y < 6 or y > 105:
				imagen.set_pixel(x, y, Color(0.62, 0.5, 0.28))
	return ImageTexture.create_from_image(imagen)


func _pedir_estado() -> void:
	_enviar(HTTPClient.METHOD_GET, "/estado", "")


func _enviar_accion(indice: int) -> void:
	_enviar(HTTPClient.METHOD_POST, "/accion", JSON.stringify({"indice": indice}))


func _enviar(metodo: int, ruta: String, cuerpo: String) -> void:
	if _ocupado:
		return
	_ocupado = true
	var cabeceras := PackedStringArray(["Content-Type: application/json"])
	var error := _http.request(_url + ruta, cabeceras, metodo, cuerpo)
	if error != OK:
		_ocupado = false
		_aviso.text = "No hay mesa en " + _url


func _al_responder(resultado: int, codigo: int, _cabeceras: PackedStringArray, cuerpo: PackedByteArray) -> void:
	_ocupado = false
	if resultado != HTTPRequest.RESULT_SUCCESS or codigo >= 400:
		var texto := cuerpo.get_string_from_utf8()
		var fallo = JSON.parse_string(texto)
		if fallo is Dictionary and fallo.has("error"):
			_aviso.text = str(fallo["error"])
		elif codigo == 0:
			_aviso.text = "Sin conexión. Arranca python mesa_api.py en motor_prueba."
		else:
			_aviso.text = "La mesa respondió %s" % codigo
		return
	var datos = JSON.parse_string(cuerpo.get_string_from_utf8())
	if datos is Dictionary:
		_aplicar(datos)


func _aplicar(estado: Dictionary) -> void:
	_estado = estado
	if str(estado.get("cartas_dir", "")) != "":
		_dir_cartas = str(estado["cartas_dir"])
	_acciones = estado.get("acciones", [])
	var legales := _ids_legales()
	_pintar_ui(estado)
	var puestos := {}
	_poner_mano(estado.get("tu", {}).get("mano", []), -3.15, -1.0, false, legales, puestos)
	_poner_campo(estado.get("tu", {}).get("campo", []), -0.85, -1.55, legales, puestos)
	_poner_mano(estado.get("rival", {}).get("mano", []), 3.55, 1.0, true, legales, puestos)
	_poner_campo(estado.get("rival", {}).get("campo", []), 1.7, 2.6, legales, puestos)
	_poner_pila(estado.get("pila", []), legales, puestos)
	_poner_zonas(estado, puestos, legales)
	_viajes(estado.get("eventos", []), puestos)
	_danos_de(estado.get("eventos", []))
	for id in _cartas.keys():
		if not puestos.has(id):
			_cartas[id].queue_free()
			_cartas.erase(id)
	_antes = puestos.duplicate()
	_marco_de(str(estado.get("prioridad", "")))


func _ids_legales() -> Dictionary:
	var ids := {}
	for accion in _acciones:
		for clave in ["carta_id", "blocker_id", "attacker_id"]:
			if accion.get(clave) != null:
				ids[str(accion[clave])] = true
	return ids


func _carta(datos: Dictionary, legales: Dictionary, extra: Dictionary):
	var id := str(datos.get("id", ""))
	if id == "":
		return null
	var nodo = _cartas.get(id)
	if nodo == null:
		nodo = CartaScript.new()
		_cartas[id] = nodo
		add_child(nodo)
	var nombre := str(datos.get("nombre", ""))
	var textura: Texture2D = null
	if not bool(datos.get("boca_abajo", false)) and nombre != "":
		textura = _textura(nombre)
	var payload := datos.duplicate()
	payload["legal"] = legales.has(id)
	for clave in extra.keys():
		payload[clave] = extra[clave]
	nodo.aplicar(payload, textura, _dorso)
	nodo.viajando = false
	return nodo


func _textura(nombre: String) -> Texture2D:
	if _texturas.has(nombre):
		return _texturas[nombre]
	var dir := _dir_cartas if _dir_cartas != "" else ProjectSettings.globalize_path("res://cartas")
	var imagen := Image.new()
	var ruta := dir.path_join(nombre + ".jpg")
	if imagen.load(ruta) != OK:
		_texturas[nombre] = null
		return null
	var textura := ImageTexture.create_from_image(imagen)
	_texturas[nombre] = textura
	return textura


func _poner_mano(cartas: Array, z: float, signo: float, boca: bool, legales: Dictionary, puestos: Dictionary) -> void:
	var n := cartas.size()
	if n == 0:
		return
	var separacion := minf(0.46, 6.6 / float(n))
	var origen := -separacion * float(n - 1) * 0.5
	for i in n:
		var datos = cartas[i]
		if boca:
			datos = datos.duplicate()
			datos["boca_abajo"] = true
		var t := 0.0 if n <= 1 else float(i) / float(n - 1) - 0.5
		var pos := Vector3(origen + separacion * float(i), 0.045 + float(i) * 0.006, z + absf(t) * 0.38 * signo)
		var nodo = _carta(datos, legales, {})
		if nodo == null:
			continue
		nodo.base_pos = pos
		nodo.base_yaw = -t * 0.4
		if nodo.position == Vector3.ZERO:
			nodo.position = pos
		puestos[nodo.carta_id] = pos


func _poner_campo(cartas: Array, z_criaturas: float, z_tierras: float, legales: Dictionary, puestos: Dictionary) -> void:
	var sueltas: Array = []
	var anexos: Array = []
	for datos in cartas:
		if datos.get("anexada_a"):
			anexos.append(datos)
		else:
			sueltas.append(datos)
	var criaturas: Array = []
	var tierras: Array = []
	for datos in sueltas:
		var tipos: Array = datos.get("tipos", [])
		if tipos.has("Land"):
			tierras.append(datos)
		else:
			criaturas.append(datos)
	_fila(criaturas, z_criaturas, legales, puestos)
	_fila(tierras, z_tierras, legales, puestos)
	for datos in anexos:
		var nodo = _carta(datos, legales, {})
		if nodo == null:
			continue
		var ancla: Vector3 = puestos.get(str(datos.get("anexada_a")), Vector3(0, 0.05, z_criaturas))
		var pos := ancla + Vector3(0.22, 0.05, 0.12)
		nodo.base_pos = pos
		nodo.base_yaw = 0.15
		if nodo.position == Vector3.ZERO:
			nodo.position = pos
		puestos[nodo.carta_id] = pos


func _fila(cartas: Array, z: float, legales: Dictionary, puestos: Dictionary) -> void:
	var n := cartas.size()
	if n == 0:
		return
	var separacion := minf(0.78, 7.4 / float(n))
	var origen := -separacion * float(n - 1) * 0.5
	for i in n:
		var nodo = _carta(cartas[i], legales, {})
		if nodo == null:
			continue
		var pos := Vector3(origen + separacion * float(i), 0.04, z)
		nodo.base_pos = pos
		nodo.base_yaw = 0.0
		if nodo.position == Vector3.ZERO:
			nodo.position = pos
		puestos[nodo.carta_id] = pos


func _poner_pila(pila: Array, legales: Dictionary, puestos: Dictionary) -> void:
	for i in pila.size():
		var datos = pila[i].duplicate()
		datos["mostrar_nombre"] = true
		var nodo = _carta(datos, legales, {})
		if nodo == null:
			continue
		var pos := Vector3(-0.2 * float(i), 0.05 + 0.035 * float(i), 0.42 - 0.18 * float(i))
		nodo.base_pos = pos
		nodo.base_yaw = 0.0
		if nodo.position == Vector3.ZERO:
			nodo.position = pos
		puestos[nodo.carta_id] = pos


func _poner_zonas(estado: Dictionary, puestos: Dictionary, legales: Dictionary) -> void:
	var sitios := {
		"tu": {"cementerio": Vector3(3.15, 0.04, -1.35), "exilio": Vector3(3.15, 0.04, -0.3), "mazo": Vector3(-3.15, 0.04, -1.35)},
		"rival": {"cementerio": Vector3(-3.15, 0.04, 2.2), "exilio": Vector3(-3.15, 0.04, 1.15), "mazo": Vector3(3.15, 0.04, 2.2)},
	}
	for quien in PackedStringArray(["tu", "rival"]):
		var jugador: Dictionary = estado.get(quien, {})
		for zona in PackedStringArray(["cementerio", "exilio"]):
			var lista: Array = jugador.get(zona, [])
			var pos: Vector3 = sitios[quien][zona]
			var clave := quien + ":" + zona
			if lista.is_empty():
				_marca_vacia(clave, pos, zona, quien, 0)
			else:
				_quitar_marca(clave)
				var datos = lista[0].duplicate()
				datos["zona"] = zona
				datos["quien"] = quien
				var nodo = _carta(datos, legales, {})
				if nodo == null:
					continue
				nodo.base_pos = pos
				nodo.base_yaw = 0.0
				if nodo.position == Vector3.ZERO:
					nodo.position = pos
				puestos[nodo.carta_id] = pos
			_cuenta(clave, pos, lista.size(), zona)
		_mazo_3d(quien, sitios[quien]["mazo"], int(jugador.get("mazo", 0)))


func _marca_vacia(clave: String, pos: Vector3, zona: String, quien: String, _n: int) -> void:
	if _zonas.has(clave):
		_zonas[clave].position = pos
		_zonas[clave].visible = true
		return
	var nodo := MeshInstance3D.new()
	var caja := BoxMesh.new()
	caja.size = Vector3(0.63, 0.02, 0.88)
	nodo.mesh = caja
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.1, 0.1, 0.12)
	nodo.material_override = mat
	nodo.position = pos
	nodo.set_meta("zona", zona)
	nodo.set_meta("quien", quien)
	add_child(nodo)
	_zonas[clave] = nodo


func _quitar_marca(clave: String) -> void:
	if _zonas.has(clave) and str(clave).find("cuenta") == -1 and str(clave).find("mazo") == -1:
		_zonas[clave].visible = false


func _cuenta(clave: String, pos: Vector3, n: int, zona: String) -> void:
	var id := "cuenta:" + clave
	var etiqueta: Label3D = _zonas.get(id)
	if etiqueta == null:
		etiqueta = Label3D.new()
		etiqueta.font_size = 48
		etiqueta.pixel_size = 0.004
		etiqueta.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		etiqueta.modulate = Color(0.95, 0.9, 0.78)
		etiqueta.outline_size = 8
		add_child(etiqueta)
		_zonas[id] = etiqueta
	etiqueta.position = pos + Vector3(0, 0.55, 0)
	etiqueta.text = "%s %d" % [zona, n]


func _mazo_3d(quien: String, pos: Vector3, n: int) -> void:
	var clave := "mazo:" + quien
	var nodo = _zonas.get(clave)
	if nodo == null:
		nodo = CartaScript.new()
		add_child(nodo)
		nodo.aplicar({"id": clave, "boca_abajo": true, "nombre": ""}, null, _dorso)
		nodo.position = pos
		_zonas[clave] = nodo
	nodo.base_pos = pos
	nodo.position = pos
	_cuenta(clave, pos, n, "mazo")


func _viajes(eventos: Array, puestos: Dictionary) -> void:
	var orden := 0
	for evento in eventos:
		var tipo := str(evento.get("tipo", ""))
		var id := str(evento.get("carta_id", ""))
		if not _cartas.has(id):
			continue
		var carta = _cartas[id]
		if tipo == "robo":
			carta.iniciar_viaje(_pos_mazo(str(evento.get("asiento", "tu"))), [puestos.get(id, carta.base_pos)])
			carta.espera = 0.08 * float(orden)
			orden += 1
		elif tipo == "lanzar":
			var destino: Vector3 = puestos.get(id, carta.base_pos)
			var desde: Vector3 = _antes.get(id, _pos_mano(str(evento.get("asiento", "tu"))))
			if bool(evento.get("resuelto", false)):
				carta.iniciar_viaje(desde, [Vector3(0, 0.22, 0.15), destino])
			else:
				carta.iniciar_viaje(desde, [destino])
			carta.espera = 0.06 * float(orden)
			orden += 1
		elif tipo == "jugar_tierra":
			var desde_tierra: Vector3 = _antes.get(id, _pos_mano(str(evento.get("asiento", "tu"))))
			carta.iniciar_viaje(desde_tierra, [puestos.get(id, carta.base_pos)])


func _pos_mazo(asiento: String) -> Vector3:
	return Vector3(-3.15, 0.08, -1.35) if asiento == "tu" else Vector3(3.15, 0.08, 2.2)


func _pos_mano(asiento: String) -> Vector3:
	return Vector3(0, 0.08, -3.15) if asiento == "tu" else Vector3(0, 0.08, 3.55)


func _danos_de(eventos: Array) -> void:
	for evento in eventos:
		if str(evento.get("tipo", "")) != "dano":
			continue
		var pos := Vector3(0, 0.7, -2.3 if str(evento.get("asiento", "")) == "tu" else 2.4)
		var id := str(evento.get("carta_id", ""))
		if id != "" and _cartas.has(id):
			pos = _cartas[id].position + Vector3(0, 0.25, 0)
		var numero := Label3D.new()
		numero.text = "-%s" % evento.get("cantidad", 0)
		numero.font_size = 96
		numero.pixel_size = 0.005
		numero.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		numero.modulate = Color(1, 0.38, 0.28)
		numero.outline_size = 10
		numero.position = pos
		add_child(numero)
		_danos.append({"nodo": numero, "t": 0.0, "origen": pos})


func _marco_de(prioridad: String) -> void:
	if prioridad != "tu" and prioridad != "rival":
		_marco.visible = false
		return
	_marco.visible = true
	var z0 := -1.55 if prioridad == "tu" else 1.15
	var z1 := -0.4 if prioridad == "tu" else 2.95
	var x0 := -3.3
	var x1 := 3.3
	var y := 0.025
	var abajo: MeshInstance3D = _marco.get_child(0)
	var arriba: MeshInstance3D = _marco.get_child(1)
	var izquierda: MeshInstance3D = _marco.get_child(2)
	var derecha: MeshInstance3D = _marco.get_child(3)
	abajo.position = Vector3(0, y, z0)
	abajo.scale = Vector3(x1 - x0, 1, 1)
	abajo.rotation = Vector3.ZERO
	arriba.position = Vector3(0, y, z1)
	arriba.scale = Vector3(x1 - x0, 1, 1)
	arriba.rotation = Vector3.ZERO
	izquierda.position = Vector3(x0, y, (z0 + z1) * 0.5)
	izquierda.rotation = Vector3(0, PI * 0.5, 0)
	izquierda.scale = Vector3(z1 - z0, 1, 1)
	derecha.position = Vector3(x1, y, (z0 + z1) * 0.5)
	derecha.rotation = Vector3(0, PI * 0.5, 0)
	derecha.scale = Vector3(z1 - z0, 1, 1)


func _pintar_ui(estado: Dictionary) -> void:
	var fase := str(estado.get("fase_visible", ""))
	var modo := str(MODOS.get(str(estado.get("modo", "menu")), str(estado.get("modo", ""))))
	var prioridad := str(estado.get("prioridad_nombre", ""))
	if bool(estado.get("terminada", false)):
		_fase.text = "Partida terminada"
	else:
		_fase.text = fase if modo == "" else "%s\n%s" % [fase, modo]
		if prioridad != "":
			_fase.text += "\nPrioridad: " + prioridad
	_aviso.text = str(estado.get("aviso", ""))
	var tu: Dictionary = estado.get("tu", {})
	var rival: Dictionary = estado.get("rival", {})
	_vida_tu.text = "%s   %s" % [tu.get("nombre", "Tú"), tu.get("vida", "")]
	_vida_rival.text = "%s   %s" % [rival.get("mazo_nombre", "Rival"), rival.get("vida", "")]
	_mazo_tu.text = "Mazo %s" % tu.get("mazo", 0)
	_mazo_rival.text = "Mazo %s" % rival.get("mazo", 0)
	_llenar_pips(_pips_tu, tu)
	_llenar_pips(_pips_rival, rival)
	_llenar_acciones()
	_llenar_historial(estado.get("historial", []))


func _llenar_pips(caja: HBoxContainer, jugador: Dictionary) -> void:
	for hijo in caja.get_children():
		caja.remove_child(hijo)
		hijo.free()
	var tierras: Array = jugador.get("mana_tierras", [])
	var reserva: Dictionary = jugador.get("mana", {})
	if tierras.is_empty():
		var vacio := Label.new()
		vacio.text = "sin maná"
		caja.add_child(vacio)
	for tierra in tierras:
		var letras := PackedStringArray()
		var tinta := Color(0.9, 0.86, 0.7)
		for color in tierra.get("colores", []):
			letras.append(str(PIP.get(color, "?")))
			tinta = TINTA.get(color, tinta)
		var pip := Label.new()
		pip.text = "/".join(letras)
		pip.modulate = tinta
		caja.add_child(pip)
	for color in PIP.keys():
		var n = int(reserva.get(color, 0))
		if n <= 0:
			continue
		var pip := Label.new()
		pip.text = "%s%d" % [PIP[color], n]
		pip.modulate = TINTA[color]
		caja.add_child(pip)


func _llenar_acciones() -> void:
	for hijo in _caja_acciones.get_children():
		_caja_acciones.remove_child(hijo)
		hijo.free()
	if _acciones.is_empty():
		var vacio := Label.new()
		vacio.text = "Sin acciones"
		_caja_acciones.add_child(vacio)
		return
	for accion in _acciones:
		var boton := Button.new()
		boton.text = str(accion.get("descripcion", accion.get("indice", "")))
		boton.alignment = HORIZONTAL_ALIGNMENT_LEFT
		boton.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		var indice := int(accion.get("indice", -1))
		boton.pressed.connect(_enviar_accion.bind(indice))
		boton.set_meta("indice", indice)
		_caja_acciones.add_child(boton)


func _llenar_historial(historial: Array) -> void:
	for hijo in _caja_historial.get_children():
		_caja_historial.remove_child(hijo)
		hijo.free()
	if historial.is_empty():
		var vacio := Label.new()
		vacio.text = "Aún no hay jugadas"
		_caja_historial.add_child(vacio)
		return
	for entrada in historial:
		var linea := Label.new()
		linea.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		linea.text = "%s — %s" % [entrada.get("jugador", ""), entrada.get("descripcion", "")]
		linea.add_theme_font_size_override("font_size", 14)
		_caja_historial.add_child(linea)


func _process(delta: float) -> void:
	for dano in _danos:
		dano["t"] = float(dano["t"]) + delta
		var nodo: Label3D = dano["nodo"]
		var origen: Vector3 = dano["origen"]
		nodo.position = origen + Vector3(0, float(dano["t"]) * 0.85, 0)
		var color := nodo.modulate
		color.a = clampf(1.0 - float(dano["t"]) / 0.85, 0.0, 1.0)
		nodo.modulate = color
	var vivos: Array = []
	for dano in _danos:
		if float(dano["t"]) < 0.9:
			vivos.append(dano)
		else:
			dano["nodo"].queue_free()
	_danos = vivos
	if _captura and not _captura_hecha:
		if not _estado.is_empty():
			_frames_listos += 1
			if _frames_listos > 4:
				_guardar_captura()
		else:
			_espera_vacia += delta
			if _espera_vacia > 4.0:
				_guardar_captura()


func _unhandled_input(evento: InputEvent) -> void:
	if _visor.visible:
		return
	if evento is InputEventMouseMotion:
		_sobre(_bajo_el_raton(evento.position))
	elif evento is InputEventMouseButton and evento.pressed and evento.button_index == MOUSE_BUTTON_LEFT:
		var carta := _bajo_el_raton(evento.position)
		if carta != null:
			_pulsar(carta)


func _bajo_el_raton(pantalla: Vector2) -> Node3D:
	var origen := _camara.project_ray_origin(pantalla)
	var direccion := _camara.project_ray_normal(pantalla)
	var mejor: Node3D = null
	var mejor_t := 100000.0
	for carta in _cartas.values():
		var t: float = carta.contiene_rayo(origen, direccion)
		if t >= 0.0 and t < mejor_t:
			mejor_t = t
			mejor = carta
	for nodo in _zonas.values():
		if not (nodo is MeshInstance3D) or not nodo.visible or not nodo.has_meta("zona"):
			continue
		var t2 := _caja_contiene(nodo, origen, direccion)
		if t2 >= 0.0 and t2 < mejor_t:
			mejor_t = t2
			mejor = nodo
	return mejor


func _caja_contiene(nodo: Node3D, origen: Vector3, direccion: Vector3) -> float:
	if absf(direccion.y) < 0.00001:
		return -1.0
	var t := (nodo.position.y - origen.y) / direccion.y
	if t < 0.0:
		return -1.0
	var golpe := origen + direccion * t
	if absf(golpe.x - nodo.position.x) <= 0.36 and absf(golpe.z - nodo.position.z) <= 0.48:
		return t
	return -1.0


func _sobre(carta: Node3D) -> void:
	if _hover == carta:
		return
	if _hover != null:
		_hover.hover = false
	_hover = carta
	if _hover != null:
		_hover.hover = true


func _pulsar(carta: Node) -> void:
	var zona := ""
	var quien := ""
	if carta.has_meta("zona"):
		zona = str(carta.get_meta("zona"))
		quien = str(carta.get_meta("quien"))
	elif carta.get("zona") != null and str(carta.zona) != "":
		zona = str(carta.zona)
		quien = str(carta.quien)
	if zona != "":
		_abrir_zona(quien, zona)
		return
	var coinciden: Array = []
	var id := str(carta.carta_id)
	for accion in _acciones:
		if str(accion.get("carta_id", "")) == id or str(accion.get("blocker_id", "")) == id or str(accion.get("attacker_id", "")) == id:
			coinciden.append(accion)
	if coinciden.size() == 1:
		_enviar_accion(int(coinciden[0].get("indice", -1)))
	elif coinciden.size() > 1:
		_marcar_filas(coinciden)


func _marcar_filas(acciones: Array) -> void:
	var indices := {}
	for accion in acciones:
		indices[int(accion.get("indice", -1))] = true
	for boton in _caja_acciones.get_children():
		if boton is Button and indices.has(int(boton.get_meta("indice", -2))):
			boton.add_theme_stylebox_override("normal", _estilo_boton(0.85))


func _abrir_zona(quien: String, zona: String) -> void:
	var jugador: Dictionary = _estado.get(quien, {})
	var lista: Array = jugador.get(zona, [])
	var titulo := "Tu " if quien == "tu" else "Rival "
	_visor_titulo.text = titulo + zona + "  (arriba primero)"
	for hijo in _visor_rejilla.get_children():
		_visor_rejilla.remove_child(hijo)
		hijo.free()
	if lista.is_empty():
		var vacio := Label.new()
		vacio.text = "Vacío"
		_visor_rejilla.add_child(vacio)
	for i in lista.size():
		var datos: Dictionary = lista[i]
		var celda := VBoxContainer.new()
		var cara := TextureRect.new()
		cara.custom_minimum_size = Vector2(120, 168)
		cara.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		cara.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
		var textura := _textura(str(datos.get("nombre", "")))
		cara.texture = textura if textura != null else _dorso
		var boton := Button.new()
		boton.text = str(datos.get("nombre", ""))
		if i == 0:
			boton.text = "arriba · " + boton.text
		var id := str(datos.get("id", ""))
		boton.pressed.connect(_pulsar_del_visor.bind(id))
		celda.add_child(cara)
		celda.add_child(boton)
		_visor_rejilla.add_child(celda)
	_visor.visible = true


func _pulsar_del_visor(id: String) -> void:
	var coinciden: Array = []
	for accion in _acciones:
		if str(accion.get("carta_id", "")) == id or str(accion.get("blocker_id", "")) == id or str(accion.get("attacker_id", "")) == id:
			coinciden.append(accion)
	if coinciden.size() == 1:
		_cerrar_visor()
		_enviar_accion(int(coinciden[0].get("indice", -1)))
	elif coinciden.size() > 1:
		_cerrar_visor()
		_marcar_filas(coinciden)


func _cerrar_visor() -> void:
	_visor.visible = false


func _al_fondo_visor(evento: InputEvent) -> void:
	if evento is InputEventMouseButton and evento.pressed and evento.button_index == MOUSE_BUTTON_LEFT:
		_cerrar_visor()


func _guardar_captura() -> void:
	if _captura_hecha:
		return
	_captura_hecha = true
	await RenderingServer.frame_post_draw
	var imagen := get_viewport().get_texture().get_image()
	var ruta := ProjectSettings.globalize_path("res://capturas/mesa.png")
	DirAccess.make_dir_recursive_absolute(ruta.get_base_dir())
	if imagen != null and imagen.save_png(ruta) == OK:
		print("captura ", ruta)
	else:
		print("captura vacía")
	get_tree().quit()
