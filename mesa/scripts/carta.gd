extends Node3D

## Plano de una carta sobre el tapete. El brillo lo enciende el servidor, no esta escena.

const ANCHO := 0.63
const ALTO := 0.88

var carta_id := ""
var zona := ""
var quien := ""
var legal := false
var hover := false
var girada := false
var boca_abajo := false
var textura_cara: Texture2D
var nombre_carta := ""
var viajando := false
var velocidad := 9.0
var espera := 0.0
var base_pos := Vector3.ZERO
var base_yaw := 0.0
var en_mano := false
var base_escala := 1.0
var base_basis := Basis.IDENTITY
var hover_offset := Vector3.ZERO

var _hitos: Array = []
var _malla: MeshInstance3D
var _marco: MeshInstance3D
var _brillo: MeshInstance3D
var _mat_cara: StandardMaterial3D
var _mat_brillo: StandardMaterial3D
var _nombre: Label3D
var _datos: Label3D
var _sombra: MeshInstance3D


func _ready() -> void:
	_malla = MeshInstance3D.new()
	_malla.mesh = _cara_completa()
	_mat_cara = StandardMaterial3D.new()
	# La foto va entera y legible. La luz cálida queda en el tapete;
	# un Lambert sobre el plano la dejaba casi negra.
	_mat_cara.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_mat_cara.albedo_color = Color(0.14, 0.12, 0.1)
	_mat_cara.cull_mode = BaseMaterial3D.CULL_DISABLED
	_mat_cara.texture_repeat = false
	_mat_cara.uv1_scale = Vector3.ONE
	_mat_cara.uv1_offset = Vector3.ZERO
	_malla.material_override = _mat_cara
	# Un plano fino se sombrea a sí mismo y deja la foto negra. La sombra
	# suave va en el quad de debajo.
	_malla.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(_malla)

	_marco = _plano(ANCHO + 0.045, ALTO + 0.045, -0.012, Color(0.04, 0.035, 0.03))
	add_child(_marco)
	_brillo = _plano(ANCHO + 0.16, ALTO + 0.16, -0.02, Color(1, 0.74, 0.28))
	_mat_brillo = _brillo.material_override
	_mat_brillo.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_brillo.visible = false
	add_child(_brillo)

	_nombre = _texto()
	_nombre.position = Vector3(0, 0.02, 0.02)
	_nombre.visible = false
	add_child(_nombre)
	_datos = _texto()
	_datos.font_size = 28
	_datos.position = Vector3(0, -0.3, 0.025)
	_datos.modulate = Color(1, 0.95, 0.82)
	_datos.visible = false
	add_child(_datos)
	_sombra = MeshInstance3D.new()
	_sombra.top_level = true
	var sombra_malla := QuadMesh.new()
	sombra_malla.size = Vector2(ANCHO * 0.96, ALTO * 0.96)
	_sombra.mesh = sombra_malla
	var mat_sombra := StandardMaterial3D.new()
	mat_sombra.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat_sombra.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat_sombra.albedo_color = Color(0, 0, 0, 0.42)
	_sombra.material_override = mat_sombra
	_sombra.rotation.x = -PI * 0.5
	_sombra.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(_sombra)


func _cara_completa() -> ArrayMesh:
	# Plano XY con UV 0–1. BoxMesh de Godot reparte la textura en un atlas
	# y la cara visible solo enseña una esquina.
	var hw := ANCHO * 0.5
	var hh := ALTO * 0.5
	var vertices := PackedVector3Array([
		Vector3(-hw, -hh, 0.0),
		Vector3(hw, -hh, 0.0),
		Vector3(hw, hh, 0.0),
		Vector3(-hw, hh, 0.0),
	])
	var uvs := PackedVector2Array([
		Vector2(0, 1),
		Vector2(1, 1),
		Vector2(1, 0),
		Vector2(0, 0),
	])
	var normales := PackedVector3Array([
		Vector3(0, 0, 1),
		Vector3(0, 0, 1),
		Vector3(0, 0, 1),
		Vector3(0, 0, 1),
	])
	var tangentes := PackedFloat32Array([
		1, 0, 0, 1,
		1, 0, 0, 1,
		1, 0, 0, 1,
		1, 0, 0, 1,
	])
	var indices := PackedInt32Array([0, 1, 2, 0, 2, 3])
	var arrays: Array = []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = vertices
	arrays[Mesh.ARRAY_NORMAL] = normales
	arrays[Mesh.ARRAY_TANGENT] = tangentes
	arrays[Mesh.ARRAY_TEX_UV] = uvs
	arrays[Mesh.ARRAY_INDEX] = indices
	var malla := ArrayMesh.new()
	malla.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	return malla


func _plano(ancho: float, alto: float, z: float, color: Color) -> MeshInstance3D:
	var malla := MeshInstance3D.new()
	var caja := BoxMesh.new()
	caja.size = Vector3(ancho, alto, 0.008)
	malla.mesh = caja
	malla.position = Vector3(0, 0, z)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 0.8
	malla.material_override = mat
	malla.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	return malla


func _texto() -> Label3D:
	var etiqueta := Label3D.new()
	etiqueta.font_size = 32
	etiqueta.pixel_size = 0.0032
	etiqueta.outline_size = 6
	etiqueta.modulate = Color(0.96, 0.93, 0.86)
	etiqueta.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	etiqueta.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	etiqueta.width = 170
	return etiqueta


func aplicar(datos: Dictionary, textura: Texture2D, dorso: Texture2D) -> void:
	carta_id = str(datos.get("id", ""))
	zona = str(datos.get("zona", ""))
	quien = str(datos.get("quien", ""))
	legal = bool(datos.get("legal", false))
	girada = bool(datos.get("girada", false))
	boca_abajo = bool(datos.get("boca_abajo", false))
	nombre_carta = "" if boca_abajo else str(datos.get("nombre", ""))
	textura_cara = null if boca_abajo else textura
	var cara: Texture2D = dorso if boca_abajo else textura
	if cara != null:
		_mat_cara.albedo_texture = cara
		_mat_cara.albedo_color = Color.WHITE
	else:
		_mat_cara.albedo_texture = null
		_mat_cara.albedo_color = Color(0.11, 0.13, 0.18) if boca_abajo else Color(0.16, 0.13, 0.1)
	var mostrar_nombre := (not boca_abajo) and (textura == null or bool(datos.get("mostrar_nombre", false)))
	_nombre.visible = mostrar_nombre
	_nombre.text = str(datos.get("nombre", ""))
	var marcas := PackedStringArray()
	if datos.get("poder") != null:
		marcas.append("%s/%s" % [datos.get("poder"), datos.get("resistencia")])
	if bool(datos.get("atacando", false)):
		marcas.append("ataca")
	if bool(datos.get("bloqueando", false)):
		marcas.append("bloquea")
	var anexa = datos.get("anexada_a_nombre")
	if anexa != null and str(anexa) != "":
		marcas.append("anexa a " + str(anexa))
	_datos.visible = marcas.size() > 0 and not boca_abajo
	_datos.text = " · ".join(marcas)
	_pintar_borde()


func iniciar_viaje(desde: Vector3, hitos: Array) -> void:
	position = desde
	_hitos = hitos.duplicate()
	if _hitos.is_empty():
		viajando = false
		return
	base_pos = _hitos[0]
	viajando = true
	velocidad = 3.6


func contiene_rayo(origen: Vector3, direccion: Vector3) -> float:
	var inversa := global_transform.affine_inverse()
	var local_origen := inversa * origen
	var local_dir := inversa.basis * direccion
	if absf(local_dir.z) < 0.00001:
		return -1.0
	var t := -local_origen.z / local_dir.z
	if t < 0.0:
		return -1.0
	var golpe := local_origen + local_dir * t
	if absf(golpe.x) <= ANCHO * 0.5 and absf(golpe.y) <= ALTO * 0.5:
		return t
	return -1.0


func _process(delta: float) -> void:
	if espera > 0.0:
		espera -= delta
		return
	var k := 1.0 - exp(-velocidad * delta)
	var destino := base_pos
	if en_mano:
		destino = base_pos + (hover_offset if hover else Vector3.ZERO)
		position = position.lerp(destino, k)
		_sombra.visible = false
		var actual := Basis(transform.basis.get_rotation_quaternion())
		transform.basis = actual.slerp(base_basis, k)
		var escala_mano := base_escala * (1.38 if hover else 1.0)
		scale = scale.lerp(Vector3.ONE * escala_mano, k)
	else:
		var extra := 0.48 if hover else 0.0
		destino = base_pos + Vector3(0, extra, 0)
		position = position.lerp(destino, k)
		_sombra.visible = true
		_sombra.position = Vector3(position.x + 0.055, 0.012, position.z + 0.07)
		# El nombre queda hacia el rival, para leerse desde esta cámara.
		var yaw := base_yaw + PI + (PI * 0.5 if girada else 0.0)
		rotation = Vector3(-PI * 0.5, lerp_angle(rotation.y, yaw, k), 0.0)
		var s := 1.2 if hover else 1.0
		scale = scale.lerp(Vector3.ONE * s, k)
	if viajando and position.distance_to(destino) < 0.07:
		if _hitos.is_empty():
			viajando = false
			velocidad = 9.0
		else:
			_hitos.pop_front()
			if _hitos.is_empty():
				viajando = false
				velocidad = 9.0
			else:
				base_pos = _hitos[0]
	_pintar_borde()


func _pintar_borde() -> void:
	_brillo.visible = legal
	if not legal:
		return
	if hover:
		_mat_brillo.albedo_color = Color(1.0, 0.96, 0.78)
	else:
		_mat_brillo.albedo_color = Color(1.0, 0.74, 0.26)
